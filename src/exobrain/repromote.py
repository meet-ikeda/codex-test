"""Rebuild the cortex from the bookshelf with the current intake (spec v0.8 §11.3).

The bookshelf keeps every original, so the cortex can be learned again. This:
1. backs the brain up first (the brain before the rebuild stays, to compare against);
2. retires what earlier sleeps made (history and links stay; recall no longer shows them);
3. keeps what the owner made directly — "remember this", corrections, /good, rewrites — and marks the owner's
   own rules as confirmed, since the owner said them;
4. puts the originals back in the hippocampus, so that the next sleeps promote them again with the new intake
   (owner's words / summarized, cases instead of one-off rules, scope).
Nothing is erased. The sleeps re-learn a few originals a night, so a long rebuild takes many nights.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brain import Brain

SLEEP_ACTOR = "sleep"
KINDS = ("procedural", "semantic", "episode", "case")
SOURCE_KINDS = ("ai_daily", "deposit", "memo", "remember_note")
WAIT_DAYS = 90  # how long re-entered originals wait in the hippocampus (a sleep takes only a few a night)


def plan(brain: Brain, since: str) -> dict[str, Any]:
    c = brain._conn
    q = ",".join("?" * len(KINDS))
    retire = [r[0] for r in c.execute(
        f"SELECT id FROM nodes WHERE status = 'active' AND kind IN ({q}) AND created_by = ? AND pinned = 0",
        (*KINDS, SLEEP_ACTOR))]
    confirm = [r[0] for r in c.execute(
        "SELECT id FROM nodes WHERE status = 'active' AND kind = 'procedural' AND created_by != ?"
        " AND stage IS NULL AND (promoted_by = 'explicit' OR promoted_by IS NULL OR pinned = 1)", (SLEEP_ACTOR,))]
    s = ",".join("?" * len(SOURCE_KINDS))
    sources = [r[0] for r in c.execute(
        f"SELECT id FROM sources WHERE erased = 0 AND kind IN ({s}) AND substr(created_at, 1, 10) >= ?"
        " ORDER BY created_at", (*SOURCE_KINDS, since))]
    kept = c.execute(f"SELECT COUNT(*) FROM nodes WHERE status = 'active' AND kind IN ({q})",
                     KINDS).fetchone()[0] - len(retire)
    return {"since": since, "retire": retire, "confirm": confirm, "sources": sources, "kept": kept}


def run(brain: Brain, since: str, actor: str = "human") -> dict[str, Any]:
    from .safety import backup

    p = plan(brain, since)
    snapshot = backup(brain)
    expires = (datetime.now(timezone.utc) + timedelta(days=WAIT_DAYS)).isoformat(timespec="seconds")
    with brain._tx():
        for nid in p["retire"]:
            brain._emit(actor, "node_updated", {"id": nid, "status": "retired", "why": "repromote"})
        for nid in p["confirm"]:
            brain._emit(actor, "node_updated", {"id": nid, "stage": "confirmed"})
        for sid in p["sources"]:
            brain._emit(actor, "hippocampus_entered", {"source_id": sid, "expires_at": expires, "why": "repromote"})
        if p["sources"]:
            brain._emit(actor, "sleep_marks_cleared", {"kind": "promoted", "source_ids": p["sources"]})
    brain._recaller.index.version = -1
    return {**{k: len(v) if isinstance(v, list) else v for k, v in p.items()}, "backup": snapshot.name}
