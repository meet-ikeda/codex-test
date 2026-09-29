"""Deposits (預け入れ): a thread the owner chose to hand over, summarised by the AI that had it (spec v0.6 §5.4).

One format for every AI. Claude's chat sends it through the MCP tool `deposit`; ChatGPT and Gemini
write the same Markdown as a file, and exobrain picks it up from ~/Downloads or the receiving box.
The owner asked for it, so it is an explicit signal: the whole deposit is a promotion candidate.
"""

from __future__ import annotations

import json
from datetime import datetime

SECTIONS = (
    ("summary", "要約"),
    ("procedural", "手続き記憶（やり方・ルール・好み・注意されたこと）"),
    ("reasons", "こだわり・理由（オーナー本人の言葉）"),
    ("semantic", "意味記憶（事実・決定・仕事の状況・考えていること）"),
    ("episode", "エピソード記憶（出来事）"),
)
NOTE_TYPES = ("", "interview", "client")


def render(source: str, thread_title: str, sections: dict[str, list[str] | str], note_type: str = "",
           confidential: bool = False, period: str = "") -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    head = ["---", "exobrain_kind: deposit", f"exobrain_source: {json.dumps(source, ensure_ascii=False)}",
            f"exobrain_thread_title: {json.dumps(thread_title, ensure_ascii=False)}",
            f"exobrain_period: {json.dumps(period, ensure_ascii=False)}",
            f"exobrain_generated_at: {json.dumps(now)}",
            f"exobrain_note_type: {json.dumps(note_type)}",
            f"exobrain_confidential: {'true' if confidential else 'false'}", "---", "",
            f"# 預け入れ · {thread_title}", ""]
    body = []
    for key, heading in SECTIONS:
        v = sections.get(key) or ([] if key != "summary" else "")
        body += [f"## {heading}", ""]
        if key == "summary":
            body += [str(v).strip() or "（なし）", ""]
        else:
            items = [" ".join(str(x).split()) for x in v if str(x).strip()]
            body += [f"- {x}" for x in items] or ["- 特になし"]
            body.append("")
    return "\n".join(head + body)


def meta_of(fm: dict) -> dict | None:
    """Metadata of a deposit file's front matter, or None if it is not a deposit."""
    if fm.get("exobrain_kind") != "deposit":
        return None
    note_type = str(fm.get("exobrain_note_type") or "")
    confidential = str(fm.get("exobrain_confidential", "")).lower() in ("true", "1", "yes")
    return {"source": str(fm.get("exobrain_source") or "unknown"),
            "thread_title": str(fm.get("exobrain_thread_title") or "無題のスレッド"),
            "period": str(fm.get("exobrain_period") or ""),
            **({"note_type": note_type} if note_type in NOTE_TYPES[1:] else {}),
            **({"confidential": True} if confidential else {})}
