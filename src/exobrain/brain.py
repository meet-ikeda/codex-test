"""The brain: elements (nodes) joined by weighted links (edges).

The event log is the source of truth. The `sources`, `nodes`, `edges` and
`sessions` tables are projections: every write appends an event and applies it
in the same transaction, and `rebuild()` can regenerate them from the log.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import events
from .bookshelf import body_sha256, read_body, write_original
from .config import Settings
from .recall import Recaller, pack
from .tokens import estimate_tokens

# Design values (docs/design.md). Tune with real use.
TEXT_MAX = 300
TITLE_MAX = 100
REPORT_MAX = 50_000
ELEMENTS_MAX = 30
CONCEPTS_MAX = 8
CONCEPT_LEN_MAX = 40
LABEL_LEN = 40
W_ABOUT = 0.5
W_SAME_REPORT = 0.3
HEBBIAN_RATE = 0.1
PROFILE_BUDGET = 500
RECALL_BUDGET = 1500
RECALL_BUDGET_RANGE = (300, 4000)
RECALL_HEBBIAN_RATE = 0.05  # weaker than confirmed use (HEBBIAN_RATE)
RECALL_REINFORCE_TOP = 5
GOOD_IMPORTANCE_STEP = 0.15  # /good: how much a used memory's importance rises (spec v0.5 §7.3)
GOOD_HEBBIAN_RATE = 0.3  # /good wires used memories together much harder than plain recall (0.05)
NODE_SEED_MIN = 0.5  # bge-m3 cosine: a cortex memory this close to the cue starts the spreading (provisional)
NODE_FOUND_MIN = 0.62  # ...and this close counts as the cortex having answered (no need to look further)
NODE_SEEDS_MAX = 20
REVISION_LINK = 0.8  # a memory and the episode of rewriting it stay closely tied
AUTO_LOGGED_AIS = ("codex", "claude code")  # their conversations are read from this Mac every night (Cowork may run
# in the cloud, where nothing is left on this Mac, so its /日報 is accepted)
FALLBACK_KEEP = 0.6  # share of the recall budget the cortex keeps when records are looked up too

ELEMENT_KINDS = ("episode", "semantic", "procedural", "case")
# Spec v0.8 §5.2 / §6: a judgment made once is a case (situation, decision, reason, reaction, when), not a rule.
# Rules have a stage: tentative (grown from cases, not handed out at the start) -> confirmed (the owner said so,
# or said "always ...") -> retired (the owner said no, or cases stopped fitting). Old rules have no stage yet.
RULE_STAGES = ("tentative", "confirmed")
CASE_FIELDS = ("situation", "decision", "reason", "reaction", "when")
# A light ontology (2026-09-30): what kind of thing a concept is, and its other names. Few types on purpose;
# add one only when real memories need it.
CONCEPT_TYPES = {"person": "人", "organization": "会社・組織", "project": "案件", "tool": "道具・サービス",
                 "place": "場所", "topic": "話題"}
KIND_LABEL_JA = {"episode": "出来事", "semantic": "知識", "procedural": "ルール", "concept": "概念", "case": "事例"}

PROJECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, author TEXT NOT NULL, ai_name TEXT,
    title TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
    erased INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, body TEXT, norm TEXT,
    source_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5, base_strength REAL NOT NULL DEFAULT 1.0,
    access_count INTEGER NOT NULL DEFAULT 0, last_activated_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    corrections INTEGER NOT NULL DEFAULT 0,  -- times the owner had to point this out again
    pinned INTEGER NOT NULL DEFAULT 0,        -- always included in recall (standing rule)
    promoted_by TEXT,                         -- explicit | demand | repetition | association (spec v0.5 §5)
    derivation TEXT,                          -- verbatim | paraphrase | inferred (OUTBRAIN v0.4 §4.3)
    confidence REAL,
    occurrences INTEGER NOT NULL DEFAULT 1,   -- seen again in another source on another day
    goods INTEGER NOT NULL DEFAULT 0,         -- times the owner sent /good for an answer that used it
    about TEXT,                               -- whose memory: owner | client | interviewee | other
    subject TEXT                              -- who, when it is not the owner (company, person, role)
);
CREATE UNIQUE INDEX IF NOT EXISTS nodes_concept_norm ON nodes(norm) WHERE kind = 'concept';
CREATE INDEX IF NOT EXISTS nodes_kind ON nodes(kind, status);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
    co_activations INTEGER NOT NULL DEFAULT 0, last_reinforced_at TEXT, origin TEXT NOT NULL,
    PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, ai_name TEXT NOT NULL, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
-- Full-text index of bookshelf originals (trigram: works for Japanese without word breaks).
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(source_id UNINDEXED, title, body, tokenize = 'trigram');
-- Topic shelves assigned during sleep (date/source shelves are derived, not stored).
CREATE TABLE IF NOT EXISTS shelves (
    source_id TEXT NOT NULL, shelf TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (source_id, shelf)
);
-- Sleep: runs, and marks for work already done (so it is not queued again).
CREATE TABLE IF NOT EXISTS sleep_runs (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, start_event INTEGER NOT NULL,
    stage_a TEXT NOT NULL, finished_at TEXT, summary TEXT, journal_source_id TEXT
);
CREATE TABLE IF NOT EXISTS sleep_marks (
    kind TEXT NOT NULL, key TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
-- Short-term memory: what each conversation has recalled.
CREATE TABLE IF NOT EXISTS session_items (
    session_id TEXT NOT NULL, node_id TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (session_id, node_id)
);
-- Chunks of every original, with where they sit in it (spec v0.5 §6.1). Derived from the file.
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, idx INTEGER NOT NULL, heading TEXT NOT NULL,
    start INTEGER NOT NULL, length INTEGER NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id);
-- Lexical index over chunks: Latin words and Japanese bigrams, space separated, ranked by bm25().
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, grams, tokenize = 'unicode61');
-- The hippocampus: information waiting for sleep, which fades after a while (spec v0.5 §3).
CREATE TABLE IF NOT EXISTS hippocampus (
    source_id TEXT PRIMARY KEY, entered_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting'  -- waiting | faded | promoted
);
-- Where each AI thread's daily logs have reached (AI daily log protocol v1).
CREATE TABLE IF NOT EXISTS ai_checkpoints (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, thread_id TEXT NOT NULL, cursor TEXT NOT NULL,
    entry_date TEXT, source_id TEXT, at TEXT NOT NULL
);
-- Where each memory came from: quote cut by the program from the original, with its lines.
CREATE TABLE IF NOT EXISTS node_sources (
    node_id TEXT NOT NULL, source_id TEXT NOT NULL, line_start INTEGER, line_end INTEGER, quote TEXT NOT NULL,
    at TEXT NOT NULL, PRIMARY KEY (node_id, source_id, line_start)
);
-- Reconsolidation (spec v0.6): a memory rewritten when recalled, with why, and the episode of rewriting it.
CREATE TABLE IF NOT EXISTS revisions (
    node_id TEXT NOT NULL, at TEXT NOT NULL, old_body TEXT NOT NULL, new_body TEXT NOT NULL, reason TEXT NOT NULL,
    episode_id TEXT, source_id TEXT, actor TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS revisions_node ON revisions(node_id);
-- Other names of a concept (「A社様」→「A社」), so one thing is not scattered over several names.
CREATE TABLE IF NOT EXISTS concept_aliases (
    norm TEXT PRIMARY KEY, concept_id TEXT NOT NULL, alias TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_vectors (
    node_id TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (node_id, model)
);
-- Embedding cache. Not a projection: rebuild() keeps it, and it can be recomputed from chunks.
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (chunk_id, model)
);
"""
PROJECTION_TABLES = ("sources", "nodes", "edges", "sessions", "session_items", "source_fts", "shelves",
                     "sleep_runs", "sleep_marks", "chunks", "chunk_fts", "hippocampus", "ai_checkpoints",
                     "node_sources", "revisions", "concept_aliases")


