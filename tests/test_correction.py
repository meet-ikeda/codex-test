import pytest

from exobrain.brain import InvalidInput
from exobrain.correction import MSG_NEW, PIN_AFTER


@pytest.fixture
def rule(brain, session):
    return brain.remember(session, [{"kind": "procedural", "text": "作る前に要件を確認する",
                                     "concepts": ["進め方", "要件定義"], "importance": 0.9}])["nodes"][0]["id"]


def test_trace_does_not_write(brain, session, rule):
    before = brain._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    t = brain.trace_correction(session, "また要件定義をせずに作り始めている", "設計の相談中")
    assert [c["id"] for c in t["brain_candidates"]][0] == rule
    assert "reinforce" in t["next_step"]
    assert brain._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_reinforce_wires_the_rule_to_this_context(brain, session, rule):
    brain.remember(session, [{"kind": "semantic", "text": "採用サイトの改修を担当している", "concepts": ["採用サイト"]}])
    t = brain.trace_correction(session, "要件定義せずに作り始めた", "採用サイトの改修で、いきなりデザイン案を出した")
    r = brain.apply_correction(session, t["trace_id"], "reinforce", target_id=rule)
    assert "2 回目" in r["message_to_user"]
    node = brain.node(rule)
    assert node["corrections"] == 1 and node["base_strength"] == 1.5
    # The rule is now tied to 採用サイト, so it comes up when that topic comes up.
    fresh = brain.start_session("Codex")["session_id"]
    got = brain.recall(fresh, "採用サイトの改修の続き")
    assert rule in got["memory_ids"]
    # The correction itself is remembered as an episode.
    assert any(n["kind"] == "episode" and "指摘を受けた" in n["text"] for n in r["nodes"])


def test_repeated_correction_pins_the_rule(brain, session, rule):
    for i in range(PIN_AFTER):
        t = brain.trace_correction(session, "要件を確認してから作って", "")
        r = brain.apply_correction(session, t["trace_id"], "reinforce", target_id=rule)
    assert r["pinned"] and "毎回必ず思い出すルール" in r["message_to_user"]
    assert brain.node(rule)["pinned"] == 1
    for i in range(40):  # plenty of other important rules
        brain.remember(session, [{"kind": "procedural", "text": f"別のルール{i}" * 5, "importance": 1.0}])
    assert brain.start_session("Codex")["profile"].splitlines()[0].endswith(f"[{rule}]")


def test_restore_from_the_bookshelf(brain, session):
    memo = brain.add_memo("9/20 の打ち合わせ", "決定: 採用サイトの写真は全て社内撮影にする。外部素材は使わない。")
    t = brain.trace_correction(session, "写真は社内撮影って決めたよね", "外部素材サイトを提案した",
                               keywords=["社内撮影", "写真"])
    assert t["brain_candidates"] == []
    assert t["bookshelf_candidates"][0]["source_id"] == memo["source_id"]
    r = brain.apply_correction(session, t["trace_id"], "restore", source_id=memo["source_id"],
                               lesson="採用サイトの写真は全て社内撮影。外部素材は使わない", concepts=["採用サイト", "写真"])
    assert "本棚の原文（9/20 の打ち合わせ" in r["message_to_user"]
    restored = brain.node(r["nodes"][0]["id"])
    assert restored["source_id"] == memo["source_id"] and restored["kind"] == "semantic"
    fresh = brain.start_session("Codex")["session_id"]
    assert restored["id"] in brain.recall(fresh, "採用サイトの写真どうする？")["memory_ids"]


def test_new_when_found_nowhere(brain, session):
    t = brain.trace_correction(session, "敬語はやめて、です・ます調で", "")
    assert t["brain_candidates"] == [] and t["bookshelf_candidates"] == []
    r = brain.apply_correction(session, t["trace_id"], "new", lesson="文体はです・ます調（過剰な敬語は使わない）")
    assert r["message_to_user"] == MSG_NEW
    assert r["nodes"][0]["kind"] == "procedural"
    assert "です・ます調" in brain.start_session("Codex")["profile"]


def test_wrong_fact_is_superseded_not_deleted(brain, session):
    wrong = brain.remember(session, [{"kind": "semantic", "text": "本棚は iCloud に置く",
                                      "concepts": ["本棚"]}])["nodes"][0]["id"]
    t = brain.trace_correction(session, "本棚は Google ドライブだよ", "", used_memory_ids=[wrong])
    r = brain.apply_correction(session, t["trace_id"], "reinforce", target_id=wrong,
                               lesson="本棚は Google ドライブに置く", superseded_ids=[wrong])
    new = r["nodes"][0]["id"]
    assert brain.node(wrong)["status"] == "superseded"  # kept in history, no longer recalled
    got = brain.recall(brain.start_session("Codex")["session_id"], "本棚はどこ？")
    assert new in got["memory_ids"] and wrong not in got["memory_ids"]
    assert any(e["kind"] == "supersedes" and e["dst"] == wrong for e in brain.edges_of(new))


def test_the_flow_is_enforced(brain, session, rule):
    t = brain.trace_correction(session, "要件定義して", "")
    with pytest.raises(InvalidInput, match="brain_candidates"):
        brain.apply_correction(session, t["trace_id"], "reinforce", target_id="n_not_a_candidate")
    with pytest.raises(InvalidInput, match="bookshelf_candidates"):
        brain.apply_correction(session, t["trace_id"], "restore", source_id="src_nope")
    with pytest.raises(InvalidInput, match="lesson"):
        brain.apply_correction(session, t["trace_id"], "new")
    with pytest.raises(InvalidInput, match="trace_correction"):
        brain.apply_correction(session, "t_unknown", "new", lesson="x")
    ep = brain.remember(session, [{"kind": "episode", "text": "出来事"}])["nodes"][0]["id"]
    with pytest.raises(InvalidInput, match="出来事"):
        brain.apply_correction(session, t["trace_id"], "new", lesson="x", superseded_ids=[ep])


def test_rebuild_after_corrections(brain, session, rule):
    t = brain.trace_correction(session, "要件定義して", "")
    brain.apply_correction(session, t["trace_id"], "reinforce", target_id=rule, lesson="まず要件を書き出して確認する")
    before = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == before
