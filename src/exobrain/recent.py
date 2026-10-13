"""最近の出来事 (recent.md): the last week in the hippocampus, summed up for "/思い出して" (2026-10-11).

The cortex fills slowly (sleep abstracts with care), so the owner asked for a short-term memory beside it: what
happened this week, read from the originals still waiting in the hippocampus. An AI that recalls gets the cortex
(rules, cases, knowledge) and this page (where each project stands now).

The same discipline as the cortex, so the quality holds:
- Only what screening let in (lines dropped or kept as small talk are left out); the owner's words stay verbatim.
- By project, newest first. When a later day changed an earlier decision, the later one stands and the change
  is written ("10/7 は A → 10/9 に B"). Older days are told shorter, as memory does.
- What was turned down is kept with its reason, so an AI does not offer it again; open threads are kept too.
- Every line names the originals behind it ([src_...]). Ids not in the week, and quotes not found word for word
  in the originals, are flagged by the program, not trusted.
- It never makes rules and is not the owner's instruction: the cortex's rules stand above it.

Rewritten after each sleep by Claude Code (the sleep's model: this page carries the week). When the AI cannot be
asked, the program lays the same sections out mechanically instead, so there is always a page.
Kept at ~/.exobrain/recent.md and copied to Google Drive (exobrain/recent.md).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .bookshelf import read_body

if TYPE_CHECKING:
    from .brain import Brain

FILE = "recent.md"
META = "recent.json"
WINDOW_DAYS = 7
INPUT_MAX_CHARS = 150_000  # about a busy week of daily logs; beyond this the oldest days are cut first
HAND_OVER_TOKENS = 5_000  # what "/思い出して" hands over at most, on top of the rules (the page is ~4,000 characters)
STALE_DAYS = 3
TIMEOUT_SECONDS = 900
SKIPPED = ("drop", "chatter")  # screening: not tied to attention / small talk
ID = re.compile(r"\[(src_[0-9a-f]+)\]")
PROJECT_LINE = re.compile(r'^案件:\s*"?\[\[(.+?)\]\]', re.M)
SECTION_ORDER = ("今の状況", "決まったこと", "ボツになったこと", "オーナーの言葉", "オーナーのこだわり・理由",
                 "前提・条件", "注意・訂正されたこと", "次にやること", "未解決・次に続くこと", "あったこと",
                 "今日の出来事", "工夫・学び", "AIの解釈")
NO_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite"
WEEKDAYS = "月火水木金土日"

PROMPT = """\
あなたは exobrain（オーナー＝池田さんの外部脳）の「最近の出来事」を書く役です。
下の「海馬の原文（この 1 週間）」だけを根拠に、別の AI が会話の最初に読む 1 ページを書いてください。
目的は 2 つ。(1) いま何がどこまで進んでいるかを取り違えないこと。(2) オーナーが何を理由に判断したかを知り、
次の判断を同じ理由で考え直せること。大脳皮質（長期の記憶）がなくても、このページだけで役に立つように書く。

決まり:
- 原文にないことは書かない。推測で補わない。性格や感情を読まない
- 重みの順: 注意・訂正されたこと ＞ オーナーの言葉・こだわり ＞ 決まったこと・ボツ ＞ 今の状況・あったこと ＞ AIの解釈。
  重いものほど落とさない。AI の解釈は「（AIの読み）」と頭に付けて分ける
- 案件名はプログラムがそろえてある（### の後の名前）。その名前で 1 案件 1 節にする。まだ同じものが別の名前で
  残っていたら（「ニュージェック」と「NJ案件」など）1 つにまとめる
- 情報を混ぜない: 1 つの行に 2 つの案件の話を書かない。ある案件の決定や理由を、別の案件の節に書かない。
  同じ案件の中に別の仕事（「仕事: …」）があれば、行頭に「〔仕事名〕」を付けて分ける
- 誰の話かを取り違えない: クライアント・取材相手の言葉と、オーナーの言葉を混ぜない
- 案件ごとに、最後に話した日が新しい順に書く。1 つの案件の中では、日付の新しい内容を正とする。
  前の日の決定が後の日に変わったら、新しいほうだけを書き「（10/7 は〜 → 10/9 に変更）」と添える
- 古い日の話ほど短く。昨日・今日の話は具体的に（数字・名前・条件を落とさない）
- 判断は「判断 ← 理由」の形で書く。理由はオーナーが言ったものだけ。言っていなければ「（理由は言っていない）」。
  オーナーの反応（喜んだ・差し戻した）が原文にあれば添える
