"""Sleep A: promote from the hippocampus to the cortex (spec v0.5 §6.2, OUTBRAIN v0.4 §5).

The AI does not decide what is important. A passage is a candidate only when a
signal says so:
- explicit:   the owner's own notes (#remember, memos) and, in AI daily logs, the
              sections "決まったこと" and "注意・訂正されたこと"
- repetition: something close to it appears in another original from another day
Anything else stays on the bookshelf.

The AI splits a candidate into atoms and answers with line numbers only; the
quote is cut from the original by this module and must match it. An atom that
says the same as an existing memory adds evidence to it instead of a new memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

import numpy as np

from .bookshelf import read_body

if TYPE_CHECKING:
    from .brain import Brain

EXPLICIT_KINDS = ("remember_note", "memo")
DAILY_EXPLICIT_SECTIONS = ("決まったこと", "注意・訂正されたこと")
ATOMS_PER_SEGMENT = 5
ATOMS_PER_OWNER_SEGMENT = 8  # the owner's own notes are dense and were marked on purpose (2026-09-27)
SIMILAR_SHOWN = 8
REPEAT_SIM = 0.80  # bge-m3 cosine for "the same thing said again" — provisional, check on real logs
IMPORTANCE = {"explicit": 1.0, "demand": 0.8, "repetition": 0.6, "association": 0.4}  # OUTBRAIN v0.4 §7.2 "I"
DERIVATIONS = ("verbatim", "paraphrase", "inferred")
NOTHING = ("特になし", "なし", "")
# The day something was said: an AI daily log's entry_date, otherwise the day it was filed.
_DAY = "COALESCE(json_extract(meta_json, '$.entry_date'), substr(created_at, 1, 10))"


@dataclass
class Segment:
    source_id: str
    line_start: int
    line_end: int
    signal: str

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.line_start}-{self.line_end}"


def _lines(body: str) -> list[str]:
    return body.splitlines()


def _daily_sections(body: str) -> list[tuple[str, int, int]]:
    """(heading, first content line, last line) for each '## ' section, 1-based."""
    lines = _lines(body)
    out, cur, start = [], None, 0
    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            if cur:
                out.append((cur, start, i - 1))
            cur, start = line[3:].strip(), i + 1
    if cur:
        out.append((cur, start, len(lines)))
    return out


def _meaningful(text: str) -> bool:
    items = [l.strip().lstrip("-").strip() for l in text.splitlines() if l.strip() and l.strip() != "#remember"]
    return any(x not in NOTHING for x in items)


def candidates(brain: Brain, done: set[str]) -> Iterator[tuple[Segment, str]]:
    """Segments waiting in the hippocampus that carry a promotion signal. Yields (segment, text)."""
    c = brain._conn
    rows = c.execute(
        "SELECT s.id, s.kind, s.path FROM sources s JOIN hippocampus h ON h.source_id = s.id"
        " WHERE h.status = 'waiting' AND s.erased = 0 ORDER BY s.created_at").fetchall()
    for r in rows:
        path = brain.settings.drive_root / r["path"]
        if not path.exists():
            continue
        body = read_body(path)
        lines = _lines(body)
        segs: list[Segment] = []
        if r["kind"] in EXPLICIT_KINDS:
            for ch in c.execute("SELECT line_start, line_end FROM chunks WHERE source_id = ? ORDER BY idx", (r["id"],)):
                segs.append(Segment(r["id"], ch[0], ch[1], "explicit"))
        elif r["kind"] == "ai_daily":
            for heading, a, b in _daily_sections(body):
                if b < a:
                    continue
                signal = "explicit" if heading in DAILY_EXPLICIT_SECTIONS else None
                content = "\n".join(lines[a - 1 : b])
                # Compared the way sections are chunked and embedded: heading line and all.
                if signal is None and _meaningful(content) and _repeated(brain, r["id"], "\n".join(lines[a - 2 : b])):
                    signal = "repetition"
                if signal:
                    segs.append(Segment(r["id"], a, b, signal))
        for seg in segs:
            if seg.key in done:
                continue
            text = "\n".join(lines[seg.line_start - 1 : seg.line_end])
            if _meaningful(text):
                yield seg, text


def _repeated(brain: Brain, source_id: str, text: str) -> bool:
    """Close to a chunk of another original written on another day."""
    emb = brain.embedder
    if emb is None:
        return False
    try:
        v = emb.embed([text])[0]
    except Exception:  # noqa: BLE001 — Ollama down: no repetition signal tonight
        return False
    brain.chunk_index.load(brain._conn, emb.model)
    sims = brain.chunk_index.cosine(v)
    if not sims:
        return False
    me = brain._conn.execute(f"SELECT {_DAY}, origin_file FROM sources WHERE id = ?", (source_id,)).fetchone()
    for cid, sim in sims.items():
        if sim < REPEAT_SIM:
            break
        other = brain._conn.execute(
            f"SELECT s.id, {_DAY.replace('meta_json', 's.meta_json').replace('created_at', 's.created_at')},"
            " s.origin_file, s.kind FROM chunks c JOIN sources s ON s.id = c.source_id WHERE c.id = ?",
            (cid,)).fetchone()
        if other and other[0] != source_id and other[1] != me[0] and other[3] != "dream" \
                and (other[2] is None or other[2] != me[1]):  # a revision of the same file is not repetition
            return True
    return False


def similar_memories(brain: Brain, text: str, limit: int = SIMILAR_SHOWN) -> list[dict]:
    """Existing cortex memories close to the text, for the AI to check duplicates against."""
    c = brain._conn
    emb = brain.embedder
    ids: list[str] = []
    if emb is not None:
        rows = c.execute("SELECT v.node_id, v.vec FROM node_vectors v JOIN nodes n ON n.id = v.node_id"
                         " WHERE v.model = ? AND n.status = 'active'", (emb.model,)).fetchall()
        if rows:
            try:
                q = np.asarray(emb.embed([text])[0], dtype=np.float32)
                m = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                sims = (m @ q) / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-12)
                ids = [rows[i][0] for i in np.argsort(-sims)[:limit]]
            except Exception:  # noqa: BLE001
                ids = []
    if not ids:  # no embeddings: fall back to shared bigrams
        from .recall import bigrams

        q = bigrams(text)
        scored = [(len(q & bigrams(r[1] or "")), r[0]) for r in
                  c.execute("SELECT id, body FROM nodes WHERE status = 'active' AND kind != 'concept'")]
        ids = [nid for s, nid in sorted(scored, reverse=True)[:limit] if s > 0]
    out = []
    for nid in ids:
        n = c.execute("SELECT id, kind, body FROM nodes WHERE id = ?", (nid,)).fetchone()
        if n:
            out.append({"id": n["id"], "kind": n["kind"], "text": n["body"]})
    return out


ABOUTS = ("owner", "client", "interviewee", "other")
NOTE_TYPE_HINT = {
    "interview": "これは取材メモ。書かれた体験・意見・経歴は取材相手のもの。オーナーのものとして覚えない。"
                 "取材相手の話は about='interviewee'、subject に相手（名前か役割）を入れる。"
                 "オーナー自身の感想・気づきとはっきり書かれた部分だけ about='owner'。",
    "client": "これはクライアントについてのメモ。クライアントの事情・方針・要望は about='client'、"
              "subject に会社名や担当者を入れる。オーナー自身のことと混ぜない。",
}
PERSONAL_INFO_RULE = ("電話番号・住所・メールアドレス・口座などの個人情報や、パスワード・鍵は、原子に書かない。")
OWNER_NOTE_HINT = (
    "これはオーナー自身のメモ（#remember またはメモ）。決定やルールに限らず、"
    "オーナーがいま取り組んでいる仕事・案件、考えていること・関心・問題意識も semantic として拾う"
    "（例: 「オーナーは〇〇社の採用サイトの構成を考えている」）。走り書きでも、読み取れることは拾う。")


def make_item(brain: Brain, seg: Segment, text: str) -> dict[str, Any]:
    s = brain._conn.execute("SELECT title, kind, author, ai_name, created_at FROM sources WHERE id = ?",
                            (seg.source_id,)).fetchone()
    numbered = [[seg.line_start + i, line] for i, line in enumerate(text.splitlines())]
    owner_note = s["kind"] in EXPLICIT_KINDS
    limit = ATOMS_PER_OWNER_SEGMENT if owner_note else ATOMS_PER_SEGMENT
    meta_row = brain._conn.execute("SELECT meta_json FROM sources WHERE id = ?", (seg.source_id,)).fetchone()[0]
    note_type = (json.loads(meta_row) if meta_row else {}).get("note_type")
    return {
        "type": "promote", "source_id": seg.source_id, "signal": seg.signal,
        "source": {"title": s["title"], "writer": s["ai_name"] if s["author"] == "ai" else "オーナー",
                   "date": s["created_at"][:10]},
        "lines": numbered, "similar_memories": similar_memories(brain, text),
        "instructions": (
            NOTE_TYPE_HINT.get(note_type, "") + (OWNER_NOTE_HINT if owner_note and not note_type else "")
            + f"この部分を、長く覚えておく価値のある「記憶の原子」に分ける（最大 {limit} 個。価値がなければ atoms を空に）。"
            "1 原子 = 1 つの決定・事実・ルール・好み・出来事。原子は単独で意味が通る 1 文（300 文字以内）にする。"
            "kind: procedural（やり方・ルール・好み・注意されたこと）/ semantic（事実・決定）/ episode（出来事）。"
            "derivation: verbatim（原文のまま）/ paraphrase（意味を変えずに整理）/ inferred（原文に直接はない推論）。"
            "lines: 根拠の行番号 [開始, 終了]（lines にある番号だけ）。引用は書かない（プログラムが切り出す）。"
            "confidence: 0〜1。concepts: 固有名詞・話題を 8 個まで。"
            "similar_memories と同じ内容なら same_as にその id を入れる（新しく作らない）。"
            "similar_memories の内容を新しい決定が置き換えるなら supersedes にその id を入れる。"
            "about: その記憶が誰についてか（owner=オーナー / client=クライアント / interviewee=取材相手 / other）。"
            "owner 以外なら subject に誰か（会社名・人名・役割）を入れ、text の主語にもする。"
            + PERSONAL_INFO_RULE + "迷ったら統合しない。書かれていないことを足さない。"),
        "max_atoms": limit, **({"note_type": note_type} if note_type else {}),
    }


def _norm(text: str) -> str:
    """For comparing wording: width, spaces, list marks and final punctuation do not count."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"^[\s\-*・]+", "", t, flags=re.M)
    return re.sub(r"[\s。．.、,]", "", t)


