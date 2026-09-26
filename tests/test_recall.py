import random
import time

import pytest

from exobrain.brain import InvalidInput
from exobrain.recall import bigrams, normalize

from .conftest import report


def remember(brain, session, kind, text, concepts=(), importance=0.5):
    return brain.remember(session, [{"kind": kind, "text": text, "concepts": list(concepts),
                                     "importance": importance}])["nodes"][0]["id"]


@pytest.fixture
def world(brain, session):
    """A small brain: direct answers about exobrain, plus a distant idea two links away."""
    ids = {}
    ids["rule"] = remember(brain, session, "procedural", "作る前に要件を確認する", ["進め方"], 0.9)
    ids["shelf"] = remember(brain, session, "episode", "exobrain では原文を本棚にそのまま保管すると決めた",
                            ["exobrain", "原文保管"])
    r = report(brain, session, [
        {"kind": "semantic", "text": f"exobrain の設計メモ その{i}", "concepts": ["exobrain"]} for i in range(6)
    ])
    ids["notes"] = [n["id"] for n in r["nodes"]]
    ids["recruit"] = remember(brain, session, "semantic",
                              "採用サイトでは社員の生の声を原文のまま載せると信頼される", ["採用サイト", "原文保管"])
    ids["weather"] = remember(brain, session, "episode", "昨日は雨で打ち合わせが延期になった", ["天気"])
    return ids


def test_normalize_and_bigrams():
    assert normalize("Ｅｘｏ Brain") == "exobrain"
    assert bigrams("設計") == {"設計"} and bigrams("a") == {"a"}


def test_recall_finds_related_and_rules(brain, session, world):
    r = brain.recall(session, "exobrain の設計を続けたい")
    assert world["shelf"] in r["memory_ids"]
    assert set(world["notes"]) <= set(r["memory_ids"])
    assert world["weather"] not in r["memory_ids"] + r["insight_ids"]
    # The rule is unrelated to the cue but is a standing rule, listed first.
    assert r["memory_ids"][0] == world["rule"]
    assert r["context"].startswith("## あなたについて（ルール）\n- 作る前に要件を確認する")


def test_insight_comes_from_a_distant_link(brain, session, world):
    r = brain.recall(session, "exobrain の設計を続けたい")
    assert r["insight_ids"] == [world["recruit"]]
    assert "## ひらめき（遠い連想）" in r["context"]
    assert "経路: " in r["context"] and "原文保管" in r["context"].split("ひらめき")[1]


def test_recall_strengthens_what_fires_together(brain, session, world):
    def weight(a, b):
        return next((e["weight"] for e in brain.edges_of(a) if b in (e["src"], e["dst"])
                     and e["kind"] == "association"), 0.0)

    before = {(a, b): weight(a, b) for a in world["notes"] for b in world["notes"] if a < b}
    r = brain.recall(session, "exobrain の設計")
    top = [i for i in r["memory_ids"] if i != world["rule"]][:2]
    assert weight(*top) > 0.3
    assert sum(weight(a, b) > w for (a, b), w in before.items()) == 10  # C(5, 2) pairs in the top five
    assert brain.node(top[0])["access_count"] == 1


def test_short_term_memory_carries_the_conversation(brain, session, world):
    brain.recall(session, "exobrain の設計")
    later = brain.recall(session, "ところで今日の天気は")
    assert world["weather"] in later["memory_ids"]
    assert world["shelf"] in later["memory_ids"]  # still "in mind" from earlier in this conversation
    other = brain.start_session("Codex")["session_id"]
    fresh = brain.recall(other, "ところで今日の天気は")
    assert world["shelf"] not in fresh["memory_ids"]


def test_rebuild_after_recall(brain, session, world):
    brain.recall(session, "exobrain の設計")
    before = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == before


@pytest.mark.parametrize("budget", [300, 800, 1500])
def test_budget_is_respected_with_many_memories(brain, session, budget):
    rng = random.Random(0)
    topics = [f"話題{i}" for i in range(40)]
    for batch in range(200):
        brain.remember(session, [
            {"kind": rng.choice(["episode", "semantic", "procedural"]),
             "text": f"記憶{batch}-{j}: " + "長めの本文。" * rng.randint(1, 20),
             "concepts": rng.sample(topics, 2)} for j in range(5)
        ])
    r = brain.recall(session, "話題3 と 話題7 の件を相談したい", budget=budget)
    assert 0 < r["tokens"] <= budget
    assert len(r["memory_ids"]) > 3


def test_recall_is_fast_enough(brain, session):
    rng = random.Random(1)
    topics = [f"トピック{i}" for i in range(200)]
    for batch in range(400):
        brain.remember(session, [{"kind": "semantic", "text": f"事実{batch}-{j} " + rng.choice(topics),
                                  "concepts": rng.sample(topics, 3)} for j in range(5)])
    brain.recall(session, "ウォームアップ")
    t = time.perf_counter()
    brain.recall(session, "トピック5 について教えて")
    assert time.perf_counter() - t < 1.0


def test_bad_input(brain, session):
    with pytest.raises(InvalidInput, match="cue"):
        brain.recall(session, "  ")
    with pytest.raises(InvalidInput, match="start_session"):
        brain.recall("s_missing", "何か")
    assert brain.recall(session, "何か", budget=10)["budget"] == 300


def test_empty_brain(brain, session):
    r = brain.recall(session, "はじめまして")
    assert r["context"] == "（関連する記憶はありません）" and r["memory_ids"] == []