- 「ボツ」は理由と一緒に残す（別の AI が同じ案をまた出さないため）
- 「次にやること」「まだ決まっていないこと」は、後の日に片づいていれば書かない
- オーナーの言葉は言い換えずに、行頭を「> 」にして原文どおりに引く（1 案件 3 つまで。理由やこだわりを言ったものを優先）
- 1 回しか出ていない判断を、いつもの決まりのように書かない（「この案件では」と場面を付ける）
- AI が提案・説明しただけで、オーナーが決めていないことは「判断」に書かない（書くなら「（AIの提案・未決）」）
- 「何度も出た理由」には、別の日の 2 つ以上の原文に出た理由だけを書き、その原文の id を 2 つ以上付ける。
  ルールではなく「この 1 週間の傾向」として書く。該当がなければ見出しごと省く
- 各行の終わりに、根拠の原文の id を [src_...] の形で 1〜3 個付ける（下にある id だけ）
- 全体で 4,000 字以内（厳守。超えた分は会話に渡らず捨てられる）。詳しく書く案件は、最近・重いものから 6 件まで。
  残りの案件は「そのほかの案件」に 1 行ずつ（案件名・最後に話した日・いまどこまで）
- 見出しは次のとおり。該当がなければその見出しごと省く

# 最近の出来事（{start}〜{end}）
## この 1 週間
（3 行以内。何に時間を使ったか）
## 注意・訂正されたこと
（オーナーに直された・注意されたこと。これから守ること。いちばん大事）
## 案件ごと
### 案件名（最後に話した日）
- いまどこまで:
- 判断 ← 理由:
- ボツ ← 理由:
- 次にやること:
- オーナーの言葉:
## そのほかの案件
## 何度も出た理由（この 1 週間の傾向。ルールではない）
## 何日も続いている話題
## まだ決まっていないこと

出力は本文だけ（前置きや説明は書かない）。

--- 何日も出てきた名前（プログラムが数えたもの: 名前・出た日数） ---
{recurring}

