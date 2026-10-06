"""Sleep: rules that grow from cases and fall away when they stop fitting (spec v0.8 §6).

A rule is never made from one judgment. When two or more cases about the same topic, from different days or
different projects, share a reason, the sleeping AI may propose one tentative rule, tied to those cases. The
owner confirms it ("yes") or takes it out ("no") — `Brain.review_rule`. Cases that fit an existing rule
strengthen it; cases that go against it weaken it, and a tentative rule that grows too weak is retired.
A confirmed rule is never retired here: only the owner does that.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from .brain import Brain

MIN_CASES = 2
CASES_SHOWN = 12
RULES_SHOWN = 8
NEW_PER_TOPIC = 1  # one new rule per topic per night (WikiSkill: change one thing at a time, spec v0.8 §6.4)
STRENGTHEN = 0.1
WEAKEN = 0.2
RETIRE_BELOW = 0.2
DERIVED_WEIGHT = 0.6
TENTATIVE_IMPORTANCE = 0.5
INSTRUCTIONS = (
    "同じ話題の事例（オーナーのそのときの判断）と、その話題の今のルールです。"
    "new: 2 件以上の事例に共通する「理由」があり、事例が別の日か別の案件にまたがるときだけ、"
    "仮のルールを 1 つまで提案する（{text, scope, reason, case_ids}）。text は条件つきの 1 文"
    "（例: 「流し読みされる見出しは、短く言い切る」）。案件名を text に入れない（場面で書く）。"
    "scope は当てはまる場面（空ならどの仕事にも）。reason は事例にある理由の言葉から書く（推測で足さない）。"
    "理由のない事例だけからは作らない。判断軸の一覧（「〇〇では△△を確かめる」）や、言い方・口調についてのルールは作らない。"
    "updates: 今のルールに合う事例があれば {rule_id, action: 'strengthen', case_ids}、"
    "合わない（ぶつかる）事例があれば {rule_id, action: 'weaken', case_ids}。"
    "stage が retired のルールはオーナーが外したもの。同じ内容をまた提案しない。"
    "迷ったら何もしない（new も updates も空でよい）。")


def _cases_by_topic(brain: Brain) -> dict[str, list[dict]]:
    rows = brain._conn.execute(
        "SELECT e.dst AS topic, n.id, n.body, n.case_json, n.scope, n.created_at FROM edges e"
        " JOIN nodes n ON n.id = e.src WHERE e.kind = 'about' AND n.kind = 'case' AND n.status = 'active'"
        " ORDER BY n.created_at DESC").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        case = json.loads(r["case_json"]) if r["case_json"] else {}
        out.setdefault(r["topic"], []).append({
            "id": r["id"], "text": r["body"], **{k: case.get(k, "") for k in ("situation", "decision", "reason",
                                                                              "reaction")},
            "when": case.get("when") or r["created_at"][:10], "scope": r["scope"] or ""})
    return out


def _spread(cases: list[dict]) -> bool:
    """Different days or different projects: one sitting is not a pattern."""
    return len({c["when"] for c in cases}) >= 2 or len({c["scope"] for c in cases}) >= 2


def candidates(brain: Brain, handed: set[str], marked) -> Iterator[tuple[str, dict[str, Any]]]:
    for topic, cases in _cases_by_topic(brain).items():
        with_reason = [c for c in cases if c["reason"]]
        if len(with_reason) < MIN_CASES or not _spread(with_reason):
            continue
        key = f"grow:{topic}"
        if key in handed or (marked(brain, "grown", topic) or 0) >= len(cases):
            continue  # nothing new about this topic since it was last looked at
        label = brain._conn.execute("SELECT label FROM nodes WHERE id = ?", (topic,)).fetchone()[0]
        rules = [dict(r) for r in brain._conn.execute(
            "SELECT n.id, n.body AS text, COALESCE(n.stage, '') AS stage, COALESCE(n.scope, '') AS scope FROM edges e"
            " JOIN nodes n ON n.id = e.src WHERE e.dst = ? AND e.kind = 'about' AND n.kind = 'procedural'"
            " AND (n.status = 'active' OR (n.status = 'retired' AND n.stage IS NOT NULL))"
            " ORDER BY n.status = 'active' DESC, n.importance DESC LIMIT ?", (topic, RULES_SHOWN))]
        for r in rules:
            st = brain._conn.execute("SELECT status FROM nodes WHERE id = ?", (r["id"],)).fetchone()[0]
            if st == "retired":
                r["stage"] = "retired"
        yield key, {"type": "grow_rules", "topic": label, "_topic_id": topic, "_case_count": len(cases),
                    "cases": cases[:CASES_SHOWN], "rules": rules, "instructions": INSTRUCTIONS}


def validate(item: dict, res: dict) -> dict[str, Any]:
    from .brain import TEXT_MAX, InvalidInput

    cases = {c["id"]: c for c in item["cases"]}
    rules = {r["id"]: r for r in item["rules"] if r["stage"] != "retired"}
    new = res.get("new") or []
    if isinstance(new, dict):
        new = [new]
    if len(new) > NEW_PER_TOPIC:
        raise InvalidInput(f"grow_rules の new は 1 晩に {NEW_PER_TOPIC} つまでです。")
    plan_new = []
    for i, n in enumerate(new):
        text = " ".join(str(n.get("text") or "").split())
        if not 0 < len(text) <= TEXT_MAX:
            raise InvalidInput(f"new[{i}].text は 1〜{TEXT_MAX} 文字の 1 文にしてください。")
        ids = [x for x in dict.fromkeys(n.get("case_ids") or [])]
        if any(x not in cases for x in ids):
            raise InvalidInput(f"new[{i}].case_ids には、この item の cases にある id だけを入れてください。")
        used = [cases[x] for x in ids if cases[x]["reason"]]
        if len(used) < MIN_CASES or not _spread(used):
            raise InvalidInput(f"new[{i}] は、理由のある事例 {MIN_CASES} 件以上（別の日か別の案件）から作ってください。")
        reason = " ".join(str(n.get("reason") or "").split())[:200]
        if not reason:
            raise InvalidInput(f"new[{i}].reason（事例にある理由）を書いてください。")
        scope = " ".join(str(n.get("scope") or "").split()).strip("[]")[:40]
        plan_new.append({"text": text, "reason": reason, "scope": scope, "case_ids": ids})
    plan_updates = []
    for i, u in enumerate(res.get("updates") or []):
        if u.get("rule_id") not in rules:
            raise InvalidInput(f"updates[{i}].rule_id には、この item の rules にある（外されていない）id を入れてください。")
        if u.get("action") not in ("strengthen", "weaken"):
            raise InvalidInput(f"updates[{i}].action は strengthen か weaken です。")
        ids = [x for x in dict.fromkeys(u.get("case_ids") or []) if x in cases]
        if not ids:
            raise InvalidInput(f"updates[{i}].case_ids に、根拠になる事例の id を入れてください。")
        plan_updates.append({"rule_id": u["rule_id"], "action": u["action"], "case_ids": ids})
    return {"new": plan_new, "updates": plan_updates}


def apply(brain: Brain, actor: str, item: dict, plan: dict) -> dict[str, int]:
    """Write a validated plan. Must run inside brain._tx()."""
    from .brain import make_label, new_id

    counts = {"tentative": 0, "strengthened": 0, "weakened": 0, "retired": 0}
    topic = item["_topic_id"]
    for n in plan["new"]:
        body = n["text"] if n["reason"] in n["text"] else f"{n['text']}（理由: {n['reason']}）"
        nid = new_id("n")
        brain._emit(actor, "node_added", {
            "id": nid, "kind": "procedural", "label": make_label(body), "body": body[:300], "source_id": None,
            "importance": TENTATIVE_IMPORTANCE, "promoted_by": "grown", "derivation": "inferred",
            "stage": "tentative", **({"scope": n["scope"]} if n["scope"] else {})})
        brain._link(actor, nid, topic, "about", 0.5, "sleep")
        for cid in n["case_ids"]:
            brain._link(actor, nid, cid, "derived_from", DERIVED_WEIGHT, "sleep")
        counts["tentative"] += 1
    for u in plan["updates"]:
        rule = brain.node(u["rule_id"])
        if u["action"] == "strengthen":
            brain._emit(actor, "node_updated", {"id": rule["id"],
                                                "importance": round(min(1.0, rule["importance"] + STRENGTHEN), 3)})
            for cid in u["case_ids"]:
                brain._link(actor, rule["id"], cid, "derived_from", DERIVED_WEIGHT, "sleep")
            counts["strengthened"] += 1
        else:
            weaker = round(max(0.0, rule["importance"] - WEAKEN), 3)
            retire = rule["stage"] != "confirmed" and weaker < RETIRE_BELOW  # the owner's rules stay theirs
            brain._emit(actor, "node_updated", {"id": rule["id"], "importance": weaker,
                                                **({"status": "retired"} if retire else {})})
            for cid in u["case_ids"]:
                brain._link(actor, rule["id"], cid, "contradicted_by", DERIVED_WEIGHT, "sleep")
            counts["weakened"] += 1
            counts["retired"] += int(retire)
    brain._emit(actor, "sleep_mark", {"kind": "grown", "key": topic, "value": item["_case_count"]})
    return counts
