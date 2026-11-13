# Systematic Tutor（Claude Code 用 学習スキル）

## 構成

```
.claude/skills/systematic-tutor/
├── SKILL.md                         # スキル本体（Claude が読む指示書）
├── test_runner.py                   # ローカルHTMLテストサーバー（Python 標準ライブラリのみ）
└── templates/
    ├── study_log.md                 # 科目の索引テンプレート（ルートの study_log.md になる）
    ├── subject_log.md               # 科目ごとの学習記録テンプレート（study_logs/<slug>.md になる）
    └── questions.example.json       # テスト問題のサンプル
```

## 使い方

1. このフォルダの中身を、学習用の作業フォルダ（任意）にそのまま展開します。`.claude` フォルダは隠しフォルダなので、展開後に見えなくても問題ありません。
2. 作業フォルダでターミナルを開き、`claude` を起動します。
3. 「`systematic-tutor` を使って〇〇を学びたい」と話しかけると始まります。
4. 2回目以降は「`study_log.md` を読んで続きから始めて」と伝えれば再開できます。

## 複数科目の学習

科目ごとに記録が分かれます。`study_log.md` は科目の一覧（索引）、各科目の詳細は `study_logs/english.md`、`study_logs/chemistry.md` のように別ファイルに保存されます。「英語の続きから」「化学を始めたい」と言えば、その科目の記録だけを読み書きします。テストの一時ファイルも `.study_tmp/<科目>/` に分かれます。

## テストサーバーの動作確認

```bash
mkdir -p .study_tmp
python3 .claude/skills/systematic-tutor/test_runner.py \
  --questions .claude/skills/systematic-tutor/templates/questions.example.json \
  --output .study_tmp/answers.json
```

ブラウザが自動で開かない環境（WSL2、リモートSSH など）では、ターミナルに表示される `http://127.0.0.1:8765` を手動で開いてください（8765 番が使用中の場合は別の番号が表示されます）。提出すると `.study_tmp/answers.json` が作られ、サーバーは自動で終了します。

## 生成されるファイル

- `study_log.md`：学習記録（プロジェクトルート）
- `.study_tmp/`：テスト用の一時ファイル（`questions.json`、`answers.json`、`draft.json`、`session_state.json`、`server.log`）

`.study_tmp/` は削除しても学習記録には影響しません。Git で管理する場合は `.gitignore` に追加しておくとよいです。

## 動作要件

- Python 3.8 以上
- 数式表示（`"math": true`）を使う場合のみインターネット接続

## 作者からの一言

大前提として、やる気も学びたいこともあるが、体系的な学習の仕方がわからない、難しい人向けのskillです。
不正対策などは考えておらず、しようと思えばいくらでも出来ますが、抜け道を探すような人には向いていないと思います。
