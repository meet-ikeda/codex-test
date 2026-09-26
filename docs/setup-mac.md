# exobrain を Mac に入れる手順

所要時間: 15〜20 分。ターミナル（「アプリケーション」→「ユーティリティ」→「ターミナル」）にコマンドを貼り付けて進めます。

- 各コマンドは 1 行ずつ貼り付けて Enter を押してください。
- 記号 `$` は入力しません。

---

## 0. 事前に入っているもの（確認だけ）

| もの | 使いみち | 確認のしかた |
|---|---|---|
| Claude Desktop（Claude Pro） | 記憶を使う AI その 1 | アプリがある |
| Codex アプリ | 記憶を使う AI その 2 | アプリがある |
| Google ドライブ for desktop | 本棚・受け取り箱・バックアップの置き場所 | Finder のサイドバー「場所」に Google ドライブがある |

## 1. uv を入れる（Python ごと面倒を見てくれる道具）

Mac に最初から入っている Python は 3.9.6 で、exobrain に必要な 3.10 以上を満たしません。uv を使うと、必要な Python も自動で用意されます。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

終わったら、**ターミナルを一度閉じて開き直し**、次で確認します。

```bash
uv --version
```

出典: uv の公式 README（ https://github.com/astral-sh/uv ）、macOS の Python 3.9.6 については https://www.freecodecamp.org/news/how-to-fix-python-installation-errors-on-mac

## 2. Claude Code（コマンド版）を入れる

AI による睡眠（記憶の整理）に使います。Claude Desktop に入っている Claude Code とは別に、ターミナル用の `claude` コマンドが必要です。

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

ターミナルを開き直して、次を実行します。

```bash
claude --version
```

続けて一度だけ `claude` を起動し、画面の案内に従って Claude Pro のアカウントでログインしてください。終わったら `/exit` で閉じます。

出典: Claude Code 公式ドキュメント（ https://code.claude.com/docs/en/overview ）

## 3. exobrain を入れる

```bash
uv tool install --python 3.12 "git+https://github.com/meet-ikeda/codex-test@claude/external-brain-memory-app-382xp2"
```

`exobrain` と `exobrain-mcp` の 2 つのコマンドが入ります。`command not found` と出たら、次を実行してからターミナルを開き直してください。

```bash
uv tool update-shell
```

## 4. 導入コマンドを実行する

毎日 3 時に眠らせる場合の例です。時刻を決めない場合は `--sleep-at 03:00` を省きます（その場合もログイン時には眠ります）。

```bash
exobrain install --sleep-at 03:00
```

このコマンドは次のことを行います。書き換える設定ファイルは、先に同じ場所へ控え（`〜.before-exobrain-日時`）を取ります。

| すること | 場所 |
|---|---|
| Google ドライブの中に exobrain フォルダを作る（本棚・受け取り箱・バックアップ） | `~/Library/CloudStorage/GoogleDrive-（アカウント）/マイドライブ（または My Drive）/exobrain` |
| Claude Desktop に登録 | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex に登録 | `~/.codex/config.toml` |
| 画面を常駐させる（ログインすると自動で起動） | `~/Library/LaunchAgents/jp.exobrain.app.plist` |
| 睡眠の自動実行（ログイン時と設定時刻） | `~/Library/LaunchAgents/jp.exobrain.sleep.plist` |

Google ドライブのアカウントが複数ある場合は、どこに置くかを番号で聞かれます。

置き場所は「マイドライブ」（英語表示では「My Drive」）の中が自動で選ばれます。別の場所にしたいときは `--drive` で指定します。

```bash
exobrain install --drive "$HOME/Library/CloudStorage/GoogleDrive-（アカウント）/マイドライブ/exobrain" --sleep-at 03:00
```

## 5. AI に読み込ませる

**Claude Desktop と Codex を完全に終了（⌘Q）してから、開き直してください。** 開き直さないと、登録した設定が読み込まれません。

## 6. 点検する

```bash
exobrain doctor
```

すべて ✓ になれば導入は完了です。! や ✗ が出たら、その行をそのまま教えてください。

## 7. 画面を開く

ブラウザで http://127.0.0.1:8765/ を開くか、次のコマンドを実行します。

```bash
exobrain open
```

---

## v0.5 の追加設定（意味検索と Obsidian）

1. Ollama の公式アプリを入れ、埋め込みモデルを取り込む（約1.2GB）

   ```bash
   /Applications/Ollama.app/Contents/Resources/ollama pull bge-m3
   ```

2. exobrain に、`#remember` を探す Obsidian の保管庫を教える（保管庫には書き込みません）

   ```bash
   exobrain settings --vault "<Obsidian の保管庫のフォルダ>"
   ```

