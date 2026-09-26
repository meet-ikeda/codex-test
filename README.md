# exobrain — AI が上書きできない外部脳

AI と一緒に使う長期記憶ストアです。**AI は記憶を追加・検索・修正提案できますが、書き換えも削除もできません。**
記憶を書き換えられるのは人間（あなた）だけで、その場合も古い版は履歴として必ず残ります。

## 設計

| 仕組み | 役割 |
| --- | --- |
| 追記専用 SQLite | `UPDATE` / `DELETE` はトリガーで拒否。変更はすべて新しいレコードの追記として表現 |
| ハッシュチェーン | 各レコードが直前レコードの SHA-256 を含む。ファイルを直接いじると `verify` で検出 |
| 権限の分離 | AI の入口は MCP サーバーだけで、上書き・撤回系のツールは公開していない。上書き・撤回・提案の採否は人間用 CLI のみ |
| 出所の記録 | すべての記憶に `author`（`human` / `ai`）が付く。AI にも「human の記憶を優先せよ」と指示している |

### レコードの種類

- `memory` — 記憶本体。`ref` があれば、その記憶の新しい版（人間のみ作成可）
- `proposal` — AI などによる修正提案。人間が `accept` するまで現在の記憶は変わらない
- `retraction` — 記憶の撤回、または提案のクローズ（人間のみ）

## インストール

```bash
pip install -e ".[dev]"
```

保存先は既定で `~/.exobrain/memory.db`。環境変数 `EXOBRAIN_DB` か `--db` で変更できます。

## AI につなぐ（MCP）

Claude Code の場合:

```bash
claude mcp add exobrain -s user -- exobrain-mcp
```

AI が使えるツール:

| ツール | 内容 |
| --- | --- |
| `remember` | 新しい記憶を追加（`author=ai` で記録） |
| `recall` | 現在の記憶を検索。対象の記憶への未処理の提案も一緒に返す |
| `history` | ある記憶の全版・提案・撤回を時系列で表示 |
| `propose_revision` | 修正を提案（現在の記憶は変わらない） |
| `verify_integrity` | ハッシュチェーンの検証 |

## 人間用 CLI

```bash
exobrain add "好きな飲み物はほうじ茶" -t 好み
exobrain list ほうじ茶            # 空白区切りの語をすべて含むものを検索
exobrain list --author ai         # AI が書いた記憶だけ見る
exobrain show 3                   # 全履歴
exobrain revise 3 "好きな飲み物は玄米茶"   # 新しい版を作る（旧版は残る）
exobrain retract 3 -r "もう不要"
exobrain proposals                # AI からの修正提案を確認
exobrain accept 7 / exobrain reject 7
exobrain verify                   # 改ざんチェック
exobrain export > backup.jsonl
```

## 保証の範囲

- AI が MCP 経由でしか触れない限り、AI は既存の記憶を変えられません（ツールが存在しないため）。
- DB ファイルに直接書き込める主体（あなた自身や、シェル権限を持つエージェント）はトリガーを外して改ざんできます。その場合も `verify` で検出はできますが、防ぐことはできません。AI にシェルを渡す場合は、DB を AI の書き込めない場所に置いてください。
- 検索は `LIKE` による部分一致です。日本語でも分かち書き不要で使えますが、意味検索ではありません。

## テスト

```bash
pytest
```
