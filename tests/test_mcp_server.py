import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from exobrain.mcp_server import build_server


def call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    return result.structured_content


@pytest.fixture
def server(brain):
    return build_server(brain)


def test_tool_list_has_nothing_destructive(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"start_session", "recall", "remember", "submit_daily_report", "open_source",
                          "trace_correction", "apply_correction"}
    assert not any(w in name for name in tools for w in ("erase", "delete", "pause", "restore"))
    assert "concepts" in tools["remember"].description
    assert "本棚" in tools["submit_daily_report"].description


def test_full_conversation(server, brain):
    sid = call(server, "start_session", {"ai_name": "Codex"})["session_id"]
    r = call(server, "submit_daily_report", {
        "session_id": sid, "title": "初めての日報", "report": "今日は exobrain を試した。",
        "elements": [{"kind": "procedural", "text": "結論を先に書く", "concepts": ["文章"], "importance": 0.9}],
    })
    assert r["nodes"][0]["kind"] == "procedural"
    assert "結論を先に書く" in call(server, "start_session", {"ai_name": "Claude Desktop"})["profile"]
    assert call(server, "open_source", {"source_id": r["source_id"]})["body"] == "今日は exobrain を試した。"
    sid2 = call(server, "start_session", {"ai_name": "Claude Desktop"})["session_id"]
    got = call(server, "recall", {"session_id": sid2, "cue": "文章の書き方"})
    assert r["nodes"][0]["id"] in got["memory_ids"] and got["tokens"] <= got["budget"]


def test_bad_input_reaches_the_ai_as_a_message(server):
    sid = call(server, "start_session", {"ai_name": "Codex"})["session_id"]
    with pytest.raises(ToolError, match="分けてください"):
        asyncio.run(server.call_tool("remember", {"session_id": sid,
                                                  "elements": [{"kind": "episode", "text": "長" * 400}]}))
