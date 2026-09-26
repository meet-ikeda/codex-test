import sqlite3

import pytest

from exobrain import events


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.executescript(events.SCHEMA)
    for i in range(3):
        events.append(c, "human", "note", {"n": i, "text": f"記録{i}"})
    yield c
    c.close()


def test_chain_verifies(conn):
    assert events.verify(conn) == (True, "OK: 3 events (0 erased), chain intact")


def test_update_and_delete_are_blocked(conn):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET payload_json = '{\"n\":9}' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET actor = 'ai' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events WHERE id = 1")


def test_erasing_a_payload_keeps_the_chain_valid(conn):
    conn.execute("UPDATE events SET payload_json = NULL WHERE id = 2")
    ok, msg = events.verify(conn)
    assert ok and "1 erased" in msg
    assert [e.payload for e in events.iter_events(conn)][1] is None


def test_tampering_is_detected(conn):
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute("UPDATE events SET payload_json = '{\"n\":1,\"text\":\"改ざん\"}' WHERE id = 2")
    ok, msg = events.verify(conn)
    assert not ok and "#2" in msg