--- 海馬の原文（この 1 週間。{now} 時点） ---
{digest}
"""


def _day_of(row) -> str:
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    if meta.get("entry_date"):
        return meta["entry_date"]
    return datetime.fromisoformat(row["created_at"]).astimezone().date().isoformat()


def _project_of(raw: str, title: str) -> str:
    m = PROJECT_LINE.search(raw)
    if m:
        return m.group(1).split("|")[0].replace("[[", "").replace("]]", "").strip()
    parts = [p.strip() for p in title.split("·")]
    return parts[1] if len(parts) >= 3 else title


def gather(brain: Brain, now: datetime | None = None) -> list[dict[str, Any]]:
    """The originals of the last week still in the hippocampus (or gone on to the cortex this week), with what
    screening kept of each, line by line."""
    from .screen import _bullets, labels_of

    now = (now or datetime.now()).astimezone()
    first = (now.date() - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    rows = brain._conn.execute(
        "SELECT s.id, s.kind, s.title, s.path, s.created_at, s.meta_json, s.ai_name, s.author FROM hippocampus h"
        " JOIN sources s ON s.id = h.source_id WHERE h.status IN ('arrived', 'waiting', 'promoted')"
        " AND s.erased = 0 AND s.kind != 'dream'").fetchall()
    items = []
    for r in rows:
        day = _day_of(r)
        if not first <= day <= now.date().isoformat():
            continue
        path = brain.settings.drive_root / r["path"]
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        body = read_body(path)
        lines: list[tuple[str, str]] = []
        if r["kind"] == "ai_daily":
            labels = labels_of(brain, r["id"])
            for heading, n, text in _bullets(body):
                label = labels.get(n) if labels is not None else None
                if label in SKIPPED:
                    continue
                lines.append((heading, text + ("（前にも出た話）" if label == "repeat" else "")))
        else:  # handed over on purpose (deposits, notes): all of it, but not without end
            lines = [("本文", body.strip()[:3000])]
        if lines:
            name = _project_of(raw, r["title"])
            items.append({"source_id": r["id"], "day": day, "kind": r["kind"], "title": r["title"],
                          "project": name, "job": name,
                          "writer": r["ai_name"] if r["author"] == "ai" else "オーナー", "lines": lines})
    names = canonical_projects(brain, [it["project"] for it in items])
    for it in items:
        it["project"] = names[it["project"]]
    return items


BRACKETS = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]?")
TRAILING = re.compile(r"[のをがとにでは・\s]+$")


def _bare(name: str) -> str:
    return TRAILING.sub("", BRACKETS.sub("", name)).strip()


def canonical_projects(brain: Brain, names: list[str]) -> dict[str, str]:
    """One name per project before the AI sees the week (2026-10-13): logs name the same work differently
    ("exobrain Mac セットアップ", "exobrainの記憶の仕組み開発"), and left to the summary they split or blur.
    A name known to the cortex as another's alias takes the concept's name; a name that begins with another
    name in the week joins it ("ニュージェック社員インタビュー" → "ニュージェック"); two names sharing a long
    beginning join under it ("リアルタイム音声会話アプリを開発" / "…の開発"). The original name stays as the job."""
    from .recall import normalize

    bare = {n: _bare(n) or n for n in set(names)}
    alias = {r[0]: r[1] for r in brain._conn.execute(
        "SELECT a.norm, n.label FROM concept_aliases a JOIN nodes n ON n.id = a.concept_id")}
    for n, b in bare.items():
        bare[n] = alias.get(normalize(b), b)
    bases = sorted(set(bare.values()), key=len)
    out = {}
    for n, b in bare.items():
        base = next((x for x in bases if x != b and len(x) >= 2 and b.startswith(x)), None)
        if base is None:  # a long shared beginning with another name
            for x in bases:
                if x == b:
                    continue
                k = 0
                while k < min(len(x), len(b)) and x[k] == b[k]:
                    k += 1
                if k >= 6:
                    base = _bare(b[:k]) or b[:k]
                    break
        out[n] = base or b
    return out


def _md_day(day: str) -> str:
    d = datetime.fromisoformat(day)
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


def by_project(items: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["project"], []).append(it)
    for g in groups.values():
        g.sort(key=lambda it: (it["day"], it["source_id"]))
    return sorted(groups.items(), key=lambda kv: kv[1][-1]["day"], reverse=True)


WIKILINK = re.compile(r"\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]")


def recurring(items: list[dict], min_days: int = 2) -> list[tuple[str, int]]:
    """Names marked [[...]] that came up on two days or more this week: repetition is a sign of weight."""
    days: dict[str, set[str]] = {}
    for it in items:
        for _, text in it["lines"]:
            for name in WIKILINK.findall(text):
                days.setdefault(name.strip(), set()).add(it["day"])
    return sorted(((n, len(d)) for n, d in days.items() if len(d) >= min_days), key=lambda x: (-x[1], x[0]))[:30]


def digest(items: list[dict]) -> str:
    """The week as plain text for the writer: by project, oldest day first inside each (so later days read as later).
    When the week is too long, the oldest originals are cut first."""
    blocks: list[tuple[str, str]] = []  # (day, text)
    for project, group in by_project(items):
        for it in group:
            sections: dict[str, list[str]] = {}
            for heading, text in it["lines"]:
                sections.setdefault(heading, []).append(text)
            order = [h for h in SECTION_ORDER if h in sections] + [h for h in sections if h not in SECTION_ORDER]
            job = f" · 仕事: {it['job']}" if it.get("job") and it["job"] != project else ""
            text = f"### {project}{job} · {_md_day(it['day'])} · {it['writer']} · {it['title']} [{it['source_id']}]\n" + \
                "\n".join(f"{h}:\n" + "\n".join(f"- {x}" for x in sections[h]) for h in order)
            blocks.append((it["day"], text))
    keep = sorted(range(len(blocks)), key=lambda i: blocks[i][0], reverse=True)
    used, chosen = 0, set()
    for i in keep:
        if used + len(blocks[i][1]) > INPUT_MAX_CHARS:
            break
        chosen.add(i)
        used += len(blocks[i][1])
    return "\n\n".join(b[1] for i, b in enumerate(blocks) if i in chosen)


def mechanical(items: list[dict]) -> str:
    """The same sections laid out by the program, when the AI cannot be asked: latest state, decisions,
    turned down, open threads and the owner's words, per project, newest first."""
    out = []
    fixes = [(t, it) for it in sorted(items, key=lambda x: x["day"], reverse=True)
             for h, t in it["lines"] if h == "注意・訂正されたこと"]
    if fixes:
        out.append("## 注意・訂正されたこと")
        out += [f"- {t}（{_md_day(it['day'])}） [{it['source_id']}]" for t, it in fixes[:10]]
        out.append("")
    out.append("## 案件ごと")
    for project, group in by_project(items):
        last = group[-1]
        out.append(f"### {project}（最後に話した日: {_md_day(last['day'])}）")
        state = [t for h, t in last["lines"] if h == "今の状況"]
        if state:
            out.append(f"- いまどこまで: {' / '.join(state[:3])} [{last['source_id']}]")
        for label, heads in (("決まったこと", ("決まったこと",)), ("ボツ", ("ボツになったこと",)),
                             ("次にやること", ("次にやること", "未解決・次に続くこと"))):
            seen = set()
            for it in reversed(group if label != "次にやること" else [last]):
                for h, t in it["lines"]:
                    if h in heads and t not in seen and len(seen) < 5:
                        seen.add(t)
                        out.append(f"- {label}: {t}（{_md_day(it['day'])}） [{it['source_id']}]")
        quotes = [(t, it) for it in reversed(group) for h, t in it["lines"]
                  if h in ("オーナーのこだわり・理由", "オーナーの言葉")][:3]
        for t, it in quotes:
            out.append(f"> {t.lstrip('> ').strip()} [{it['source_id']}]")
        out.append("")
    return "\n".join(out).strip()


