"""Full-text search over the bookshelf (design §6.3).

The index is SQLite FTS5 with the trigram tokenizer, so Japanese needs no word
segmentation. Terms of three or more characters use MATCH; shorter ones fall
back to LIKE, which the trigram index can also serve.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

# Split free text into candidate terms at punctuation, spaces and common particles.
_SPLIT = re.compile(
    r"[\s、。，．,.!?！？「」『』（）()\[\]【】:：;；/\\・…\"'`]+"
    r"|(?:から|まで|より|では|には|とは|って|ので|のに|けど|です|ます|でした|ました)"
    # a one-letter particle right after kanji/katakana, unless more hiragana follows (part of a word)
    "|(?<=[一-鿿゠-ヿ])[はがをにでとのもへや](?![぀-ゟ])"
)
PASSAGE_RADIUS = 100
MAX_TERMS = 12


def extract_terms(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    terms = []
    for part in _SPLIT.split(text):
        part = part.strip()
        if len(part) >= 2 and part not in terms:
            terms.append(part)
    terms.sort(key=len, reverse=True)
    return terms[:MAX_TERMS]


def _fts_quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _passage(body: str, terms: list[str]) -> str:
    low = body.casefold()
    for t in terms:
        i = low.find(t.casefold())
        if i >= 0:
            start, end = max(0, i - PASSAGE_RADIUS), min(len(body), i + len(t) + PASSAGE_RADIUS)
            return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")
    return body[: PASSAGE_RADIUS * 2]


def search(conn: sqlite3.Connection, query: str, keywords: list[str] | None = None, limit: int = 5) -> list[dict]:
    terms = []
    for t in [*(keywords or []), *extract_terms(query)]:
        t = unicodedata.normalize("NFKC", t).strip()
        if len(t) >= 2 and t not in terms:
            terms.append(t)
    if not terms and query.strip():
        terms = [unicodedata.normalize("NFKC", query).strip()]  # a one-character query is still a query
    if not terms:
        return []
    matched: dict[str, set[str]] = {}
    for t in terms:
        if len(t) >= 3:
            rows = conn.execute("SELECT source_id FROM source_fts WHERE source_fts MATCH ?", (_fts_quote(t),))
        else:
            like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = conn.execute(
                "SELECT source_id FROM source_fts WHERE body LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'",
                (like, like),
            )
        for (sid,) in rows:
            matched.setdefault(sid, set()).add(t)
    if not matched:
        return []

    def rank(sid: str) -> tuple:
        found = matched[sid]
        return (-len(found), -sum(len(t) for t in found))

    out = []
    for sid in sorted(matched, key=rank)[:limit]:
        r = conn.execute(
            "SELECT s.id, s.kind, s.author, s.ai_name, s.title, s.created_at, f.body FROM sources s"
            " JOIN source_fts f ON f.source_id = s.id WHERE s.id = ?", (sid,)
        ).fetchone()
        if r is None:
            continue
        found = sorted(matched[sid], key=len, reverse=True)
        out.append({
            "source_id": r[0], "kind": r[1], "author": r[2], "ai_name": r[3], "title": r[4], "created_at": r[5],
            "matched_terms": found, "coverage": round(len(found) / len(terms), 3),
            "passage": _passage(r[6], found),
        })
    return out