def validate(brain: Brain, item: dict, res: dict) -> list[dict]:
    from .brain import CONCEPTS_MAX, ELEMENT_KINDS, TEXT_MAX, InvalidInput

    atoms = res.get("atoms") or []
    limit = item.get("max_atoms", ATOMS_PER_SEGMENT)
    if len(atoms) > limit:
        raise InvalidInput(f"この promote の atoms は {limit} 個までです。")
    lo, hi = item["lines"][0][0], item["lines"][-1][0]
    known = {m["id"] for m in item["similar_memories"]}
    body_lines = {n: t for n, t in item["lines"]}
    out = []
    for i, a in enumerate(atoms):
        kind, text = a.get("kind"), " ".join(str(a.get("text") or "").split())
        if kind not in ELEMENT_KINDS:
            raise InvalidInput(f"atoms[{i}].kind は {', '.join(ELEMENT_KINDS)} のいずれかです。")
        if not text or len(text) > TEXT_MAX:
            raise InvalidInput(f"atoms[{i}].text は 1〜{TEXT_MAX} 文字にしてください。")
        der = a.get("derivation")
        if der not in DERIVATIONS:
            raise InvalidInput(f"atoms[{i}].derivation は {', '.join(DERIVATIONS)} のいずれかです。")
        ln = a.get("lines")
        if not (isinstance(ln, list) and len(ln) == 2 and all(isinstance(x, int) for x in ln) and lo <= ln[0] <= ln[1] <= hi):
            raise InvalidInput(f"atoms[{i}].lines は [開始, 終了] で、{lo}〜{hi} の範囲にしてください。")
        quote = "\n".join(body_lines[n] for n in range(ln[0], ln[1] + 1)).strip()
        if not quote:
            raise InvalidInput(f"atoms[{i}].lines の範囲が空行だけです。")
        try:
            conf = float(a.get("confidence", 0.7))
        except (TypeError, ValueError):
            raise InvalidInput(f"atoms[{i}].confidence は 0〜1 の数です。") from None
        if not 0 <= conf <= 1:
            raise InvalidInput(f"atoms[{i}].confidence は 0〜1 の数です。")
        if der == "inferred":
            conf = min(conf, 0.5)  # an inference is never taken as a fact (OUTBRAIN v0.4 §4.3)
        if der == "verbatim" and _norm(text) not in _norm(quote):
            der = "paraphrase"  # claimed word for word but it is not in the original: say so honestly
        for key in ("same_as", "supersedes"):
            if a.get(key) and a[key] not in known:
                raise InvalidInput(f"atoms[{i}].{key} には similar_memories にある id だけを入れてください。")
        concepts = [" ".join(str(x).split())[:40] for x in (a.get("concepts") or []) if str(x).strip()][:CONCEPTS_MAX]
        default_about = {"interview": "interviewee", "client": "client"}.get(item.get("note_type"), "owner")
        about = a.get("about") or default_about
        if about not in ABOUTS:
            raise InvalidInput(f"atoms[{i}].about は {', '.join(ABOUTS)} のいずれかです。")
        subject = " ".join(str(a.get("subject") or "").split())[:40] or None
        if about != "owner" and not subject and item.get("note_type"):
            raise InvalidInput(f"atoms[{i}] は {about} の話なので、subject に誰か（名前か役割）を入れてください。")
        out.append({"kind": kind, "text": text, "derivation": der, "lines": ln, "quote": quote,
                    "confidence": round(conf, 3), "concepts": concepts, "about": about, "subject": subject,
                    "same_as": a.get("same_as"), "supersedes": a.get("supersedes")})
    return out


