"""Look inside the brain (spec v0.7 §10): what is in it, and how it sees the owner.

Everything here only reads. Unlike `recall`, nothing is reinforced and no
session is touched, so looking does not change what is being looked at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .recall import strength, strength_sql

if TYPE_CHECKING:
    from .brain import Brain

MEMORY_KINDS = ("procedural", "semantic", "episode")


def _concepts_of(brain: Brain, node_id: str) -> list[str]:
    return [r[0] for r in brain._conn.execute(
        "SELECT c.label FROM edges e JOIN nodes c ON c.kind = 'concept'"
        " AND c.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END"
        " WHERE e.kind = 'about' AND (e.src = ? OR e.dst = ?) ORDER BY c.label", (node_id, node_id, node_id))]


def _row(brain: Brain, r) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "text": r["body"] or r["label"], "pinned": bool(r["pinned"]),
            "promoted_by": r["promoted_by"], "about": r["about"] or "owner", "subject": r["subject"],
            "importance": round(r["importance"], 2), "strength": round(strength(r["base_strength"], r["occurrences"], r["access_count"], r["goods"],
                                      r["corrections"], r["last_activated_at"] or r["created_at"], r["pinned"]), 2),
            "times_recalled": r["access_count"], "corrections": r["corrections"], "goods": r["goods"],
            "created_at": r["created_at"][:10], "concepts": _concepts_of(brain, r["id"])}


def overview(brain: Brain) -> dict[str, Any]:
    c = brain._conn
    count = lambda sql, *a: {k or "": n for k, n in c.execute(sql, a)}  # noqa: E731
    active = "status = 'active' AND kind IN ('procedural', 'semantic', 'episode')"
    rules = c.execute(f"SELECT * FROM nodes WHERE {active} AND pinned = 1 ORDER BY importance DESC").fetchall()
    strongest = c.execute(
        f"SELECT * FROM nodes WHERE {active} AND pinned = 0"
        " ORDER BY importance * " + strength_sql() + " * (1 + corrections + goods) DESC LIMIT 15").fetchall()
    topics = c.execute(
        "SELECT c.label, c.concept_type, COUNT(*) AS n FROM edges e JOIN nodes c ON c.kind = 'concept'"
        " AND c.id IN (e.src, e.dst) WHERE e.kind = 'about' GROUP BY c.id ORDER BY n DESC LIMIT 25").fetchall()
    first, last = c.execute(f"SELECT MIN(created_at), MAX(created_at) FROM nodes WHERE {active}").fetchone()
    return {
        "memories_by_kind": count(f"SELECT kind, COUNT(*) FROM nodes WHERE {active} GROUP BY kind"),
        "memories_by_reason": count(f"SELECT promoted_by, COUNT(*) FROM nodes WHERE {active} GROUP BY promoted_by"),
        "memories_by_whose": count(f"SELECT COALESCE(about, 'owner'), COUNT(*) FROM nodes WHERE {active}"
                                   " GROUP BY COALESCE(about, 'owner')"),
        "dormant": c.execute("SELECT COUNT(*) FROM nodes WHERE status = 'dormant'").fetchone()[0],
        "sources_by_kind": count("SELECT kind, COUNT(*) FROM sources WHERE erased = 0 GROUP BY kind"),
        "period": [first[:10] if first else None, last[:10] if last else None],
        "standing_rules": [_row(brain, r) for r in rules],
        "strongest_other_memories": [_row(brain, r) for r in strongest],
        "busiest_topics": [{"topic": t["label"], "type": t["concept_type"], "memories": t["n"]} for t in topics],
    }


def memories(brain: Brain, kind: str = "", query: str = "", topic: str = "", limit: int = 30,
             offset: int = 0) -> dict[str, Any]:
    from .brain import normalize_concept

    where, args = ["n.status = 'active'", "n.kind IN ('procedural', 'semantic', 'episode')"], []
    if kind:
        where.append("n.kind = ?")
        args.append(kind)
    if query:
        where.append("n.body LIKE ?")
        args.append(f"%{query}%")
    if topic:
        where.append("EXISTS (SELECT 1 FROM edges e JOIN nodes c ON c.kind = 'concept' AND c.id IN (e.src, e.dst)"
                     " AND c.id != n.id WHERE e.kind = 'about' AND n.id IN (e.src, e.dst) AND (c.norm = ?"
                     " OR c.id IN (SELECT concept_id FROM concept_aliases WHERE norm = ?)))")
        args += [normalize_concept(topic)] * 2
    sql = " AND ".join(where)
    total = brain._conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE {sql}", args).fetchone()[0]
    rows = brain._conn.execute(
        f"SELECT n.* FROM nodes n WHERE {sql} ORDER BY n.pinned DESC, n.importance * " + strength_sql("n.") + " DESC,"
        " n.created_at DESC LIMIT ? OFFSET ?", [*args, max(1, min(limit, 100)), max(0, offset)]).fetchall()
    return {"total": total, "memories": [_row(brain, r) for r in rows]}


def memory(brain: Brain, memory_id: str) -> dict[str, Any]:
    from .brain import InvalidInput
    from datetime import datetime, timezone

    from .recall import effective_weight

    c = brain._conn
    r = c.execute("SELECT * FROM nodes WHERE id = ?", (memory_id,)).fetchone()
    if r is None or r["status"] == "erased":
        raise InvalidInput(f"記憶 {memory_id} は見つかりません。")
    quotes = [{"source_id": q["source_id"], "title": q["title"], "date": q["created_at"][:10],
               "lines": [q["line_start"], q["line_end"]], "quote": q["quote"]} for q in c.execute(
        "SELECT ns.*, s.title, s.created_at FROM node_sources ns JOIN sources s ON s.id = ns.source_id"
        " WHERE ns.node_id = ? ORDER BY ns.at", (memory_id,))]
    links, now = [], datetime.now(timezone.utc)
    for e in c.execute("SELECT * FROM edges WHERE (src = ? OR dst = ?) AND kind != 'about'", (memory_id, memory_id)):
        other = e["dst"] if e["src"] == memory_id else e["src"]
        o = c.execute("SELECT kind, body, label, status FROM nodes WHERE id = ?", (other,)).fetchone()
        if o and o["status"] == "active":
            links.append({"id": other, "kind": o["kind"], "text": o["body"] or o["label"], "link": e["kind"],
                          "weight": round(effective_weight(e["weight"], e["last_reinforced_at"], now), 3)})
    links.sort(key=lambda x: -x["weight"])
    revisions = [dict(v) for v in c.execute(
        "SELECT at, old_body, new_body, reason FROM revisions WHERE node_id = ? ORDER BY at", (memory_id,))]
    return {**_row(brain, r), "status": r["status"], "derivation": r["derivation"], "evidence": quotes,
            "linked_memories": links[:15], "revisions": revisions}
