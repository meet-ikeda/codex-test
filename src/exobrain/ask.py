"""`exobrain ask`: talk to the brain about what is inside it (spec v0.7 §10).

Claude Code runs with only the read-only introspection tools, so the answer
comes from the brain's own contents, not from the chat's own memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .brain import Brain

SERVER = "exobrain-introspect"
TIMEOUT_SECONDS = 600
SYSTEM = """\
あなたは exobrain（オーナーの外部脳）の中身を、オーナー本人に見せる役です。
答えは、道具（brain_overview・list_memories・open_memory・open_source）で読んだ脳の中身だけを根拠にします。
あなた自身の一般知識や、この会話より前の記憶で埋めないでください。

答え方:
- 「脳に入っていること」と「そこからの解釈」を分けて書く。入っていることには記憶の id か原文の日付を添える
- オーナーのことを聞かれたら、個々のメモの言い換えで終わらせず、複数の記憶に共通する傾向（大事にしていること・判断の癖・避けたいこと）に抽象化して答える。根拠になった記憶を2〜3件添える
- 気づいたことがあれば正直に言う: 特定の案件だけの発言が全体のルールとして入っていそうなもの、同じことの重複、古くなっていそうなもの、偏り
- 脳に見当たらないことは「脳には見当たらない」と言う
- 日本語で、簡潔に。見出しや箇条書きは必要なときだけ
"""


def _config(brain: Brain) -> tuple[str, dict[str, str]]:
    env = {"EXOBRAIN_HOME": str(brain.settings.home), "EXOBRAIN_DRIVE": str(brain.settings.drive_root)}
    config = {"mcpServers": {SERVER: {"command": sys.executable, "args": ["-m", "exobrain.mcp_server", "--introspect"],
                                      "env": env}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(config, f)
    return f.name, env


def ask(brain: Brain, question: str | None) -> int:
    """With a question: answer once and print it. Without: open a conversation in the terminal."""
    from .sleep import find_claude

    exe = find_claude(brain)
    if exe is None:
        print("Claude Code（claude コマンド）が見つかりません。docs/setup-mac.md の手順 2 で入れてください。", file=sys.stderr)
        return 2
    config_path, env = _config(brain)
    args = [exe, "--mcp-config", config_path, "--strict-mcp-config", "--allowedTools", f"mcp__{SERVER}",
            "--append-system-prompt", SYSTEM]
    try:
        if question:
            proc = subprocess.run([*args, "-p", question], capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                                  cwd=str(brain.settings.home), env={**os.environ, **env})
            if proc.returncode != 0:
                print(proc.stderr.strip() or proc.stdout.strip() or f"claude が終了コード {proc.returncode} で終わりました。",
                      file=sys.stderr)
                return 1
            print(proc.stdout.strip())
            return 0
        print("exobrain の脳と話します（読むだけで、記憶は変わりません）。終えるときは /exit。\n")
        return subprocess.run(args, cwd=str(brain.settings.home), env={**os.environ, **env}).returncode
    except subprocess.TimeoutExpired:
        print("時間内に答えが返りませんでした。質問を絞ってもう一度試してください。", file=sys.stderr)
        return 1
    finally:
        os.unlink(config_path)
