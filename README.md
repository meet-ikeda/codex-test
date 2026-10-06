# exobrain — AI をまたいで共有する外部脳

どの AI の、どのスレッドでも、あなたのことを最初から知っている状態で会話を始めるための記憶アプリです。
情報を「要素」に分けて「つながり」で結び、人間の脳のように記憶します。

- **仕様書 v0.8（最新。これだけ読めばよい）: [docs/spec-v0.8.md](docs/spec-v0.8.md)**
- 日報の書き方: [docs/daily-log-rules.md](docs/daily-log-rules.md)、記憶の流れの図: [docs/flow.html](docs/flow.html)
- 経緯: [v0.6](docs/spec-v0.6.md)・[v0.7](docs/spec-v0.7.md)（v0.8 にまとめ直した）
- 要件: [docs/requirements.md](docs/requirements.md)
- 設計: [docs/design.md](docs/design.md)（v0.1 の設計。v0.8 と食い違う部分は v0.8 が優先）
- 評価: [docs/eval-questions-v2.md](docs/eval-questions-v2.md)（固定した評価質問）、[docs/eval-embedding.md](docs/eval-embedding.md)（検索の設定の測定）
- Mac への導入手順: [docs/setup-mac.md](docs/setup-mac.md)

## 進み具合

| 段階 | 内容 | 状態 |
|---|---|---|
| M1 | イベント記録・要素・つながり・本棚への原文保存、`start_session` / `remember` / `submit_daily_report` / `open_source` | ✅ |
| M2 | 活性化拡散による想起（`recall`）、トークン上限、ひらめき枠、短期記憶、ヘッブ則による強化 | ✅ |
| M3 | 受け取り箱、本棚の全文検索、指摘による書き換え（`trace_correction` / `apply_correction`）、消去・一時停止・バックアップと復元 | ✅ |
| M4 | 睡眠（段階 A・B、夢日記、棚の目録、`exobrain sleep [--if-due] [--no-ai]`、launchd 設定の生成） | ✅ |
| M5 | 画面（`exobrain app`）: グラフ・本棚・メモ・睡眠ボタンとタイマー・安全装置 | ✅ |
| M6 | Mac への導入（`exobrain install` / `doctor` / `uninstall` / `open`）と実地確認 | ✅（2026-09-26 に導入） |
| v0.5 第1区切り | 預かりBOX（`#remember` の見張り、AI 日報 形式 v1、ダウンロードの見張り、追加の受け口）、海馬と本棚の索引（チャンク・bge-m3 の意味検索）、段階的な想起（大脳皮質 → 海馬 → 本棚 → Obsidian → 記録なし）、`remember_explicit` / `submit_daily_log`。AI が自分の判断で大脳皮質に書く道具（`remember` / `submit_daily_report`）は廃止 | ✅ |
| v0.5 第2区切り | 睡眠A（明示・反復の信号で昇格。引用はプログラムが原文から切り出す。重複は出典を足す）、`/good`、画面の4区分（01 Brain）、大脳皮質の Google ドライブへの書き出し（写し）、Codex・Claude Code の会話から毎晩の日報、大脳皮質の意味による想起、睡眠のトークン記録（`exobrain usage`） | ✅ |
| v0.5 第3区切り | 睡眠B（連想）、重みの本格運用、忘却、画面の脳らしい動き、意外な結びつき、スマホからの入口 | これから |

## AI とノートからの預け方

| どこから | どうする |
|---|---|
| Obsidian | exobrain プラグイン（`integrations/obsidian-plugin/exobrain` を保管庫の `.obsidian/plugins/` に置いて有効化）の脳のボタン。受付で種類を確かめ、受領印を押し、次からは差分だけ送る。`#remember` タグでも取り込む |
| Claude の Chat | 「/預けて」（スキル `integrations/claude-skill/exobrain-deposit.zip` を Claude の設定から追加）、会話の終わりに「/日報」 |
| ChatGPT・Gemini | `integrations/chatgpt-gemini.md` の文面をカスタム指示・Gem に貼り、「/預けて」で出るファイルをダウンロード |
| Codex・Claude Code・Cowork | 何もしなくてよい（毎晩の睡眠で日報にする）。過去の会話は画面の 04 Sleep の「過去のスレッド」から預ける |
| どの AI でも | 「/思い出して」で記憶を読む。「覚えておいて」「/good」「前にも言ったよね」 |

## データの置き場所

| もの | 場所 | 変更できるか |
|---|---|---|
| 脳（`brain.db`） | `~/.exobrain/`（PC 内）。環境変数 `EXOBRAIN_HOME` で変更可 | イベント記録は追記のみ |
| 本棚・受け取り箱・バックアップ | Google ドライブ同期フォルダ（`~/.exobrain/config.json` の `drive_root`、または環境変数 `EXOBRAIN_DRIVE`） | 原文は書き換えない |

## 開発

```bash
pip install -e ".[dev]"
pytest
exobrain verify    # 改ざんと本棚の原文をチェック
exobrain rebuild   # イベント記録から脳の表を作り直す
exobrain stats     # 記憶の件数
exobrain ingest    # 預かりBOX と Obsidian の #remember を取り込む（常駐の画面が 1 分ごとにも実行）
exobrain encode --all   # 意味検索の埋め込みを作る（Ollama の bge-m3）
exobrain recall "話題"  # 思い出す（大脳皮質 → 海馬 → 本棚 → Obsidian）
exobrain settings --vault <保管庫> --add-extra-inbox <フォルダ> --embed-model bge-m3
exobrain settings --daily-logs-from 2026-09-27   # この日以降の Codex・Claude Code の会話から毎晩日報を作る
exobrain export   # 大脳皮質の写しを Google ドライブへ（睡眠のたびにも自動）
exobrain usage    # 睡眠で使ったトークン数
exobrain threads --search 採用     # この Mac に残っている Codex・Claude Code のスレッド
exobrain backfill 01a0cc8f          # そのスレッドの過去分（毎晩の日報より前）を、次の睡眠から日報にする
exobrain fade src_...              # 原文を海馬から外す（昇格させない。本棚には残る）
exobrain memo 題名 < メモ.md
exobrain search 語句
exobrain ask [質問]   # 脳と話す（中身・自分がどう見えているか。読むだけ）
exobrain look [--topic 話題] [--kind 種類] [--id 記憶]   # 脳の中を AI なしで見る
exobrain sleep [--if-due] [--no-ai]   # 睡眠（AI による整理は Claude Code を使う）
exobrain app      # 画面を開く（http://127.0.0.1:8765）
exobrain pause / resume
exobrain backup / restore [バックアップ]
exobrain erase --source <原文 id> [--node <要素 id>] [--since 日付 --until 日付] --confirm 消去する
```

## 同梱しているライブラリ

画面のグラフ表示に次のライブラリを同梱しています（いずれも MIT ライセンス。全文は `src/exobrain/web/vendor/LICENSES.txt`）。

- graphology 0.26.0
- graphology-library 0.8.0（配置の計算 ForceAtlas2 に使用）

グラフの描画（粒子の銀河）は自作の WebGL（`src/exobrain/web/galaxy.js`）です。

書体（SIL Open Font License 1.1。全文は `src/exobrain/web/fonts/LICENSES.txt`。@fontsource 5.3.0 から取得）:

- Inter Tight
