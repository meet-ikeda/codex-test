"""Hippocampus and bookshelf search: find the passage an answer rests on (spec v0.5 §7.1, §8).

Every original is split into chunks. A chunk is found by meaning (embedding
cosine, when Ollama is available) and by words (bm25 over Japanese bigrams),
mixed 70:30 as in OUTBRAIN. Results carry date, writer, heading, line numbers
and a quote cut from the original, so an AI can say where it read something.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from .chunks import lexical_tokens
from .embed import EmbedUnavailable, pack

if TYPE_CHECKING:
    from .brain import Brain

SEMANTIC_SHARE = 0.7  # OUTBRAIN's starting point; tune on the fixed question set
CANDIDATES = 50
QUOTE_CHARS = 220
# Below these, a hit is not evidence. Calibrated on eval-questions-v2 (see docs/eval-embedding.md).
MIN_SEMANTIC = 0.46
RELATIVE_MARGIN = float(__import__("os").environ.get("EXOBRAIN_MARGIN", "0.10"))  # measured: docs/eval-embedding.md
MIN_LEXICAL_ONLY = 0.5  # share of query bigrams found, when there is no embedding
EXCLUDED_KINDS = ("dream",)  # the sleep's own journal is not evidence of what the owner said


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    kind: str
    author: str
    ai_name: str | None
    title: str
    created_at: str
    heading: str
    line_start: int
    line_end: int
    text: str
    semantic: float
    lexical: float
    score: float
    in_hippocampus: bool

    def quote(self, cue_tokens: set[str]) -> str:
        """The part of the chunk that best matches the cue, cut verbatim from the original."""
        body = self.text.strip()
        if len(body) <= QUOTE_CHARS:
            return body
        best, best_i = -1, 0
        step = QUOTE_CHARS // 4
        for i in range(0, max(1, len(body) - QUOTE_CHARS + 1), step):
            window = set(lexical_tokens(body[i : i + QUOTE_CHARS]))
            n = len(window & cue_tokens)
            if n > best:
                best, best_i = n, i
        piece = body[best_i : best_i + QUOTE_CHARS].strip()
        return ("…" if best_i else "") + piece + ("…" if best_i + QUOTE_CHARS < len(body) else "")

    def to_dict(self, cue_tokens: set[str]) -> dict:
        who = self.ai_name if self.author == "ai" else "オーナー"
        return {"source_id": self.source_id, "chunk_id": self.chunk_id, "place": "海馬" if self.in_hippocampus else "本棚",
                "title": self.title, "kind": self.kind, "writer": who, "created_at": self.created_at,
                "heading": self.heading, "lines": [self.line_start, self.line_end],
                "quote": self.quote(cue_tokens), "score": round(self.score, 3)}


class ChunkIndex:
    """In-memory matrix of chunk vectors for one model, refreshed when chunks change."""

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.ids: list[str] = []
        self.matrix: np.ndarray | None = None

    def load(self, conn: sqlite3.Connection, model: str) -> None:
        key = (model, *conn.execute("SELECT COUNT(*), MAX(rowid) FROM chunk_vectors WHERE model = ?",
                                    (model,)).fetchone())
        if key == self.key:
            return
        rows = conn.execute("SELECT chunk_id, vec FROM chunk_vectors WHERE model = ?", (model,)).fetchall()
        self.ids = [r[0] for r in rows]
        if rows:
            m = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
            self.matrix = m
        else:
            self.matrix = None
        self.key = key

    def cosine(self, q: list[float]) -> dict[str, float]:
        if self.matrix is None:
            return {}
        v = np.asarray(q, dtype=np.float32)
        v /= np.linalg.norm(v) + 1e-12
        sims = self.matrix @ v
        top = np.argsort(-sims)[:CANDIDATES]
        return {self.ids[i]: float(sims[i]) for i in top}


def encode_pending(brain: Brain, limit: int = 200) -> int:
    """Embed chunks that have no vector for the current model yet. Returns how many were embedded."""
    emb = brain.embedder
    if emb is None:
        return 0
    with brain._lock:
        rows = brain._conn.execute(
            "SELECT c.id, c.heading, c.text FROM chunks c JOIN sources s ON s.id = c.source_id"
            " WHERE s.erased = 0 AND c.id NOT IN (SELECT chunk_id FROM chunk_vectors WHERE model = ?)"
            " ORDER BY s.created_at LIMIT ?", (emb.model, limit)).fetchall()
    if not rows:
        with brain._lock:  # vectors of chunks that no longer exist (re-chunked or erased)
            brain._conn.execute("DELETE FROM chunk_vectors WHERE chunk_id NOT IN (SELECT id FROM chunks)")
        return 0
    try:
        vecs = emb.embed([(r["heading"] + "\n" + r["text"]).strip() for r in rows])
    except EmbedUnavailable:
        return 0
    with brain._lock:
        brain._conn.executemany("INSERT OR REPLACE INTO chunk_vectors (chunk_id, model, vec) VALUES (?, ?, ?)",
                                [(r["id"], emb.model, pack(v)) for r, v in zip(rows, vecs)])
    return len(rows)


def unencoded_count(brain: Brain) -> int:
    emb = brain.embedder
    if emb is None:
        return 0
    return brain._conn.execute(
        "SELECT COUNT(*) FROM chunks c JOIN sources s ON s.id = c.source_id WHERE s.erased = 0"
        " AND c.id NOT IN (SELECT chunk_id FROM chunk_vectors WHERE model = ?)", (emb.model,)).fetchone()[0]


def _lexical(conn: sqlite3.Connection, tokens: list[str]) -> dict[str, float]:
    """Share of the query's distinct bigrams that appear in each chunk (0..1), best CANDIDATES only."""
    distinct = list(dict.fromkeys(t for t in tokens if '"' not in t))
    if not distinct:
        return {}
    query = " OR ".join(f'"{t}"' for t in distinct)
    rows = conn.execute("SELECT chunk_id, grams FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts)"
                        " LIMIT ?", (query, CANDIDATES * 2)).fetchall()
    q = set(distinct)
    out = {}
    for cid, grams in rows:
        out[cid] = len(q & set(grams.split())) / len(q)
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:CANDIDATES])


