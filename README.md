# exobrain — AI をまたいで共有する外部脳

どの AI の、どのスレッドでも、あなたのことを最初から知っている状態で会話を始めるための記憶アプリです。
情報を「要素」に分けて「つながり」で結び、人間の脳のように記憶します。

- 要件: [docs/requirements.md](docs/requirements.md)
- 設計: [docs/design.md](docs/design.md)

## 進み具合

| 段階 | 内容 | 状態 |
|---|---|---|
| M1 | イベント記録・要素・つながり・本棚への原文保存、`start_session` / `remember` / `submit_daily_report` / `open_source` | ✅ |
| M2 | 活性化拡散による想起（`recall`）、トークン上限、ひらめき枠、短期記憶、ヘッブ則による強化 | ✅ |
| M3 | 受け取り箱、本棚の全文検索、指摘による書き換え（`trace_correction` / `apply_correction`）、消去・一時停止・バックアップと復元 | ✅ |
| M4 | 睡眠 | 未着手 |
| M5 | グラフ画面 | 未着手 |
| M6 | Mac への導入（`exobrain install`）と実地確認 | 未着手 |

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
exobrain ingest    # 受け取り箱のメモを本棚へ（AI が会話を始めたときにも自動で実行）
exobrain memo 題名 < メモ.md
exobrain search 語句
exobrain pause / resume
exobrain backup / restore [バックアップ]
exobrain erase --source <原文 id> [--node <要素 id>] [--since 日付 --until 日付] --confirm 消去する
```
