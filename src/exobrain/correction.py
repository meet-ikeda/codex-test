"""Rewiring on correction (FR-23, design §6).

trace()  only looks: brain candidates (spreading activation) and bookshelf
         candidates (full-text search). Nothing is written.
apply()  writes, and enforces the order of the flow:
  reinforce  the memory existed but did not fire here → wire it to this context
  restore    not in the brain but on the bookshelf → add it to the brain
  new        found nowhere → tell the owner so, and remember the correction
Every mode also records the correction itself as an episode.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .search import search

if TYPE_CHECKING:
    from .brain import Brain

# Design values.
TRACE_TTL_SECONDS = 3600
BRAIN_CANDIDATES = 5
SHELF_CANDIDATES = 5
REINFORCE_RATE = 0.3  # much stronger than recall-time learning: the owner had to say it again
STRENGTH_STEP = 0.5
STRENGTH_MAX = 3.0
PIN_AFTER = 3  # corrections before a memory is always recalled
WRONG_DAMPING = 0.5
EPISODE_LINK = 0.5

MSG_REINFORCE = "以前にも同じ指摘を受けていました（今回で {n} 回目）。いまの場面とのつながりを強めたので、次からはこの場面でも思い出します。"
MSG_PINNED = "同じ指摘が {n} 回に達したので、毎回必ず思い出すルールにしました。"
MSG_AWAKENED = "長く使われず眠っていた記憶でしたが、呼び起こしました。"
MSG_RESTORE = "脳には残っていませんでしたが、本棚の原文（{title}、{date}）に記録がありました。脳に追加しました。"
MSG_NEW = "過去の記憶にも本棚にもありません。今回の指摘を新しい記憶として残しました。"


@dataclass
class Trace:
    id: str
    session_id: str
    correction: str
    context: str
    brain_ids: list[str]
    shelf: dict[str, dict]  # source_id -> search hit
    created: float = field(default_factory=time.time)


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def trace(brain: Brain, session_id: str, correction: str, context: str = "",
          keywords: list[str] | None = None, used_memory_ids: list[str] | None = None) -> dict[str, Any]:
    from .brain import InvalidInput, new_id

    brain.session_ai(session_id)
    if not correction.strip():
        raise InvalidInput("correction（指摘の内容）が空です。")
    keywords = [k for k in (keywords or []) if k.strip()]
    cue = " ".join([correction, context, *keywords])
    r = brain._recaller
    with brain._lock:
        seeds = r.seeds(cue, None)
        for nid in used_memory_ids or []:  # memories used when the mistake happened
            seeds.setdefault(nid, 0.5)
        activation, hops, parent = r.spread(seeds)
        hits = [h for h in r.score(activation, hops, parent) if h.hops <= 1][:BRAIN_CANDIDATES]
        shelf = search(brain._conn, cue, keywords, SHELF_CANDIDATES)
    brain_out = []
    for h in hits:
        n = brain.node(h.id)
        brain_out.append({"id": h.id, "kind": h.kind, "text": h.body, "created_at": h.created_at,
                          "times_corrected": n["corrections"], "score": round(h.score, 4)})
    brain_out += _dormant_matches(brain, cue, {b["id"] for b in brain_out})
    t = Trace(new_id("t"), session_id, correction, context, [b["id"] for b in brain_out],
              {s["source_id"]: s for s in shelf})
    traces = brain.__dict__.setdefault("_traces", {})
    now = time.time()
    for k in [k for k, v in traces.items() if now - v.created > TRACE_TTL_SECONDS]:
        del traces[k]
    traces[t.id] = t

    if brain_out:
        step = ("脳に候補があります。今回の指摘と同じ内容のものがあれば apply_correction(mode='reinforce', target_id=…)。"
                "なければ本棚の候補を確認してください。")
    elif shelf:
        step = "脳にはありませんが、本棚に候補があります。該当すれば apply_correction(mode='restore', source_id=…)。"
    else:
        step = "脳にも本棚にもありません。apply_correction(mode='new') で今回の指摘を記憶してください。"
    return {"trace_id": t.id, "brain_candidates": brain_out, "bookshelf_candidates": shelf, "next_step": step}


def apply(brain: Brain, session_id: str, trace_id: str, mode: str, lesson: str = "",
          kind: str | None = None, concepts: list[str] | None = None, target_id: str | None = None,
          source_id: str | None = None, wrong_memory_ids: list[str] | None = None,
          superseded_ids: list[str] | None = None) -> dict[str, Any]:
    from .brain import (ELEMENT_KINDS, TEXT_MAX, W_ABOUT, InvalidInput, hebbian, make_label, new_id,
                        parse_elements)

    ai_name = brain.session_ai(session_id)
    brain._require_writable()
    t: Trace | None = brain.__dict__.get("_traces", {}).get(trace_id)
    if t is None or t.session_id != session_id:
        raise InvalidInput("trace_id が見つかりません。先に trace_correction を呼んでください。")
    if mode not in ("reinforce", "restore", "new"):
        raise InvalidInput("mode は reinforce / restore / new のいずれかです。")
    lesson = lesson.strip()
    if len(lesson) > TEXT_MAX:
        raise InvalidInput(f"lesson は {TEXT_MAX} 文字までです。")
    concepts = parse_elements([{"kind": "episode", "text": "x", "concepts": concepts or []}])[0].concepts
    actor = f"ai:{ai_name}"
    c = brain._conn

    # Validate before writing anything.
    if mode == "reinforce":
        if target_id not in t.brain_ids:
            raise InvalidInput("reinforce できるのは trace_correction の brain_candidates にある記憶だけです。")
    elif mode == "restore":
        if source_id not in t.shelf:
            raise InvalidInput("restore できるのは trace_correction の bookshelf_candidates にある原文だけです。")
    if mode == "new" and not lesson:
        raise InvalidInput("mode='new' では lesson（今後どうするか）が必要です。")
    kind = kind or ("procedural" if mode == "new" else "semantic")
    if kind not in ELEMENT_KINDS or kind == "episode":
        raise InvalidInput("kind は semantic か procedural にしてください（出来事は自動で記録します）。")
    superseded_ids = list(dict.fromkeys(superseded_ids or []))
    for sid in superseded_ids:
        n = brain.node(sid)
        if n is None or n["status"] != "active":
            raise InvalidInput(f"{sid} は置き換えられる記憶ではありません。")
        if n["kind"] == "episode":
            raise InvalidInput("出来事（episode）は置き換えられません。事実やルールの誤りだけを置き換えてください。")
    if superseded_ids and not lesson:
        raise InvalidInput("superseded_ids を指定するときは、正しい内容を lesson に書いてください。")
    wrong, _ = brain._existing_ids(wrong_memory_ids or [])

    created: list[dict] = []
    pinned = awakened = False
    times = 0
    with brain._tx():
        context_ids = _context_concepts(brain, actor, t, concepts)

        def add_node(k: str, text: str, src: str | None, importance: float) -> str:
            nid = new_id("n")
            brain._emit(actor, "node_added", {"id": nid, "kind": k, "label": make_label(text), "body": text,
                                              "source_id": src, "importance": importance,
                                              # the owner pointed it out: a rule from their own correction holds
                                              **({"stage": "confirmed"} if k == "procedural" else {})})
            for cid in context_ids:
                brain._link(actor, nid, cid, "about", W_ABOUT, "correction")
            created.append({"id": nid, "kind": k, "text": text})
            return nid

        if mode == "reinforce":
            n = brain.node(target_id)
            times = n["corrections"] + 1
            pinned = times >= PIN_AFTER
            awakened = n["status"] == "dormant"
            brain._emit(actor, "node_updated", {
                "id": target_id, "base_strength": min(STRENGTH_MAX, n["base_strength"] + STRENGTH_STEP),
                "corrections_delta": 1, **({"pinned": 1} if pinned else {}),
                **({"status": "active"} if awakened else {}),
            })
            for cid in context_ids:
                w = hebbian(brain._edge_weight(target_id, cid, "about"), REINFORCE_RATE)
                brain._emit(actor, "edge_set", {"src": target_id, "dst": cid, "kind": "about",
                                                "weight": round(w, 6), "origin": "correction", "co_activation": True})
            anchor = target_id
            if lesson and lesson != n["body"]:
                anchor = add_node(kind, lesson, None, max(0.9, n["importance"]))
        elif mode == "restore":
            hit = t.shelf[source_id]
            anchor = add_node(kind, lesson or hit["passage"][:TEXT_MAX], source_id, 0.8)
        else:
            anchor = add_node(kind, lesson, None, 0.9)

        for sid in superseded_ids:
            if sid == anchor:
                continue
            brain._emit(actor, "node_updated", {"id": sid, "status": "superseded"})
            brain._emit(actor, "edge_set", {"src": anchor, "dst": sid, "kind": "supersedes", "weight": 1.0,
                                            "origin": "correction"})
        for wid in wrong:
            if wid in superseded_ids:
                continue
            for cid in context_ids:
                w = brain._edge_weight(wid, cid, "about")
                if w:
                    brain._emit(actor, "edge_set", {"src": wid, "dst": cid, "kind": "about",
                                                    "weight": round(w * WRONG_DAMPING, 6), "origin": "correction"})

        episode = add_node("episode", f"指摘を受けた: {t.correction}"[:TEXT_MAX], None, 0.7)
        brain._link(actor, episode, anchor, "association", EPISODE_LINK, "correction")

    if mode == "reinforce":
        message = ((MSG_AWAKENED if awakened else "") + MSG_REINFORCE.format(n=times + 1)
                   + (MSG_PINNED.format(n=times + 1) if pinned else ""))
    elif mode == "restore":
        hit = t.shelf[source_id]
        message = MSG_RESTORE.format(title=hit["title"], date=hit["created_at"][:10])
    else:
        message = MSG_NEW
    del brain._traces[trace_id]
    return {"mode": mode, "message_to_user": message, "nodes": created, "pinned": pinned,
            "superseded_ids": [s for s in superseded_ids]}


def _dormant_matches(brain: Brain, cue: str, exclude: set[str], limit: int = 3) -> list[dict]:
    """Dormant memories are not recalled, but being corrected can wake them (like being reminded)."""
    from .recall import CUE_MATCH_MIN, bigrams

    cue_bg = bigrams(cue)
    if not cue_bg:
        return []
    found = []
    for r in brain._conn.execute("SELECT * FROM nodes WHERE status = 'dormant'"):
        if r["id"] in exclude or not r["body"]:
            continue
        score = len(cue_bg & bigrams(r["body"])) / len(cue_bg)
        if score >= CUE_MATCH_MIN:
            found.append({"id": r["id"], "kind": r["kind"], "text": r["body"], "created_at": r["created_at"],
                          "times_corrected": r["corrections"], "score": round(score, 4), "dormant": True})
    return sorted(found, key=lambda f: -f["score"])[:limit]


def _context_concepts(brain: Brain, actor: str, t: Trace, concepts: list[str]) -> list[str]:
    """Concepts named by the AI plus existing concepts mentioned in the correction or its context."""
    ids = [brain._concept_id(actor, name) for name in concepts]
    text = _norm(t.correction + t.context)
    for row in brain._conn.execute("SELECT id, norm FROM nodes WHERE kind = 'concept' AND status = 'active'"):
        if row["norm"] and len(row["norm"]) >= 2 and row["norm"] in text and row["id"] not in ids:
            ids.append(row["id"])
    return ids
