"""AI daily logs: one file per thread per day, only what is new since the last cursor.

The format is OUTBRAIN's "AI daily log protocol v1" unchanged, so that logs
written for OUTBRAIN (e.g. by the Codex automation) are read as they are.
A log whose previous_cursor does not continue the thread's checkpoint is
refused: something was skipped or sent twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

REQUIRED = ("source", "ai_provider", "ai_product", "ai_model", "thread_id", "entry_date",
            "period_start", "period_end", "generated_at", "cursor")
# Log format v2 (docs/daily-log-rules.md, 2026-10-05). Keys stay where they meant the same thing.
SECTIONS = (
    ("status", "今の状況"),
    ("decisions", "決まったこと"),
    ("rejected", "ボツになったこと"),
    ("conditions", "前提・条件"),
    ("reasons", "オーナーの言葉"),  # only what the owner said as a reason or a preference, word for word
    ("events", "あったこと"),        # what happened, reactions included
    ("unresolved", "次にやること"),
    ("ai_notes", "AIの解釈"),        # the AI's own reading, never mixed with the owner's
)
# v1 keys still accepted from older callers: folded into the v2 sections.
V1_KEYS = {"corrections": "conditions", "learnings": "ai_notes"}  # a correction is a condition from now on
# Headings of both versions, for readers of logs already on the bookshelf.
HEADINGS = {
    "status": ("今の状況",),
    "decisions": ("決まったこと",),
    "rejected": ("ボツになったこと",),
    "conditions": ("前提・条件",),
    "reasons": ("オーナーの言葉", "オーナーのこだわり・理由"),
    "events": ("あったこと", "今日の出来事", "注意・訂正されたこと"),
    "unresolved": ("次にやること", "未解決・次に続くこと"),
    "ai_notes": ("AIの解釈", "工夫・学び"),
}
PROJECT_KEY = "案件"


def fold_v1(sections: dict[str, list[str]]) -> dict[str, list[str]]:
    out = {k: list(v or []) for k, v in sections.items() if k not in V1_KEYS}
    for old, new in V1_KEYS.items():
        out.setdefault(new, []).extend(sections.get(old) or [])
    return out


def section_items(body: str, key: str) -> list[str]:
    """Bullet items under one section of a log (v1 or v2 headings)."""
    names = HEADINGS[key]
    items, inside = [], False
    for line in body.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() in names
            continue
        if inside and line.lstrip().startswith(("- ", "* ")):
            text = line.lstrip()[2:].strip()
            if text and text not in ("特になし", "なし", "（なし）"):
                items.append(text)
    return items


_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_KEY = re.compile(r"^([^\s:#\-][^:]*?):\s*(.*)$")  # Japanese keys too (the plugin's receipt: exobrain受領)
_TITLE = re.compile(r"^#\s+AI日報\s*·\s*(.+?)\s*·\s*\d{4}-\d{2}-\d{2}\s*$", re.M)


class DailyRejected(ValueError):
    """A daily log that cannot be filed as it is. The message says why, in Japanese."""


@dataclass(frozen=True)
class DailyMeta:
    fields: dict[str, str]
    thread_title: str

    @property
    def key(self) -> str:
        return f"{self.fields['source']}:{self.fields['thread_id']}"

    @property
    def ai_name(self) -> str:
        return self.fields.get("ai_agent") or self.fields["ai_product"]


def front_matter(text: str) -> dict[str, Any]:
    """Tiny YAML reader for flat `key: value` headers (values may be JSON strings or [a, b] lists)."""
    m = _FRONT.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        km = _KEY.match(lines[i])
        i += 1
        if not km:
            continue
        key, raw = km.group(1), km.group(2).strip()
        if raw == "":  # block list: "tags:\n  - a\n  - b"
            items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(lines[i].lstrip()[2:].strip().strip("\"'"))
                i += 1
            out[key] = items if items else ""
            continue
        if raw.startswith("[") and raw.endswith("]"):
            out[key] = [x.strip().strip("\"'") for x in raw[1:-1].split(",") if x.strip()]
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw.strip("\"'")
        out[key] = value
    return out


def parse(text: str) -> DailyMeta | None:
    """Return the log's metadata, or None if this is not an AI daily log at all."""
    fm = front_matter(text)
    if fm.get("outbrain_kind") != "ai_daily":
        return None
    fields = {k[len("outbrain_"):]: "" if v is None else str(v) for k, v in fm.items() if k.startswith("outbrain_")}
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        raise DailyRejected(f"AI 日報に必要な項目がありません: {', '.join(missing)}")
    m = _TITLE.search(text)
    return DailyMeta(fields, m.group(1) if m else fields["thread_id"])


def check_continuity(meta: DailyMeta, current_cursor: str | None) -> bool:
    """True if the log is new and continues the thread; False if it was already filed."""
    prev = meta.fields.get("previous_cursor", "")
    if current_cursor == meta.fields["cursor"]:
        return False
    if current_cursor is not None and prev != current_cursor:
        raise DailyRejected(f"AI 日報の差分がつながっていません（前回の cursor: {current_cursor}、"
                            f"この日報の previous_cursor: {prev or '空'}）")
    if current_cursor is None and prev:
        raise DailyRejected("このスレッドの最初の日報なのに previous_cursor が入っています。前の日報が届いていない可能性があります。")
    return True


def _bullets(items: list[str] | None) -> str:
    items = [" ".join(str(x).split()) for x in (items or []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in (items or ["特になし"]))


def _yaml(value: str) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def render(fields: dict[str, str], thread_title: str, sections: dict[str, list[str]], project: str = "") -> str:
    """Write a log: protocol v1's header (so cursors keep working), v2's sections (docs/daily-log-rules.md)."""
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        raise DailyRejected(f"AI 日報に必要な項目がありません: {', '.join(missing)}")
    head = ["---", "outbrain_kind: ai_daily"]
    for k in ("source", "ai_provider", "ai_product", "ai_model"):
        head.append(f"outbrain_{k}: {_yaml(fields[k])}")
    head.append(f"outbrain_ai_agent: {_yaml(fields.get('ai_agent') or fields['ai_product'])}")
    for k in ("generated_at", "thread_id", "entry_date", "period_start", "period_end"):
        head.append(f"outbrain_{k}: {_yaml(fields[k])}")
    head.append(f"outbrain_previous_cursor: {_yaml(fields.get('previous_cursor') or '')}")
    head.append(f"outbrain_cursor: {_yaml(fields['cursor'])}")
    project = " ".join(str(project or "").split()).strip("[]") or "なし"
    head.append(f"{PROJECT_KEY}: {_yaml('[[' + project + ']]' if project != 'なし' else 'なし')}")
    head += ["tags: [remember, outbrain/ai-daily]", "---", "", f"# AI日報 · {thread_title} · {fields['entry_date']}", ""]
    body = []
    sections = fold_v1(sections)
    for key, heading in SECTIONS:
        if key == "ai_notes" and not sections.get(key):
            continue
        body += [f"## {heading}", "", _bullets(sections.get(key)), ""]
    return "\n".join(head + body) + "#remember\n"


def filename(fields: dict[str, str], thread_title: str) -> str:
    readable = unicodedata.normalize("NFKC", thread_title or "untitled")
    readable = re.sub(r'[\\/:*?"<>|\s]+', "-", readable).strip("-")[:30] or "untitled"
    short = hashlib.sha256(f"{fields['source']}\0{fields['thread_id']}".encode()).hexdigest()[:8]
    return f"{fields['entry_date']}__{fields['source']}__{readable}__{short}.md"
