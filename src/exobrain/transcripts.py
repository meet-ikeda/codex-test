"""Conversations kept on this Mac, read for the nightly AI daily logs (spec v0.5 §5.2).

- Codex:       ~/.codex/sessions/**/rollout-*.jsonl, only the owner's threads (Codex's own
               guardian sub-agents are skipped)
- Claude Code: ~/.claude/projects/<project>/<session>.jsonl, except exobrain's own sleep runs

Only what is new since the thread's checkpoint is taken, and never anything before
`daily_logs_since` (set by the owner, so a first run does not swallow years of history).
The cursor of a thread is the timestamp of the last message included.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

EXCERPT_MAX = 24_000  # characters of conversation handed to the AI for one thread's log
HEAD_KEEP = 6_000
MIN_NEW_CHARS = 200  # a thread with less than this since the last log is not worth a log
SKIP_PREFIXES = ("<", "# AGENTS.md", "## Referenced", "Caveat:")
SLEEP_PROJECT_MARK = "-exobrain"  # Claude Code runs started by exobrain's sleep (cwd ~/.exobrain)
SLEEP_PROMPT_HEAD = "あなたは exobrain（利用者の外部脳）の睡眠処理です"  # ...wherever they were started from


@dataclass
class Message:
    at: str  # ISO 8601, UTC
    role: str  # "owner" | "ai"
    text: str


@dataclass
class Thread:
    source: str  # protocol v1 "source"
    provider: str
    product: str
    thread_id: str
    title: str
    model: str = "unknown"
    messages: list[Message] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.thread_id}"


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text") and c.get("text"):
            out.append(c["text"])
    return out


def _clean(texts: list[str]) -> str:
    kept = [t.strip() for t in texts if t.strip() and not t.lstrip().startswith(SKIP_PREFIXES)]
    return "\n".join(kept).strip()


def _read_jsonl(path: Path) -> Iterator[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def codex_threads(root: Path) -> Iterator[Thread]:
    by_id: dict[str, Thread] = {}
    titles = {}
    index = root.parent / "session_index.jsonl"
    for d in _read_jsonl(index):
        if d.get("id") and d.get("thread_name"):
            titles[d["id"]] = d["thread_name"]  # later lines win (renamed threads)
    for path in sorted(root.rglob("rollout-*.jsonl")):
        meta, model, msgs = None, "unknown", []
        for d in _read_jsonl(path):
            t, p = d.get("type"), d.get("payload") or {}
            if t == "session_meta":
                meta = p
                if isinstance(p.get("source"), dict):  # sub-agents (guardian review etc.) are not conversations
                    break
            elif t == "turn_context" and p.get("model"):
                model = p["model"]
            elif t == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
                text = _clean(_texts(p.get("content")))
                if text and d.get("timestamp"):
                    msgs.append(Message(d["timestamp"], "owner" if p["role"] == "user" else "ai", text))
        if not meta or isinstance(meta.get("source"), dict) or not msgs:
            continue
        tid = meta.get("id") or meta.get("session_id")
        th = by_id.setdefault(tid, Thread("codex", "openai", "Codex", tid, titles.get(tid, "Codex のスレッド")))
        th.model = model
        th.messages.extend(msgs)
    for th in by_id.values():
        th.messages.sort(key=lambda m: m.at)
        yield th


def claude_code_threads(root: Path) -> Iterator[Thread]:
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        if project.name.endswith(SLEEP_PROJECT_MARK):
            continue
        for path in sorted(project.glob("*.jsonl")):
            title, model, msgs = None, "unknown", []
            for d in _read_jsonl(path):
                t = d.get("type")
                if t in ("custom-title", "ai-title"):
                    title = d.get("customTitle") or d.get("title") or d.get("aiTitle") or title
                if t not in ("user", "assistant") or d.get("isMeta") or d.get("isSidechain"):
                    continue
                m = d.get("message") or {}
                if t == "assistant" and m.get("model"):
                    model = m["model"]
                content = m.get("content")
                if t == "user" and isinstance(content, list) and any(
                        isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                    continue  # tool output, not the owner speaking
                text = _clean(_texts(content))
                if text and d.get("timestamp"):
                    msgs.append(Message(d["timestamp"], "owner" if t == "user" else "ai", text))
            if msgs and msgs[0].role == "owner" and msgs[0].text.startswith(SLEEP_PROMPT_HEAD):
                continue  # exobrain's own sleep, not a conversation with the owner
            if msgs:
                yield Thread("claude-code", "anthropic", "Claude Code", path.stem,
                             title or "Claude Code のスレッド", model, msgs)


def since_utc(since: str) -> str:
    """The owner's start date is a local date; message times are UTC ('...Z'). Compare like with like."""
    local_midnight = datetime.fromisoformat(since[:10]).astimezone()
    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pending(threads: Iterator[Thread], cursors: dict[str, str], since: str) -> Iterator[tuple[Thread, list[Message]]]:
    """Messages newer than each thread's cursor (and than `since`, a local date), when there is enough to log."""
    start = since_utc(since)
    for th in threads:
        after = max(cursors.get(th.key, ""), start)
        new = [m for m in th.messages if m.at > after]
        if not any(m.role == "owner" for m in new) or sum(len(m.text) for m in new) < MIN_NEW_CHARS:
            continue
        yield th, new


def excerpt(messages: list[Message]) -> tuple[str, bool]:
    """The conversation as plain lines, trimmed in the middle if too long. Returns (text, truncated)."""
    lines = []
    for m in messages:
        at = datetime.fromisoformat(m.at.replace("Z", "+00:00")).astimezone()
        who = "オーナー" if m.role == "owner" else "AI"
        lines.append(f"[{at:%m/%d %H:%M} {who}] {m.text}")
    text = "\n\n".join(lines)
    if len(text) <= EXCERPT_MAX:
        return text, False
    tail = EXCERPT_MAX - HEAD_KEEP
    return text[:HEAD_KEEP] + "\n\n（……途中を省略……）\n\n" + text[-tail:], True
