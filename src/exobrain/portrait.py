"""memory.md: what this brain thinks, in one page (2026-10-07).

The cortex shows memories one by one; this reads them all and says, in short, what the brain holds about the
owner: what they are working on, what they care about and why (from the reasons in cases), the rules, and where
the brain is still thin. Claude Code writes it from a digest of the cortex only — no tools, no other memory —
and every statement carries the ids of the memories behind it. Ids that do not exist are flagged, not trusted.

Written after each sleep and on demand from the screen. Kept at ~/.exobrain/memory.md and copied to
Google Drive (exobrain/memory.md) so other AIs can read it too.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .recall import strength_sql

if TYPE_CHECKING:
    from .brain import Brain

FILE = "memory.md"
META = "memory.json"
TIMEOUT_SECONDS = 600
ID = re.compile(r"\[(n_[0-9a-f]+)\]")
KINDS = ("procedural", "semantic", "case", "episode")
DIGEST_MAX_TOKENS = 60_000  # the whole cortex, until it outgrows this; then the weakest memories are left out
NO_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite"
PROMPT = """\
あなたは exobrain（オーナー＝池田さんの外部脳）の中身を、オーナー本人に「この脳は何を思っているか」として
1 ページで見せる役です。下の「大脳皮質の中身」だけを根拠に、memory.md を書いてください。

決まり:
- 主語はオーナー。脳に入っていることだけから書く。一般論や推測で足さない。入っていないことは書かない
- 文の終わりに、根拠にした記憶の id を [n_...] の形で 1〜3 個付ける（下の一覧にある id だけ）
- オーナーの言葉と AI の解釈を混ぜない。複数の記憶から読んだ傾向には、文頭に「（読み）」と付ける
- 感情や性格を決めつけない。口調・言い方の強さ・回数から性格を読まない
- 事例は「そのときの判断」。判断の中身より、繰り返し出てくる「理由」に注目して傾向を書く。場面（scope）があれば
  「〇〇の場面では」と添え、別の場面に広げない
- 仮のルールは「育ちかけ」として分けて書く。未整理のルールは、ルールとしてではなく「以前入ったメモ」として扱う
- 案件名やクライアント名は、脳に入っている名前のまま書いてよい（このファイルはオーナーのものです）
- 全体で 1,500〜2,500 字。見出しは次の順で、名前を変えない。該当がなければ「まだ脳に入っていません」と書く

# この脳が思っていること
## ひとことで
（3 行以内）
## いま取り組んでいること
## 大事にしていること・判断の筋
## 本決まりのルール
## 育ちかけのルール（仮）
## よく出てくる話題
## まだ薄いところ
（記憶が少ない・古い・偏っていると見えるところ。脳の作り直し中ならそれも書く）

出力は memory.md の本文だけ（前置きや説明は書かない）。

