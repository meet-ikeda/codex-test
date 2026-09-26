"""Append-only memory store.

Every change is a new row. Rows are never updated or deleted: SQLite triggers
reject UPDATE/DELETE, and each row carries a SHA-256 hash chained to the
previous row so that tampering with the file outside this API is detectable.

Record types:
  memory      -- a piece of remembered content. May `ref` an older memory it
                 supersedes (humans only).
  proposal    -- a suggested revision of a memory. Never changes the current
                 view; a human must accept it.
  retraction  -- marks a memory or proposal as withdrawn (humans only).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Author = Literal["human", "ai"]
RecordType = Literal["memory", "proposal", "retraction"]

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT    NOT NULL,
    author     TEXT    NOT NULL CHECK (author IN ('human', 'ai')),
    type       TEXT    NOT NULL CHECK (type IN ('memory', 'proposal', 'retraction')),
    content    TEXT    NOT NULL,
    tags       TEXT    NOT NULL DEFAULT '[]',
    ref        INTEGER REFERENCES records(id),
    prev_hash  TEXT    NOT NULL,
    hash       TEXT    NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS records_ref ON records(ref);

CREATE TRIGGER IF NOT EXISTS records_no_update
BEFORE UPDATE ON records
BEGIN
    SELECT RAISE(ABORT, 'records are append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS records_no_delete
BEFORE DELETE ON records
BEGIN
    SELECT RAISE(ABORT, 'records are append-only: DELETE is forbidden');
END;
"""


class PermissionDenied(Exception):
    """Raised when an author tries an operation reserved for humans."""


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Record:
    id: int
    created_at: str
    author: Author
    type: RecordType
    content: str
    tags: list[str]
    ref: int | None
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)


