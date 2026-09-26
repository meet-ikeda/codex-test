"""The bookshelf: originals (memos, AI daily reports) kept verbatim as Markdown.

Files are created exclusively and never rewritten. A small front-matter block
carries metadata; everything after it is the original text, byte for byte.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FRONT_MATTER_END = "\n---\n"

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass(frozen=True)
class Original:
    source_id: str
    kind: str  # "daily_report" | "memo"
    author: str  # "ai" | "human"
    ai_name: str | None
    title: str
    created_at: str  # local time, ISO 8601
    sha256: str
    path: Path


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _slug(text: str, limit: int = 40) -> str:
    s = _UNSAFE.sub("_", text).strip().replace(" ", "_")
    return s[:limit] or "untitled"


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def write_original(
    originals_dir: Path,
    source_id: str,
    kind: str,
    author: str,
    ai_name: str | None,
    title: str,
    body: str,
    created: datetime | None = None,
) -> Original:
    created = (created or datetime.now()).astimezone()
    sha = body_sha256(body)
    who = _slug(ai_name or "") if author == "ai" else "memo"
    folder = originals_dir / f"{created:%Y}" / f"{created:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{created:%Y%m%d-%H%M}_{who}_{_slug(title)}.md"
    if path.exists():
        path = path.with_name(f"{path.stem}_{source_id[-6:]}.md")

    lines = [
        "---",
        f"id: {source_id}",
        f"kind: {kind}",
        f"author: {author}",
        f"ai_name: {_yaml_str(ai_name) if ai_name else 'null'}",
        f"title: {_yaml_str(title)}",
        f"created_at: {created.isoformat(timespec='seconds')}",
        f"sha256: {sha}",
        "---",
        "",  # join() then ends the header with "---\n"; the body follows directly
    ]
    # newline="" keeps the original's line endings exactly as given.
    with open(path, "x", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + body)
    return Original(source_id, kind, author, ai_name, title, created.isoformat(timespec="seconds"), sha, path)


def read_body(path: Path) -> str:
    """Return the original text without the front matter."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    end = text.index(FRONT_MATTER_END, 3) + len(FRONT_MATTER_END)
    return text[end:]
