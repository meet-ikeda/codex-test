import asyncio

import pytest

from exobrain.mcp_server import build_server
from exobrain.store import MemoryStore


@pytest.fixture
def env(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    yield store, build_server(store)
    store.close()


def test_ai_has_no_destructive_tools(env):
    _, server = env
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"remember", "recall", "history", "propose_revision", "verify_integrity"}


def test_ai_remember_is_marked_ai(env):
    store, server = env
    asyncio.run(server.call_tool("remember", {"content": "猫の名前はミケ", "tags": ["家族"]}))
    [rec] = store.current()
    assert rec.author == "ai" and rec.tags == ["家族"]


def test_ai_proposal_leaves_memory_intact(env):
    store, server = env
    rec = store.remember("住所は東京", "human")
    asyncio.run(server.call_tool("propose_revision", {"memory_id": rec.id, "content": "住所は大阪"}))
    assert [r.content for r in store.current()] == ["住所は東京"]
    assert len(store.pending_proposals()) == 1
