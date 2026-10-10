"""The owner's screen (design §8): graph, bookshelf, memos, sleep, safeguards.

A small local web app: stdlib HTTP server on 127.0.0.1 only. Every API call
must carry a per-launch token (sent by the page in a custom header) and a
local Host header, so other websites open in the browser cannot read or
change anything (no CORS, no DNS rebinding).
"""

from __future__ import annotations

import json
import math
import secrets
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import safety, sleep
from .brain import Brain, InvalidInput
from .inbox import ingest
from .recall import effective_weight, strength, strength_sql

WEB = Path(__file__).parent / "web"
DEFAULT_PORT = 8765
MAX_GRAPH_NODES = 5000
MAX_GRAPH_EDGES = 20000
INBOX_POLL_SECONDS = 60
VAULT_SCAN_HOURS = 12  # the #remember scan; sleep also runs one every night
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2"}


def _label_counts(labels_json: str | None) -> dict[str, int] | None:
    if not labels_json:
        return None
    counts: dict[str, int] = {}
    for v in json.loads(labels_json).values():
        counts[v] = counts.get(v, 0) + 1
    return counts


class App:
    def __init__(self, brain: Brain):
        self.brain = brain
        self.token = secrets.token_urlsafe(24)
        self.erase_plans: dict[str, dict] = {}
        self.sleep_state: dict[str, Any] = {"running": False}
        self.memory_state: dict[str, Any] = {"running": False}
        self._stop = threading.Event()

    # ---- graph ----------------------------------------------------------------------

    def graph(self, q: dict[str, str]) -> dict[str, Any]:
        c = self.brain._conn
        kinds = [k for k in (q.get("kinds") or "episode,semantic,procedural").split(",")
                 if k in ("episode", "semantic", "procedural")]
        if "episode" in kinds:
            kinds.append("case")  # a case is an episodic memory (spec v0.8 §5.1), shown with the episodes
        statuses = ["active", "dormant"] if q.get("dormant") == "1" else ["active"]
        sql = (f"SELECT rowid AS no, id, kind, label, status, pinned, base_strength, access_count, created_at,"
               f" created_by, source_id, occurrences, goods, corrections, last_activated_at"
               f" FROM nodes WHERE kind IN ({','.join('?' * len(kinds))}) AND status IN ({','.join('?' * len(statuses))})")
        args: list[Any] = [*kinds, *statuses]
        if q.get("days"):
            since = datetime.now(timezone.utc) - timedelta(days=int(q["days"]))
            sql += " AND created_at >= ?"
            args.append(since.isoformat())
        origin = q.get("origin")
        if origin == "memo":
            sql += " AND source_id IN (SELECT id FROM sources WHERE kind = 'memo')"
        elif origin == "sleep":
            sql += " AND created_by = 'sleep'"
        elif origin:
            sql += " AND created_by = ?"
            args.append(f"ai:{origin}")
        sql += " ORDER BY pinned DESC, access_count DESC, created_at DESC LIMIT ?"
        args.append(MAX_GRAPH_NODES)
        elements = [dict(r) for r in c.execute(sql, args)] if kinds else []
        ids = {n["id"] for n in elements}

        now = datetime.now(timezone.utc)
        edges, concept_ids = [], set()
        if ids:
            rows = c.execute("SELECT src, dst, kind, weight, last_reinforced_at, origin FROM edges"
                             " WHERE weight > 0 AND kind != 'supersedes'").fetchall()
            concept_rows = {r["id"]: dict(r) for r in c.execute(
                "SELECT id, label, status FROM nodes WHERE kind = 'concept' AND status = 'active'")}
            for r in rows:
                s, t = r["src"], r["dst"]
                if s in ids and (t in ids or t in concept_rows):
                    if t in concept_rows:
                        concept_ids.add(t)
                    edges.append({"s": s, "t": t, "kind": r["kind"], "origin": r["origin"],
                                  "w": round(effective_weight(r["weight"], r["last_reinforced_at"], now, r["kind"]), 4)})
            edges.sort(key=lambda e: -e["w"])
            edges = edges[:MAX_GRAPH_EDGES]
        degree: dict[str, int] = defaultdict(int)
        for e in edges:
            degree[e["s"]] += 1
            degree[e["t"]] += 1
        changed = self._changed_in_last_sleep()
        nodes = [{"id": n["id"], "no": n["no"], "kind": n["kind"], "label": n["label"], "status": n["status"],
                  "pinned": bool(n["pinned"]), "created_at": n["created_at"], "created_by": n["created_by"],
                  "size": round(3 + 7 * strength(n["base_strength"], n["occurrences"], n["access_count"], n["goods"],
                                                 n["corrections"], n["last_activated_at"] or n["created_at"],
                                                 n["pinned"]) + (4 if n["pinned"] else 0), 2),
                  "changed": n["id"] in changed} for n in elements]
        concepts = c.execute(
            f"SELECT id, label FROM nodes WHERE id IN ({','.join('?' * len(concept_ids))})",
            sorted(concept_ids)).fetchall() if concept_ids else []
        nodes += [{"id": r["id"], "kind": "concept", "label": r["label"], "status": "active", "pinned": False,
                   "size": round(2 + min(10.0, math.sqrt(degree[r["id"]])), 2), "changed": r["id"] in changed}
                  for r in concepts]
        origins = sorted({n["created_by"][3:] for n in elements if n["created_by"].startswith("ai:")})
        return {"nodes": nodes, "edges": edges, "truncated": len(elements) >= MAX_GRAPH_NODES,
                "origins": origins, "has_changes": bool(changed)}

    def _changed_in_last_sleep(self) -> set[str]:
        c = self.brain._conn
        run = c.execute("SELECT start_event FROM sleep_runs WHERE finished_at IS NOT NULL"
                        " ORDER BY finished_at DESC LIMIT 1").fetchone()
        if run is None:
            return set()
        nxt = c.execute("SELECT MIN(start_event) FROM sleep_runs WHERE start_event > ?",
                        (run["start_event"],)).fetchone()[0]
        changed: set[str] = set()
        for r in c.execute("SELECT type, payload_json FROM events WHERE id > ? AND id < ? AND actor = 'sleep'"
                           " AND payload_json IS NOT NULL", (run["start_event"], nxt or 1 << 62)):
            p = json.loads(r["payload_json"])
            if r["type"] in ("node_added", "node_updated"):
                changed.add(p["id"])
            elif r["type"] == "edge_set":
                changed.update((p["src"], p["dst"]))
        return changed

    def node(self, node_id: str) -> dict[str, Any]:
        b = self.brain
        n = b.node(node_id)
        if n is None:
            raise InvalidInput("この記憶は見つかりません（消去された可能性があります）。")
        now = datetime.now(timezone.utc)
        nbrs = []
        for e in b.edges_of(node_id):
            other = e["dst"] if e["src"] == node_id else e["src"]
            o = b.node(other)
            if o is None:
                continue
            nbrs.append({"id": other, "label": o["label"], "kind": o["kind"], "status": o["status"],
                         "link": e["kind"], "origin": e["origin"],
                         "w": round(effective_weight(e["weight"], e["last_reinforced_at"], now, e["kind"]), 3)})
        nbrs.sort(key=lambda x: -x["w"])
        source = None
        if n["source_id"]:
            s = b._conn.execute("SELECT id, kind, author, ai_name, title, created_at FROM sources WHERE id = ?",
                                (n["source_id"],)).fetchone()
            source = dict(s) if s else None
        shelves = [r[0] for r in b._conn.execute("SELECT shelf FROM shelves WHERE source_id = ?",
                                                 (n["source_id"],))] if n["source_id"] else []
        no = b._conn.execute("SELECT rowid FROM nodes WHERE id = ?", (node_id,)).fetchone()[0]
        return {"node": {"no": no, **{k: n[k] for k in ("id", "kind", "label", "body", "status", "pinned",
                                                        "corrections", "importance", "base_strength",
                                                        "access_count", "created_at", "created_by",
                                                        "last_activated_at", "stage", "scope", "occurrences",
                                                        "goods")},
                         "strength": round(strength(n["base_strength"], n["occurrences"], n["access_count"],
                                                    n["goods"], n["corrections"],
                                                    n["last_activated_at"] or n["created_at"], n["pinned"]), 2),
                         "case": json.loads(n["case_json"]) if n["case_json"] else None},
                # a rule's receipts: the cases behind it (and against it), each with its quoted original
                "derived_from": [dict(r) for r in b._conn.execute(
                    "SELECT x.id, x.body, e.kind AS link, x.created_at,"
                    " (SELECT quote FROM node_sources WHERE node_id = x.id ORDER BY at LIMIT 1) AS quote"
                    " FROM edges e JOIN nodes x ON x.id = e.dst"
                    " WHERE e.src = ? AND e.kind IN ('derived_from', 'contradicted_by')", (node_id,))],
                "neighbors": nbrs[:40], "source": source, "shelves": shelves,
                "revisions": [dict(r) for r in b._conn.execute(
                    "SELECT at, old_body, new_body, reason, episode_id, source_id, actor FROM revisions"
                    " WHERE node_id = ? ORDER BY at DESC", (node_id,))],
                "quotes": [dict(r) for r in b._conn.execute(
                    "SELECT ns.quote, ns.line_start, ns.line_end, s.id AS source_id, s.title, s.created_at"
                    " FROM node_sources ns JOIN sources s ON s.id = ns.source_id WHERE ns.node_id = ?"
                    " ORDER BY ns.at", (node_id,))]}

    # ---- bookshelf --------------------------------------------------------------------

    def shelves(self) -> dict[str, Any]:
        c = self.brain._conn
        rows = [dict(r) for r in c.execute(
            "SELECT id, kind, author, ai_name, title, created_at FROM sources ORDER BY created_at DESC")]
        topics: dict[str, list[str]] = defaultdict(list)
        for r in c.execute("SELECT source_id, shelf FROM shelves ORDER BY shelf"):
            topics[r["shelf"]].append(r["source_id"])
        return {"sources": rows, "topics": topics}

    # ---- sleep ------------------------------------------------------------------------

    def start_sleep(self, use_ai: bool) -> dict[str, Any]:
        if self.sleep_state.get("running"):
            raise InvalidInput("いま睡眠中です。")
        self.sleep_state = {"running": True, "started_at": datetime.now(timezone.utc).isoformat(), "use_ai": use_ai}

        def work():
            try:
                result = sleep.run(self.brain, use_ai=use_ai)
                self.sleep_state = {"running": False, "result": result}
            except Exception as e:  # shown on screen; details in the terminal
                self.sleep_state = {"running": False, "error": str(e)}

        threading.Thread(target=work, daemon=True).start()
        return self.sleep_state

    # ---- memory.md: what this brain thinks ------------------------------------------------

    def memory(self) -> dict[str, Any]:
        from . import portrait

        return {**portrait.read(self.brain), **self.memory_state, "recall_view": portrait.recall_view(self.brain)}

    def write_memory(self) -> dict[str, Any]:
        from . import portrait

        if self.memory_state.get("running"):
            raise InvalidInput("いま要約を作っています。")
        self.memory_state = {"running": True, "started_at": datetime.now(timezone.utc).isoformat()}

        def work():
            try:
                portrait.write(self.brain)
                self.memory_state = {"running": False}
            except Exception as e:  # shown on screen
                self.memory_state = {"running": False, "error": str(e)}

        threading.Thread(target=work, daemon=True).start()
        return self.memory_state

    def sleep_status(self) -> dict[str, Any]:
        last = sleep.last_sleep(self.brain)
        dreams = [dict(r) for r in self.brain._conn.execute(
            "SELECT id, title, created_at FROM sources WHERE kind = 'dream' ORDER BY created_at DESC LIMIT 30")]
        return {**self.sleep_state, "last_sleep": last.isoformat() if last else None,
                "due": sleep.is_due(self.brain), "dreams": dreams, "timer": self._config().get("sleep_timer"),
                "claude_found": sleep.find_claude(self.brain) is not None, "usage": self._recent_usage()}

    def _recent_usage(self) -> list[dict]:
        path = self.brain.settings.home / "sleep-usage.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-7:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[::-1]

    def set_timer(self, body: dict) -> dict[str, Any]:
        cfg = self._config()
        if body.get("off"):
            cfg.pop("sleep_timer", None)
        else:
            hour, minute = int(body["hour"]), int(body.get("minute", 0))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise InvalidInput("時刻が正しくありません。")
            cfg["sleep_timer"] = {"hour": hour, "minute": minute}
        self._save_config(cfg)
        from .macos import apply_sleep_timer

        applied = apply_sleep_timer(self.brain.settings, cfg.get("sleep_timer"))
        return {"timer": cfg.get("sleep_timer"), "applied": applied}

    def _config(self) -> dict:
        p = self.brain.settings.home / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _save_config(self, cfg: dict) -> None:
        p = self.brain.settings.home / "config.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- safeguards ---------------------------------------------------------------------

    def safety_status(self) -> dict[str, Any]:
        return {"paused": self.brain.paused, "stats": self.brain.stats(),
                "backups": [p.name for p in safety.list_backups(self.brain.settings.backups)],
                "fts_secure_delete": self.brain.fts_secure_delete, "confirm_phrase": safety.CONFIRM_PHRASE}

    def plan_erase(self, body: dict) -> dict[str, Any]:
        plan = safety.plan_erase(self.brain, body.get("source_ids") or [], body.get("node_ids") or [],
                                 body.get("since") or None, body.get("until") or None)
        token = secrets.token_urlsafe(12)
        self.erase_plans = {token: plan}  # only the latest plan can be executed
        return {"plan_token": token, **plan}

    def erase(self, body: dict) -> dict[str, Any]:
        plan = self.erase_plans.pop(body.get("plan_token", ""), None)
        if plan is None:
            raise InvalidInput("消去の内容をもう一度確認してください（確認画面が古くなっています）。")
        return safety.erase(self.brain, plan, body.get("confirm", ""))

    def restore(self, body: dict) -> dict[str, Any]:
        names = {p.name: p for p in safety.list_backups(self.brain.settings.backups)}
        if body.get("name") not in names:
            raise InvalidInput("そのバックアップは見つかりません。")
        return safety.restore(self.brain, names[body["name"]])

    # ---- the brain at a glance (spec v0.5 §10) ----------------------------------------

    def brain_overview(self) -> dict[str, Any]:
        from datetime import datetime, timedelta, timezone

        from .inbox import pending_inbox
        from .promote import candidates

        b, c = self.brain, self.brain._conn
        now = datetime.now(timezone.utc)
        week = (now - timedelta(days=7)).isoformat()
        with b._lock:
            promoted_keys = [r[0] for r in c.execute("SELECT key FROM sleep_marks WHERE kind = 'promoted'")]
            waiting_signals: dict[str, set[str]] = {}
            try:
                for seg, _ in candidates(b, set(promoted_keys)):
                    waiting_signals.setdefault(seg.source_id, set()).add(seg.signal)
            except Exception:  # noqa: BLE001 — the overview must not fail because of Ollama
                pass
            hippo = []
            for r in c.execute(
                    "SELECT s.id, s.title, s.kind, s.author, s.ai_name, h.entered_at, h.expires_at,"
                    " (SELECT COUNT(*) FROM chunks WHERE source_id = s.id) AS chunks,"
                    " (SELECT COUNT(*) FROM node_sources WHERE source_id = s.id) AS memories,"
                    " (SELECT labels_json FROM screenings WHERE source_id = s.id) AS labels"
                    " FROM hippocampus h JOIN sources s ON s.id = h.source_id WHERE h.status = 'waiting'"
                    " ORDER BY h.entered_at DESC"):
                left = (datetime.fromisoformat(r["expires_at"]) - now).total_seconds() / 86400
                hippo.append({"id": r["id"], "title": r["title"], "kind": r["kind"],
                              "writer": r["ai_name"] if r["author"] == "ai" else "オーナー",
                              "entered_at": r["entered_at"], "days_left": round(max(0.0, left), 1),
                              "days_total": b.settings.hippocampus_days,
                              "signals": sorted(waiting_signals.get(r["id"], set())),
                              "memories": r["memories"], "chunks": r["chunks"],
                              "screened": _label_counts(r["labels"])})
            # A. the receiving box: arrived, waiting to be screened (spec v0.8 §3.4)
            arrived = [{"id": r["id"], "title": r["title"], "kind": r["kind"],
                        "writer": r["ai_name"] if r["author"] == "ai" else "オーナー", "arrived_at": r["entered_at"]}
                       for r in c.execute(
                           "SELECT s.id, s.title, s.kind, s.author, s.ai_name, h.entered_at FROM hippocampus h"
                           " JOIN sources s ON s.id = h.source_id WHERE h.status = 'arrived' ORDER BY h.entered_at")]
            kinds = {k: c.execute("SELECT COUNT(*) FROM nodes WHERE kind IN (?, ?) AND status = 'active'",
                                  (k, "case" if k == "episode" else k)).fetchone()[0]
                     for k in ("procedural", "semantic", "episode")}  # cases sit in the episode box (spec v0.8 §5.1)
            memories = []
            for k in ("procedural", "semantic", "episode"):  # each box shows its strongest
                memories += [dict(r) for r in c.execute(
                    "SELECT n.id, n.kind, n.body, n.promoted_by, n.importance, n.base_strength, n.goods, n.corrections,"
                    " n.about, n.subject, n.stage, n.scope, n.last_activated_at," + strength_sql("n.") + " AS strength,"
                    " n.occurrences, n.pinned, n.created_at, n.access_count,"
                    " (SELECT COUNT(*) FROM revisions r WHERE r.node_id = n.id) AS revisions"
                    " FROM nodes n WHERE n.status = 'active' AND (n.kind = ? OR (? = 'episode' AND n.kind = 'case'))"
                    " ORDER BY n.pinned DESC, n.importance * " + strength_sql("n.") + " DESC,"
                    " n.created_at DESC LIMIT 20", (k, k))]
            shelf = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM sources WHERE erased = 0 GROUP BY kind")}
            flow = {
                "received": c.execute("SELECT COUNT(*) FROM sources WHERE created_at >= ? AND kind != 'dream'",
                                      (week,)).fetchone()[0],
                "promoted": c.execute("SELECT COUNT(*) FROM nodes WHERE created_at >= ? AND kind != 'concept'",
                                      (week,)).fetchone()[0],
                "faded": c.execute("SELECT COUNT(*) FROM hippocampus WHERE status = 'faded'").fetchone()[0],
            }
        return {"inbox": pending_inbox(b), "arrived": arrived, "hippocampus": hippo, "cortex": {"kinds": kinds, "memories": memories},
                "bookshelf": shelf, "flow": flow}

    # ---- past threads (spec v0.6 decision 6) -----------------------------------------

    def threads(self, q: dict[str, str]) -> dict[str, Any]:
        from . import transcripts
        from .inbox import current_cursor

        st = self.brain.settings
        before = transcripts.since_utc(st.daily_logs_since) if st.daily_logs_since else "9999"
        queue = set(sleep.backfill_queue(self.brain))
        rows = []
        for th in transcripts.local_threads(st.codex_sessions, st.claude_projects, st.cowork_sessions):
            if q.get("q") and q["q"] not in th.title:
                continue
            done = current_cursor(self.brain, th.key + transcripts.BACKFILL_SUFFIX) or ""
            left = sum(1 for m in th.messages if done < m.at < before)
            rows.append({"key": th.key, "title": th.title, "ai": th.product, "first": th.messages[0].at,
                         "last": th.messages[-1].at, "past_left": left, "queued": th.key in queue})
        rows.sort(key=lambda r: r["last"], reverse=True)
        return {"threads": rows[:200], "nightly_since": st.daily_logs_since}

    def backfill(self, body: dict) -> dict[str, Any]:
        keys = [k for k in body.get("keys", []) if isinstance(k, str)]
        queue = sleep.backfill_queue(self.brain)
        if body.get("cancel"):
            queue = [k for k in queue if k not in keys]
        else:
            queue += [k for k in keys if k not in queue]
        sleep.save_backfill_queue(self.brain, queue)
        return {"queue": queue}

    # ---- routing -----------------------------------------------------------------------

    def get(self, path: str, q: dict[str, str]) -> Any:
        if path == "/api/brain":
            return self.brain_overview()
        if path == "/api/threads":
            return self.threads(q)
        if path == "/api/graph":
            return self.graph(q)
        if path.startswith("/api/node/"):
            return self.node(path.rsplit("/", 1)[1])
        if path == "/api/shelves":
            return self.shelves()
        if path.startswith("/api/source/"):
            return self.brain.open_source(path.rsplit("/", 1)[1], 200_000)
        if path == "/api/search":
            return {"bookshelf": self.brain.search_bookshelf(q.get("q", ""), limit=20)}
        if path == "/api/memory":
            return self.memory()
        if path == "/api/sleep":
            return self.sleep_status()
        if path == "/api/safety":
            return self.safety_status()
        return None

    def post(self, path: str, body: dict) -> Any:
        b = self.brain
        if path == "/api/memo":
            r = b.add_memo(body.get("title", ""), body.get("text", ""))
            return r or {"duplicate": True}
        if path == "/api/sleep":
            return self.start_sleep(bool(body.get("use_ai", True)))
        if path == "/api/rule/review":  # the owner's yes / no on a tentative rule (spec v0.8 §6.1)
            return self.brain.review_rule(str(body.get("id") or ""), str(body.get("verdict") or ""), "", "human")
        if path == "/api/memory":
            return self.write_memory()
        if path == "/api/sleep/timer":
            return self.set_timer(body)
        if path == "/api/backfill":
            return self.backfill(body)
        if path == "/api/pause":
            b.set_paused(bool(body.get("paused")))
            return {"paused": b.paused}
        if path == "/api/verify":
            ok, msg = b.verify()
            return {"ok": ok, "message": msg}
        if path == "/api/backup":
            return {"backup": safety.backup(b).name}
        if path == "/api/restore":
            return self.restore(body)
        if path == "/api/erase/plan":
            return self.plan_erase(body)
        if path == "/api/erase":
            return self.erase(body)
        return None

    def poll_inbox(self) -> None:
        """Take in what arrives, look through the vault now and then, and embed new chunks."""
        from .hippocampus import encode_pending

        while not self._stop.wait(INBOX_POLL_SECONDS):
            from . import screen

            for step in (lambda: ingest(self.brain), self._scan_vault_if_due, lambda: encode_pending(self.brain),
                         lambda: screen.run_if_due(self.brain)):  # around noon and 16:00 (spec v0.8 §3.4)
                try:
                    step()
                except Exception as e:  # keep the app alive; report in the terminal
                    print(f"inbox: {e}")

    def _scan_vault_if_due(self) -> None:
        """#remember is looked for twice a day at most (the Obsidian plugin sends notes at once)."""
        import time

        from .inbox import scan_vault

        mark = self.brain.settings.home / "vault_scanned_at"
        last = float(mark.read_text()) if mark.exists() else 0.0
        if time.time() - last >= VAULT_SCAN_HOURS * 3600:
            scan_vault(self.brain)
            mark.write_text(str(time.time()))


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "exobrain"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
                             " font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _guard(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(HTTPStatus.FORBIDDEN, {"error": "host not allowed"})
                return False
            return True

        def _api_guard(self) -> bool:
            if not self._guard():
                return False
            if not secrets.compare_digest(self.headers.get("X-Exobrain-Token", ""), app.token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "token required"})
                return False
            return True

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                if not self._api_guard():
                    return
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                self._dispatch(lambda: app.get(url.path, q))
                return
            if not self._guard():
                return
            name = "index.html" if url.path in ("/", "/index.html") else url.path.lstrip("/")
            file = (WEB / name).resolve()
            if WEB.resolve() not in file.parents or not file.is_file() or file.suffix not in STATIC_TYPES:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            data = file.read_bytes()
            if file.name == "index.html":
                data = data.replace(b"__EXOBRAIN_TOKEN__", app.token.encode())
            self._send(HTTPStatus.OK, data, STATIC_TYPES[file.suffix])

        def do_POST(self):
            url = urlparse(self.path)
            if not url.path.startswith("/api/") or not self._api_guard():
                if url.path.startswith("/api/"):
                    return
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json only"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return
            self._dispatch(lambda: app.post(url.path, body))

        def _dispatch(self, fn) -> None:
            try:
                with app.brain._lock:
                    result = fn()
            except InvalidInput as e:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            except sleep.SleepBusy as e:
                self._json(HTTPStatus.CONFLICT, {"error": str(e)})
                return
            if result is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            else:
                self._json(HTTPStatus.OK, result)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return  # the browser went away mid-response; nothing to report
        super().handle_error(request, client_address)


