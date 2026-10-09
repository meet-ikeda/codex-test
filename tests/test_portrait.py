"""memory.md: a one-page reading of what the brain holds, every statement tied to memories."""

from datetime import datetime

from exobrain import portrait
from exobrain.brain import Element


def test_digest_lists_the_cortex_with_kinds_stages_and_cases(brain, session):
    brain.remember_explicit(session, "報告はいつも結論から書く", "procedural", ["報告"])
    case = {"situation": "LP の見出し", "decision": "体言止め", "reason": "目を止めたい", "reaction": "", "when": "2026-10-01"}
    with brain._tx():
        brain._add_elements("sleep", [Element("case", "見出しを体言止めにした", ["見出し"], 0.6)], None,
                            extra={"case": case, "scope": "案件A"})
    text, counts = portrait.digest(brain)
    assert "報告はいつも結論から書く（本決まり）" in text
    assert "見出しを体言止めにした（場面: 案件A）" in text and "理由: 目を止めたい" in text
    assert counts["procedural"] == 1 and counts["case"] == 1


def test_save_flags_ids_that_are_not_in_the_brain_and_read_reports_changes(brain, session):
    nid = brain.remember_explicit(session, "報告はいつも結論から書く", "procedural")["node_id"]
    out = portrait.save(brain, f"# この脳が思っていること\n- 結論から書く [{nid}]\n- 作り話 [n_ffffffff]\n",
                        {"procedural": 1}, datetime.now().astimezone())
    assert out["unknown_ids"] == ["n_ffffffff"] and "[n_ffffffff・脳に見当たらない]" in out["markdown"]
    assert f"[{nid}]" in out["markdown"] and out["markdown"].startswith("> ")
    assert (brain.settings.drive_root / "memory.md").exists()
    assert portrait.read(brain)["changes_since"] == 0
    brain.remember_explicit(session, "数字は半角", "procedural")
    assert portrait.read(brain)["changes_since"] >= 1


def test_no_memory_md_yet(brain):
    assert portrait.read(brain) == {"markdown": None}


def test_the_whole_cortex_is_read_and_the_recall_view_shows_what_an_ai_receives(brain, session):
    for i in range(70):  # more than the old per-kind limit (60 rules)
        brain.remember_explicit(session, f"ルール{i}: 報告はいつも結論から書く", "procedural")
    text, counts = portrait.digest(brain)
    assert counts["procedural"] == 70 and text.count("（本決まり）") == 70
    view = portrait.recall_view(brain)
    assert view["rules_total"] == 70 and 0 < view["rules_shown"] <= 70 and view["profile_tokens"] <= view["profile_budget"]


def test_each_job_has_its_model(brain):
    import json

    from exobrain.models import model_for

    assert model_for(brain, "sleep") == "claude-sonnet-5-5" and model_for(brain, "summary") == "claude-haiku-5-5"
    (brain.settings.home / "config.json").write_text(json.dumps({"models": {"sleep": "claude-opus-5-5"}}))
    assert model_for(brain, "sleep") == "claude-opus-5-5" and model_for(brain, "screen") == "claude-haiku-5-5"
