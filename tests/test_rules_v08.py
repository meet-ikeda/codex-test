"""Spec v0.8: cases instead of one-off rules, scope, rules that grow from cases, the owner's yes/no,
the rebuild from the bookshelf, and handing confirmed rules to AGENTS.md / CLAUDE.md."""

import pytest

from exobrain import daily, grow, inbox, promote, repromote, rules_export, sleep
from exobrain.brain import Element, InvalidInput


def _item_for(brain, text_has, kind="memo"):
    """The promote item for the passage containing `text_has`."""
    from exobrain import screen

    screen.run(brain)
    for seg, text in promote.candidates(brain, set()):
        if text_has in text:
            return promote.make_item(brain, seg, text)
    raise AssertionError(f"no candidate with {text_has!r}")


def _line(item, text_has):
    return next(n for n, t in item["lines"] if text_has in t)


def test_a_one_off_judgment_must_be_a_case_and_a_general_rule_is_confirmed(brain):
    inbox.add_memo(brain, "メモ", "- 見出しは体言止めにした\n- 報告はいつも結論から書く\n")
    item = _item_for(brain, "体言止め")
    one = _line(item, "体言止め")
    with pytest.raises(InvalidInput, match="case"):
        promote.validate(brain, item, {"atoms": [
            {"kind": "procedural", "text": "見出しは体言止めにする", "derivation": "paraphrase", "lines": [one, one]}]})
    general = _line(item, "いつも")
    atoms = promote.validate(brain, item, {"atoms": [
        {"kind": "case", "text": "見出しを体言止めにした", "derivation": "paraphrase", "lines": [one, one],
         "case": {"situation": "LP の見出し", "decision": "体言止めにした", "reason": "", "reaction": ""}},
        {"kind": "procedural", "text": "報告はいつも結論から書く", "derivation": "verbatim",
         "lines": [general, general]}]})
    with brain._tx():
        promote.apply(brain, "sleep", item, atoms)
    rule = brain._conn.execute("SELECT stage FROM nodes WHERE body = '報告はいつも結論から書く'").fetchone()
    case = brain._conn.execute("SELECT case_json FROM nodes WHERE kind = 'case'").fetchone()
    assert rule["stage"] == "confirmed" and '"decision":"体言止めにした"' in case["case_json"]


def test_signals_owner_words_are_explicit_summaries_are_one_step_weaker(brain, session):
    brain.submit_daily_log(session, "相談", ["打ち合わせをした"], [], [], ["写真は社内で撮る"], [],
                           reasons=["> 写真は自分たちで撮りたい"])
    signals = {}
    from exobrain import screen

    screen.run(brain)
    for seg, text in promote.candidates(brain, set()):
        signals[text.strip().splitlines()[0]] = seg.signal
    assert signals.get("- > 写真は自分たちで撮りたい") == "explicit"
    assert signals.get("- 写真は社内で撮る") == "summarized"


def test_a_case_from_a_project_log_belongs_to_that_project(brain):
    fields = {"source": "codex", "ai_provider": "openai", "ai_product": "Codex", "ai_model": "t",
              "generated_at": "2026-10-07T10:00:00+09:00", "thread_id": "P1", "entry_date": "2026-10-07",
              "period_start": "2026-10-07T09:00:00+09:00", "period_end": "2026-10-07T10:00:00+09:00",
              "previous_cursor": "", "cursor": "c1"}
    inbox.file_daily(brain, daily.render(fields, "ネーミング", {"decisions": ["案名は短くした"]}, "テスト案件"))
    item = _item_for(brain, "案名")
    assert item["project"] == "テスト案件"
    n = _line(item, "案名")
    atoms = promote.validate(brain, item, {"atoms": [
        {"kind": "case", "text": "案名を短くした", "derivation": "paraphrase", "lines": [n, n],
         "case": {"situation": "ネーミング", "decision": "短くした"}}]})
    assert atoms[0]["scope"] == "テスト案件" and atoms[0]["case"]["when"] == "2026-10-07"


def _case(brain, text, reason, when, scope="", topic="見出し"):
    case = {"situation": "LP", "decision": text, "reason": reason, "reaction": "", "when": when}
    with brain._tx():
        return brain._add_elements("sleep", [Element("case", text, [topic], 0.6)], None, promoted_by="summarized",
                                   extra={"case": case, **({"scope": scope} if scope else {})})[0]["id"]


def test_rules_grow_from_cases_and_wait_for_the_owner(brain, session):
    a = _case(brain, "見出しを言い切りにした", "流し読みで目を止めたい", "2026-10-01", "案件A")
    b = _case(brain, "見出しを短くした", "流し読みで目を止めたい", "2026-10-04", "案件B")
    items = list(grow.candidates(brain, set(), sleep._marked))
    assert len(items) == 1
    key, item = items[0]
    with pytest.raises(InvalidInput):  # one case is not a pattern
        grow.validate(item, {"new": [{"text": "流し読みされる見出しは言い切る", "reason": "目を止めたい",
                                      "case_ids": [a]}]})
    plan = grow.validate(item, {"new": [{"text": "流し読みされる見出しは、短く言い切る", "reason": "目を止めたい",
                                         "case_ids": [a, b]}]})
    with brain._tx():
        counts = grow.apply(brain, "sleep", item, plan)
    assert counts["tentative"] == 1
    rule = brain._conn.execute("SELECT * FROM nodes WHERE kind = 'procedural'").fetchone()
    assert rule["stage"] == "tentative"
    assert {e["dst"] for e in brain.edges_of(rule["id"]) if e["kind"] == "derived_from"} == {a, b}
    assert "言い切る" not in brain.start_session("Codex")["profile"]  # not handed out before the owner says so
    assert list(grow.candidates(brain, set(), sleep._marked)) == []  # nothing new about the topic
    assert brain.rules_to_review()[0]["id"] == rule["id"]

    brain.review_rule(rule["id"], "yes")
    assert "言い切る" in brain.start_session("Codex")["profile"]


