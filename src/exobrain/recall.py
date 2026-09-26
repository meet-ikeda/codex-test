"""Recall by spreading activation (design §4).

cue → seeds (concepts and elements that share text with the cue, plus what this
session recalled recently) → activation spreads up to MAX_HOPS along weighted
links → the strongest elements are packed into a token budget. A few distant
associations that do not match the cue directly are offered separately as
"insights", each with the path that reached it.
"""

from __future__ import annotations

import math
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .tokens import estimate_tokens

# Design values (docs/design.md §4). Tune with real use.
MAX_HOPS = 3
HOP_DECAY = 0.5
BEAM = 60  # nodes expanded per hop
SESSION_SEED = 0.3
SESSION_RECENT = 10
RULES_SHARE = 0.30
INSIGHT_SHARE = 0.15
INSIGHT_MAX = 3
INSIGHT_MIN_HOPS = 2
INSIGHT_MIN_ACTIVATION = 1e-3
RELATED_MIN_RATIO = 0.02  # weaker than this share of the best score is noise, not a memory
CUE_MATCH_MIN = 0.12  # bigram overlap that counts as "matches the cue"
RECENCY_DAYS = 30.0
KIND_LABEL_JA = {"episode": "出来事", "semantic": "知識", "procedural": "ルール", "concept": "概念"}


def normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def bigrams(text: str) -> set[str]:
    t = normalize(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def overlap(cue: set[str], text: set[str]) -> float:
    """Share of the text's bigrams found in the cue (so short labels can match long cues)."""
    if not cue or not text:
        return 0.0
    return len(cue & text) / len(text)


@dataclass
class _Index:
    """Per-process cache of node text for seed matching; reloaded when the log grows."""

    version: int = -1
    concepts: list[tuple[str, str, set[str]]] = field(default_factory=list)  # id, norm, bigrams
    elements: list[tuple[str, set[str]]] = field(default_factory=list)  # id, bigrams


@dataclass
class Hit:
    id: str
    kind: str
    body: str
    created_at: str
    created_by: str
    activation: float
    score: float
    hops: int
    path: list[str]  # node ids from a seed to this node


class Recaller:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.index = _Index()

    # ---- seeds ------------------------------------------------------------

    def _refresh(self) -> None:
        version = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        if version == self.index.version:
            return
        idx = _Index(version=version)
        for r in self.conn.execute("SELECT id, kind, norm, body FROM nodes WHERE status = 'active'"):
            if r[1] == "concept":
                idx.concepts.append((r[0], r[2], bigrams(r[2])))
            elif r[3]:
                idx.elements.append((r[0], bigrams(r[3])))
        self.index = idx

    def seeds(self, cue: str, session_id: str | None) -> dict[str, float]:
        self._refresh()
        cue_norm, cue_bg = normalize(cue), bigrams(cue)
        seeds: dict[str, float] = {}
        for cid, norm, bg in self.index.concepts:
            score = 1.0 if norm and norm in cue_norm else overlap(cue_bg, bg)
            if score >= 0.5:
                seeds[cid] = max(seeds.get(cid, 0.0), score)
        for nid, bg in self.index.elements:
            # For elements, measure against the cue's side: how much of the cue the element covers.
            score = len(cue_bg & bg) / len(cue_bg) if cue_bg else 0.0
            if score >= CUE_MATCH_MIN:
                seeds[nid] = max(seeds.get(nid, 0.0), score)
        if session_id:
            for (nid,) in self.conn.execute(
                "SELECT node_id FROM session_items WHERE session_id = ? ORDER BY at DESC LIMIT ?",
                (session_id, SESSION_RECENT),
            ):
                seeds[nid] = max(seeds.get(nid, 0.0), SESSION_SEED)
        return seeds

    def cue_matches(self, cue: str) -> set[str]:
        """Element ids that directly match the cue text (never offered as insights)."""
        self._refresh()
        cue_bg = bigrams(cue)
        if not cue_bg:
            return set()
        return {nid for nid, bg in self.index.elements if len(cue_bg & bg) / len(cue_bg) >= CUE_MATCH_MIN}

    # ---- spreading --------------------------------------------------------

    def _neighbors(self, nid: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT e.src, e.dst, e.weight FROM edges e JOIN nodes n"
            " ON n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END"
            " WHERE (e.src = ? OR e.dst = ?) AND n.status = 'active' AND e.kind != 'supersedes'",
            (nid, nid, nid),
        ).fetchall()
        return [(dst if src == nid else src, w) for src, dst, w in rows]

    def spread(self, seeds: dict[str, float]) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
        activation = dict(seeds)
        hops = {n: 0 for n in seeds}
        parent: dict[str, str] = {}
        frontier = dict(seeds)
        for hop in range(1, MAX_HOPS + 1):
            nxt: dict[str, float] = {}
            best_in: dict[str, tuple[float, str]] = {}
            for nid, a in sorted(frontier.items(), key=lambda kv: -kv[1])[:BEAM]:
                nbrs = self._neighbors(nid)
                if not nbrs:
                    continue
                fan = math.sqrt(len(nbrs))  # hubs spread thinner (fan effect)
                for other, w in nbrs:
                    push = a * w * HOP_DECAY / fan
                    if push <= 1e-6:
                        continue
                    nxt[other] = nxt.get(other, 0.0) + push
                    if push > best_in.get(other, (0.0, ""))[0]:
                        best_in[other] = (push, nid)
            for other, push in nxt.items():
                activation[other] = activation.get(other, 0.0) + push
                if other not in hops:
                    hops[other] = hop
                    parent[other] = best_in[other][1]
            frontier = nxt
            if not frontier:
                break
        return activation, hops, parent

    # ---- scoring ----------------------------------------------------------

    def score(self, activation: dict[str, float], hops: dict[str, int], parent: dict[str, str]) -> list[Hit]:
        if not activation:
            return []
        now = datetime.now(timezone.utc)
        hits = []
        ids = list(activation)
        for chunk in range(0, len(ids), 500):
            part = ids[chunk : chunk + 500]
            q = ",".join("?" * len(part))
            for r in self.conn.execute(
                f"SELECT id, kind, body, created_at, created_by, importance, base_strength, access_count,"
                f" last_activated_at FROM nodes WHERE id IN ({q}) AND status = 'active' AND kind != 'concept'",
                part,
            ):
                last = r["last_activated_at"] or r["created_at"]
                age_days = max(0.0, (now - datetime.fromisoformat(last)).total_seconds() / 86400)
                recency = 0.5 + 0.5 * math.exp(-age_days / RECENCY_DAYS)
                base = r["base_strength"] * (1 + math.log1p(r["access_count"])) * recency * (0.5 + r["importance"])
                a = activation[r["id"]]
                hits.append(Hit(r["id"], r["kind"], r["body"], r["created_at"], r["created_by"], a, a * base,
                                hops[r["id"]], self._path(r["id"], parent)))
        hits.sort(key=lambda h: -h.score)
        return hits

    @staticmethod
    def _path(nid: str, parent: dict[str, str]) -> list[str]:
        path = [nid]
        while path[-1] in parent and len(path) <= MAX_HOPS + 1:
            path.append(parent[path[-1]])
        return list(reversed(path))

    def labels(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        return {r[0]: r[1] for r in self.conn.execute(f"SELECT id, label FROM nodes WHERE id IN ({q})", ids)}

    def standing_rules(self, exclude: set[str], limit: int = 20) -> list[Hit]:
        """Strongest rules overall, used to fill the rules share when the cue activates few."""
        rows = self.conn.execute(
            "SELECT id, kind, body, created_at, created_by FROM nodes WHERE status = 'active'"
            " AND kind = 'procedural' ORDER BY pinned DESC, importance * base_strength DESC, access_count DESC LIMIT ?",
            (limit + len(exclude),),
        )
        return [Hit(r[0], r[1], r[2], r[3], r[4], 0.0, 0.0, 0, [r[0]]) for r in rows if r[0] not in exclude][:limit]


# ---- packing -----------------------------------------------------------------


def _source_note(hit: Hit) -> str:
    who = hit.created_by.split(":", 1)[-1] if hit.created_by.startswith("ai:") else hit.created_by
    try:
        day = datetime.fromisoformat(hit.created_at).astimezone()
        when = f"{day.month}/{day.day}"
    except ValueError:
        when = hit.created_at[:10]
    return f"{when}, {who}"


def format_line(hit: Hit) -> str:
    if hit.kind == "procedural":
        return f"- {hit.body} [{hit.id}]"
    return f"- [{KIND_LABEL_JA[hit.kind]}] {hit.body}（{_source_note(hit)}）[{hit.id}]"


@dataclass
class Pack:
    rules: list[Hit]
    related: list[Hit]
    insights: list[tuple[Hit, str]]  # hit, path text
    tokens: int

    def text(self) -> str:
        parts = []
        if self.rules:
            parts.append("## あなたについて（ルール）\n" + "\n".join(format_line(h) for h in self.rules))
        if self.related:
            parts.append("## 関連する記憶\n" + "\n".join(format_line(h) for h in self.related))
        if self.insights:
            parts.append("## ひらめき（遠い連想）\n" + "\n".join(
                f"{format_line(h)}\n  経路: {path}" for h, path in self.insights))
        return "\n".join(parts) if parts else "（関連する記憶はありません）"


def pack(recaller: Recaller, hits: list[Hit], cue_ids: set[str], budget: int) -> Pack:
    used = 0

    def take(pool: list[Hit], limit: int, fmt=format_line, out=None) -> list:
        nonlocal used
        out = [] if out is None else out
        spent = 0
        for h in pool:
            cost = estimate_tokens(fmt(h)) + 1
            if spent + cost > limit or used + cost > budget:
                continue
            out.append(h)
            spent += cost
            used += cost
        return out

    header_cost = 40  # the three "## ..." headings
    used += header_cost
    chosen: set[str] = set()

    # 1) Rules: activated rules first, then the strongest standing rules.
    activated_rules = [h for h in hits if h.kind == "procedural"]
    rules = take(activated_rules, int(budget * RULES_SHARE))
    chosen |= {h.id for h in rules}
    rest = int(budget * RULES_SHARE) - sum(estimate_tokens(format_line(h)) + 1 for h in rules)
    rules = take(recaller.standing_rules(chosen), rest, out=rules)
    chosen |= {h.id for h in rules}

    # 2) Insights: reached through >= 2 hops, not matching the cue, not linked to the top answers.
    others = [h for h in hits if h.id not in chosen]
    top_ids = {h.id for h in others[:5]}
    best = others[0].score if others else 0.0
    insight_pool = []
    for h in others:
        # Distant associations are weak by nature, so no share-of-best floor here.
        if h.hops < INSIGHT_MIN_HOPS or h.id in cue_ids or h.id in top_ids or h.activation < INSIGHT_MIN_ACTIVATION:
            continue
        if any(n in top_ids for n, _ in recaller._neighbors(h.id)):
            continue
        insight_pool.append(h)
    labels = recaller.labels(sorted({n for h in insight_pool[:INSIGHT_MAX * 3] for n in h.path}))

    def path_text(h: Hit) -> str:
        return " → ".join(labels.get(n, n) for n in h.path)

    insights: list[tuple[Hit, str]] = []
    limit = int(budget * INSIGHT_SHARE)
    spent = 0
    for h in insight_pool:
        if len(insights) >= INSIGHT_MAX:
            break
        text = path_text(h)
        cost = estimate_tokens(format_line(h)) + estimate_tokens(text) + 3
        if spent + cost > limit or used + cost > budget:
            continue
        insights.append((h, text))
        spent += cost
        used += cost
    chosen |= {h.id for h, _ in insights}

    # 3) Related: everything else by score, until the budget is full.
    related = take([h for h in others if h.id not in chosen and h.score >= best * RELATED_MIN_RATIO], budget)
    return Pack(rules, related, insights, used)