TAIL_HEADING = "## 直近の原文（要約していない）"
TAIL_SECTIONS = ("注意・訂正されたこと", "オーナーの言葉", "オーナーのこだわり・理由", "決まったこと", "ボツになったこと",
                 "次にやること", "未解決・次に続くこと")
TAIL_LINES = 40
TAIL_TOKENS = 1_500


def tail(items: list[dict]) -> str:
    """The last two days' weightiest lines, word for word (2026-10-13, after DeepSeek Harness's compaction: the old
    part is summarised, the newest part is kept as it was, so numbers and wording are not rounded off)."""
    days = sorted({it["day"] for it in items})[-2:]
    out = []
    for it in sorted((it for it in items if it["day"] in days), key=lambda it: it["day"], reverse=True):
        for heading, text in it["lines"]:
            if heading in TAIL_SECTIONS and len(out) < TAIL_LINES:
                out.append(f"- {_md_day(it['day'])} {it['project']}・{heading}: {text[:200]} [{it['source_id']}]")
    return (TAIL_HEADING + "\n" + "\n".join(out)) if out else ""


def write(brain: Brain, now: datetime | None = None, use_ai: bool = True) -> dict[str, Any]:
    """Write recent.md. Falls back to the mechanical page when the AI is not there or fails."""
    from .sleep import find_claude
    from .tokens import estimate_tokens

    now = (now or datetime.now()).astimezone()
    items = gather(brain, now)
    start = now.date() - timedelta(days=WINDOW_DAYS - 1)
    span = f"{start.month}/{start.day}〜{now.month}/{now.day}"
    if not items:
        return save(brain, f"# 最近の出来事（{span}）\n\nこの 1 週間、海馬に入った出来事はありません。", items, now,
                    how="none")
    text = digest(items)
    exe = find_claude(brain) if use_ai else None
    error = None
    if exe is not None:
        from .models import model_for

        prompt = PROMPT.format(start=f"{start.month}/{start.day}", end=f"{now.month}/{now.day}",
                               now=f"{now:%Y-%m-%d %H:%M}", digest=text,
                               recurring="、".join(f"{n}（{k} 日）" for n, k in recurring(items)) or "（なし）")
        try:
            proc = subprocess.run([exe, "-p", "--model", model_for(brain, "recent"), "--output-format", "json",
                                   "--disallowedTools", NO_TOOLS], input=prompt, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, cwd=str(brain.settings.home), env=os.environ.copy())
            try:
                out = json.loads(proc.stdout)
            except ValueError:
                out = {}
            body = (out.get("result") or "").strip()
            if proc.returncode == 0 and body and not out.get("is_error") and len(body) >= len(text):
                error = "要約が元の原文より短くなりませんでした"  # a summary that does not shrink is not used
            elif proc.returncode == 0 and body and not out.get("is_error"):
                u = out.get("usage") or {}
                usage = {"model": next(iter(out.get("modelUsage") or {}), None), "digest_tokens": estimate_tokens(text),
                         "input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                         + u.get("cache_creation_input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
                return save(brain, body, items, now, how="ai", source_text=text, cost=out.get("total_cost_usd"),
                            usage=usage)
            else:
                error = (proc.stderr.strip() or body or f"終了コード {proc.returncode}")[:300]
        except subprocess.TimeoutExpired:
            error = "時間内に返りませんでした"
    body = f"# 最近の出来事（{span}）\n\n" + mechanical(items)
    return save(brain, body, items, now, how="mechanical", source_text=text, error=error)


def _check(body: str, ids: set[str] | dict[str, str], source_text: str) -> tuple[str, list[str], int]:
    """Flag ids that are not this week's originals, owner quotes not found word for word in them, and "reasons
    that came up again and again" resting on fewer than two days. `ids` may map each id to its day."""
    unknown = sorted({i for i in ID.findall(body) if i not in ids})
    for i in unknown:
        body = body.replace(f"[{i}]", f"[{i}・見当たらない]")
    flat = re.sub(r"\s+", "", source_text)
    bad = 0
    lines = []
    section = ""
    for line in body.splitlines():
        if line.startswith("## "):
            section = line
        if section.startswith("## 何度も出た理由") and line.lstrip().startswith("- ") \
                and len({ids[i] if isinstance(ids, dict) else i for i in ID.findall(line) if i in ids}) < 2:
            line += "（※別の日の根拠が 2 つありません。傾向とは言えません）"
            bad += 1
        if line.startswith(">"):
            quote = re.sub(r"\s*\[src_[^\]]+\]", "", line.lstrip("> ").strip()).strip()
            quote = re.sub(r"（[^（）]*）$", "", quote).strip().strip("「」")
            if quote and re.sub(r"\s+", "", quote) not in flat:
                line += "（※原文と一字一句は一致しません）"
                bad += 1
        lines.append(line)
    return "\n".join(lines), unknown, bad


def save(brain: Brain, body: str, items: list[dict], now: datetime, how: str, source_text: str = "",
         cost: float | None = None, usage: dict | None = None, error: str | None = None) -> dict[str, Any]:
    ids = {it["source_id"]: it["day"] for it in items}
    body, unknown, bad_quotes = _check(body, ids, source_text) if source_text else (body, [], 0)
    if items:
        body = body.rstrip() + "\n\n" + tail(items)
    days = sorted({it["day"] for it in items})
    how_ja = {"ai": "AI が要約したもの", "mechanical": "AI に頼めなかったため、プログラムが項目を並べたもの",
              "none": "出来事がなかったもの"}[how]
    head = (f"> {now:%Y-%m-%d %H:%M} に、海馬にあるこの 1 週間の原文 {len(items)} 件"
            + (f"（{_md_day(days[0])}〜{_md_day(days[-1])}）" if days else "")
            + f"から、{how_ja}です。まだ大脳皮質に整理される前の、短期の記憶です。[src_...] は根拠の原文です。\n\n")
    text = head + body.strip() + "\n"
    home = brain.settings.home
    (home / FILE).write_text(text, encoding="utf-8")
    meta = {"generated_at": now.isoformat(timespec="seconds"), "how": how, "sources": len(items),
            "projects": len({it["project"] for it in items}), "unknown_ids": unknown, "bad_quotes": bad_quotes,
            "cost_usd": cost, "usage": usage or {}, "error": error}
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
    return {"markdown": (home / FILE).read_text(encoding="utf-8"), **meta}


def hand_over(brain: Brain, budget: int = HAND_OVER_TOKENS) -> str | None:
    """What "/思い出して" gives an AI next to the rules: the page, cut at a line within the budget, and said to be
    old when it is."""
    from .tokens import estimate_tokens

    r = read(brain)
    if not r.get("markdown"):
        return None
    summary, _, rest = r["markdown"].partition(TAIL_HEADING)
    kept_tail, tail_used = [], 0
    for line in ((TAIL_HEADING + rest).splitlines() if rest else []):
        cost = estimate_tokens(line) + 1
        if tail_used + cost > TAIL_TOKENS:
            break
        kept_tail.append(line)
        tail_used += cost
    lines, used = [], tail_used
    for line in summary.rstrip().splitlines():
        cost = estimate_tokens(line) + 1
        if used + cost > budget:
            lines.append("（ここから先は量の都合で省きました。必要なら recall で海馬を探してください）")
            break
        lines.append(line)
        used += cost
    lines += ["", *kept_tail] if kept_tail else []
    lines.append("（[src_...] の原文は open_source で読めます。判断の前に細部を確かめたいときに使う）")
    text = "\n".join(lines)
    at = r.get("generated_at")
    if at and datetime.now().astimezone() - datetime.fromisoformat(at) > timedelta(days=STALE_DAYS):
        text = f"（注意: これは {datetime.fromisoformat(at):%m/%d} に作ったもので、古くなっています）\n" + text
    return text
