---
name: exobrain-deposit
description: オーナー（池田さん）が「/預けて」「exobrain に預けて」「この会話を覚えといて」と言ったときに使う。この会話（前に預けたあとの部分）を、要約と手続き記憶・意味記憶・エピソード記憶の候補にまとめ、exobrain の預かりBOX に届ける。取材・クライアント・機密かを先に確かめる。
---

# exobrain に預ける

## 1. 受付で確かめる
次が会話から明らかでなければ、預ける前にオーナーに短く聞く（明らかなら聞かない）。
- 取材の会話か → note_type = "interview"（書かれた体験は取材相手のもの）
- クライアントについての話か → note_type = "client"
- 外に出したくない内容か → confidential = true

## 2. まとめる
- 対象は、この会話のうち前に預けたあとの部分だけ（初めてなら全体）
- summary: 何の会話か（数行）/ procedural: やり方・ルール・好み・注意されたこと / reasons: オーナーがこだわり・理由・気持ちを口にしたもの。何についてかと、オーナーの言葉をなるべくそのまま書く。AI が人柄や性格を推測して書かない / semantic: 事実・決定・オーナーの仕事の状況・考えていること / episodes: 出来事
- どれも 1 項目 1 文。会話にあったことだけ。誰の話かを主語で書く。電話番号・住所・パスワードなどは書かない

## 3. 届ける
- **exobrain の道具（MCP）が使えるとき**: `deposit` を呼ぶ（会話の id は `exo_session`。なければ `start_session` を include_profile=false で呼ぶ）。返ってきた message_to_user をそのまま伝える
- **道具がないとき**: 次の形の Markdown ファイルを作り、ダウンロードできる形で渡す。ファイル名は exobrain-預け入れ-YYYYMMDD-短い題名.md。ダウンロードすると、exobrain が「ダウンロード」フォルダから拾う

```
---
exobrain_kind: deposit
exobrain_source: "chatgpt"
exobrain_thread_title: "この会話の短い題名"
exobrain_note_type: ""
exobrain_confidential: false
---

# 預け入れ · この会話の短い題名

## 要約

## 手続き記憶（やり方・ルール・好み・注意されたこと）

## こだわり・理由（オーナー本人の言葉）

## 意味記憶（事実・決定・仕事の状況・考えていること）

## エピソード記憶（出来事）
```

該当がない欄は「- 特になし」。--- で囲んだ先頭部分は必ずそのまま入れる。
