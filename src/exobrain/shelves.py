"""Shelf index files (FR-18): 本棚/棚/*.md, rebuilt after every sleep.

Originals never move; these Markdown indexes list links to them by date, by
source, and by topic. They open in Obsidian or any Markdown viewer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import quote

from .config import SHELVES_DIR

if TYPE_CHECKING:
    from .brain import Brain

TOPIC_PREFIX = "話題_"
SOURCE_LABEL = {"memo": "あなたのメモ"}


def _safe(name: str) -> str:
    return "".join("_" if ch in '\\/:*?"<>|' else ch for ch in name).strip() or "無題"


def write_shelf_index(brain: Brain) -> list[str]:
    folder = brain.settings.bookshelf / SHELVES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    rows = [dict(r) for r in brain._conn.execute(
        "SELECT id, kind, author, ai_name, title, path, created_at FROM sources ORDER BY created_at DESC")]
    topics: dict[str, list[dict]] = defaultdict(list)
    for r in brain._conn.execute("SELECT source_id, shelf FROM shelves ORDER BY shelf"):
        topics[r["shelf"]].append(r["source_id"])
    by_id = {r["id"]: r for r in rows}

    def link(r: dict) -> str:
        rel = PurePosixPath("..") / PurePosixPath(r["path"]).relative_to("本棚")
        day = datetime.fromisoformat(r["created_at"]).strftime("%Y-%m-%d")
        return f"- {day} [{r['title']}]({quote(str(rel))})"

    files: dict[str, str] = {}
    shelved = [r for r in rows if r["kind"] in ("memo", "daily_report")]
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in shelved:
        by_month[r["created_at"][:7]].append(r)
    files["日付別.md"] = "# 日付別\n\n" + "\n\n".join(
        f"## {m}\n\n" + "\n".join(link(r) for r in rs) for m, rs in by_month.items())
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in shelved:
        by_source[SOURCE_LABEL.get(r["kind"]) or r["ai_name"] or "不明"].append(r)
    files["出所別.md"] = "# 出所別\n\n" + "\n\n".join(
        f"## {s}\n\n" + "\n".join(link(r) for r in rs) for s, rs in sorted(by_source.items()))
    dreams = [r for r in rows if r["kind"] == "dream"]
    files["夢日記.md"] = "# 夢日記\n\n" + "\n".join(link(r) for r in dreams)
    for name, ids in topics.items():
        items = sorted((by_id[i] for i in ids if i in by_id), key=lambda r: r["created_at"], reverse=True)
        files[f"{TOPIC_PREFIX}{_safe(name)}.md"] = f"# {name}\n\n" + "\n".join(link(r) for r in items)

    for old in folder.glob(f"{TOPIC_PREFIX}*.md"):
        if old.name not in files:
            old.unlink()
    for name, text in files.items():
        (folder / name).write_text(text + "\n", encoding="utf-8")
    return sorted(files)
