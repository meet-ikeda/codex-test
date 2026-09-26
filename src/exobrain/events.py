"""Append-only, hash-chained event log: the single source of truth for the brain.

Each event's hash covers the SHA-256 of its payload rather than the payload
itself, so a human can later erase a payload (set it to NULL) without breaking
the chain. Apart from that one operation, SQLite triggers reject every UPDATE
and DELETE.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT NOT NULL,
    actor          TEXT NOT NULL,
    type           TEXT NOT NULL,
    payload_json   TEXT,
    content_sha256 TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    hash           TEXT NOT NULL UNIQUE
);

-- The only permitted update is erasing a payload; everything else is frozen.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
WHEN NOT (
    NEW.payload_json IS NULL AND OLD.payload_json IS NOT NULL
    AND NEW.id = OLD.id AND NEW.at = OLD.at AND NEW.actor = OLD.actor AND NEW.type = OLD.type
    AND NEW.content_sha256 = OLD.content_sha256 AND NEW.prev_hash = OLD.prev_hash AND NEW.hash = OLD.hash
)
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: only erasing a payload is allowed');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: DELETE is forbidden');
END;
"""


@dataclass(frozen=True)
class Event:
    id: int
    at: str
    actor: str
    type: str
    payload: dict[str, Any] | None  # None once erased
    content_sha256: str
    prev_hash: str
    hash: str


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str, at: str, actor: str, type_: str, content_sha256: str) -> str:
    return sha256_text(canonical_json([prev_hash, at, actor, type_, content_sha256]))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def append(conn: sqlite3.Connection, actor: str, type_: str, payload: dict[str, Any]) -> Event:
    """Append one event. Must be called inside a write transaction (BEGIN IMMEDIATE)."""
    at = now_iso()
    payload_json = canonical_json(payload)
    content_sha = sha256_text(payload_json)
    row = conn.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
    prev = row[0] if row else GENESIS_HASH
    h = chain_hash(prev, at, actor, type_, content_sha)
    cur = conn.execute(
        "INSERT INTO events (at, actor, type, payload_json, content_sha256, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (at, actor, type_, payload_json, content_sha, prev, h),
    )
    return Event(cur.lastrowid, at, actor, type_, payload, content_sha, prev, h)


def iter_events(conn: sqlite3.Connection):
    for r in conn.execute(
        "SELECT id, at, actor, type, payload_json, content_sha256, prev_hash, hash FROM events ORDER BY id"
    ):
        payload = json.loads(r[4]) if r[4] is not None else None
        yield Event(r[0], r[1], r[2], r[3], payload, r[5], r[6], r[7])


def verify(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Recompute the chain. Erased payloads are fine; altered ones are not."""
    prev = GENESIS_HASH
    count = erased = 0
    for r in conn.execute(
        "SELECT id, at, actor, type, payload_json, content_sha256, prev_hash, hash FROM events ORDER BY id"
    ):
        eid, at, actor, type_, payload_json, content_sha, prev_hash, h = r
        if prev_hash != prev:
            return False, f"chain broken at event #{eid}"
        if chain_hash(prev, at, actor, type_, content_sha) != h:
            return False, f"event #{eid} was modified (hash mismatch)"
        if payload_json is None:
            erased += 1
        elif sha256_text(payload_json) != content_sha:
            return False, f"event #{eid} payload was modified"
        prev = h
        count += 1
    return True, f"OK: {count} events ({erased} erased), chain intact"
