"""Spec v0.8 §3.4 and §7.3.1: screening the receiving box, and abstraction, links and weights into the cortex."""

from exobrain import promote, screen, sleep
from exobrain.brain import Element


def _log(brain, session, **sections):
    keys = ("events", "corrections", "learnings", "decisions", "unresolved")
    brain.submit_daily_log(session, "無鄰菴の課題", *[sections.get(k, []) for k in keys],
                           reasons=sections.get("reasons", []))
    return brain._conn.execute("SELECT id FROM sources WHERE kind = 'ai_daily' ORDER BY created_at DESC").fetchone()[0]


def test_arrivals_wait_in_the_receiving_box_until_screened(brain, session):
    sid = _log(brain, session, events=["葉を流れに乗せる案を出した"], decisions=["その案は取り下げた"])
    assert [r["id"] for r in screen.pending(brain)] == [sid]
    assert not list(promote.candidates(brain, set()))  # nothing goes on before screening


def test_lines_tied_to_attention_stay_small_talk_is_small_talk_the_rest_is_left_out(brain, session):
    sid = _log(brain, session, events=["葉を流れに乗せる案を出した", "雑談で天気の話をした", "ファイルを保存した"],
               decisions=["その案は取り下げた"], unresolved=["無鄰菴の広さを調べる"], learnings=["空間の大きさを見落とした"])

    def judge(brain, text):
        assert "[オーナー] その案は取り下げた" in text and "[AI] 無鄰菴の広さを調べる" in text
        ids = {line.split()[1][3:]: line for line in text.splitlines() if line.startswith("- id=")}
        pick = lambda word: next(k for k, v in ids.items() if word in v)  # noqa: E731
        return {pick("葉を"): {"label": "related"}, pick("天気"): {"label": "chatter"}, pick("保存"): {"label": "drop"}}

    out = screen.run(brain, judge=judge)
    assert out == {"screened": 1, "dropped_sources": 0, "lines_dropped": 1, "lines_kept": 5, "repeats": 0, "chatter": 1}
    labels = screen.labels_of(brain, sid)
    assert sorted(labels.values()) == ["attention", "attention", "chatter", "drop", "owner", "related"]
    signals = {}
    for seg, text in promote.candidates(brain, set()):
        signals[text.strip()] = seg.signal
    assert signals["- 葉を流れに乗せる案を出した"] == "attended"
    assert signals["- 雑談で天気の話をした"] == "chatter"
    assert signals["- その案は取り下げた"] == "summarized"
    assert not any("保存" in t or "広さを調べる" in t or "見落とした" in t for t in signals)  # left out / markers only


def test_what_the_brain_already_holds_strengthens_it_and_leaves_early(brain, session):
    from tests.test_stage2 import FakeEmbedder

    brain.embedder = FakeEmbedder()
    old = brain.remember_explicit(session, "納品物は PDF と PNG の両方で渡す", "semantic")["node_id"]
    promote.encode_nodes(brain)
    before = brain.node(old)["importance"]
    sid = _log(brain, session, events=["納品物は PDF と PNG の両方で渡す"])
    screen.run(brain)
    assert set(screen.labels_of(brain, sid).values()) == {"repeat"}
    assert brain.node(old)["occurrences"] == 2 and brain.node(old)["importance"] >= min(1.0, before)
    h = brain._conn.execute("SELECT status, expires_at, entered_at FROM hippocampus WHERE source_id = ?", (sid,)).fetchone()
    assert h["status"] == "waiting"
    assert not list(promote.candidates(brain, set()))  # nothing new for the cortex


def test_no_ai_means_nothing_is_lost(brain, session):
    sid = _log(brain, session, events=["葉を流れに乗せる案を出した"])
    screen.run(brain)  # in tests the AI cannot be asked
    assert screen.labels_of(brain, sid) and set(screen.labels_of(brain, sid).values()) == {"related"}
    assert brain._conn.execute("SELECT judge FROM screenings").fetchone()[0].startswith("none")