def test_cases_that_do_not_fit_retire_a_tentative_rule_but_never_a_confirmed_one(brain):
    a = _case(brain, "見出しを言い切りにした", "目を止めたい", "2026-10-01")
    b = _case(brain, "見出しを短くした", "目を止めたい", "2026-10-02")
    _, item = next(grow.candidates(brain, set(), sleep._marked))
    with brain._tx():
        grow.apply(brain, "sleep", item, grow.validate(item, {"new": [
            {"text": "見出しは言い切る", "reason": "目を止めたい", "case_ids": [a, b]}]}))
    rule = brain._conn.execute("SELECT id FROM nodes WHERE kind = 'procedural'").fetchone()[0]
    c = _case(brain, "見出しを問いかけにした", "読み手に考えてほしい", "2026-10-05")
    _, item = next(grow.candidates(brain, set(), sleep._marked))
    for _ in range(2):
        with brain._tx():
            grow.apply(brain, "sleep", item, grow.validate(item, {"updates": [
                {"rule_id": rule, "action": "weaken", "case_ids": [c]}]}))
    assert brain.node(rule)["status"] == "retired"

    confirmed = brain.remember_explicit(brain.start_session("x")["session_id"], "報告はいつも結論から", "procedural",
                                        ["見出し"])["node_id"]
    _, item = next(grow.candidates(brain, set(), lambda *a: None))  # look again even with no new case
    assert any(r["id"] == rule and r["stage"] == "retired" for r in item["rules"])  # shown so it is not re-proposed
    for _ in range(5):
        with brain._tx():
            grow.apply(brain, "sleep", item, {"new": [], "updates": [
                {"rule_id": confirmed, "action": "weaken", "case_ids": [c]}]})
    assert brain.node(confirmed)["status"] == "active"


def test_the_owner_can_say_no(brain):
    a = _case(brain, "見出しを言い切りにした", "目を止めたい", "2026-10-01")
    b = _case(brain, "見出しを短くした", "目を止めたい", "2026-10-02")
    _, item = next(grow.candidates(brain, set(), sleep._marked))
    with brain._tx():
        grow.apply(brain, "sleep", item, grow.validate(item, {"new": [
            {"text": "見出しは言い切る", "reason": "目を止めたい", "case_ids": [a, b]}]}))
    rule = brain.rules_to_review()[0]["id"]
    out = brain.review_rule(rule, "no")
    assert brain.node(rule)["status"] == "retired" and "外しました" in out["message_to_user"]
    assert brain.node(a)["status"] == "active"  # the cases stay
    with pytest.raises(InvalidInput):
        brain.review_rule(rule, "yes")


def test_recall_says_what_kind_of_memory_it_is(brain, session):
    _case(brain, "見出しを言い切りにした", "目を止めたい", "2026-10-01", "案件A")
    r = brain.recall(session, "見出しを言い切りにした件")
    assert "[事例・そのときの判断]" in r["context"] and "（案件A の中だけ）" in r["context"]


def test_repromote_keeps_the_owners_memories_and_relearns_the_rest(brain, session):
    own = brain.remember_explicit(session, "資料は PDF で渡す", "procedural")["node_id"]
    with brain._tx():  # an old rule the owner made before stages existed
        brain._emit("human", "node_updated", {"id": own, "stage": None})
    brain._conn.execute("UPDATE nodes SET stage = NULL WHERE id = ?", (own,))
    memo = inbox.add_memo(brain, "メモ", "- 写真は社内で撮る\n")
    with brain._tx():
        made = brain._add_elements("sleep", [Element("procedural", "写真は社内で撮る", [], 1.0)], memo["source_id"],
                                   promoted_by="explicit")[0]["id"]
        brain._emit("sleep", "sleep_mark", {"kind": "promoted", "key": f"{memo['source_id']}:1-1"})
        brain._emit("sleep", "hippocampus_faded", {"source_ids": [memo["source_id"]]})
    out = repromote.run(brain, "2000-01-01")
    assert out["retire"] == 1 and out["confirm"] == 1 and out["sources"] >= 1 and out["backup"]
    assert brain.node(made)["status"] == "retired" and brain.node(own)["stage"] == "confirmed"
    waiting = brain._conn.execute("SELECT status FROM hippocampus WHERE source_id = ?",
                                  (memo["source_id"],)).fetchone()[0]
    assert waiting == "arrived"  # back in the receiving box, to be screened anew (spec v0.8 §3.4)
    assert not brain._conn.execute("SELECT 1 FROM sleep_marks WHERE kind = 'promoted' AND key LIKE ?",
                                   (memo["source_id"] + ":%",)).fetchone()
    snapshot = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == snapshot  # everything went through events


def test_confirmed_rules_are_written_only_between_exobrains_markers(brain, session, tmp_path):
    brain.remember_explicit(session, "報告はいつも結論から書く", "procedural")
    f = tmp_path / "AGENTS.md"
    f.write_text("# 手で書いた決まり\n\n- テストを先に流す\n", encoding="utf-8")
    assert rules_export.write(brain, [f]) == [str(f)]
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# 手で書いた決まり") and "- 報告はいつも結論から書く" in text
    brain.remember_explicit(session, "数字は半角で書く", "procedural")
    rules_export.write(brain, [f])
    text = f.read_text(encoding="utf-8")
    assert text.count(rules_export.START) == 1 and "- 数字は半角で書く" in text and "- テストを先に流す" in text
    assert not rules_export.enabled(brain)  # off unless the owner turns it on
