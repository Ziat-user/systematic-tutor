#!/usr/bin/env python3
"""
Local one-shot HTML test server for the Systematic Tutor skill.

- questions.json から受験用フォームを生成して 127.0.0.1 で配信する
- 開始時刻はサーバー側で記録（ブラウザ側の改ざん・古いデータの影響を受けない）
- 入力途中の解答はブラウザ(localStorage)とサーバー(draft.json)の両方に自動保存
- long モードは制限時間で自動提出。ブラウザが閉じられていても、
  制限時間＋猶予を過ぎるとサーバーが下書きを answers.json として確定して終了する
- 提出されないまま --max-wait-minutes を過ぎた場合も下書きで確定して終了する
- 選択式は「番号＋選択肢テキスト」で記録（表記揺れによる採点ずれを防ぐ）

Python 標準ライブラリのみで動作します。
"""

import argparse
import html
import json
import os
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

PREFERRED_PORT = 8765
GRACE_SECONDS = 120  # 制限時間後、ブラウザからの自動提出を待つ猶予


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def build_html(data: dict) -> str:
    title = html.escape(str(data.get("title", "理解度テスト")))
    use_math = bool(data.get("math", False))
    questions = data.get("questions", [])

    q_blocks = []
    for idx, q in enumerate(questions, start=1):
        qid = html.escape(str(q.get("id", f"q{idx}")))
        cat = html.escape(str(q.get("category", "標準")))
        qtype = q.get("type", "text")
        prompt = html.escape(str(q.get("prompt", "")))

        badge_class = "badge-std"
        if "基礎" in cat:
            badge_class = "badge-basic"
        elif "発展" in cat or "チャレンジ" in cat:
            badge_class = "badge-adv"

        if qtype == "choice" and isinstance(q.get("options"), list):
            opts_html = []
            for opt_i, opt in enumerate(q["options"]):
                opt_esc = html.escape(str(opt))
                opts_html.append(
                    f'<label class="option-label">'
                    f'<input type="radio" name="{qid}" value="{opt_i}" onchange="saveDraft()">'
                    f'<span class="opt-no">{opt_i + 1}.</span>'
                    f'<span class="opt-text">{opt_esc}</span>'
                    f'</label>'
                )
            input_area = f'<div class="options-group">{"".join(opts_html)}</div>'
        else:
            rows = int(q.get("rows", 6))
            input_area = (
                f'<textarea name="{qid}" rows="{rows}" '
                f'placeholder="解答を入力してください（分からない場合は空欄のまま提出できます）" '
                f'oninput="saveDraft()"></textarea>'
            )

        q_blocks.append(f"""
        <section class="question-card">
          <div class="q-header">
            <span class="q-num">問 {idx}</span>
            <span class="q-badge {badge_class}">{cat}</span>
          </div>
          <div class="q-prompt">{prompt}</div>
          {input_area}
        </section>
        """)

    math_head = ""
    math_init = ""
    if use_math:
        # 数式表示（KaTeX）。インターネット接続が必要。オフラインでも問題文はそのまま表示される。
        math_head = (
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">'
            '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>'
            '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>'
        )
        math_init = """
    window.addEventListener("load", () => {
      if (window.renderMathInElement) {
        document.querySelectorAll(".q-prompt, .opt-text").forEach(el =>
          renderMathInElement(el, {
            delimiters: [
              {left: "$$", right: "$$", display: true},
              {left: "$", right: "$", display: false},
              {left: "\\\\(", right: "\\\\)", display: false},
              {left: "\\\\[", right: "\\\\]", display: true}
            ],
            throwOnError: false
          })
        );
      }
    });
"""

    test_id_js = json.dumps(str(data.get("test_id", "default_test")))
    mode_js = json.dumps(str(data.get("mode", "quick")))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  {math_head}
  <style>
    :root {{
      --bg: #f3f4f6; --card: #ffffff; --text: #1f2937; --muted: #6b7280;
      --border: #d1d5db; --hover: #f9fafb; --header: #111827; --header-text: #ffffff;
      --timer: #374151; --accent: #2563eb; --accent-hover: #1d4ed8;
      --notice-bg: #eff6ff; --notice-border: #3b82f6; --ok: #059669; --warn: #dc2626;
      --basic: #059669; --std: #2563eb; --adv: #d97706;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0f172a; --card: #1e293b; --text: #e5e7eb; --muted: #9ca3af;
        --border: #334155; --hover: #273449; --header: #020617; --header-text: #f1f5f9;
        --timer: #334155; --notice-bg: #172554; --notice-border: #60a5fa;
      }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Noto Sans JP", Roboto, Arial, sans-serif;
      background: var(--bg); color: var(--text); margin: 0;
    }}
    header {{
      position: sticky; top: 0; background: var(--header); color: var(--header-text);
      padding: 14px 24px; display: flex; justify-content: space-between; align-items: center;
      gap: 12px; box-shadow: 0 2px 6px rgba(0,0,0,0.15); z-index: 100;
    }}
    header h1 {{ font-size: 1.1rem; margin: 0; }}
    .timer {{
      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 1.05rem; font-weight: bold;
      background: var(--timer); padding: 6px 12px; border-radius: 6px; white-space: nowrap;
    }}
    .timer.warning {{ background: var(--warn); color: #fff; }}
    main {{ max-width: 820px; margin: 28px auto; padding: 0 16px 60px; }}
    .notice {{
      background: var(--notice-bg); border-left: 4px solid var(--notice-border);
      padding: 12px 16px; margin-bottom: 24px; border-radius: 4px; font-size: 0.92rem; line-height: 1.6;
    }}
    .question-card {{
      background: var(--card); border-radius: 8px; padding: 20px; margin-bottom: 20px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}
    .q-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
    .q-num {{ font-weight: bold; font-size: 1.05rem; }}
    .q-badge {{ color: #fff; font-size: 0.78rem; padding: 2px 8px; border-radius: 999px; font-weight: 600; }}
    .badge-basic {{ background: var(--basic); }}
    .badge-std {{ background: var(--std); }}
    .badge-adv {{ background: var(--adv); }}
    .q-prompt {{ font-size: 1rem; line-height: 1.7; margin-bottom: 14px; white-space: pre-wrap; overflow-x: auto; }}
    .options-group {{ display: flex; flex-direction: column; gap: 8px; }}
    .option-label {{
      display: flex; align-items: center; gap: 10px; padding: 10px 12px;
      border: 1px solid var(--border); border-radius: 6px; cursor: pointer;
    }}
    .option-label:hover {{ background: var(--hover); }}
    .opt-no {{ color: var(--muted); font-weight: 600; }}
    textarea {{
      width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid var(--border);
      border-radius: 6px; background: var(--card); color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.95rem; line-height: 1.5; resize: vertical;
    }}
    textarea:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37,99,235,0.2); }}
    .actions {{ text-align: center; margin-top: 32px; }}
    button#submit-btn {{
      background: var(--accent); color: #fff; border: none; padding: 14px 36px;
      font-size: 1.05rem; font-weight: bold; border-radius: 8px; cursor: pointer;
    }}
    button#submit-btn:hover {{ background: var(--accent-hover); }}
    button#submit-btn:disabled {{ background: #9ca3af; cursor: not-allowed; }}
    #status-msg {{ margin-top: 16px; font-weight: bold; color: var(--ok); line-height: 1.6; }}
    #save-state {{ font-size: 0.8rem; color: var(--muted); margin-top: 8px; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div id="timer-box" class="timer"><span id="timer-label">経過時間</span>: <span id="timer-display">--:--</span></div>
  </header>
  <main>
    <div class="notice">
      入力内容は自動保存されます（タブを閉じても再度開けば続きから解答できます）。
      分からない問題は空欄のまま提出して構いません。提出後に採点と振り返りを行います。
    </div>
    <form id="test-form" onsubmit="return false;">
      {"".join(q_blocks)}
      <div class="actions">
        <button type="button" id="submit-btn" onclick="submitAnswers(false)">回答を提出する</button>
        <div id="save-state"></div>
        <div id="status-msg"></div>
      </div>
    </form>
  </main>

  <script>
    const TEST_ID = {test_id_js};
    const MODE = {mode_js};
    const STORAGE_KEY = "systematic_tutor_draft_" + TEST_ID;
    let submitted = false;
    let startedAtMs = null;     // サーバーが記録した開始時刻
    let timeLimitSec = 0;
    let clockOffsetMs = 0;      // サーバー時刻 - ブラウザ時刻
    let autosaveTimer = null;

    function nowServer() {{ return Date.now() + clockOffsetMs; }}

    function collectAnswers() {{
      const form = document.getElementById("test-form");
      const result = {{}};
      for (const el of form.elements) {{
        if (el.name && !(el.name in result)) result[el.name] = "";
      }}
      for (const [k, v] of new FormData(form).entries()) result[k] = v;
      return result;
    }}

    function applyAnswers(saved) {{
      const form = document.getElementById("test-form");
      for (const [key, val] of Object.entries(saved || {{}})) {{
        const els = form.elements[key];
        if (!els) continue;
        if (els.length && els[0] && els[0].type === "radio") {{
          for (const r of els) r.checked = (r.value === String(val));
        }} else if (els.type === "radio") {{
          els.checked = (els.value === String(val));
        }} else {{
          els.value = val;
        }}
      }}
    }}

    function saveDraft() {{
      if (submitted) return;
      try {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(collectAnswers())); }} catch (e) {{}}
      // サーバーへの保存は入力が落ち着いてから送る
      clearTimeout(autosaveTimer);
      autosaveTimer = setTimeout(pushDraft, 1500);
    }}

    async function pushDraft() {{
      if (submitted) return;
      try {{
        const res = await fetch("/autosave", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ test_id: TEST_ID, answers: collectAnswers() }})
        }});
        if (res.ok) {{
          document.getElementById("save-state").textContent =
            "自動保存済み " + new Date().toLocaleTimeString();
        }}
      }} catch (e) {{
        document.getElementById("save-state").textContent = "サーバーへの自動保存に失敗（ブラウザには保存済み）";
      }}
    }}

    function formatTime(sec) {{
      sec = Math.max(0, Math.floor(sec));
      const m = Math.floor(sec / 60), s = sec % 60;
      return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }}

    function tick() {{
      if (submitted || startedAtMs === null) return;
      const elapsed = (nowServer() - startedAtMs) / 1000;
      const display = document.getElementById("timer-display");
      if (timeLimitSec > 0) {{
        const remaining = timeLimitSec - elapsed;
        display.textContent = formatTime(remaining);
        if (remaining <= 300) document.getElementById("timer-box").classList.add("warning");
        if (remaining <= 0) submitAnswers(true);
      }} else {{
        display.textContent = formatTime(elapsed) + "（制限なし）";
      }}
    }}

    async function init() {{
      // まずブラウザ内の下書きを復元
      try {{ applyAnswers(JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}")); }} catch (e) {{}}
      try {{
        const res = await fetch("/state");
        const st = await res.json();
        if (st.submitted) {{
          submitted = true;
          document.getElementById("submit-btn").disabled = true;
          document.getElementById("submit-btn").textContent = "提出済み";
          document.getElementById("status-msg").textContent = "このテストはすでに提出されています。";
          return;
        }}
        startedAtMs = st.started_at_ms;
        timeLimitSec = st.time_limit_seconds;
        clockOffsetMs = st.server_now_ms - Date.now();
        document.getElementById("timer-label").textContent = timeLimitSec > 0 ? "残り時間" : "経過時間";
        // ブラウザに下書きがなく、サーバーにある場合（別ブラウザで開き直した等）はサーバー側を採用
        const local = localStorage.getItem(STORAGE_KEY);
        if ((!local || local === "{{}}") && st.draft) applyAnswers(st.draft);
      }} catch (e) {{
        startedAtMs = Date.now();
      }}
      tick();
      setInterval(tick, 500);
      setInterval(pushDraft, 30000);
    }}

    async function submitAnswers(isTimeout) {{
      if (submitted) return;
      if (!isTimeout && !confirm("回答を提出しますか？（提出後は修正できません）")) return;
      submitted = true;
      const btn = document.getElementById("submit-btn");
      btn.disabled = true;
      btn.textContent = "送信中...";
      try {{
        const res = await fetch("/submit", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ test_id: TEST_ID, timed_out: isTimeout, answers: collectAnswers() }})
        }});
        if (!res.ok) throw new Error("Server error");
        try {{ localStorage.removeItem(STORAGE_KEY); }} catch (e) {{}}
        btn.textContent = "提出完了";
        document.getElementById("status-msg").textContent =
          (isTimeout ? "制限時間になったため自動提出しました。" : "提出が完了しました！") +
          "このタブを閉じ、Claude Code のチャットで「提出した」と伝えてください。";
      }} catch (e) {{
        submitted = false;
        btn.disabled = false;
        btn.textContent = "回答を提出する";
        alert("送信に失敗しました。もう一度お試しください。");
      }}
    }}

    init();
{math_init}
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def make_server(handler_cls):
    """8765 番を優先し、使用中なら OS に空きポートを割り当ててもらう。"""
    try:
        return HTTPServer(("127.0.0.1", PREFERRED_PORT), handler_cls)
    except OSError:
        return HTTPServer(("127.0.0.1", 0), handler_cls)


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 書きかけのファイルを読ませない