def compute_hash(
    prev_hash: str,
    created_at: str,
    author: str,
    type_: str,
    content: str,
    tags: list[str],
    ref: int | None,
) -> str:
    payload = json.dumps(
        {
            "prev_hash": prev_hash,
            "created_at": created_at,
            "author": author,
            "type": type_,
            "content": content,
            "tags": tags,
            "ref": ref,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_db_path() -> Path:
    env = os.environ.get("EXOBRAIN_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".exobrain" / "memory.db"


class MemoryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # The MCP server runs sync tools in worker threads; writes are serialized by _lock.
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- writes (append only) -------------------------------------------

    def remember(self, content: str, author: Author, tags: list[str] | None = None) -> Record:
        """Append a brand-new memory. Available to both humans and AI."""
        return self._append(author, "memory", content, tags or [], None)

    def supersede(self, target_id: int, content: str, tags: list[str] | None = None) -> Record:
        """Human-only: record a new version of a memory. The old one stays in history."""
        target = self._require(target_id, "memory")
        if self._superseded_by(target_id) is not None:
            raise ValueError(f"memory #{target_id} is already superseded; revise the latest version")
        new_tags = target.tags if tags is None else tags
        return self._append("human", "memory", content, new_tags, target_id)

    def propose_revision(self, target_id: int, content: str, author: Author = "ai") -> Record:
        """Suggest a revision. Does not change what `recall` returns until a human accepts it."""
        self._require(target_id, "memory")
        return self._append(author, "proposal", content, [], target_id)

    def retract(self, target_id: int, reason: str = "") -> Record:
        """Human-only: withdraw a memory or reject a proposal."""
        self._require(target_id)
        return self._append("human", "retraction", reason, [], target_id)

    def accept_proposal(self, proposal_id: int) -> Record:
        """Human-only: adopt a proposal as the new version of its target."""
        proposal = self._require(proposal_id, "proposal")
        if proposal_id not in self._pending_proposal_ids():
            raise ValueError(f"proposal #{proposal_id} is no longer pending")
        new = self.supersede(proposal.ref, proposal.content)
        # Close the proposal so it no longer shows as pending.
        self._append("human", "retraction", f"accepted as #{new.id}", [], proposal_id)
        return new

    def _append(
        self, author: Author, type_: RecordType, content: str, tags: list[str], ref: int | None
    ) -> Record:
        if author not in ("human", "ai"):
            raise ValueError(f"unknown author: {author!r}")
        if author == "ai" and (type_ == "retraction" or (type_ == "memory" and ref is not None)):
            raise PermissionDenied("AI may not overwrite or retract memories")
        if not content.strip() and type_ != "retraction":
            raise ValueError("content must not be empty")
        tags = sorted({t.strip() for t in tags if t.strip()})
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

        with self._lock:
            return self._insert(author, type_, content, tags, ref, created_at)

    def _insert(
        self, author: Author, type_: RecordType, content: str, tags: list[str], ref: int | None, created_at: str
    ) -> Record:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT hash FROM records ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else GENESIS_HASH
            h = compute_hash(prev_hash, created_at, author, type_, content, tags, ref)
            cur = self._conn.execute(
                "INSERT INTO records (created_at, author, type, content, tags, ref, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (created_at, author, type_, content, json.dumps(tags, ensure_ascii=False), ref, prev_hash, h),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(cur.lastrowid)

    # ---- reads ------------------------------------------------------------

    def get(self, record_id: int) -> Record:
        row = self._conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"record #{record_id} not found")
        return _row_to_record(row)

    def all_records(self) -> list[Record]:
        return [_row_to_record(r) for r in self._conn.execute("SELECT * FROM records ORDER BY id")]

    def current(
        self, query: str | None = None, tag: str | None = None, author: Author | None = None, limit: int | None = None
    ) -> list[Record]:
        """Live memories: not superseded, not retracted. Newest first.

        `query` is split on whitespace; every term must appear (case-insensitive).
        """
        sql = [
            "SELECT * FROM records r WHERE r.type = 'memory'",
            "AND NOT EXISTS (SELECT 1 FROM records s WHERE s.ref = r.id AND s.type IN ('memory', 'retraction'))",
        ]
        params: list = []
        for term in (query or "").split():
            sql.append("AND (r.content LIKE ? ESCAPE '\\' OR r.tags LIKE ? ESCAPE '\\')")
            like = "%" + _escape_like(term) + "%"
            params += [like, like]
        if tag:
            sql.append("AND EXISTS (SELECT 1 FROM json_each(r.tags) WHERE json_each.value = ?)")
            params.append(tag)
        if author:
            sql.append("AND r.author = ?")
            params.append(author)
        sql.append("ORDER BY r.id DESC")
        if limit is not None:
            sql.append("LIMIT ?")
            params.append(limit)
        return [_row_to_record(r) for r in self._conn.execute(" ".join(sql), params)]

    def pending_proposals(self, target_id: int | None = None) -> list[Record]:
        ids = self._pending_proposal_ids()
        records = [self.get(i) for i in sorted(ids)]
        if target_id is not None:
            records = [r for r in records if r.ref == target_id]
        return records

    def history(self, record_id: int) -> list[Record]:
        """Full lineage of a memory (oldest first) plus proposals/retractions attached to any version."""
        # Every ref chain ends at the original memory, which has no ref.
        root = self.get(record_id)
        while root.ref is not None:
            root = self.get(root.ref)
        # Walk forward collecting every descendant.
        seen: dict[int, Record] = {root.id: root}
        frontier = [root.id]
        while frontier:
            rid = frontier.pop()
            for row in self._conn.execute("SELECT * FROM records WHERE ref = ?", (rid,)):
                rec = _row_to_record(row)
                if rec.id not in seen:
                    seen[rec.id] = rec
                    frontier.append(rec.id)
        return [seen[k] for k in sorted(seen)]

    def status(self, record_id: int) -> str:
        rec = self.get(record_id)
        if rec.type == "retraction":
            return "retraction"
        refs = [_row_to_record(r) for r in self._conn.execute("SELECT * FROM records WHERE ref = ?", (record_id,))]
        if any(r.type == "retraction" for r in refs):
            return "retracted" if rec.type == "memory" else "closed"
        if rec.type == "memory" and any(r.type == "memory" for r in refs):
            return "superseded"
        return "current" if rec.type == "memory" else "pending"

    def verify(self) -> tuple[bool, str]:
        """Recompute the hash chain. Returns (ok, message)."""
        prev = GENESIS_HASH
        count = 0
        for rec in self.all_records():
            if rec.prev_hash != prev:
                return False, f"chain broken at #{rec.id}: prev_hash does not match #{rec.id - 1}"
            expected = compute_hash(prev, rec.created_at, rec.author, rec.type, rec.content, rec.tags, rec.ref)
            if expected != rec.hash:
                return False, f"record #{rec.id} was modified (hash mismatch)"
            prev = rec.hash
            count += 1
        return True, f"OK: {count} records, chain intact"

    # ---- helpers ----------------------------------------------------------

    def _require(self, record_id: int, type_: RecordType | None = None) -> Record:
        rec = self.get(record_id)
        if type_ is not None and rec.type != type_:
            raise ValueError(f"record #{record_id} is a {rec.type}, not a {type_}")
        return rec

    def _superseded_by(self, record_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM records WHERE ref = ? AND type = 'memory' ORDER BY id LIMIT 1", (record_id,)
        ).fetchone()
        return row["id"] if row else None

    def _pending_proposal_ids(self) -> set[int]:
        rows = self._conn.execute(
            "SELECT p.id FROM records p WHERE p.type = 'proposal'"
            " AND NOT EXISTS (SELECT 1 FROM records x WHERE x.ref = p.id AND x.type = 'retraction')"
        )
        return {r["id"] for r in rows}


def _row_to_record(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"],
        created_at=row["created_at"],
        author=row["author"],
        type=row["type"],
        content=row["content"],
        tags=json.loads(row["tags"]),
        ref=row["ref"],
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
