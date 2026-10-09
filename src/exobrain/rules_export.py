"""Hand the confirmed rules to the files AIs always read (spec v0.8 §6.5).

exobrain is read only on "/思い出して", so rules the owner wants kept every time are also written to
~/.codex/AGENTS.md (Codex) and ~/.claude/CLAUDE.md (Claude Code). Only confirmed rules for all work, only a few
(long instruction files make AIs worse: arXiv:2602.11988), and only between exobrain's own markers: what the
owner wrote by hand is never touched. Off unless the owner turns it on (config "export_rules").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .recall import strength_sql

if TYPE_CHECKING:
    from .brain import Brain

START = "<!-- exobrain: 本決まりのルール（睡眠のたびに自動で書き直す。手で直すときはこの外に書く） -->"
END = "<!-- /exobrain -->"
MAX_RULES = 15


def enabled(brain: Brain) -> bool:
    cfg = brain.settings.home / "config.json"
    return bool(cfg.exists() and json.loads(cfg.read_text(encoding="utf-8")).get("export_rules"))


def targets() -> list[Path]:
    home = Path.home()
    return [home / ".codex" / "AGENTS.md", home / ".claude" / "CLAUDE.md"]


def block(brain: Brain) -> str:
    rows = brain._conn.execute(
        "SELECT body FROM nodes WHERE kind = 'procedural' AND status = 'active' AND stage = 'confirmed'"
        " AND COALESCE(scope, '') = '' ORDER BY pinned DESC, importance * " + strength_sql() + " DESC LIMIT ?",
        (MAX_RULES,)).fetchall()
    lines = [START, "## オーナー（池田さん）の本決まりのルール（exobrain から）", ""]
    lines += [f"- {r[0]}" for r in rows] or ["- （まだありません）"]
    return "\n".join([*lines, "", END])


def write(brain: Brain, files: list[Path] | None = None) -> list[str]:
    """Replace exobrain's block in each file (or add it at the end). Returns the files written."""
    text = block(brain)
    written = []
    for f in files or targets():
        if not f.parent.is_dir():
            continue  # that AI is not on this Mac
        old = f.read_text(encoding="utf-8") if f.exists() else ""
        if START in old and END in old.split(START, 1)[1]:
            before, rest = old.split(START, 1)
            new = before + text + rest.split(END, 1)[1]
        else:
            new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + text + "\n"
        if new != old:
            tmp = f.with_name(f.name + ".exobrain.tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(f)
            written.append(str(f))
    return written