def test_one_event_becomes_a_linked_bundle_weighed_within_the_owners_signal(brain, session):
    with brain._tx():
        past = brain._add_elements("sleep", [Element("episode", "大学院の別の課題で、模型の縮尺を間違えて叱られた",
                                                     ["大学院"], 0.6)], None)[0]["id"]
    sid = _log(brain, session, events=["[[大学院]]の[[無鄰菴]]の課題で葉を流れに乗せる案を出したが、見えにくいので取り下げた"],
               decisions=["[[大学院]]の課題では、実際の空間を調べてから提案する"])
    screen.run(brain)
    seg, text = next((s, t) for s, t in promote.candidates(brain, set()) if "葉を" in t)
    item = promote.make_item(brain, seg, text)
    assert any(m["id"] == past for m in item["past_memories"] + item["similar_memories"])
    n = item["lines"][0][0]
    atoms = promote.validate(brain, item, {"atoms": [
        {"kind": "episode", "text": "無鄰菴の課題で葉を流れに乗せる案を出したが却下された", "derivation": "paraphrase",
         "lines": [n, n], "importance": 0.95, "links": [{"to": "a1", "type": "same_event", "why": "同じ出来事"},
                                                         {"to": past, "type": "related", "why": "同じ大学院の課題"}]},
        {"kind": "case", "text": "実在の庭園での演出案を、見えにくいので取り下げた", "derivation": "paraphrase",
         "lines": [n, n], "case": {"situation": "実在の庭園での演出", "decision": "取り下げ",
                                   "reason": "散策路から葉は見えない", "reaction": "却下",
                                   "lesson": "実在の空間での提案は、調べてから出す"},
         "links": [{"to": "a0", "type": "reason", "why": "取り下げた理由"}]}]})
    assert atoms[0]["importance"] == 0.7  # the judge said 0.95, but an attended line is capped (spec v0.8 §7.3.1)
    with brain._tx():
        counts = promote.apply(brain, "sleep", item, atoms)
    assert counts["new"] == 2 and counts["links"] == 3
    ep = brain._conn.execute("SELECT id FROM nodes WHERE kind = 'episode' AND body LIKE '無鄰菴の課題%'").fetchone()[0]
    kinds = {(e["kind"], e["dst"] == past) for e in brain.edges_of(ep) if e["origin"] == "judge"}
    assert ("same_event", False) in kinds and ("related", True) in kinds
    case = brain._conn.execute("SELECT case_json FROM nodes WHERE kind = 'case'").fetchone()[0]
    assert "調べてから出す" in case


def test_small_talk_is_remembered_as_small_talk(brain, session):
    sid = _log(brain, session, events=["今日は寒いねという話をした"], decisions=["案は来週出す"])
    screen.run(brain, judge=lambda b, text: {f"{sid}:{line.split()[1].split(':')[1]}": {"label": "chatter"}
                                             for line in text.splitlines() if line.startswith("- id=")})
    seg, text = next((s, t) for s, t in promote.candidates(brain, set()) if s.signal == "chatter")
    item = promote.make_item(brain, seg, text)
    n = item["lines"][0][0]
    atoms = promote.validate(brain, item, {"atoms": [{"kind": "episode", "text": "寒いという話をした",
                                                      "derivation": "paraphrase", "lines": [n, n]}]})
    assert atoms[0]["text"].startswith("雑談") and atoms[0]["importance"] <= 0.3


def test_screening_runs_at_its_slots():
    from datetime import datetime, timezone, timedelta

    jst = timezone(timedelta(hours=9))

    class B:  # just what due() reads
        class settings:
            home = None

    import tempfile
    from pathlib import Path

    B.settings.home = Path(tempfile.mkdtemp())
    assert not screen.due(B, datetime(2026, 10, 8, 11, 0, tzinfo=jst))
    assert screen.due(B, datetime(2026, 10, 8, 12, 5, tzinfo=jst))
    (B.settings.home / "screened_at").write_text(datetime(2026, 10, 8, 12, 5, tzinfo=jst).isoformat())
    assert not screen.due(B, datetime(2026, 10, 8, 15, 0, tzinfo=jst))
    assert screen.due(B, datetime(2026, 10, 8, 16, 1, tzinfo=jst))