def code_stamp() -> float:
    """When exobrain's own files last changed (an upgrade rewrites them)."""
    return max((f.stat().st_mtime for f in Path(__file__).parent.rglob("*.py")), default=0.0)


def watch_for_upgrade(stop: threading.Event, on_upgrade, interval: float = 60.0, stamp=code_stamp) -> None:
    """The resident screen lives for days. Without this, an upgraded exobrain keeps running the old
    code there (its sleep button and bookshelf indexes included) until the Mac restarts."""
    first = stamp()
    while not stop.wait(interval):
        if stamp() != first:
            on_upgrade()
            return


def _restart_for_upgrade() -> None:
    import os
    import sys

    print("exobrain: 新しい版が入ったので、画面を起動し直します。", file=sys.stderr, flush=True)
    os._exit(75)  # launchd (KeepAlive) starts the new version


def serve(brain: Brain, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    app = App(brain)
    server = _Server(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=app.poll_inbox, daemon=True).start()
    if not open_browser:  # the resident form started by launchd, which restarts it
        threading.Thread(target=watch_for_upgrade, args=(app._stop, _restart_for_upgrade), daemon=True).start()
    print(f"exobrain: {url}  （終了は Ctrl+C）")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app._stop.set()
        server.server_close()
        time.sleep(0)
