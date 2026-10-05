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
    assert set(tools) == {"start_session", "recall", "remember_explicit", "submit_daily_log", "deposit", "good", "revise_memory", "open_project", "open_source",
                          "trace_correction", "apply_correction"}
    assert not any(w in name for name in tools for w in ("erase", "delete", "pause", "restore"))
    # The AI no longer writes to the cortex on its own judgment (spec v0.5 §1).
    assert "覚えておいて" in tools["remember_explicit"].description
    assert "/日報" in tools["submit_daily_log"].description
    assert "/思い出して" in tools["start_session"].description and "/思い出して" in tools["recall"].description


def test_full_conversation(server, brain):
    sid = call(server, "start_session", {"ai_name": "Codex"})["session_id"]
    r = call(server, "remember_explicit", {"session_id": sid, "words": "文章は結論から書いてほしい",
                                           "kind": "procedural", "concepts": ["文章"]})
    assert not r["already_remembered"]
    assert brain.node(r["node_id"])["promoted_by"] == "explicit"
    # Memory is read only on "/思い出して": a plain session carries no profile.
    assert "profile" not in call(server, "start_session", {"ai_name": "Claude Desktop"})
    assert "結論から書いて" in call(server, "start_session", {"ai_name": "Claude Desktop",
                                                           "include_profile": True})["profile"]
    assert call(server, "open_source", {"source_id": r["source_id"]})["body"].startswith("文章は結論から書いてほしい")
    sid2 = call(server, "start_session", {"ai_name": "Claude Desktop"})["session_id"]
    got = call(server, "recall", {"session_id": sid2, "cue": "文章の書き方"})
    assert r["node_id"] in got["memory_ids"] and got["tokens"] <= got["budget"]


def test_daily_log_goes_to_the_hippocampus_not_the_cortex(server, brain):
    sid = call(server, "start_session", {"ai_name": "Claude Desktop"})["session_id"]
    before = brain.stats()
    r = call(server, "submit_daily_log", {"session_id": sid, "thread_title": "exobrain の相談",
                                          "events": ["仕様書 v0.5 を書いた"], "decisions": ["大脳皮質の md は写し"]})
    assert r["filed"]
    after = brain.stats()
    assert {k: after[k] for k in ("episode", "semantic", "procedural")} == \
        {k: before[k] for k in ("episode", "semantic", "procedural")}
    h = brain._conn.execute("SELECT status FROM hippocampus WHERE source_id = ?", (r["source_id"],)).fetchone()
    assert h["status"] == "waiting"
    body = call(server, "open_source", {"source_id": r["source_id"]})["body"]
    assert "outbrain_kind: ai_daily" in body and "- 大脳皮質の md は写し" in body and "## ボツになったこと\n\n- 特になし" in body
    # The second /日報 continues the first one's cursor.
    r2 = call(server, "submit_daily_log", {"session_id": sid, "thread_title": "exobrain の相談",
                                           "events": ["評価質問を固定した"]})
    body2 = call(server, "open_source", {"source_id": r2["source_id"]})["body"]
    cur1 = brain._conn.execute("SELECT meta_json FROM sources WHERE id = ?", (r["source_id"],)).fetchone()
    assert r2["filed"] and cur1 is not None
    assert "outbrain_previous_cursor: \"\"" not in body2


def test_bad_input_reaches_the_ai_as_a_message(server):
    sid = call(server, "start_session", {"ai_name": "Codex"})["session_id"]
    with pytest.raises(ToolError, match="分けてください"):
        asyncio.run(server.call_tool("remember_explicit", {"session_id": sid, "words": "長" * 400}))


def test_the_conversation_id_survives_a_relay_that_drops_session_id(server, brain):
    started = call(server, "start_session", {"ai_name": "Claude (Cowork)", "include_profile": True})
    assert started["exo_session"] == started["session_id"]
    nid = call(server, "remember_explicit", {"exo_session": started["exo_session"], "words": "見出しは短く"})["node_id"]
    # A relay removed session_id and nothing else: the latest conversation is used.
    got = call(server, "recall", {"cue": "見出し"})
    assert nid in got["memory_ids"]