def search(brain: Brain, cue: str, scope: str = "all", limit: int = 5) -> list[Hit]:
    """scope: 'hippocampus' (waiting for sleep), 'bookshelf' (everything else), or 'all'."""
    conn = brain._conn
    tokens = lexical_tokens(cue)
    with brain._lock:
        lex = _lexical(conn, tokens)
        sem: dict[str, float] = {}
        emb = brain.embedder
        if emb is not None:
            try:
                qv = emb.embed([cue])[0]
            except EmbedUnavailable:
                qv = None
            if qv is not None:
                brain.chunk_index.load(conn, emb.model)
                sem = brain.chunk_index.cosine(qv)
        use_semantic = bool(sem)
        embedded = set(brain.chunk_index.ids) if use_semantic else set()
        ids = set(lex) | set(sem)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            "SELECT c.id, c.source_id, c.heading, c.line_start, c.line_end, c.text, s.kind, s.author, s.ai_name,"
            " s.title, s.created_at, h.status AS hstatus FROM chunks c JOIN sources s ON s.id = c.source_id"
            f" LEFT JOIN hippocampus h ON h.source_id = c.source_id WHERE c.id IN ({marks}) AND s.erased = 0",
            tuple(ids)).fetchall()
    hits = []
    for r in rows:
        if r["kind"] in EXCLUDED_KINDS:
            continue
        in_h = r["hstatus"] == "waiting"
        if scope == "hippocampus" and not in_h or scope == "bookshelf" and in_h:
            continue
        s_val, l_val = sem.get(r["id"], 0.0), lex.get(r["id"], 0.0)
        if r["id"] in embedded:
            if s_val < MIN_SEMANTIC:
                continue
            score = SEMANTIC_SHARE * s_val + (1 - SEMANTIC_SHARE) * l_val
        else:
            if l_val < MIN_LEXICAL_ONLY:
                continue
            score = l_val
        hits.append(Hit(r["id"], r["source_id"], r["kind"], r["author"], r["ai_name"], r["title"], r["created_at"],
                        r["heading"], r["line_start"], r["line_end"], r["text"], s_val, l_val, score, in_h))
    hits.sort(key=lambda h: -h.score)
    if hits:  # far below the best answer is noise, not evidence
        hits = [h for h in hits if h.score >= hits[0].score - RELATIVE_MARGIN]
    # One passage per original: the best one.
    out, seen = [], set()
    for h in hits:
        if h.source_id in seen:
            continue
        seen.add(h.source_id)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def fade_expired(brain: Brain, actor: str) -> int:
    """Information not taken into the cortex leaves the hippocampus after its time (it stays on the bookshelf)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids = [r[0] for r in brain._conn.execute(
        "SELECT source_id FROM hippocampus WHERE status = 'waiting' AND expires_at < ?", (now,))]
    if ids:
        brain._emit(actor, "hippocampus_faded", {"source_ids": ids})
    return len(ids)


def search_vault(brain: Brain, cue: str, limit: int = 3) -> list[dict]:
    """Last resort before "no record": look through the owner's vault notes by words (read only)."""
    vault = brain.settings.vault_root
    if not vault or not vault.is_dir():
        return []
    q = set(lexical_tokens(cue))
    if not q:
        return []
    filed = {r[0] for r in brain._conn.execute("SELECT origin_file FROM sources WHERE origin_file IS NOT NULL")}
    found = []
    for path in vault.rglob("*.md"):
        rel = path.relative_to(vault)
        if any(p.startswith(".") for p in rel.parts) or rel.as_posix() in filed:
            continue
        try:
            if path.stat().st_size > 300_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        share = len(q & set(lexical_tokens(text))) / len(q)
        if share >= MIN_LEXICAL_ONLY:
            found.append((share, rel.as_posix(), text))
    found.sort(key=lambda x: -x[0])
    out = []
    for share, rel, text in found[:limit]:
        h = Hit("", "", "vault", "human", None, rel, "", "", 0, 0, text, 0.0, share, share, False)
        out.append({"place": "Obsidian", "file": rel, "quote": h.quote(q), "score": round(share, 3)})
    return out
