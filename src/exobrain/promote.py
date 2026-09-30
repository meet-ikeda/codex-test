"""Sleep A: promote from the hippocampus to the cortex (spec v0.5 §6.2, OUTBRAIN v0.4 §5).

The AI does not decide what is important. A passage is a candidate only when a
signal says so:
- explicit:   the owner's own notes (#remember, memos) and, in AI daily logs, the
              sections "決まったこと", "注意・訂正されたこと" and "オーナーのこだわり・理由"
- repetition: something close to it appears in another original from another day
Anything else stays on the bookshelf.

The AI splits a candidate into atoms and answers with line numbers only; the
quote is cut from the original by this module and must match it. An atom that
says the same as an existing memory adds evidence to it instead of a new memory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

import numpy as np

from .bookshelf import read_body

if TYPE_CHECKING:
    from .brain import Brain

EXPLICIT_KINDS = ("remember_note", "memo", "deposit")  # the owner handed these over on purpose
DAILY_EXPLICIT_SECTIONS = ("決まったこと", "注意・訂正されたこと", "オーナーのこだわり・理由")
ATOMS_PER_SEGMENT = 5
ATOMS_PER_OWNER_SEGMENT = 8  # the owner's own notes are dense and were marked on purpose (2026-09-27)
SIMILAR_SHOWN = 8
REPEAT_SIM = 0.80  # bge-m3 cosine for "the same thing said again" — provisional, check on real logs
IMPORTANCE = {"explicit": 1.0, "demand": 0.8, "repetition": 0.6, "association": 0.4}  # OUTBRAIN v0.4 §7.2 "I"
DERIVATIONS = ("verbatim", "paraphrase", "inferred")
NOTHING = ("特になし", "なし", "")
WIKILINK = re.compile(r"\[\[([^\]\n]+?)\]\]")  # [[A社]] / [[A社|A社様]]: a name marked by whoever wrote the note
KNOWN_CONCEPTS_SHOWN = 20
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
REASON_RULE = ("ルールや決定に、オーナーが言った理由・こだわりが原文にあれば、原子の文に「（理由: …）」として含め、"
               "その行も lines に含める。オーナーの人柄・性格・美学を推測した原子は作らない"
               "（「オーナーは〜を嫌う人だ」ではなく、オーナーが言ったことと、何についての話かを書く）。")
LINK_RULE = ("原文の [[名前]] は、書いた人が印を付けた固有名詞・話題。[[名前|別名]] は「別名」で書かれた「名前」のこと。"
             "「> 」で始まる行はオーナーの言葉そのまま。原子にするときは言葉を変えず derivation=verbatim にする。"
             "concepts は {name, type} で返す。type: person（人）/ organization（会社・組織）/ project（案件）/ "
             "tool（道具・サービス）/ place（場所）/ topic（話題）。known_concepts にある話題と同じものは、"
             "その name をそのまま使う（表記を変えない）。原子の text には [[ ]] を書かない。")
PERSONAL_INFO_RULE = ("電話番号・住所・メールアドレス・口座などの個人情報や、パスワード・鍵は、原子に書かない。")
DEPOSIT_HINT = ("これはオーナーが預けると決めた会話を、その会話の AI がまとめたもの。見出しの種類（手続き・意味・"
                "エピソード）は目安で、中身に合わせて kind を決めてよい。オーナーの仕事の状況や考えも semantic として拾う。")
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
    meta = json.loads(meta_row) if meta_row else {}
    note_type, subject = meta.get("note_type"), meta.get("subject")
    evidence = None
    evidence_source_id = meta.get("conversation_source_id") if s["kind"] == "ai_daily" else None
    if evidence_source_id:
        evidence_row = brain._conn.execute(
            "SELECT path FROM sources WHERE id = ? AND erased = 0", (evidence_source_id,)).fetchone()
        if evidence_row:
            evidence_body = read_body(brain.settings.drive_root / evidence_row[0])
            all_lines = evidence_body.splitlines()
            if len(evidence_body) <= 24_000:
                evidence = {
                    "source_id": evidence_source_id,
                    "lines": [[i, line] for i, line in enumerate(all_lines, 1)],
                }
                all_lines = []
            from .recall import bigrams

            query = bigrams(text)
            scored = sorted(
                ((len(query & bigrams(line)), i) for i, line in enumerate(all_lines, 1) if line.strip()),
                reverse=True,
            )
            chosen: set[int] = set()
            for score, line_no in scored[:20]:
                if score <= 0:
                    break
                chosen.update(range(max(1, line_no - 2), min(len(all_lines), line_no + 2) + 1))
            if not chosen and all_lines:  # heavily paraphrased long logs: keep a bounded start for grounding
                chosen.update(range(1, min(len(all_lines), 80) + 1))
            if all_lines:
                evidence = {
                    "source_id": evidence_source_id,
                    "lines": [[i, all_lines[i - 1]] for i in sorted(chosen)[:120]],
                }
    return {
        "type": "promote", "source_id": seg.source_id, "signal": seg.signal,
        "source": {"title": s["title"], "writer": s["ai_name"] if s["author"] == "ai" else "オーナー",
                   "date": s["created_at"][:10]},
        "lines": numbered, "similar_memories": similar_memories(brain, text),
        "known_concepts": known_concepts(brain, text),
        "instructions": (
            NOTE_TYPE_HINT.get(note_type, "") + (f"（この話の相手: {subject}）" if subject else "")
            + (DEPOSIT_HINT if s["kind"] == "deposit" else OWNER_NOTE_HINT if owner_note and not note_type else "")
            + f"この部分を、長く覚えておく価値のある「記憶の原子」に分ける（最大 {limit} 個。価値がなければ atoms を空に）。"
            "1 原子 = 1 つの決定・事実・ルール・好み・出来事。原子は単独で意味が通る 1 文（300 文字以内）にする。"
            "kind: procedural（やり方・ルール・好み・注意されたこと）/ semantic（事実・決定）/ episode（出来事）。"
            + REASON_RULE +
            "derivation: verbatim（原文のまま）/ paraphrase（意味を変えずに整理）/ inferred（原文に直接はない推論）。"
            "lines: 日報内の根拠行 [開始, 終了]（lines にある番号だけ）。引用は書かない（プログラムが切り出す）。"
            + ("evidence は日報の元になった会話抜粋。各原子について、内容を裏づける連続した原会話の"
               "evidence_lines: [開始, 終了] を必ず返す。裏づけが見つからない原子は作らない。"
               if evidence else "")
            + LINK_RULE + "confidence: 0〜1。concepts: 固有名詞・話題を 8 個まで。"
            "similar_memories と同じ内容なら same_as にその id を入れる（新しく作らない）。"
            "similar_memories の内容を新しい決定が置き換えるなら supersedes にその id を入れる。"
            "about: その記憶が誰についてか（owner=オーナー / client=クライアント / interviewee=取材相手 / other）。"
            "owner 以外なら subject に誰か（会社名・人名・役割）を入れ、text の主語にもする。"
            + PERSONAL_INFO_RULE + "迷ったら統合しない。書かれていないことを足さない。"),
        "max_atoms": limit, **({"note_type": note_type} if note_type else {}),
        **({"evidence": evidence} if evidence else {}),
    }


def known_concepts(brain: Brain, text: str) -> list[dict]:
    """Concepts already in the brain whose name (or other name) shares text with this passage: shown so that
    the same thing keeps the same name."""
    from .recall import bigrams, overlap

    marked = {m.split("|")[0].strip() for m in WIKILINK.findall(text)}
    bg = bigrams(text)
    scored = {}
    rows = brain._conn.execute(
        "SELECT id, label, concept_type, label AS name FROM nodes WHERE kind = 'concept' AND status = 'active'"
        " UNION ALL SELECT n.id, n.label, n.concept_type, a.alias FROM concept_aliases a"
        " JOIN nodes n ON n.id = a.concept_id WHERE n.status = 'active'").fetchall()
    for r in rows:
        s = 1.0 if r["name"] in marked else overlap(bg, bigrams(r["name"]))
        if s >= 0.5 and s > scored.get(r["id"], (0,))[0]:
            scored[r["id"]] = (s, r)
    best = sorted(scored.values(), key=lambda x: -x[0])[:KNOWN_CONCEPTS_SHOWN]
    return [{"name": r["label"], **({"type": r["concept_type"]} if r["concept_type"] else {})} for _, r in best]


def unlink(text: str) -> str:
    """[[A社|A社様]] → A社様, [[A社]] → A社: the words as they read."""
    return WIKILINK.sub(lambda m: (m.group(1).split("|", 1) + [""])[1].strip() or m.group(1).split("|")[0].strip(),
                        text)


def _norm(text: str) -> str:
    """For comparing wording: width, spaces, list and quote marks, [[ ]] and final punctuation do not count."""
    import unicodedata

    t = unicodedata.normalize("NFKC", unlink(text))
    t = re.sub(r"^[\s\-*・>]+", "", t, flags=re.M)
    return re.sub(r"[\s。．.、,]", "", t)


def validate(brain: Brain, item: dict, res: dict) -> list[dict]:
    from .brain import CONCEPTS_MAX, ELEMENT_KINDS, TEXT_MAX, InvalidInput, parse_elements

    atoms = res.get("atoms") or []
    limit = item.get("max_atoms", ATOMS_PER_SEGMENT)
    if len(atoms) > limit:
        raise InvalidInput(f"この promote の atoms は {limit} 個までです。")
    lo, hi = item["lines"][0][0], item["lines"][-1][0]
    known = {m["id"] for m in item["similar_memories"]}
    body_lines = {n: t for n, t in item["lines"]}
    evidence = item.get("evidence")
    evidence_lines = {n: t for n, t in (evidence or {}).get("lines", [])}
    out = []
    for i, a in enumerate(atoms):
        kind, text = a.get("kind"), unlink(" ".join(str(a.get("text") or "").split()))
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
        evidence_range, evidence_quote = None, None
        if evidence:
            evidence_range = a.get("evidence_lines")
            if not (isinstance(evidence_range, list) and len(evidence_range) == 2
                    and all(isinstance(x, int) for x in evidence_range)
                    and evidence_range[0] <= evidence_range[1]
                    and all(n in evidence_lines for n in range(evidence_range[0], evidence_range[1] + 1))):
                raise InvalidInput(f"atoms[{i}].evidence_lines は evidence にある連続行の [開始, 終了] にしてください。")
            evidence_quote = "\n".join(
                evidence_lines[n] for n in range(evidence_range[0], evidence_range[1] + 1)).strip()
            if not evidence_quote:
                raise InvalidInput(f"atoms[{i}].evidence_lines の範囲が空行だけです。")
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
        # The names the writer marked with [[ ]] in these lines come first: they are not left to the AI to notice.
        from .brain import split_concept

        raw_concepts, seen = [], set()
        for c in [*WIKILINK.findall(quote), *(a.get("concepts") or [])]:
            name = split_concept(c.get("name", "") if isinstance(c, dict) else c)[0]
            if name and (name in seen or len(seen) < CONCEPTS_MAX):  # same name again: keeps its type/aliases
                seen.add(name)
                raw_concepts.append(c)
        try:
            el = parse_elements([{"kind": kind, "text": text, "concepts": raw_concepts}])[0]
        except InvalidInput as e:
            raise InvalidInput(f"atoms[{i}]: {e}") from None
        concepts = [c[:40] for c in el.concepts][:CONCEPTS_MAX]
        concept_meta = {c: el.concept_meta[c] for c in concepts if c in el.concept_meta}
        default_about = {"interview": "interviewee", "client": "client"}.get(item.get("note_type"), "owner")
        about = a.get("about") or default_about
        if about not in ABOUTS:
            raise InvalidInput(f"atoms[{i}].about は {', '.join(ABOUTS)} のいずれかです。")
        subject = " ".join(str(a.get("subject") or "").split())[:40] or None
        if about != "owner" and not subject and item.get("note_type"):
            raise InvalidInput(f"atoms[{i}] は {about} の話なので、subject に誰か（名前か役割）を入れてください。")
        out.append({"kind": kind, "text": text, "derivation": der, "lines": ln, "quote": quote,
                    "confidence": round(conf, 3), "concepts": concepts, "concept_meta": concept_meta, "about": about, "subject": subject,
                    "same_as": a.get("same_as"), "supersedes": a.get("supersedes"),
                    "evidence_lines": evidence_range, "evidence_quote": evidence_quote})
    return out


def apply(brain: Brain, actor: str, item: dict, atoms: list[dict]) -> dict[str, int]:
    """Write validated atoms. Must run inside brain._tx()."""
    from .brain import Element, new_id

    counts = {"new": 0, "reinforced": 0, "superseded": 0}
    src = item["source_id"]
    evidence_source = (item.get("evidence") or {}).get("source_id")
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
            if evidence_source and a["evidence_quote"]:
                brain._emit(actor, "node_sourced", {
                    "node_id": a["same_as"], "source_id": evidence_source,
                    "line_start": a["evidence_lines"][0], "line_end": a["evidence_lines"][1],
                    "quote": a["evidence_quote"], "occurrence": False})
            counts["reinforced"] += 1
            continue
        created = brain._add_elements(
            actor, [Element(a["kind"], a["text"], a["concepts"], IMPORTANCE[item["signal"]], a.get("concept_meta") or {})], src,
            promoted_by=item["signal"], extra={"derivation": a["derivation"], "confidence": a["confidence"],
                                               "about": a["about"], **({"subject": a["subject"]} if a["subject"] else {})})
        nid = created[0]["id"]
        brain._emit(actor, "node_sourced", {"node_id": nid, "source_id": src, "line_start": a["lines"][0],
                                            "line_end": a["lines"][1], "quote": a["quote"]})
        if evidence_source and a["evidence_quote"]:
            brain._emit(actor, "node_sourced", {
                "node_id": nid, "source_id": evidence_source,
                "line_start": a["evidence_lines"][0], "line_end": a["evidence_lines"][1],
                "quote": a["evidence_quote"]})
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