def main():
    parser = argparse.ArgumentParser(description="One-shot test server for Systematic Tutor")
    parser.add_argument("--questions", required=True, help="questions.json のパス")
    parser.add_argument("--output", required=True, help="answers.json の保存先")
    parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    parser.add_argument("--max-wait-minutes", type=float, default=360,
                        help="提出されない場合にサーバーを終了するまでの最大待機時間（分）")
    args = parser.parse_args()

    with open(args.questions, "r", encoding="utf-8") as f:
        q_data = json.load(f)

    test_id = str(q_data.get("test_id", "default_test"))
    mode = str(q_data.get("mode", "quick"))
    time_limit_sec = max(0, int(q_data.get("time_limit_minutes", 0))) * 60
    questions = q_data.get("questions", [])
    choice_options = {
        str(q.get("id", f"q{i}")): q.get("options", [])
        for i, q in enumerate(questions, start=1)
        if q.get("type") == "choice"
    }

    out_path = os.path.abspath(args.output)
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    draft_path = os.path.join(out_dir, "draft.json")
    state_path = os.path.join(out_dir, "session_state.json")

    # 古い answers.json を誤って採点しないよう退避する
    if os.path.exists(out_path):
        shutil.move(out_path, out_path + ".prev")

    # 同じ test_id で再起動した場合は開始時刻を引き継ぐ（タイマーのリセット防止）
    started_at_ms = None
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                st = json.load(f)
            if st.get("test_id") == test_id and not st.get("submitted"):
                started_at_ms = st.get("started_at_ms")
        except (OSError, ValueError):
            pass

    draft = {}
    if started_at_ms is not None and os.path.exists(draft_path):
        try:
            with open(draft_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("test_id") == test_id:
                draft = d.get("answers", {})
        except (OSError, ValueError):
            pass

    lock = threading.Lock()
    state = {"started_at_ms": started_at_ms, "draft": draft, "submitted": False}
    server_holder = {}

    def ensure_started():
        if state["started_at_ms"] is None:
            state["started_at_ms"] = int(time.time() * 1000)
            write_json(state_path, {"test_id": test_id, "started_at_ms": state["started_at_ms"],
                                    "submitted": False})

    def normalize(answers: dict) -> dict:
        """選択式を {selected_index, selected_text} に変換する。"""
        result = {}
        for qid, val in answers.items():
            if qid in choice_options:
                opts = choice_options[qid]
                try:
                    i = int(val)
                    if 0 <= i < len(opts):
                        text = opts[i]
                    else:
                        i, text = None, None
                except (TypeError, ValueError):
                    i, text = None, None
                result[qid] = {"selected_index": i, "selected_text": text}
            else:
                result[qid] = val if isinstance(val, str) else ""
        return result

    def finalize(answers: dict, timed_out: bool, by_server: bool, reason: str = "") -> None:
        """回答を確定して answers.json に書き出し、サーバーを止める。"""
        with lock:
            if state["submitted"]:
                return
            state["submitted"] = True
            now_ms = int(time.time() * 1000)
            started = state["started_at_ms"] or now_ms
            payload = {
                "test_id": test_id,
                "mode": mode,
                "timed_out": timed_out,
                "auto_submitted_by_server": by_server,
                "auto_submit_reason": reason,
                "time_limit_seconds": time_limit_sec,
                "elapsed_seconds": round((now_ms - started) / 1000),
                "started_at": datetime.fromtimestamp(started / 1000).isoformat(timespec="seconds"),
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "answers": normalize(answers),
            }
            write_json(out_path, payload)
            write_json(state_path, {"test_id": test_id, "started_at_ms": started, "submitted": True})
            if os.path.exists(draft_path):
                os.remove(draft_path)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note = f"（サーバー側で自動確定: {reason}）" if by_server else ""
        sys.stdout.write(f"\a\n[TEST SUBMITTED @ {now_str}] 回答を {out_path} に保存しました。{note}\n")
        sys.stdout.flush()
        threading.Thread(target=server_holder["server"].shutdown, daemon=True).start()

    def watchdog():
        launched = time.time()
        while not state["submitted"]:
            time.sleep(2)
            if time_limit_sec > 0 and state["started_at_ms"] is not None:
                elapsed = time.time() - state["started_at_ms"] / 1000
                if elapsed > time_limit_sec + GRACE_SECONDS:
                    finalize(dict(state["draft"]), True, True, "制限時間超過（ブラウザからの提出なし）")
                    return
            if time.time() - launched > args.max_wait_minutes * 60:
                finalize(dict(state["draft"]), False, True, "最大待機時間を超過")
                return

    class Handler(BaseHTTPRequestHandler):
        html_bytes = build_html(q_data).encode("utf-8")

        def log_message(self, format, *log_args):
            pass  # HTTP ログは出さない

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > 5_000_000:
                raise ValueError("bad length")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("answers", {}), dict):
                raise ValueError("bad payload")
            if data.get("test_id") != test_id:
                raise ValueError("test_id mismatch")
            return data

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with lock:
                    ensure_started()
                self._send(200, self.html_bytes, "text/html; charset=utf-8")
            elif self.path == "/state":
                with lock:
                    ensure_started()
                    self._json(200, {
                        "started_at_ms": state["started_at_ms"],
                        "server_now_ms": int(time.time() * 1000),
                        "time_limit_seconds": time_limit_sec,
                        "submitted": state["submitted"],
                        "draft": state["draft"],
                    })
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            try:
                data = self._read_json()
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "invalid request"})
                return

            if self.path == "/autosave":
                with lock:
                    if state["submitted"]:
                        self._json(409, {"error": "already submitted"})
                        return
                    state["draft"] = data.get("answers", {})
                    write_json(draft_path, {"test_id": test_id, "answers": state["draft"],
                                            "saved_at": datetime.now().isoformat(timespec="seconds")})
                self._json(200, {"status": "saved"})
            elif self.path == "/submit":
                if state["submitted"]:
                    self._json(409, {"error": "already submitted"})
                    return
                self._json(200, {"status": "ok"})
                finalize(data.get("answers", {}), bool(data.get("timed_out", False)), False)
            else:
                self._json(404, {"error": "not found"})

    server = make_server(Handler)
    server_holder["server"] = server
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    threading.Thread(target=watchdog, daemon=True).start()

    print(f"[TEST SERVER READY] {url}  (pid={os.getpid()})", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
