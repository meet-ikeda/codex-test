"""Memos from the owner: the inbox folder and pasted text (FR-15).

A memo is filed on the bookshelf verbatim as soon as it arrives. Splitting it
into elements happens later, during sleep; until then it is still findable by
bookshelf search.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .bookshelf import body_sha256, write_original

if TYPE_CHECKING:
    from .brain import Brain

ACCEPTED = {".md", ".markdown", ".txt"}
SETTLE_SECONDS = 2.0  # a file still being written or synced is left for next time
UNREADABLE_DIR = "読めなかった"


def add_memo(brain: Brain, title: str, text: str, actor: str = "human") -> dict | None:
    """File a memo on the bookshelf. Returns None if the same memo is already there."""
    from .brain import TITLE_MAX, InvalidInput, new_id

    title = " ".join(title.split())[:TITLE_MAX] or "メモ"
    if not text.strip():
        raise InvalidInput("メモが空です。")
    sha = body_sha256(text)
    if brain._conn.execute("SELECT 1 FROM sources WHERE kind = 'memo' AND sha256 = ?", (sha,)).fetchone():
        return None
    source_id = new_id("src")
    original = write_original(brain.settings.originals, source_id, "memo", "human", None, title, text)
    rel = original.path.relative_to(brain.settings.drive_root).as_posix()
    try:
        with brain._tx():
            brain._emit(actor, "source_added", {
                "id": source_id, "kind": "memo", "author": "human", "ai_name": None, "title": title,
                "path": rel, "sha256": original.sha256, "created_at": original.created_at,
            })
    except BaseException:
        original.path.unlink(missing_ok=True)
        raise
    return {"source_id": source_id, "title": title, "path": rel}


def ingest(brain: Brain) -> list[dict]:
    """Move every settled text file from the inbox onto the bookshelf."""
    inbox = brain.settings.inbox
    if not inbox.exists():
        return []
    added = []
    now = time.time()
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.name.startswith((".", "~")) or path.suffix.lower() not in ACCEPTED:
            continue
        if now - path.stat().st_mtime < SETTLE_SECONDS:
            continue
        # Claim the file first so two exobrain processes never file it twice.
        claimed = path.with_name(f".claimed-{os.getpid()}-{path.name}")
        try:
            path.rename(claimed)
        except FileNotFoundError:
            continue
        try:
            text = claimed.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            _set_aside(claimed, path.name)
            continue
        try:
            result = add_memo(brain, path.stem, text)
        except BaseException:
            claimed.rename(path)  # put it back for next time
            raise
        claimed.unlink()
        if result:
            added.append(result)
    return added


def _set_aside(claimed: Path, name: str) -> None:
    target_dir = claimed.parent / UNREADABLE_DIR
    target_dir.mkdir(exist_ok=True)
    target = target_dir / name
    if target.exists():
        target = target.with_name(f"{target.stem}_{int(time.time())}{target.suffix}")
    claimed.rename(target)