def apply(brain: Brain, actor: str, item: dict, atoms: list[dict]) -> dict[str, int]:
    """Write validated atoms. Must run inside brain._tx()."""
    from .brain import Element, new_id

    counts = {"new": 0, "reinforced": 0, "superseded": 0}
    src = item["source_id"]
    origin = brain._conn.execute("SELECT origin_file FROM sources WHERE id = ?", (src,)).fetchone()[0]
    for a in atoms:
        if a["same_as"]:
            # Seen again: add evidence. A revision of the same file adds a source but is not a new occurrence.
            prev_origins = {r[0] for r in brain._conn.execute(
                "SELECT s.origin_file FROM node_sources ns JOIN sources s ON s.id = ns.source_id WHERE ns.node_id = ?",
                (a["same_as"],))}
            brain._emit(actor, "node_sourced", {
                "node_id": a["same_as"], "source_id": src, "line_start": a["lines"][0], "line_end": a["lines"][1],
                "quote": a["quote"], "occurrence": origin is None or origin not in prev_origins})
            counts["reinforced"] += 1
            continue
        created = brain._add_elements(
            actor, [Element(a["kind"], a["text"], a["concepts"], IMPORTANCE[item["signal"]])], src,
            promoted_by=item["signal"], extra={"derivation": a["derivation"], "confidence": a["confidence"],
                                               "about": a["about"], **({"subject": a["subject"]} if a["subject"] else {})})
        nid = created[0]["id"]
        brain._emit(actor, "node_sourced", {"node_id": nid, "source_id": src, "line_start": a["lines"][0],
                                            "line_end": a["lines"][1], "quote": a["quote"]})
        counts["new"] += 1
        if a["supersedes"]:
            brain._emit(actor, "node_updated", {"id": a["supersedes"], "status": "superseded"})
            brain._emit(actor, "edge_set", {"src": nid, "dst": a["supersedes"], "kind": "supersedes", "weight": 1.0,
                                            "origin": "sleep"})
            counts["superseded"] += 1
    return counts


def encode_nodes(brain: Brain, limit: int = 500) -> int:
    """Embed cortex memories that have no vector yet (used to find duplicates and to recall by meaning)."""
    from .embed import EmbedUnavailable, pack

    emb = brain.embedder
    if emb is None:
        return 0
    with brain._lock:
        rows = brain._conn.execute(
            "SELECT id, body FROM nodes WHERE status = 'active' AND kind != 'concept' AND body IS NOT NULL"
            " AND id NOT IN (SELECT node_id FROM node_vectors WHERE model = ?) LIMIT ?", (emb.model, limit)).fetchall()
    if not rows:
        return 0
    try:
        vecs = emb.embed([r["body"] for r in rows])
    except EmbedUnavailable:
        return 0
    with brain._lock:
        brain._conn.executemany("INSERT OR REPLACE INTO node_vectors (node_id, model, vec) VALUES (?, ?, ?)",
                                [(r["id"], emb.model, pack(v)) for r, v in zip(rows, vecs)])
    return len(rows)
