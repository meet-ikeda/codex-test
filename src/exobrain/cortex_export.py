"""A read-only copy of the cortex on Google Drive (spec v0.5 §4.1, §11).

The brain's truth is the event log. These Markdown files are a copy, rewritten
after every sleep, so that AIs on a phone (through their Google Drive
connectors) and the owner in Obsidian can read what the brain remembers.
Editing them changes nothing in the brain.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .recall import strength_sql

if TYPE_CHECKING:
    from .brain import Brain

CORTEX_DIR = "大脳皮質"
FOLDERS = {"procedural": "手続き記憶", "semantic": "意味記憶", "episode": "エピソード記憶"}
KIND_JA = {"procedural": "ルール・やり方", "semantic": "事実・決定", "episode": "出来事・事例", "case": "事例"}
FOLDER_OF = {"case": "episode"}  # a case is an episodic memory (spec v0.8 §5.1)
STAGE_JA = {"confirmed": "本決まり", "tentative": "仮（オーナーの確認待ち）", None: "未整理（以前の入れ方で入ったもの）"}
PROMOTED_JA = {"explicit": "明示", "demand": "需要（前にも言った）", "repetition": "反復", "association": "連想",
               "reconsolidation": "記憶を書き換えた経緯", "summarized": "AI の要約（日報・預け入れ）",
               "grown": "事例から育った",
               None: "（v0.1 で記憶）"}
ABOUT_JA = {"owner": "オーナー", "client": "クライアント", "interviewee": "取材相手", "other": "その他"}
NOTICE = "> これは exobrain の大脳皮質の**写し**です。書き換えても脳は変わりません。直すときは、AI に「前にも言ったよね」と伝えるか、exobrain の画面を使ってください。"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f#^\[\]]+')


def _name(label: str, node_id: str) -> str:
    return f"{_UNSAFE.sub('_', label)[:40].strip() or 'memory'}__{node_id[-8:]}.md"


def export(brain: Brain) -> dict[str, int]:
    root = brain.settings.drive_root / CORTEX_DIR
    tmp = brain.settings.drive_root / f".{CORTEX_DIR}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    c = brain._conn
    nodes = c.execute(
        "SELECT * FROM nodes WHERE status = 'active' AND kind IN ('procedural', 'semantic', 'episode', 'case')"
        " ORDER BY pinned DESC, importance * " + strength_sql() + " DESC, created_at").fetchall()
    counts = {k: 0 for k in FOLDERS}
    index = ["# 大脳皮質（写し）", "", NOTICE, "",
             f"書き出した日時: {datetime.now().astimezone():%Y-%m-%d %H:%M}", ""]
    for kind, folder in FOLDERS.items():
        (tmp / folder).mkdir(parents=True, exist_ok=True)
        group = [n for n in nodes if FOLDER_OF.get(n["kind"], n["kind"]) == kind]
        index += [f"## {folder}（{KIND_JA[kind]}）{len(group)} 件", ""]
        for n in group:
            fname = _name(n["label"], n["id"])
            (tmp / folder / fname).write_text(_note(brain, n), encoding="utf-8")
            mark = "📌 " if n["pinned"] else ""
            tag = ("（仮）" if n["stage"] == "tentative" else "（未整理）" if not n["stage"] else "") \
                if n["kind"] == "procedural" else "（事例）" if n["kind"] == "case" else ""
            where = f"（{n['scope']} の中だけ）" if n["scope"] else ""
            index.append(f"- {mark}[[{folder}/{fname[:-3]}|{n['body']}]]{tag}{where}")
            counts[kind] += 1
        index.append("")
    (tmp / "大脳皮質.md").write_text("\n".join(index), encoding="utf-8")
    if root.exists():
        shutil.rmtree(root)
    tmp.rename(root)
    return counts


def _note(brain: Brain, n) -> str:
    c = brain._conn
    lines = ["---", f"id: {n['id']}", f"kind: {n['kind']}", f"promoted_by: {n['promoted_by'] or ''}",
             f"derivation: {n['derivation'] or ''}", f"confidence: {n['confidence'] if n['confidence'] is not None else ''}",
             f"importance: {round(n['importance'], 3)}", f"occurrences: {n['occurrences']}", f"goods: {n['goods']}",
             f"corrections: {n['corrections']}", f"created_at: {n['created_at']}", "---", "",
             f"# {n['body']}", "", NOTICE, "",
             f"- 種類: {KIND_JA[n['kind']]}",
             f"- 誰の話か: {ABOUT_JA.get(n['about'] or 'owner', n['about'])}" + (f"（{n['subject']}）" if n['subject'] else ""),
             f"- 大脳皮質に入った理由: {PROMOTED_JA.get(n['promoted_by'], n['promoted_by'])}",
             *([f"- ルールの段階: {STAGE_JA.get(n['stage'], n['stage'])}"] if n["kind"] == "procedural" else []),
             f"- 当てはまる範囲: {n['scope'] or 'どの仕事にも'}",
             f"- 再登場: {n['occurrences']} 回 / 褒められた: {n['goods']} 回 / 注意された: {n['corrections']} 回", ""]
    if n["case_json"]:
        case = json.loads(n["case_json"])
        lines += ["## 事例", "", f"- 状況: {case.get('situation') or '—'}", f"- 判断: {case.get('decision') or '—'}",
                  f"- 理由: {case.get('reason') or '（オーナーは言っていない）'}",
                  f"- 反応: {case.get('reaction') or '—'}", f"- 時期: {case.get('when') or '—'}", ""]
    quotes = c.execute(
        "SELECT ns.quote, ns.line_start, ns.line_end, s.title, s.path, s.created_at, s.author, s.ai_name"
        " FROM node_sources ns JOIN sources s ON s.id = ns.source_id WHERE ns.node_id = ? ORDER BY ns.at", (n["id"],))
    rows = quotes.fetchall()
    if rows:
        lines += ["## 根拠（原文からの引用）", ""]
        for q in rows:
            who = q["ai_name"] if q["author"] == "ai" else "オーナー"
            where = f"{q['line_start']}〜{q['line_end']}行" if q["line_start"] else ""
            lines.append(f"- {q['created_at'][:10]} · {who} · {q['title']} {where}")
            lines += [f"  > {l}" for l in q["quote"].splitlines()]
        lines.append("")
    revs = c.execute("SELECT at, old_body, new_body, reason, actor FROM revisions WHERE node_id = ? ORDER BY at",
                     (n["id"],)).fetchall()
    if revs:
        lines += ["## 書き換えの履歴（再固定化）", ""]
        for r in revs:
            who = r["actor"][3:] if r["actor"].startswith("ai:") else r["actor"]
            lines += [f"- {r['at'][:10]} · {who}", f"  - 前: {r['old_body']}", f"  - 後: {r['new_body']}",
                      f"  - 理由: {r['reason']}"]
        lines.append("")
    return "\n".join(lines)


def cortex_root(brain: Brain) -> Path:
    return brain.settings.drive_root / CORTEX_DIR
