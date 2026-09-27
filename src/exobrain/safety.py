"""Human-only safeguards: erase (FR-09) and backup / restore (NFR-03, design §9).

None of this is reachable from the MCP server.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brain import Brain

BACKUP_KEEP = 7
CONFIRM_PHRASE = "消去する"
TRASH_NOTICE = ("本棚の原文は Google ドライブの同期フォルダから削除しました。"
                "Google ドライブのゴミ箱にも残る場合があるため、ゴミ箱も空にしてください。")


# ---- backup / restore -----------------------------------------------------------


def backup(brain: Brain) -> Path:
    """Write a consistent snapshot (SQLite online backup, never a raw copy of the live file)."""
    folder = brain.settings.backups
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"brain-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    dest = sqlite3.connect(str(path))
    try:
        with brain._lock:
            brain._conn.backup(dest)
    finally:
        dest.close()
    for old in list_backups(brain.settings.backups)[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
    return path


def list_backups(folder: Path) -> list[Path]:
    return sorted(folder.glob("brain-*.db"), reverse=True)


def restore(brain: Brain, snapshot: Path) -> dict[str, Any]:
    """Replace the brain with a snapshot. The current brain is kept next to it first."""
    from .brain import InvalidInput

    if not snapshot.exists():
        raise InvalidInput(f"バックアップが見つかりません: {snapshot}")
    src = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        if src.execute("SELECT name FROM sqlite_master WHERE name = 'events'").fetchone() is None:
            raise InvalidInput("exobrain のバックアップではありません。")
        keep = brain.settings.home / f"brain.before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        cur = sqlite3.connect(str(keep))
        with brain._lock:
            brain._conn.backup(cur)
            cur.close()
            src.backup(brain._conn)
    finally:
        src.close()
    brain._recaller.index.version = -1
    ok, msg = brain.verify()
    return {"restored_from": str(snapshot), "previous_brain": str(keep), "verified": ok, "message": msg}


# ---- erase ------------------------------------------------------------------------


def plan_erase(brain: Brain, source_ids: list[str] = (), node_ids: list[str] = (),
               since: str | None = None, until: str | None = None) -> dict[str, list]:
    """Work out exactly what an erase would remove, so the owner can see it before confirming."""
    c = brain._conn
    sources = {s for s in source_ids if c.execute("SELECT 1 FROM sources WHERE id = ?", (s,)).fetchone()}
    if since or until:
        q, args = "SELECT id FROM sources WHERE 1 = 1", []
        if since:
            q, args = q + " AND created_at >= ?", [*args, since]
        if until:
            q, args = q + " AND created_at < ?", [*args, until]
        sources |= {r[0] for r in c.execute(q, args)}
    nodes = {n for n in node_ids if c.execute("SELECT 1 FROM nodes WHERE id = ?", (n,)).fetchone()}
    for s in sources:
        nodes |= {r[0] for r in c.execute("SELECT id FROM nodes WHERE source_id = ?", (s,))}
        # Memories whose only evidence is this original go with it; shared ones just lose the quote.
        for (nid,) in c.execute("SELECT node_id FROM node_sources WHERE source_id = ?", (s,)):
            others = c.execute("SELECT COUNT(*) FROM node_sources WHERE node_id = ? AND source_id NOT IN"
                               f" ({','.join('?' * len(sources))})", (nid, *sorted(sources))).fetchone()[0]
            if others == 0:
                nodes.add(nid)
    # The episode of rewriting a memory quotes its old wording: it goes with the memory.
    for n in list(nodes):
        nodes |= {r[0] for r in c.execute("SELECT episode_id FROM revisions WHERE node_id = ? AND episode_id IS NOT NULL",
                                          (n,))}
    if since or until:
        q, args = "SELECT id FROM nodes WHERE kind != 'concept'", []
        if since:
            q, args = q + " AND created_at >= ?", [*args, since]
        if until:
            q, args = q + " AND created_at < ?", [*args, until]
        nodes |= {r[0] for r in c.execute(q, args)}
    src_rows = [dict(r) for r in c.execute(
        f"SELECT id, title, path, created_at FROM sources WHERE id IN ({','.join('?' * len(sources))})",
        sorted(sources))] if sources else []
    node_rows = [dict(r) for r in c.execute(
        f"SELECT id, kind, label FROM nodes WHERE id IN ({','.join('?' * len(nodes))})", sorted(nodes))] if nodes else []
    return {"sources": src_rows, "nodes": node_rows}


def erase(brain: Brain, plan: dict[str, list], confirm: str) -> dict[str, Any]:
    from .brain import InvalidInput

    if confirm != CONFIRM_PHRASE:
        raise InvalidInput(f"確認の語句「{CONFIRM_PHRASE}」が入力されていません。")
    source_ids = [s["id"] for s in plan["sources"]]
    node_ids = [n["id"] for n in plan["nodes"]]
    if not source_ids and not node_ids:
        return {"erased_sources": 0, "erased_nodes": 0, "notice": ""}
    c = brain._conn
    with brain._tx():
        for sid in source_ids:
            brain._emit("human", "erased", {"target": "source", "id": sid})
        for nid in node_ids:
            brain._emit("human", "erased", {"target": "node", "id": nid})
        # Null the payloads that carried the content; the chain keeps only their hashes.
        for type_, ids in (("source_added", source_ids), ("node_added", node_ids)):
            for i in range(0, len(ids), 500):
                part = ids[i : i + 500]
                c.execute(
                    f"UPDATE events SET payload_json = NULL WHERE type = ? AND payload_json IS NOT NULL"
                    f" AND json_extract(payload_json, '$.id') IN ({','.join('?' * len(part))})",
                    (type_, *part),
                )
        # Old and new wordings of an erased memory.
        for i in range(0, len(node_ids), 500):
            part = node_ids[i : i + 500]
            c.execute(
                f"UPDATE events SET payload_json = NULL WHERE type = 'node_revised' AND payload_json IS NOT NULL"
                f" AND json_extract(payload_json, '$.id') IN ({','.join('?' * len(part))})", tuple(part))
        # Quotes copied from an erased original, or belonging to an erased memory.
        for field, ids in (("source_id", source_ids), ("node_id", node_ids)):
            for i in range(0, len(ids), 500):
                part = ids[i : i + 500]
                c.execute(
                    f"UPDATE events SET payload_json = NULL WHERE type = 'node_sourced' AND payload_json IS NOT NULL"
                    f" AND json_extract(payload_json, '$.{field}') IN ({','.join('?' * len(part))})",
                    tuple(part),
                )
    if source_ids and not brain.fts_secure_delete:
        _rebuild_fts(brain)
    with brain._lock:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the WAL file may still hold the old pages
    for s in plan["sources"]:
        (brain.settings.drive_root / s["path"]).unlink(missing_ok=True)
    brain._recaller.index.version = -1
    purge_restore_leftovers(brain)
    # Old snapshots still hold the erased content: replace them with a fresh one.
    for old in list_backups(brain.settings.backups):
        old.unlink(missing_ok=True)
    backup(brain)
    return {"erased_sources": len(source_ids), "erased_nodes": len(node_ids),
            "notice": TRASH_NOTICE if source_ids else ""}


def _rebuild_fts(brain: Brain) -> None:
    """Without FTS5 secure-delete, recreate the index so deleted text leaves no trace in it."""
    with brain._tx():
        rows = brain._conn.execute("SELECT source_id, title, body FROM source_fts").fetchall()
        brain._conn.execute("DROP TABLE source_fts")
        brain._conn.execute(
            "CREATE VIRTUAL TABLE source_fts USING fts5(source_id UNINDEXED, title, body, tokenize = 'trigram')")
        brain._conn.executemany("INSERT INTO source_fts VALUES (?, ?, ?)", [tuple(r) for r in rows])


def purge_restore_leftovers(brain: Brain) -> None:
    """Pre-restore copies may contain erased content."""
    for p in brain.settings.home.glob("brain.before-restore-*.db"):
        p.unlink(missing_ok=True)
