"""The brain: elements (nodes) joined by weighted links (edges).

The event log is the source of truth. The `sources`, `nodes`, `edges` and
`sessions` tables are projections: every write appends an event and applies it
in the same transaction, and `rebuild()` can regenerate them from the log.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
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

ELEMENT_KINDS = ("episode", "semantic", "procedural")
KIND_LABEL_JA = {"episode": "出来事", "semantic": "知識", "procedural": "ルール", "concept": "概念"}

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
    pinned INTEGER NOT NULL DEFAULT 0         -- always included in recall (standing rule)
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
"""
PROJECTION_TABLES = ("sources", "nodes", "edges", "sessions", "session_items", "source_fts", "shelves",
                     "sleep_runs", "sleep_marks")


class InvalidInput(ValueError):
    """Input from an AI that should be fixed and resent. The message is shown to the AI."""


@dataclass(frozen=True)
class Element:
    kind: str
    text: str
    concepts: list[str]
    importance: float


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
        concepts = []
        for c in e.get("concepts") or []:
            c = " ".join(str(c).split())
            if c and c not in concepts:
                concepts.append(c)
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
        out.append(Element(kind, text, concepts, importance))
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

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(nodes)")}
        for col in ("corrections", "pinned"):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")

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
                "INSERT INTO sources (id, kind, author, ai_name, title, path, sha256, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p["id"], p["kind"], p["author"], p["ai_name"], p["title"], p["path"], p["sha256"], p["created_at"]),
            )
            path = self.settings.drive_root / p["path"]
            body = read_body(path) if path.exists() else ""
            c.execute("INSERT INTO source_fts (source_id, title, body) VALUES (?, ?, ?)", (p["id"], p["title"], body))
        elif ev.type == "node_added":
            c.execute(
                "INSERT INTO nodes (id, kind, label, body, norm, source_id, created_by, created_at, importance)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p["id"], p["kind"], p["label"], p.get("body"), p.get("norm"), p.get("source_id"),
                 ev.actor, ev.at, p.get("importance", 0.5)),
            )
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
            for col in ("base_strength", "pinned", "status"):
                if col in p:
                    sets.append(f"{col} = ?")
                    args.append(p[col])
            if p.get("corrections_delta"):
                sets.append("corrections = corrections + ?")
                args.append(p["corrections_delta"])
            if sets:
                c.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", (*args, p["id"]))
        elif ev.type == "erased":
            # Payloads that held the content are nulled separately; here we drop the projection rows.
            if p["target"] == "source":
                c.execute("DELETE FROM sources WHERE id = ?", (p["id"],))
                c.execute("DELETE FROM shelves WHERE source_id = ?", (p["id"],))
                c.execute("DELETE FROM source_fts WHERE source_id = ?", (p["id"],))
            else:
                c.execute("DELETE FROM nodes WHERE id = ?", (p["id"],))
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
        elif ev.type == "sleep_started":
            c.execute("INSERT INTO sleep_runs (id, started_at, start_event, stage_a) VALUES (?, ?, ?, ?)",
                      (p["id"], ev.at, p.get("from_event", ev.id), events.canonical_json(p["stage_a"])))
        elif ev.type == "sleep_finished":
            c.execute("UPDATE sleep_runs SET finished_at = ?, summary = ?, journal_source_id = ? WHERE id = ?",
                      (ev.at, p["summary"], p.get("journal_source_id"), p["id"]))
        elif ev.type == "session_started":
            c.execute(
                "INSERT INTO sessions (id, ai_name, started_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (p["id"], p["ai_name"], ev.at, ev.at),
            )
        else:
            raise ValueError(f"unknown event type: {ev.type}")

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

    def _concept_id(self, actor: str, name: str) -> str:
        norm = normalize_concept(name)
        row = self._conn.execute("SELECT id FROM nodes WHERE kind = 'concept' AND norm = ?", (norm,)).fetchone()
        if row:
            return row["id"]
        nid = new_id("c")
        self._emit(actor, "node_added", {"id": nid, "kind": "concept", "label": name[:LABEL_LEN], "norm": norm})
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

    def _add_elements(self, actor: str, elements: list[Element], source_id: str | None) -> list[dict]:
        created = []
        for el in elements:
            nid = new_id("n")
            self._emit(actor, "node_added", {"id": nid, "kind": el.kind, "label": make_label(el.text),
                                             "body": el.text, "source_id": source_id,
                                             "importance": el.importance})
            concept_ids = [self._concept_id(actor, c) for c in el.concepts]
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

    def start_session(self, ai_name: str) -> dict[str, Any]:
        ai_name = ai_name.strip() or "unknown"
        sid = new_id("s")
        with self._tx():
            self._emit(f"ai:{ai_name}", "session_started", {"id": sid, "ai_name": ai_name})
        from .inbox import ingest

        ingest(self)  # memos dropped in the inbox reach the bookshelf before the conversation starts
        return {"session_id": sid, "profile": self.profile(PROFILE_BUDGET)}

    def profile(self, budget: int) -> str:
        """The owner's standing rules and key facts, strongest first, within a token budget."""
        rows = self._conn.execute(
            "SELECT id, kind, body FROM nodes WHERE status = 'active' AND kind IN ('procedural', 'semantic')"
            " ORDER BY pinned DESC, CASE kind WHEN 'procedural' THEN 0 ELSE 1 END,"
            " importance * base_strength DESC, access_count DESC, created_at DESC"
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
        """Spreading-activation recall packed into a token budget (design §4)."""
        ai_name = self.session_ai(session_id)
        if not cue.strip():
            raise InvalidInput("cue（手がかり）が空です。いまの話題を短い文で渡してください。")
        lo, hi = RECALL_BUDGET_RANGE
        budget = max(lo, min(int(budget), hi))
        with self._lock:
            r = self._recaller
            seeds = r.seeds(cue, session_id)
            activation, hops, parent = r.spread(seeds)
            hits = r.score(activation, hops, parent)
            result = pack(r, hits, r.cue_matches(cue), budget)
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
        text = result.text()
        return {
            "context": text,
            "memory_ids": [h.id for h in result.rules + result.related],
            "insight_ids": [h.id for h, _ in result.insights],
            "tokens": estimate_tokens(text),
            "budget": budget,
        }

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


def open_brain(settings: Settings | None = None) -> Brain:
    from .config import load_settings

    return Brain(settings or load_settings())

