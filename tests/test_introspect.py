import asyncio
import json
import stat

from exobrain import ask as ask_mod
from exobrain import introspect
from exobrain.mcp_server import build_introspect_server

from .conftest import report


def fill(brain, session):
    r = report(brain, session, [
        {"kind": "procedural", "text": "見出しは体言止めにする", "concepts": ["ハピホテ"], "importance": 0.8},
        {"kind": "semantic", "text": "ハピホテの公開は11月", "concepts": ["ハピホテ"], "importance": 0.6},
        {"kind": "procedural", "text": "結論から書く", "concepts": ["文章"], "importance": 0.9},
    ])
    return [n["id"] for n in r["nodes"]]


def test_overview_and_lists_show_what_is_inside(brain, session):
    ids = fill(brain, session)
    o = introspect.overview(brain)
    assert o["memories_by_kind"] == {"procedural": 2, "semantic": 1}
    assert {"topic": "ハピホテ", "type": None, "memories": 2} in o["busiest_topics"]

    hapi = introspect.memories(brain, topic="ハピホテ")
    assert hapi["total"] == 2 and all("ハピホテ" in m["concepts"] for m in hapi["memories"])
    assert introspect.memories(brain, kind="procedural", query="結論")["memories"][0]["text"] == "結論から書く"

    detail = introspect.memory(brain, ids[0])
    assert detail["concepts"] == ["ハピホテ"] and detail["kind"] == "procedural"


def test_looking_does_not_change_the_brain(brain, session):
    ids = fill(brain, session)
    before = brain._conn.execute("SELECT MAX(id) FROM events").fetchone()[0]
    accessed = brain._conn.execute("SELECT SUM(access_count) FROM nodes").fetchone()[0]
    server = build_introspect_server(brain)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"brain_overview", "list_memories", "open_memory", "open_source"}
    assert all(t.annotations.read_only_hint for t in tools.values())
    asyncio.run(server.call_tool("brain_overview", {}))
    asyncio.run(server.call_tool("list_memories", {"topic": "ハピホテ"}))
    asyncio.run(server.call_tool("open_memory", {"memory_id": ids[1]}))
    assert brain._conn.execute("SELECT MAX(id) FROM events").fetchone()[0] == before
    assert brain._conn.execute("SELECT SUM(access_count) FROM nodes").fetchone()[0] == accessed


def test_ask_runs_claude_with_only_the_reading_tools(brain, tmp_path, monkeypatch, capsys):
    fake = tmp_path / "claude"
    log = tmp_path / "args.json"
    fake.write_text(f"#!/usr/bin/env python3\nimport json, sys\njson.dump(sys.argv[1:], open({str(log)!r}, 'w'))\n"
                    "print('あなたは結論を先に求める人です（記憶 mem_x）')\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr("exobrain.sleep.find_claude", lambda b: str(fake))

    assert ask_mod.ask(brain, "俺のことどう見えてる？") == 0
    assert "結論を先に求める人" in capsys.readouterr().out
    args = json.loads(log.read_text())
    assert args[args.index("-p") + 1] == "俺のことどう見えてる？"
    assert args[args.index("--allowedTools") + 1] == "mcp__exobrain-introspect" and "--strict-mcp-config" in args
    assert "抽象化" in args[args.index("--append-system-prompt") + 1]
