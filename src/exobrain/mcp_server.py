"""MCP server: how each AI reaches the brain.

There is deliberately no tool that erases, rewrites an original, or edits an
episode. Those are human-only (app screen / CLI).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .brain import Brain, InvalidInput, open_brain

INSTRUCTIONS = """\
exobrain は、利用者（オーナー）の外部脳です。どの AI・どのスレッドからも同じ記憶を共有します。
利用者に頼まれなくても、次のとおり自分から使ってください。

1. 会話の最初に start_session を呼び、返ってきた profile（利用者のルールと知識）に従って会話する。
   続けて、利用者の最初の発言を cue にして recall を呼ぶ。
2. 話題が変わったとき、利用者の過去・好み・決定・進行中の仕事が関わりそうなときは recall を呼ぶ。
   返ってきた「ひらめき（遠い連想）」は、役に立ちそうなら経路とともに利用者に提案してよい。
3. 会話の区切り（用件が片付いた、話題が大きく変わった、会話が終わりそう）で submit_daily_report を呼ぶ。
   - report: その会話で起きたこと・決まったこと・注意されたこと・利用者の望みを、日報として Markdown で書く。
   - elements: report を要素に分解したもの。1 要素 = 1 つの出来事(episode)・知識(semantic)・ルール(procedural)。
     注意されたことや利用者の好みは procedural にし、importance を高くする。
   - used_memory_ids: この会話で実際に役立った記憶の id（[n_...] の部分）。
4. 途中でも、すぐ残すべきことがあれば remember で追加してよい。

author が human の記憶は利用者自身の言葉で、最優先です。
記憶を消したり原文を書き換えたりする道具はありません。それは利用者だけが行います。
"""

ELEMENTS_DOC = (
    "elements: [{kind: 'episode'|'semantic'|'procedural', text: 300 文字以内, "
    "concepts: 関連する概念（固有名詞・話題）8 個まで, importance: 0〜1}]"
)


def build_server(brain: Brain) -> MCPServer:
    server = MCPServer(name="exobrain", instructions=INSTRUCTIONS)
    reads = ToolAnnotations(read_only_hint=True)
    appends = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

    def guarded(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InvalidInput as e:
            raise ToolError(str(e)) from None

    @server.tool(annotations=appends)
    def start_session(ai_name: str) -> dict[str, Any]:
        """会話の最初に必ず呼ぶ。ai_name には自分の名前（例: "Claude Desktop", "Codex"）を入れる。
        session_id と、利用者のルール・知識の要約 (profile) を返す。"""
        return guarded(brain.start_session, ai_name)

    @server.tool(annotations=appends)
    def recall(session_id: str, cue: str, budget: int = 1500) -> dict[str, Any]:
        """いまの話題 (cue) から、つながりをたどって関連する記憶を思い出す。
        返り値の context をそのまま読めばよい。budget はトークン上限（300〜4000）。
        [n_...] は記憶の id。役に立ったものは日報の used_memory_ids で報告する。"""
        return guarded(brain.recall, session_id, cue, budget)

    @server.tool(annotations=appends, description="会話の途中で、すぐ残すべき記憶を追加する。" + ELEMENTS_DOC)
    def remember(session_id: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
        return guarded(brain.remember, session_id, elements)

    @server.tool(
        annotations=appends,
        description="会話の区切りで日報を提出する。report は原文のまま本棚に保管され、elements は脳に記憶される。"
        + ELEMENTS_DOC,
    )
    def submit_daily_report(
        session_id: str,
        title: str,
        report: str,
        elements: list[dict[str, Any]],
        used_memory_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return guarded(brain.submit_daily_report, session_id, title, report, elements, used_memory_ids or [])

    @server.tool(annotations=reads)
    def open_source(source_id: str, max_chars: int = 8000) -> dict[str, Any]:
        """本棚の原文を読む。記憶の出所を確かめたいときに使う。"""
        return guarded(brain.open_source, source_id, max(500, min(max_chars, 50_000)))

    return server


def main() -> None:
    build_server(open_brain()).run("stdio")


if __name__ == "__main__":
    main()