class InvalidInput(ValueError):
    """Input from an AI that should be fixed and resent. The message is shown to the AI."""


@dataclass(frozen=True)
class Element:
    kind: str
    text: str
    concepts: list[str]
    importance: float
    concept_meta: dict = field(default_factory=dict)  # name -> {"type": ..., "aliases": [...]}


def split_concept(raw: Any) -> tuple[str, str | None]:
    """「[[A社|A社様]]」「A社|A社様」→ ("A社", "A社様"): the Obsidian way of writing a link under another name."""
    s = " ".join(str(raw).split()).removeprefix("[[").removesuffix("]]")
    name, _, alias = s.partition("|")
    name, alias = name.split("#")[0].strip(), alias.strip()
    return name, (alias if alias and alias != name else None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):011x}{secrets.token_hex(4)}"


def normalize_concept(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def make_label(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= LABEL_LEN else one_line[: LABEL_LEN - 1] + "…"


def parse_elements(raw: Iterable[Any]) -> list[Element]:
    raw = list(raw or [])
    if len(raw) > ELEMENTS_MAX:
        raise InvalidInput(f"elements は 1 回に {ELEMENTS_MAX} 件までです（{len(raw)} 件）。分けて送ってください。")
    out = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise InvalidInput(f"elements[{i}] はオブジェクトにしてください。")
        kind = e.get("kind")
        if kind not in ELEMENT_KINDS:
            raise InvalidInput(f"elements[{i}].kind は {', '.join(ELEMENT_KINDS)} のいずれかです（{kind!r}）。")
        text = (e.get("text") or "").strip()
        if not text:
            raise InvalidInput(f"elements[{i}].text が空です。")
        if len(text) > TEXT_MAX:
            raise InvalidInput(
                f"elements[{i}].text が {len(text)} 文字あります。1 要素は {TEXT_MAX} 文字までです。"
                "1 つの事実・出来事・ルールごとに分けてください。"
            )
        concepts, meta = [], {}
        for c in e.get("concepts") or []:
            info = c if isinstance(c, dict) else {"name": c}
            c, alias = split_concept(info.get("name") or "")
            if not c:
                continue
            ctype = info.get("type") or None
            if ctype and ctype not in CONCEPT_TYPES:
                raise InvalidInput(f"elements[{i}].concepts の type は {', '.join(CONCEPT_TYPES)} のいずれかです（{ctype!r}）。")
            aliases = [a for a in [alias, *(info.get("aliases") or [])] if a]
            if c not in concepts:
                concepts.append(c)
            m = meta.setdefault(c, {"type": None, "aliases": []})
            m["type"] = m["type"] or ctype
            m["aliases"] += [a for a in aliases if a not in m["aliases"]]
        if len(concepts) > CONCEPTS_MAX:
            raise InvalidInput(f"elements[{i}].concepts は {CONCEPTS_MAX} 個までです。")
        if any(len(c) > CONCEPT_LEN_MAX for c in concepts):
            raise InvalidInput(f"elements[{i}].concepts の各語は {CONCEPT_LEN_MAX} 文字までです。")
        try:
            importance = float(e.get("importance", 0.5))
        except (TypeError, ValueError):
            raise InvalidInput(f"elements[{i}].importance は 0〜1 の数にしてください。") from None
        if not 0.0 <= importance <= 1.0:
            raise InvalidInput(f"elements[{i}].importance は 0〜1 の数にしてください。")
        out.append(Element(kind, text, concepts, importance, meta))
    return out


def hebbian(weight: float, rate: float = HEBBIAN_RATE) -> float:
    return weight + rate * (1.0 - weight)


def _pair(a: str, b: str) -> tuple[str, str]:
    """Association links are undirected; store them in a canonical order."""
    return (a, b) if a < b else (b, a)


class Brain:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self._conn = sqlite3.connect(str(settings.db_path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA secure_delete = ON")  # erased content is overwritten, not just unlinked
        self._conn.executescript(events.SCHEMA + PROJECTION_SCHEMA)
        self._migrate()
        self.fts_secure_delete = self._enable_fts_secure_delete()
        # MCP runs sync tools in worker threads; one write transaction at a time per process.
        # Across processes, BEGIN IMMEDIATE + busy_timeout serialize writers.
        self._lock = threading.RLock()
        self._recaller = Recaller(self._conn)
        from .embed import make_embedder
        from .hippocampus import ChunkIndex

        self.embedder = make_embedder(settings.embed_model, settings.ollama_url)
        self.chunk_index = ChunkIndex()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(nodes)")}
        for col in ("corrections", "pinned"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        for col, decl in (("promoted_by", "TEXT"), ("derivation", "TEXT"), ("confidence", "REAL"),
                          ("occurrences", "INTEGER NOT NULL DEFAULT 1"), ("goods", "INTEGER NOT NULL DEFAULT 0"),
                          ("about", "TEXT"), ("subject", "TEXT"), ("concept_type", "TEXT"),
                          ("scope", "TEXT"), ("stage", "TEXT"), ("case_json", "TEXT")):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} {decl}")
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(sources)")}
        for col in ("origin_file", "meta_json"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE sources ADD COLUMN {col} TEXT")
        # Sources filed before chunks existed: chunk them now (derived data, no events needed).
        missing = self._conn.execute(
            "SELECT id, path, kind FROM sources WHERE erased = 0 AND id NOT IN (SELECT DISTINCT source_id FROM chunks)"
        ).fetchall()
        for r in missing:
            path = self.settings.drive_root / r["path"]
            if path.exists():
                self._index_chunks(r["id"], read_body(path), r["kind"])

    def _enable_fts_secure_delete(self) -> bool:
        """FTS5 secure-delete (SQLite 3.42+) removes forensic traces from the index on delete."""
        try:
            self._conn.execute("INSERT INTO source_fts (source_fts, rank) VALUES ('secure-delete', 1)")
            return True
        except sqlite3.OperationalError:
            return False  # older SQLite: erase() falls back to rebuilding the index

    def close(self) -> None:
        self._conn.close()

    # ---- pause (FR-10) ------------------------------------------------------

    @property
    def paused(self) -> bool:
        return (self.settings.home / "paused").exists()

    def set_paused(self, value: bool) -> None:
        flag = self.settings.home / "paused"
        if value:
            flag.touch()
        else:
            flag.unlink(missing_ok=True)

    def _require_writable(self) -> None:
        if self.paused:
            raise InvalidInput("exobrain は利用者によって一時停止中です。記憶の追加はできません（思い出すことはできます）。")

    def __enter__(self) -> Brain:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- transaction + event plumbing ------------------------------------

    @contextmanager
    def _tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _emit(self, actor: str, type_: str, payload: dict[str, Any]) -> events.Event:
        ev = events.append(self._conn, actor, type_, payload)
        self._apply(ev)
        return ev

    def _apply(self, ev: events.Event) -> None:
        p = ev.payload
        if p is None:
            return  # erased payload; handled by explicit erase events (M3)
        c = self._conn
        if ev.type == "source_added":
            c.execute(
                "INSERT INTO sources (id, kind, author, ai_name, title, path, sha256, created_at, origin_file, meta_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p["id"], p["kind"], p["author"], p["ai_name"], p["title"], p["path"], p["sha256"], p["created_at"],
                 p.get("origin_file"), events.canonical_json(p["meta"]) if p.get("meta") else None),
            )
            path = self.settings.drive_root / p["path"]
            body = read_body(path) if path.exists() else ""
            c.execute("INSERT INTO source_fts (source_id, title, body) VALUES (?, ?, ?)", (p["id"], p["title"], body))
            self._index_chunks(p["id"], body, p["kind"])
        elif ev.type == "node_sourced":
            c.execute("INSERT OR IGNORE INTO node_sources (node_id, source_id, line_start, line_end, quote, at)"
                      " VALUES (?, ?, ?, ?, ?, ?)",
                      (p["node_id"], p["source_id"], p.get("line_start"), p.get("line_end"), p["quote"], ev.at))
            if p.get("occurrence"):
                c.execute("UPDATE nodes SET occurrences = occurrences + 1 WHERE id = ?", (p["node_id"],))
        elif ev.type == "node_revised":
            c.execute("UPDATE nodes SET body = ?, label = ? WHERE id = ?", (p["new_body"], p["label"], p["id"]))
            c.execute("DELETE FROM node_vectors WHERE node_id = ?", (p["id"],))  # re-embedded with the new wording
            c.execute("INSERT INTO revisions (node_id, at, old_body, new_body, reason, episode_id, source_id, actor)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (p["id"], ev.at, p["old_body"], p["new_body"], p["reason"], p.get("episode_id"),
                       p.get("source_id"), ev.actor))
        elif ev.type == "hippocampus_entered":
            # Entering again (repromote, spec v0.8 §11.3) puts it back to waiting with a new expiry.
            c.execute("INSERT INTO hippocampus (source_id, entered_at, expires_at) VALUES (?, ?, ?)"
                      " ON CONFLICT (source_id) DO UPDATE SET entered_at = excluded.entered_at,"
                      " expires_at = excluded.expires_at, status = 'waiting'",
                      (p["source_id"], ev.at, p["expires_at"]))
        elif ev.type == "hippocampus_faded":
            c.executemany("UPDATE hippocampus SET status = 'faded' WHERE source_id = ? AND status = 'waiting'",
                          [(sid,) for sid in p["source_ids"]])
        elif ev.type == "ai_checkpoint_set":
            c.execute("INSERT INTO ai_checkpoints (key, source, thread_id, cursor, entry_date, source_id, at)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET cursor = excluded.cursor,"
                      " entry_date = excluded.entry_date, source_id = excluded.source_id, at = excluded.at",
                      (p["key"], p["source"], p["thread_id"], p["cursor"], p.get("entry_date"), p.get("source_id"),
                       ev.at))
        elif ev.type == "node_added":
            extra_cols = {k: p[k] for k in ("scope", "stage") if p.get(k)}
            if p.get("case"):
                extra_cols["case_json"] = events.canonical_json(p["case"])
            c.execute(
                "INSERT INTO nodes (id, kind, label, body, norm, source_id, created_by, created_at, importance,"
                " promoted_by, derivation, confidence, about, subject) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p["id"], p["kind"], p["label"], p.get("body"), p.get("norm"), p.get("source_id"),
                 ev.actor, ev.at, p.get("importance", 0.5), p.get("promoted_by"), p.get("derivation"),
                 p.get("confidence"), p.get("about"), p.get("subject")),
            )
            if extra_cols:
                c.execute(f"UPDATE nodes SET {', '.join(f'{k} = ?' for k in extra_cols)} WHERE id = ?",
                          (*extra_cols.values(), p["id"]))
        elif ev.type == "edge_set":
            c.execute(
                "INSERT INTO edges (src, dst, kind, weight, co_activations, last_reinforced_at, origin)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (src, dst, kind) DO UPDATE SET weight = excluded.weight,"
                " co_activations = edges.co_activations + excluded.co_activations,"
                " last_reinforced_at = excluded.last_reinforced_at, origin = excluded.origin",
                (p["src"], p["dst"], p["kind"], p["weight"], 1 if p.get("co_activation") else 0, ev.at, p["origin"]),
            )
        elif ev.type == "nodes_touched":
            c.executemany(
                "UPDATE nodes SET access_count = access_count + 1, last_activated_at = ? WHERE id = ?",
                [(ev.at, nid) for nid in p["ids"]],
            )
        elif ev.type == "recalled":
            c.executemany(
                "UPDATE nodes SET access_count = access_count + 1, last_activated_at = ? WHERE id = ?",
                [(ev.at, nid) for nid in p["ids"]],
            )
            c.executemany(
                "INSERT INTO session_items (session_id, node_id, at) VALUES (?, ?, ?)"
                " ON CONFLICT (session_id, node_id) DO UPDATE SET at = excluded.at",
                [(p["session_id"], nid, ev.at) for nid in p["ids"]],
            )
            c.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (ev.at, p["session_id"]))
        elif ev.type == "node_updated":
            sets, args = [], []
            for col in ("base_strength", "pinned", "status", "importance", "stage", "scope"):
                if col in p:
                    sets.append(f"{col} = ?")
                    args.append(p[col])
            if p.get("goods_delta"):
                sets.append("goods = goods + ?")
                args.append(p["goods_delta"])
            if p.get("corrections_delta"):
                sets.append("corrections = corrections + ?")
                args.append(p["corrections_delta"])
            if sets:
                c.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", (*args, p["id"]))
        elif ev.type == "erased":
            # Payloads that held the content are nulled separately; here we drop the projection rows.
            if p["target"] == "source":
                c.execute("DELETE FROM chunk_vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id = ?)",
                          (p["id"],))
                c.execute("DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id = ?)",
                          (p["id"],))
                c.execute("DELETE FROM chunks WHERE source_id = ?", (p["id"],))
                c.execute("DELETE FROM hippocampus WHERE source_id = ?", (p["id"],))
                c.execute("DELETE FROM node_sources WHERE source_id = ?", (p["id"],))
                c.execute("DELETE FROM sources WHERE id = ?", (p["id"],))
                c.execute("DELETE FROM shelves WHERE source_id = ?", (p["id"],))
                c.execute("DELETE FROM source_fts WHERE source_id = ?", (p["id"],))
            else:
                c.execute("DELETE FROM nodes WHERE id = ?", (p["id"],))
                c.execute("DELETE FROM node_sources WHERE node_id = ?", (p["id"],))
                c.execute("DELETE FROM node_vectors WHERE node_id = ?", (p["id"],))
                c.execute("DELETE FROM revisions WHERE node_id = ?", (p["id"],))
                c.execute("DELETE FROM edges WHERE src = ? OR dst = ?", (p["id"], p["id"]))
                c.execute("DELETE FROM session_items WHERE node_id = ?", (p["id"],))
        elif ev.type == "shelf_assigned":
            c.execute("DELETE FROM shelves WHERE source_id = ?", (p["source_id"],))
            c.executemany("INSERT INTO shelves (source_id, shelf, at) VALUES (?, ?, ?)",
                          [(p["source_id"], name, ev.at) for name in p["shelves"]])
        elif ev.type == "sleep_mark":
            c.execute("INSERT INTO sleep_marks (kind, key, value, at) VALUES (?, ?, ?, ?)"
                      " ON CONFLICT (kind, key) DO UPDATE SET value = excluded.value, at = excluded.at",
                      (p["kind"], p["key"], p.get("value", 0), ev.at))
        elif ev.type == "sleep_marks_cleared":
            c.executemany("DELETE FROM sleep_marks WHERE kind = ? AND key LIKE ?",
                          [(p["kind"], f"{sid}:%") for sid in p["source_ids"]])
        elif ev.type == "sleep_started":
            c.execute("INSERT INTO sleep_runs (id, started_at, start_event, stage_a) VALUES (?, ?, ?, ?)",
                      (p["id"], ev.at, p.get("from_event", ev.id), events.canonical_json(p["stage_a"])))
        elif ev.type == "sleep_finished":
            c.execute("UPDATE sleep_runs SET finished_at = ?, summary = ?, journal_source_id = ? WHERE id = ?",
                      (ev.at, p["summary"], p.get("journal_source_id"), p["id"]))
        elif ev.type == "concept_described":
            if p.get("type"):
                c.execute("UPDATE nodes SET concept_type = ? WHERE id = ?", (p["type"], p["id"]))
            c.executemany("INSERT OR IGNORE INTO concept_aliases (norm, concept_id, alias, at) VALUES (?, ?, ?, ?)",
                          [(normalize_concept(a), p["id"], a, ev.at) for a in p.get("aliases") or []])
        elif ev.type == "session_started":
            c.execute(
                "INSERT INTO sessions (id, ai_name, started_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (p["id"], p["ai_name"], ev.at, ev.at),
            )
        else:
            raise ValueError(f"unknown event type: {ev.type}")

    def _index_chunks(self, source_id: str, body: str, kind: str | None = None) -> None:
        from .chunks import chunk_document, lexical_tokens

        for ch in chunk_document(body, every_heading=kind in ("ai_daily", "deposit")):
            # The id carries the text's hash: if chunking changes, stale vectors can never attach to new text.
            cid = f"{source_id}#{ch.idx}:{hashlib.sha256(ch.text.encode()).hexdigest()[:10]}"
            self._conn.execute(
                "INSERT INTO chunks (id, source_id, idx, heading, start, length, line_start, line_end, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, source_id, ch.idx, ch.heading, ch.start, ch.length, ch.line_start, ch.line_end, ch.text),
            )
            grams = " ".join(lexical_tokens(ch.heading + "\n" + ch.text))
            self._conn.execute("INSERT INTO chunk_fts (chunk_id, grams) VALUES (?, ?)", (cid, grams))

    def rebuild(self) -> int:
        """Regenerate every projection table from the event log."""
        with self._tx():
            for t in PROJECTION_TABLES:
                self._conn.execute(f"DELETE FROM {t}")
            n = 0
            for ev in events.iter_events(self._conn):
                self._apply(ev)
                n += 1
        return n

    def verify(self) -> tuple[bool, str]:
        ok, msg = events.verify(self._conn)
        if not ok:
            return ok, msg
        bad = [s["id"] for s in self._conn.execute("SELECT * FROM sources WHERE erased = 0")
               if not self._source_intact(s)]
        if bad:
            return False, f"bookshelf originals changed or missing: {', '.join(bad)}"
        return True, msg

    def _source_intact(self, s: sqlite3.Row) -> bool:
        path = self.settings.drive_root / s["path"]
        return path.exists() and body_sha256(read_body(path)) == s["sha256"]

    # ---- building blocks --------------------------------------------------

    def _concept_id(self, actor: str, name: str, meta: dict | None = None) -> str:
        """The concept for a name (or one of its other names); a new one if unknown. The type and other names
        given with it are kept; a type already set is not overwritten (deciding between two is a later job)."""
        norm = normalize_concept(name)
        row = self._conn.execute("SELECT id, concept_type FROM nodes WHERE kind = 'concept' AND norm = ?",
                                 (norm,)).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT n.id, n.concept_type FROM concept_aliases a JOIN nodes n ON n.id = a.concept_id"
                " WHERE a.norm = ?", (norm,)).fetchone()
        if row:
            nid, current = row["id"], row["concept_type"]
        else:
            nid, current = new_id("c"), None
            self._emit(actor, "node_added", {"id": nid, "kind": "concept", "label": name[:LABEL_LEN], "norm": norm})
        meta = meta or {}
        ctype = meta.get("type") if meta.get("type") in CONCEPT_TYPES and not current else None
        aliases = []
        for a in meta.get("aliases") or []:
            an = normalize_concept(a)
            if an == norm or len(a) > CONCEPT_LEN_MAX:
                continue
            taken = self._conn.execute("SELECT 1 FROM nodes WHERE kind = 'concept' AND norm = ?"
                                       " UNION SELECT 1 FROM concept_aliases WHERE norm = ?", (an, an)).fetchone()
            if not taken:  # a name that is already its own concept is not merged here
                aliases.append(a)
        if ctype or aliases:
            self._emit(actor, "concept_described", {"id": nid, **({"type": ctype} if ctype else {}),
                                                    **({"aliases": aliases} if aliases else {})})
        return nid

    def _edge_weight(self, src: str, dst: str, kind: str) -> float:
        row = self._conn.execute(
            "SELECT weight FROM edges WHERE src = ? AND dst = ? AND kind = ?", (src, dst, kind)
        ).fetchone()
        return row["weight"] if row else 0.0

    def _link(self, actor: str, src: str, dst: str, kind: str, weight: float, origin: str) -> None:
        if kind == "association":
            src, dst = _pair(src, dst)
        weight = max(weight, self._edge_weight(src, dst, kind))
        self._emit(actor, "edge_set", {"src": src, "dst": dst, "kind": kind, "weight": round(weight, 6),
                                       "origin": origin})

    def _reinforce(self, actor: str, a: str, b: str, rate: float = HEBBIAN_RATE) -> None:
        src, dst = _pair(a, b)
        w = hebbian(self._edge_weight(src, dst, "association"), rate)
        self._emit(actor, "edge_set", {"src": src, "dst": dst, "kind": "association", "weight": round(w, 6),
                                       "origin": "hebbian", "co_activation": True})

    def _existing_ids(self, ids: Iterable[str]) -> tuple[list[str], list[str]]:
        known, unknown = [], []
        for nid in dict.fromkeys(ids or []):
            row = self._conn.execute("SELECT 1 FROM nodes WHERE id = ? AND status != 'erased'", (nid,)).fetchone()
            (known if row else unknown).append(nid)
        return known, unknown

    def _add_elements(self, actor: str, elements: list[Element], source_id: str | None,
                      promoted_by: str | None = None, extra: dict | None = None) -> list[dict]:
        created = []
        for el in elements:
            nid = new_id("n")
            self._emit(actor, "node_added", {"id": nid, "kind": el.kind, "label": make_label(el.text),
                                             "body": el.text, "source_id": source_id,
                                             "importance": el.importance,
                                             **({"promoted_by": promoted_by} if promoted_by else {}),
                                             **(extra or {})})
            concept_ids = [self._concept_id(actor, c, el.concept_meta.get(c)) for c in el.concepts]
            for cid in concept_ids:
                self._link(actor, nid, cid, "about", W_ABOUT, "ai")
            created.append({"id": nid, "kind": el.kind, "label": make_label(el.text),
                            "concepts": el.concepts})
        # Things learned together are linked together.
        ids = [c["id"] for c in created]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                self._link(actor, a, b, "association", W_SAME_REPORT, "ai")
        return created

    # ---- public API used by the MCP tools --------------------------------

    def start_session(self, ai_name: str, include_profile: bool = True) -> dict[str, Any]:
        ai_name = ai_name.strip() or "unknown"
        sid = new_id("s")
        with self._tx():
            self._emit(f"ai:{ai_name}", "session_started", {"id": sid, "ai_name": ai_name})
        from .inbox import ingest

        ingest(self)  # what arrived in the receiving box is taken in before the conversation starts
        # exo_session is the name the tools take (a relay dropped "session_id"); both are returned.
        if not include_profile:  # the owner compares answers with and without memory: read only on "/思い出して"
            return {"exo_session": sid, "session_id": sid}
        return {"exo_session": sid, "session_id": sid, "profile": self.profile(PROFILE_BUDGET)}

    def latest_session(self) -> str | None:
        row = self._conn.execute("SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def remember_explicit(self, session_id: str, words: str, kind: str = "procedural",
                          concepts: list[str] | None = None, context: str = "") -> dict[str, Any]:
        """The owner said "remember this" / made a decision: straight into the cortex (spec v0.5 §5.3).
        The owner's words are also filed on the bookshelf, so the memory can always be traced back."""
        from .inbox import add_source

        ai_name = self.session_ai(session_id)
        self._require_writable()
        words = words.strip()
        if not words:
            raise InvalidInput("words（オーナーの言葉）が空です。")
        if kind not in ELEMENT_KINDS:
            raise InvalidInput(f"kind は {', '.join(ELEMENT_KINDS)} のいずれかです。")
        element = parse_elements([{"kind": kind, "text": words, "concepts": concepts or [], "importance": 1.0}])[0]
        existing = self._conn.execute(
            "SELECT id, source_id FROM nodes WHERE body = ? AND kind = ? AND status = 'active'", (words, kind)).fetchone()
        if existing:
            return {"node_id": existing[0], "source_id": existing[1], "already_remembered": True}
        body = f"{words}\n" + (f"\n（そのときの話題: {context.strip()}）\n" if context.strip() else "")
        actor = f"ai:{ai_name}"
        src = add_source(self, kind="explicit", author="human", ai_name=ai_name, title=make_label(words),
                         body=body, actor=actor, meta={"relayed_by": ai_name, "session_id": session_id})
        source_id = src["source_id"] if src else self._conn.execute(
            "SELECT id FROM sources WHERE sha256 = ?", (body_sha256(body),)).fetchone()[0]
        with self._tx():
            created = self._add_elements(actor, [element], source_id, promoted_by="explicit",
                                         extra={"derivation": "verbatim", "confidence": 1.0,
                                                **({"stage": "confirmed"} if kind == "procedural" else {})})
            self._emit(actor, "node_sourced", {"node_id": created[0]["id"], "source_id": source_id,
                                               "line_start": 1, "line_end": 1, "quote": words})
        return {"node_id": created[0]["id"], "source_id": source_id, "already_remembered": False}

    def review_rule(self, rule_id: str, verdict: str, owner_words: str = "", actor: str = "human") -> dict[str, Any]:
        """The owner's answer to a rule (spec v0.8 §6.1): "yes" makes a tentative rule a confirmed one,
        "no" takes a rule out (its cases and history stay). Only the owner's own answer may do this."""
        n = self.node(rule_id)
        if n is None or n["kind"] != "procedural" or n["status"] != "active":
            raise InvalidInput(f"{rule_id} は、今あるルールではありません。")
        if verdict not in ("yes", "no"):
            raise InvalidInput("verdict は 'yes'（そのとおり）か 'no'（違う）です。")
        with self._tx():
            if verdict == "yes":
                self._emit(actor, "node_updated", {"id": rule_id, "stage": "confirmed",
                                                   "importance": round(max(n["importance"], 0.8), 3)})
            else:
                self._emit(actor, "node_updated", {"id": rule_id, "status": "retired"})
            self._emit(actor, "sleep_mark", {"kind": "rule_reviewed", "key": rule_id,
                                             "value": 1 if verdict == "yes" else 0})
        self._recaller.index.version = -1
        what = "ルールにしました（会話の最初に渡します）" if verdict == "yes" else "ルールから外しました（元の事例と履歴は残ります）"
        return {"rule_id": rule_id, "verdict": verdict, "owner_words": owner_words,
                "message_to_user": f"「{n['body']}」を{what}。"}

    def rules_to_review(self, limit: int = 20) -> list[dict]:
        """Tentative rules waiting for the owner's answer, newest first, with the cases they came from."""
        rows = self._conn.execute(
            "SELECT id, body, scope, created_at FROM nodes WHERE kind = 'procedural' AND status = 'active'"
            " AND stage = 'tentative' ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            cases = [c[0] for c in self._conn.execute(
                "SELECT n.body FROM edges e JOIN nodes n ON n.id = e.dst WHERE e.src = ? AND e.kind = 'derived_from'"
                " AND n.kind = 'case'", (r["id"],))]
            out.append({"id": r["id"], "text": r["body"], "scope": r["scope"] or "", "created_at": r["created_at"],
                        "cases": cases})
        return out

    def good(self, session_id: str, praised: str, owner_words: str = "/good",
             used_memory_ids: list[str] | None = None, concepts: list[str] | None = None) -> dict[str, Any]:
        """The owner sent /good (spec v0.5 §7.3): what was praised becomes a memory at once, and the memories
        the praised answer used are strengthened. Only the explicit signal counts, never the tone of words."""
        from .inbox import add_source

        ai_name = self.session_ai(session_id)
        self._require_writable()
        praised = " ".join(praised.split())
        if not praised:
            raise InvalidInput("praised（褒められたやり方）が空です。直前の返答の何が良かったのかを 1 文で書いてください。")
        lesson = f"オーナーが良いと評価したやり方: {praised}"
        element = parse_elements([{"kind": "procedural", "text": lesson, "concepts": concepts or [],
                                   "importance": 1.0}])[0]
        used, unknown = self._existing_ids(used_memory_ids or [])
        body = (f"{owner_words.strip() or '/good'}\n\n（{ai_name} が理解した、褒められたこと: {praised}）\n")
        actor = f"ai:{ai_name}"
        src = add_source(self, kind="good", author="human", ai_name=ai_name, title=make_label(lesson), body=body,
                         actor=actor, meta={"relayed_by": ai_name, "session_id": session_id, "used": used})
        source_id = src["source_id"] if src else None
        existing = self._conn.execute("SELECT id FROM nodes WHERE body = ? AND status = 'active'",
                                      (lesson,)).fetchone()
        strengthened = []
        with self._tx():
            if existing:
                nid = existing[0]
            else:
                nid = self._add_elements(actor, [element], source_id, promoted_by="explicit",
                                         extra={"derivation": "paraphrase", "confidence": 0.9,
                                                "stage": "tentative"})[0]["id"]
            self._emit(actor, "node_updated", {"id": nid, "goods_delta": 1})
            if source_id:
                self._emit(actor, "node_sourced", {"node_id": nid, "source_id": source_id, "line_start": 1,
                                                   "line_end": 1, "quote": owner_words.strip() or "/good",
                                                   "occurrence": bool(existing)})
            for mid in used:
                n = self.node(mid)
                if n is None or n["kind"] == "concept":
                    continue
                self._emit(actor, "node_updated", {"id": mid, "goods_delta": 1,
                                                   "importance": round(min(1.0, n["importance"] + GOOD_IMPORTANCE_STEP), 3)})
                self._reinforce(actor, mid, nid, GOOD_HEBBIAN_RATE)
                strengthened.append(n["body"])
            for i, a in enumerate(used):
                for b in used[i + 1:]:
                    self._reinforce(actor, a, b, GOOD_HEBBIAN_RATE)
        msg = f"覚えました: 「{praised}」を、オーナーが良いと評価したやり方として記憶しました。"
        if strengthened:
            msg += f" この返答で使った記憶 {len(strengthened)} 件のつながりも強めました。"
        return {"node_id": nid, "strengthened": strengthened, "unknown_memory_ids": unknown, "message_to_user": msg}

    def revise_memory(self, session_id: str, memory_id: str, new_text: str, reason: str,
                      owner_words: str = "", source_id: str | None = None) -> dict[str, Any]:
        """Reconsolidation (spec v0.6 §7.4): a memory recalled in a conversation turned out outdated or imprecise.
        It is rewritten in place (id and links kept); the episode of rewriting it — when, by which AI, from what
        to what, and why — is remembered too and tied to it. Needs evidence: the owner's words in this
        conversation, or an original on the bookshelf. The old wording stays in the history."""
        from datetime import datetime

        from .inbox import add_source

        ai_name = self.session_ai(session_id)
        self._require_writable()
        n = self.node(memory_id)
        if n is None or n["kind"] == "concept" or n["status"] not in ("active", "dormant"):
            raise InvalidInput(f"記憶 {memory_id} は見つからないか、書き換えられない状態です。")
        new_text = " ".join(new_text.split())
        reason = " ".join(reason.split())
        if not new_text or len(new_text) > TEXT_MAX:
            raise InvalidInput(f"new_text は 1〜{TEXT_MAX} 文字にしてください。")
        if new_text == n["body"]:
            raise InvalidInput("new_text が今の記憶と同じです。")
        if not reason:
            raise InvalidInput("reason（なぜ書き換えるのか）を書いてください。")
        owner_words = owner_words.strip()
        if not owner_words and not source_id:
            raise InvalidInput("根拠が必要です。この会話でのオーナーの言葉（owner_words）か、本棚の原文（source_id）を渡してください。")
        if source_id and not self._conn.execute("SELECT 1 FROM sources WHERE id = ? AND erased = 0",
                                                (source_id,)).fetchone():
            raise InvalidInput(f"原文 {source_id} は本棚にありません。")
        actor = f"ai:{ai_name}"
        if owner_words:  # the owner's words are filed on the bookshelf as the evidence
            body = f"{owner_words}\n\n（{ai_name} が記憶を書き換えた根拠。理由: {reason}）\n"
            src = add_source(self, kind="revision", author="human", ai_name=ai_name, title=make_label(owner_words),
                             body=body, actor=actor, meta={"relayed_by": ai_name, "session_id": session_id,
                                                           "memory_id": memory_id})
            source_id = src["source_id"] if src else self._conn.execute(
                "SELECT id FROM sources WHERE sha256 = ?", (body_sha256(body),)).fetchone()[0]
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        story = f"{today}、{ai_name} との会話で記憶を書き換えた: 「{n['body']}」→「{new_text}」。理由: {reason}"
        if len(story) > TEXT_MAX:
            story = story[: TEXT_MAX - 1] + "…"
        with self._tx():
            episode = self._add_elements(actor, [Element("episode", story, [], 0.6)], source_id,
                                         promoted_by="reconsolidation",
                                         extra={"derivation": "verbatim", "confidence": 1.0})[0]["id"]
            self._emit(actor, "node_revised", {"id": memory_id, "old_body": n["body"], "new_body": new_text,
                                               "label": make_label(new_text), "reason": reason,
                                               "episode_id": episode, "source_id": source_id})
            self._link(actor, memory_id, episode, "revised_in", REVISION_LINK, "reconsolidation")
            self._reinforce(actor, memory_id, episode, REVISION_LINK)
            quote = owner_words or self._conn.execute("SELECT title FROM sources WHERE id = ?",
                                                      (source_id,)).fetchone()[0]
            self._emit(actor, "node_sourced", {"node_id": memory_id, "source_id": source_id, "line_start": 1,
                                               "line_end": 1, "quote": quote})
        self._recaller.index.version = -1
        return {"memory_id": memory_id, "episode_id": episode, "source_id": source_id,
                "message_to_user": f"記憶を書き換えました: 「{n['body']}」→「{new_text}」（理由: {reason}）。"
                                   "書き換えた経緯も出来事として覚えました。前の内容は履歴に残っています。"}

    def deposit(self, session_id: str, thread_title: str, summary: str, procedural: list[str], semantic: list[str],
                episodes: list[str], note_type: str = "", confidential: bool = False,
                reasons: list[str] | None = None) -> dict[str, Any]:
        """`/預けて`: the owner hands this thread over. The AI summarises it into the three kinds of memory;
        it goes to the receiving box and the hippocampus, and every part is a promotion candidate."""
        from . import deposit as dep
        from .inbox import file_deposit

        ai_name = self.session_ai(session_id)
        self._require_writable()
        if note_type not in dep.NOTE_TYPES:
            raise InvalidInput("note_type は ''（オーナー自身の話）・'interview'（取材）・'client'（クライアント）のどれかです。")
        if not (summary.strip() or procedural or semantic or episodes or reasons):
            raise InvalidInput("預ける中身が空です。")
        source, _ = _ai_source(ai_name)
        text = dep.render(source, thread_title.strip() or "無題のスレッド",
                          {"summary": summary, "procedural": procedural, "reasons": reasons or [], "semantic": semantic,
                           "episode": episodes},
                          note_type, confidential)
        r = file_deposit(self, text, actor=f"ai:{ai_name}")
        if r is None:
            return {"filed": False, "message_to_user": "同じ内容はすでに預かっています。"}
        where = "本棚にだけ置きました（機密のため、大脳皮質には移しません）" if confidential else \
            "今夜の睡眠で、大脳皮質に移ります"
        return {"filed": True, **r, "message_to_user": f"預かりBOX に受け取りました。{where}。"}

    def submit_daily_log(self, session_id: str, thread_title: str, events_: list[str], corrections: list[str],
                         learnings: list[str], decisions: list[str], unresolved: list[str],
                         ai_model: str = "unknown", thread_id: str | None = None,
                         reasons: list[str] | None = None, project: str = "",
                         more: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """`/日報`: the AI writes what is new in this conversation since its last log (protocol v1).
        The log goes to the receiving box and the hippocampus; nothing is written to the cortex."""
        import hashlib
        from datetime import datetime

        from . import daily
        from .inbox import current_cursor, file_daily

        ai_name = self.session_ai(session_id)
        self._require_writable()
        if self.settings.daily_logs_since and any(k in ai_name.casefold() for k in AUTO_LOGGED_AIS):
            raise InvalidInput(f"{ai_name} の会話は、毎晩の睡眠で自動的に日報にしています。/日報 は要りません"
                               "（送ると同じ会話が二重に入ります）。オーナーにそう伝えてください。")
        started = self._conn.execute("SELECT started_at FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
        source, provider = _ai_source(ai_name)
        fields = {"source": source, "ai_provider": provider, "ai_product": ai_name, "ai_agent": ai_name,
                  "ai_model": (ai_model or "unknown").strip() or "unknown",
                  "thread_id": (thread_id or f"session:{session_id}").strip()}
        key = f"{fields['source']}:{fields['thread_id']}"
        prev = current_cursor(self, key)
        now = datetime.now().astimezone()
        last = self._conn.execute("SELECT at FROM ai_checkpoints WHERE key = ?", (key,)).fetchone()
        period_start = datetime.fromisoformat(last[0] if last else started).astimezone()
        sections = {"events": events_, "corrections": corrections, "learnings": learnings,
                    "decisions": decisions, "reasons": reasons or [], "unresolved": unresolved,
                    **{k: list(v or []) for k, v in (more or {}).items()}}
        digest = hashlib.sha256(events.canonical_json([key, prev, sections]).encode()).hexdigest()
        fields.update(entry_date=f"{now:%Y-%m-%d}", period_start=period_start.isoformat(timespec="seconds"),
                      period_end=now.isoformat(timespec="seconds"), generated_at=now.isoformat(timespec="seconds"),
                      previous_cursor=prev or "", cursor=digest)
        try:
            text = daily.render(fields, thread_title.strip() or "無題のスレッド", sections, project)
            result = file_daily(self, text, actor=f"ai:{ai_name}")
        except daily.DailyRejected as e:
            raise InvalidInput(str(e)) from None
        if result is None:
            return {"filed": False, "message": "同じ内容の日報がすでに届いています。"}
        return {"filed": True, **result, "message": "日報を預かりました。今夜の睡眠で、覚えるべきものが大脳皮質に移ります。"}

    def profile(self, budget: int) -> str:
        """What every conversation starts with (spec v0.8 §8.1): the confirmed rules that hold for all work,
        strongest first, within a token budget. Cases, tentative rules and one project's memories come up only
        when the talk turns to them (recall)."""
        rows = self._conn.execute(
            "SELECT id, kind, body FROM nodes WHERE status = 'active' AND kind = 'procedural'"
            " AND (pinned = 1 OR (stage = 'confirmed' AND COALESCE(scope, '') = ''))"
            " ORDER BY pinned DESC, importance * base_strength DESC, access_count DESC, created_at DESC"
        )
        lines, used = [], 0
        for r in rows:
            line = f"- [{KIND_LABEL_JA[r['kind']]}] {r['body']} [{r['id']}]"
            cost = estimate_tokens(line) + 1
            if used + cost > budget:
                break
            lines.append(line)
            used += cost
        return "\n".join(lines) if lines else "（まだ記憶はありません）"

    def session_ai(self, session_id: str) -> str:
        row = self._conn.execute("SELECT ai_name FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise InvalidInput(f"session_id {session_id!r} が見つかりません。先に start_session を呼んでください。")
        return row["ai_name"]

    def remember(self, session_id: str, elements: Iterable[Any]) -> dict[str, Any]:
        ai_name = self.session_ai(session_id)
        self._require_writable()
        parsed = parse_elements(elements)
        if not parsed:
            raise InvalidInput("elements が空です。")
        with self._tx():
            created = self._add_elements(f"ai:{ai_name}", parsed, None)
        return {"nodes": created}

    def submit_daily_report(
        self,
        session_id: str,
        title: str,
        report: str,
        elements: Iterable[Any],
        used_memory_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        ai_name, title = self.session_ai(session_id), title.strip()
        self._require_writable()
        if not title or len(title) > TITLE_MAX:
            raise InvalidInput(f"title は 1〜{TITLE_MAX} 文字にしてください。")
        if not report.strip():
            raise InvalidInput("report（日報本文）が空です。")
        if len(report) > REPORT_MAX:
            raise InvalidInput(f"report は {REPORT_MAX} 文字までです。")
        parsed = parse_elements(elements)
        actor = f"ai:{ai_name}"
        source_id = new_id("src")
        original = write_original(self.settings.originals, source_id, "daily_report", "ai", ai_name, title, report)
        try:
            with self._tx():
                self._emit(actor, "source_added", {
                    "id": source_id, "kind": "daily_report", "author": "ai", "ai_name": ai_name, "title": title,
                    "path": original.path.relative_to(self.settings.drive_root).as_posix(),
                    "sha256": original.sha256, "created_at": original.created_at,
                })
                created = self._add_elements(actor, parsed, source_id)
                used, unknown = self._existing_ids(used_memory_ids)
                if used:
                    self._emit(actor, "nodes_touched", {"ids": used})
                    for i, a in enumerate(used):
                        for b in used[i + 1:]:
                            self._reinforce(actor, a, b)
        except BaseException:
            original.path.unlink(missing_ok=True)  # keep the shelf in step with the log
            raise
        return {
            "source_id": source_id,
            "path": original.path.relative_to(self.settings.drive_root).as_posix(),
            "nodes": created,
            "reinforced_memory_ids": used,
            "unknown_memory_ids": unknown,
        }

    def recall(self, session_id: str, cue: str, budget: int = RECALL_BUDGET) -> dict[str, Any]:
        """Staged recall (spec v0.5 §7.1): cortex, then hippocampus, bookshelf, the owner's vault, then "no record"."""
        ai_name = self.session_ai(session_id)
        if not cue.strip():
            raise InvalidInput("cue（手がかり）が空です。いまの話題を短い文で渡してください。")
        lo, hi = RECALL_BUDGET_RANGE
        budget = max(lo, min(int(budget), hi))
        with self._lock:
            r = self._recaller
            seeds = r.seeds(cue, session_id)
            by_meaning = self._semantic_seeds(cue)
            for nid, sim in by_meaning.items():
                seeds[nid] = max(seeds.get(nid, 0.0), sim)
            activation, hops, parent = r.spread(seeds)
            hits = r.score(activation, hops, parent)
            cue_ids = r.cue_matches(cue) | {nid for nid, sim in by_meaning.items() if sim >= NODE_FOUND_MIN}
            result = pack(r, hits, cue_ids, budget)
            ids = [h.id for h in result.rules + result.related] + [h.id for h, _ in result.insights]
            if ids and not self.paused:
                actor = f"ai:{ai_name}"
                with self._tx():
                    self._emit(actor, "recalled", {"session_id": session_id, "ids": ids})
                    # Recalled together, wired together (weakly: use is not yet confirmed).
                    top = [h.id for h in result.related[:RECALL_REINFORCE_TOP]]
                    for i, a in enumerate(top):
                        for b in top[i + 1:]:
                            self._reinforce(actor, a, b, RECALL_HEBBIAN_RATE)
        # The cortex answered the cue itself: no need to go further down.
        cortex_found = bool(cue_ids & {h.id for h in result.rules + result.related})
        evidence, stages = [], ["大脳皮質"]
        if not cortex_found:
            # Make room: the cortex keeps FALLBACK_KEEP of the budget, the records get the rest.
            with self._lock:
                result = pack(r, hits, cue_ids, int(budget * FALLBACK_KEEP))
            evidence, more = self._fallback(cue, budget - estimate_tokens(result.text()))
            stages += more
        text = result.text()
        if not cortex_found:
            text += "\n" + format_evidence(evidence)
            while evidence and estimate_tokens(text) > budget:
                evidence = evidence[:-1]
                text = result.text() + "\n" + format_evidence(evidence, searched=True)
        return {
            "context": text,
            "memory_ids": [h.id for h in result.rules + result.related],
            "insight_ids": [h.id for h, _ in result.insights],
            "evidence": evidence,
            "searched": stages,
            "no_record": not cortex_found and not evidence,
            "tokens": estimate_tokens(text),
            "budget": budget,
        }

    def _semantic_seeds(self, cue: str) -> dict[str, float]:
        """Cortex memories close to the cue in meaning (bge-m3), so wording does not have to match."""
        import numpy as np

        from .embed import EmbedUnavailable

        emb = self.embedder
        if emb is None:
            return {}
        rows = self._conn.execute("SELECT v.node_id, v.vec FROM node_vectors v JOIN nodes n ON n.id = v.node_id"
                                  " WHERE v.model = ? AND n.status = 'active'", (emb.model,)).fetchall()
        if not rows:
            return {}
        try:
            q = np.asarray(emb.embed([cue])[0], dtype=np.float32)
        except EmbedUnavailable:
            return {}
        m = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        sims = (m @ q) / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-12)
        return {rows[i][0]: float(sims[i]) for i in np.argsort(-sims)[:NODE_SEEDS_MAX] if sims[i] >= NODE_SEED_MIN}

    def _fallback(self, cue: str, budget: int) -> tuple[list[dict], list[str]]:
        """Hippocampus, then bookshelf, then the vault. Stops at the first stage that has evidence."""
        from .hippocampus import search, search_vault
        from .chunks import lexical_tokens

        q = set(lexical_tokens(cue))
        stages = []
        for scope, name in (("hippocampus", "海馬"), ("bookshelf", "本棚")):
            stages.append(name)
            found = [h.to_dict(q) for h in search(self, cue, scope=scope)]
            if found:
                return _fit(found, budget), stages
        stages.append("Obsidian")
        found = search_vault(self, cue)
        return _fit(found, budget), stages

    def search_bookshelf(self, query: str, keywords: list[str] | None = None, limit: int = 5) -> list[dict]:
        from .search import search

        with self._lock:
            return search(self._conn, query, keywords, limit)

    def add_memo(self, title: str, text: str) -> dict[str, Any] | None:
        """The owner hands over a memo (pasted text). Filed on the bookshelf as is."""
        from .inbox import add_memo

        return add_memo(self, title, text)

    def trace_correction(self, session_id: str, correction: str, context: str = "",
                         keywords: list[str] | None = None, used_memory_ids: list[str] | None = None) -> dict[str, Any]:
        from . import correction as corr

        return corr.trace(self, session_id, correction, context, keywords, used_memory_ids)

    def apply_correction(self, session_id: str, trace_id: str, mode: str, **kwargs: Any) -> dict[str, Any]:
        from . import correction as corr

        return corr.apply(self, session_id, trace_id, mode, **kwargs)

    def open_source(self, source_id: str, max_chars: int = 8000) -> dict[str, Any]:
        s = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if s is None:
            raise InvalidInput(f"原文 {source_id} は見つかりません。")
        meta = {k: s[k] for k in ("id", "kind", "author", "ai_name", "title", "created_at")}
        if s["erased"]:
            return {**meta, "erased": True, "body": None}
        path = self.settings.drive_root / s["path"]
        if not path.exists():
            raise InvalidInput(f"原文ファイルが見つかりません: {s['path']}")
        body = read_body(path)
        return {**meta, "erased": False, "intact": body_sha256(body) == s["sha256"],
                "truncated": len(body) > max_chars, "body": body[:max_chars]}

    # ---- inspection -------------------------------------------------------

    def node(self, node_id: str) -> dict[str, Any] | None:
        r = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(r) if r else None

    def edges_of(self, node_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM edges WHERE src = ? OR dst = ? ORDER BY weight DESC",
                                  (node_id, node_id))
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        q = lambda sql: self._conn.execute(sql).fetchone()[0]  # noqa: E731
        out = {k: q(f"SELECT COUNT(*) FROM nodes WHERE kind = '{k}' AND status = 'active'")
               for k in (*ELEMENT_KINDS, "concept")}
        out.update(edges=q("SELECT COUNT(*) FROM edges"), sources=q("SELECT COUNT(*) FROM sources"),
                   events=q("SELECT COUNT(*) FROM events"))
        return out

    def snapshot(self) -> dict[str, list[tuple]]:
        """Projection contents without volatile columns, for comparing after rebuild()."""
        return {t: [tuple(r) for r in self._conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in PROJECTION_TABLES}


def _ai_source(ai_name: str) -> tuple[str, str]:
    """Map an AI's display name to protocol v1's source / ai_provider."""
    n = ai_name.casefold()
    for key, source, provider in (("claude", "claude", "anthropic"), ("codex", "codex", "openai"),
                                  ("chatgpt", "chatgpt", "openai"), ("gpt", "chatgpt", "openai"),
                                  ("gemini", "gemini", "google")):
        if key in n:
            return source, provider
    return ("".join(ch for ch in n if ch.isalnum()) or "unknown"), "unknown"


def format_evidence(evidence: list[dict], searched: bool = False) -> str:
    if not evidence and searched:
        return "## 記録\n（予算の都合で引用を省きました。open_source で原文を読んでください）"
    if not evidence:
        return ("## 記録\n確認できる記録はありませんでした（大脳皮質・海馬・本棚・Obsidian を探しました）。"
                "以前に聞いた・決めたと答えないでください。")
    lines = ["## まだ記憶になっていない記録（原文からの引用。答えるときは出典を添える）"]
    for e in evidence:
        if e["place"] == "Obsidian":
            lines.append(f"- [Obsidian {e['file']}] 「{e['quote']}」")
            continue
        head = f" · {e['heading']}" if e.get("heading") else ""
        secret = " · 機密（外に出さない）" if e.get("confidential") else ""
        lines.append(f"- [{e['place']}{secret} {e['created_at'][:10]} · {e['writer']} · {e['title']}{head}"
                     f" · {e['lines'][0]}〜{e['lines'][1]}行] 「{e['quote']}」 [{e['source_id']}]")
    return "\n".join(lines)


def _fit(evidence: list[dict], budget: int) -> list[dict]:
    out, used = [], 60
    for e in evidence:
        cost = estimate_tokens(e.get("quote", "")) + 40
        if used + cost > budget and out:
            break
        out.append(e)
        used += cost
    return out


def open_brain(settings: Settings | None = None) -> Brain:
    from .config import load_settings

    return Brain(settings or load_settings())