--- 大脳皮質の中身（{now} 時点） ---
{digest}
"""


def digest(brain: Brain) -> tuple[str, dict[str, int]]:
    """The cortex as plain lines for the writer: kind, stage, scope, case parts, date, id."""
    c = brain._conn
    parts, counts = [], {}
    titles = {"procedural": "ルール", "semantic": "知識", "case": "事例（そのときの判断）", "episode": "出来事"}
    from .tokens import estimate_tokens

    # Strongest first, so that if the cortex outgrows the budget only the weakest are left out.
    ranked = c.execute(
        "SELECT id FROM nodes WHERE status = 'active' AND kind IN ('procedural', 'semantic', 'case', 'episode')"
        " ORDER BY pinned DESC, importance * " + strength_sql() + " DESC, access_count DESC, created_at DESC").fetchall()
    keep, used = set(), 0
    for (nid,) in ranked:
        cost = estimate_tokens(c.execute("SELECT COALESCE(body, '') || COALESCE(case_json, '') FROM nodes WHERE id = ?",
                                         (nid,)).fetchone()[0]) + 25
        if used + cost > DIGEST_MAX_TOKENS:
            break
        keep.add(nid)
        used += cost
    for kind in KINDS:
        rows = [r for r in c.execute(
            "SELECT id, body, stage, scope, case_json, created_at, about, subject FROM nodes"
            " WHERE status = 'active' AND kind = ? ORDER BY pinned DESC, importance * " + strength_sql() + " DESC,"
            " access_count DESC, created_at DESC", (kind,)).fetchall() if r["id"] in keep]
        counts[kind] = c.execute("SELECT COUNT(*) FROM nodes WHERE status = 'active' AND kind = ?",
                                 (kind,)).fetchone()[0]
        parts.append(f"## {titles[kind]}（{counts[kind]} 件中 {len(rows)} 件）")
        for r in rows:
            tags = []
            if kind == "procedural":
                tags.append({"confirmed": "本決まり", "tentative": "仮"}.get(r["stage"], "未整理"))
            if r["scope"]:
                tags.append(f"場面: {r['scope']}")
            if r["about"] and r["about"] != "owner":
                tags.append(f"{r['subject'] or r['about']} の話")
            line = f"- {r['body']}" + (f"（{' / '.join(tags)}）" if tags else "") + f" {r['created_at'][:10]} [{r['id']}]"
            if r["case_json"]:
                case = json.loads(r["case_json"])
                line += (f"\n  状況: {case.get('situation') or '—'} / 判断: {case.get('decision') or '—'}"
                         f" / 理由: {case.get('reason') or '（言っていない）'} / 反応: {case.get('reaction') or '—'}")
            parts.append(line)
        parts.append("")
    topics = c.execute(
        "SELECT n.label, COUNT(*) AS k FROM edges e JOIN nodes n ON n.id = e.dst JOIN nodes m ON m.id = e.src"
        " WHERE e.kind = 'about' AND n.kind = 'concept' AND m.status = 'active' GROUP BY n.id ORDER BY k DESC LIMIT 30")
    parts.append("## 話題（つながっている記憶の数）")
    parts.append("、".join(f"{r[0]}（{r[1]}）" for r in topics) or "（なし）")
    waiting = c.execute("SELECT COUNT(*) FROM hippocampus WHERE status = 'waiting'").fetchone()[0]
    parts.append(f"\n## 海馬で覚え直しを待っている原文: {waiting} 件")
    return "\n".join(parts), counts


def write(brain: Brain) -> dict[str, Any]:
    """Ask Claude Code for memory.md and keep it. Raises RuntimeError with a message for the owner."""
    from .sleep import find_claude

    exe = find_claude(brain)
    if exe is None:
        raise RuntimeError("Claude Code（claude コマンド）が見つからないため、要約を作れませんでした。")
    from .tokens import estimate_tokens

    text, counts = digest(brain)
    now = datetime.now().astimezone()
    prompt = PROMPT.format(now=f"{now:%Y-%m-%d %H:%M}", digest=text)
    try:
        from .models import model_for

        proc = subprocess.run([exe, "-p", "--model", model_for(brain, "summary"), "--output-format", "json",
                               "--disallowedTools", NO_TOOLS],
                              input=prompt, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                              cwd=str(brain.settings.home), env=os.environ.copy())
    except subprocess.TimeoutExpired:
        raise RuntimeError("時間内に要約が返りませんでした。") from None
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {}
    body = (out.get("result") or "").strip()
    if proc.returncode != 0 or not body or out.get("is_error"):
        raise RuntimeError("要約を作れませんでした: " + (proc.stderr.strip() or body or f"終了コード {proc.returncode}")[:300])
    u = out.get("usage") or {}
    usage = {"model": next(iter(out.get("modelUsage") or {}), None), "digest_tokens": estimate_tokens(text),
             "input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
             + u.get("cache_creation_input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
             "read": sum(1 for line in text.splitlines() if line.startswith("- ")), "total": sum(counts.values())}
    return save(brain, body, counts, now, out.get("total_cost_usd"), usage)


def save(brain: Brain, body: str, counts: dict[str, int], now: datetime, cost: float | None = None,
         usage: dict | None = None) -> dict[str, Any]:
    """Check the cited ids, add the header and footer, and keep the file (and its copy on Drive)."""
    known = {r[0] for r in brain._conn.execute("SELECT id FROM nodes WHERE status = 'active'")}
    cited = ID.findall(body)
    unknown = sorted({i for i in cited if i not in known})
    for i in unknown:
        body = body.replace(f"[{i}]", f"[{i}・脳に見当たらない]")
    total = sum(counts.values())
    head = (f"> {now:%Y-%m-%d %H:%M} に、大脳皮質の記憶 {total} 件（ルール {counts.get('procedural', 0)}・"
            f"知識 {counts.get('semantic', 0)}・事例 {counts.get('case', 0)}・出来事 {counts.get('episode', 0)}）"
            "から AI が読んだ要約です。脳そのものではありません。[n_...] は根拠の記憶です。\n\n")
    text = head + body.strip() + "\n"
    home = brain.settings.home
    (home / FILE).write_text(text, encoding="utf-8")
    meta = {"generated_at": now.isoformat(timespec="seconds"), "counts": counts, "cited": len(set(cited)),
            "unknown_ids": unknown, "cost_usd": cost, "usage": usage or {},
            "events_at": brain._conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]}
    (home / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        (brain.settings.drive_root / FILE).write_text(text, encoding="utf-8")
    except OSError:
        pass  # Drive not mounted: the copy waits for the next time
    return {"markdown": text, **meta}


def read(brain: Brain) -> dict[str, Any]:
    home = brain.settings.home
    if not (home / FILE).exists():
        return {"markdown": None}
    meta = json.loads((home / META).read_text(encoding="utf-8")) if (home / META).exists() else {}
    changed = brain._conn.execute(
        "SELECT COUNT(*) FROM events WHERE id > ? AND type IN ('node_added', 'node_updated', 'node_revised')",
        (meta.get("events_at", 0),)).fetchone()[0]
    return {"markdown": (home / FILE).read_text(encoding="utf-8"), **meta, "changes_since": changed}


def recall_view(brain: Brain) -> dict[str, Any]:
    """What an AI actually receives on "/思い出して": the start of the conversation (start_session's profile),
    and then up to the recall budget each time it recalls (spec v0.8 §8.1)."""
    from .brain import PROFILE_BUDGET, RECALL_BUDGET
    from .tokens import estimate_tokens

    profile = brain.profile(PROFILE_BUDGET)
    rules = sum(1 for line in profile.splitlines() if line.startswith("- "))
    total = brain._conn.execute("SELECT COUNT(*) FROM nodes WHERE status = 'active' AND kind = 'procedural'"
                                " AND (pinned = 1 OR (stage = 'confirmed' AND COALESCE(scope, '') = ''))").fetchone()[0]
    return {"profile": profile, "profile_tokens": estimate_tokens(profile), "profile_budget": PROFILE_BUDGET,
            "rules_shown": rules, "rules_total": total, "recall_budget": RECALL_BUDGET}