3. 取り込んで、埋め込みを作る。`exobrain doctor` で「意味検索（Ollama）」と「Obsidian の保管庫」が ✓ になれば完了

   ```bash
   exobrain ingest
   exobrain encode --all
   exobrain doctor
   ```

Ollama が止まっていても exobrain は動きます（文字の一致だけで探します）。

4. 毎晩の睡眠で AI（Claude Code）を使うため、コマンド版の Claude Code にログインしておく。`exobrain doctor` の「Claude Code のログイン」が ✓ なら済んでいます

   ```bash
   claude auth login
   ```

5. Codex・Claude Code の会話から毎晩日報を作るなら、どの日以降の会話を対象にするかを決める（決めるまでは作りません）

   ```bash
   exobrain settings --daily-logs-from 2026-09-27
   ```

チャットでの合図: 「覚えておいて」でその場で記憶、`/日報` でその会話の日報、`/good` で褒めたやり方を記憶して、使った記憶を強めます。

---

## 実地確認のチェックリスト（要件定義書 9 章の受け入れ基準）

上から順に試して、結果を教えてください。うまくいかなかったものは、どの AI で・何を言ったかを添えてください。

| # | 試すこと | 期待する結果 |
|---|---|---|
| 1 | Claude Desktop で新しいチャットを開き、「私の好みを覚えておいて: 文章は結論から書いてほしい」と伝える。最後に `/日報` と送る | AI が exobrain の道具（start_session、remember_explicit、submit_daily_log）を使う。許可を求められたら「許可」 |
| 2 | Claude Desktop で**別の新しいチャット**を開き、「メールの下書きを書いて」と頼む | 何も説明しなくても結論から書く |
| 3 | Codex で同じように何か頼む | Claude Desktop で覚えたことを踏まえている |
| 4 | 画面の「01 Graph」を見る | 覚えた記憶が粒子として現れている |
| 5 | Google ドライブの `exobrain/受け取り箱` に、メモ（.md か .txt）を置く | 1 分ほどで「02 Bookshelf」に原文のまま並ぶ |
| 6 | AI に同じ注意を 2 回する（例: 「さっきも言ったけど、結論から書いて」） | 「以前にも同じ指摘を受けていました（今回で 2 回目）」と答える |
| 7 | AI に、脳にも本棚にもないことを指摘する | 「過去の記憶にも本棚にもありません」と答える |
| 8 | 画面の「04 Sleep」で「眠る — AI で整理する」を押す | しばらくして夢日記が増え、「Changed in sleep」で変化が見える |
| 9 | 画面の「05 Safety」で一時停止を入れ、AI に何か覚えさせる | AI が「一時停止中です」と返す。確認できたら解除 |
| 10 | 画面の「05 Safety」→「改ざんをチェックする」 | 「Intact — 改ざんはありません」 |
| 11 | 画面のグラフを拡大・移動する | 引っかからずに動く（重ければ、記憶のおおよその件数と Mac の機種を教えてください） |

## 困ったとき

| 症状 | 対処 |
|---|---|
| Claude Desktop / Codex に exobrain の道具が出てこない | アプリを ⌘Q で完全に終了して開き直す。それでもだめなら `exobrain doctor` の結果を教えてください |
| 画面が開かない | `exobrain open` を実行（常駐が止まっていても、その場で画面を起動します） |
| AI による睡眠が動かない | `~/.exobrain/last-sleep.log` の中身を教えてください |
| 元に戻したい | `exobrain uninstall`（AI への登録と常駐を外します。記憶は消えません） |
| アップデートしたい | `uv tool upgrade exobrain` |
| `exobrain install` が「フォルダを作れませんでした」で止まる | 書き込めない場所（「その他のパソコン」など）が選ばれています。`uv tool upgrade exobrain` で最新にするか、`--drive` でマイドライブの中を指定してください。このエラーのときは、ほかの設定は何も変更していません |

## 確認できていないこと（実機で確かめたい点）

- お手元の Mac の Python に含まれる SQLite が 3.42 以上か（`exobrain doctor` に表示されます。古い場合も動きますが、消去のしかたが変わります）
- `launchctl bootstrap` による常駐・睡眠の登録が、お手元の macOS で成功するか（出典は第三者の記事のみ）
- AI（Claude Desktop・Codex）が、指示なしで自発的に記憶を使うか（MCP サーバーの指示に従うかは AI 次第）
- 記憶が増えたときのグラフの滑らかさ（開発環境には GPU がないため未計測）
- AI による睡眠 1 回で使う Claude Pro の利用枠
