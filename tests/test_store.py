import sqlite3

import pytest

from exobrain.store import MemoryStore, PermissionDenied


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    yield s
    s.close()


def test_remember_and_recall(store):
    store.remember("好きな飲み物はほうじ茶", "human", ["好み"])
    store.remember("プロジェクトXの締切は10月末", "ai", ["仕事"])
    assert [r.content for r in store.current("ほうじ茶")] == ["好きな飲み物はほうじ茶"]
    assert [r.author for r in store.current(tag="仕事")] == ["ai"]
    assert len(store.current()) == 2


def test_raw_sql_update_and_delete_are_blocked(store):
    rec = store.remember("original", "human")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("UPDATE records SET content = 'hacked' WHERE id = ?", (rec.id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM records WHERE id = ?", (rec.id,))
    assert store.get(rec.id).content == "original"


def test_ai_cannot_overwrite_or_retract(store):
    rec = store.remember("original", "human")
    with pytest.raises(PermissionDenied):
        store._append("ai", "memory", "overwrite", [], rec.id)
    with pytest.raises(PermissionDenied):
        store._append("ai", "retraction", "", [], rec.id)


def test_proposal_does_not_change_current_until_accepted(store):
    rec = store.remember("会議は火曜", "human")
    prop = store.propose_revision(rec.id, "会議は水曜", author="ai")
    assert [r.content for r in store.current()] == ["会議は火曜"]
    assert [p.id for p in store.pending_proposals()] == [prop.id]

    new = store.accept_proposal(prop.id)
    assert new.author == "human" and new.ref == rec.id
    assert [r.content for r in store.current()] == ["会議は水曜"]
    assert store.pending_proposals() == []
    assert store.status(rec.id) == "superseded"
    # Old version is still there.
    assert [r.content for r in store.history(new.id) if r.type == "memory"] == ["会議は火曜", "会議は水曜"]


def test_rejected_proposal(store):
    rec = store.remember("A", "human")
    prop = store.propose_revision(rec.id, "B")
    store.retract(prop.id, "no")
    assert store.pending_proposals() == []
    assert [r.content for r in store.current()] == ["A"]
    with pytest.raises(ValueError):
        store.accept_proposal(prop.id)


def test_supersede_and_retract_keep_history(store):
    v1 = store.remember("v1", "human", ["x"])
    v2 = store.supersede(v1.id, "v2")
    assert v2.tags == ["x"]
    with pytest.raises(ValueError, match="already superseded"):
        store.supersede(v1.id, "fork")
    store.retract(v2.id, "obsolete")
    assert store.current() == []
    assert [r.id for r in store.history(v1.id)][:2] == [v1.id, v2.id]
    assert store.status(v2.id) == "retracted"


def test_verify_detects_tampering(tmp_path):
    path = tmp_path / "m.db"
    with MemoryStore(path) as s:
        s.remember("one", "human")
        s.remember("two", "ai")
        assert s.verify()[0]

    # Someone with direct file access drops the trigger and edits a row.
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER records_no_update")
    conn.execute("UPDATE records SET content = 'tampered' WHERE id = 1")
    conn.commit()
    conn.close()

    with MemoryStore(path) as s:
        ok, msg = s.verify()
        assert not ok and "#1" in msg


def test_like_wildcards_are_literal(store):
    store.remember("100% sure", "human")
    store.remember("1000 items", "human")
    assert [r.content for r in store.current("100%")] == ["100% sure"]
