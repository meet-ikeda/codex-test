"""Split an original into chunks that remember where they came from (spec v0.5 §6.1).

Ported from OUTBRAIN's chunkDocument: cut at headings or at a size limit, and
keep each chunk's offset, length, line numbers and heading so that any quote can
be cut back out of the original and checked against it.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

MAX_CHARS = int(os.environ.get("EXOBRAIN_CHUNK_CHARS", "800"))  # measured: docs/eval-embedding.md
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_NOT_JA = re.compile(r"[\s\W_a-z0-9]+")
_WORDS = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class Chunk:
    idx: int
    start: int  # offset in the original body
    length: int
    line_start: int  # 1-based, inclusive
    line_end: int
    heading: str
    text: str


def chunk_document(body: str, max_chars: int = MAX_CHARS) -> list[Chunk]:
    if not body.strip():
        return []
    lines: list[tuple[str, int]] = []  # (text with its line ending, offset)
    offset = 0
    for part in body.splitlines(keepends=True):
        lines.append((part, offset))
        offset += len(part)

    chunks: list[Chunk] = []
    start_line = 0
    size = 0

    def flush(end_line: int) -> None:
        nonlocal start_line, size
        if end_line <= start_line:
            return
        start = lines[start_line][1]
        end = lines[end_line - 1][1] + len(lines[end_line - 1][0])
        text = body[start:end]
        if text.strip():
            m = _HEADING.search(text)
            chunks.append(Chunk(len(chunks), start, end - start, start_line + 1, end_line,
                                m.group(1) if m else "", text))
        start_line, size = end_line, 0

    for i, (text, _) in enumerate(lines):
        is_heading = bool(_HEADING.match(text))
        if i > start_line and (size + len(text) > max_chars or (is_heading and size >= max_chars / 2)):
            flush(i)
        size += len(text)
        if len(text) > max_chars:
            flush(i + 1)
    flush(len(lines))
    return chunks


def lexical_tokens(text: str) -> list[str]:
    """Latin words plus Japanese character bigrams: search without a dictionary."""
    norm = unicodedata.normalize("NFKC", text).casefold()
    words = _WORDS.findall(norm)
    ja = _NOT_JA.sub("", norm)
    grams = [ja[i : i + 2] for i in range(len(ja) - 1)]
    if len(ja) == 1:
        grams.append(ja)
    return words + grams
