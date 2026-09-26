import threading

import pytest

from exobrain.bookshelf import read_body
from exobrain.brain import HEBBIAN_RATE, W_ABOUT, W_SAME_REPORT, Brain, InvalidInput
from exobrain.tokens import estimate_tokens

from .conftest import report

ELEMENTS = [
    {"kind": "episode", "text": "要件定義を飛ばして実装し、注意された", "concepts": ["exobrain", "進め方"]},
    {"kind": "procedural", "text": "作る前に要件を確認する", "concepts": ["進め方"], "importance": 0.9},
    {"kind": "semantic", "text": "Claude は Pro プラン", "concepts": ["Claude"]},
]


def test_daily_report_goes_to_shelf_and_brain(brain, settings, session):
    body = "# 9/26 日報\n- 要件定義をやり直した\n"
    r = report(brain, session, ELEMENTS, title="記憶アプリの要件定義", body=body)

    path = settings.drive_root / r["path"]
    assert r["path"].startswith("本棚/原文/")
    assert read_body(path) == body
    assert [n["kind"] for n in r["nodes"]] == ["episode", "procedural", "semantic"]

    ep = brain.node(r["nodes"][0]["id"])
    assert ep["source_id"] == r["source_id"] and ep["created_by"] == "ai:Claude Desktop"
    assert brain.stats() == {"episode": 1, "semantic": 1, "procedural": 1, "concept": 3,
                             "edges": 4 + 3, "sources": 1, "events": brain.stats()["events"]}
    assert brain.verify()[0]


def test_concepts_are_shared_and_normalized(brain, session):
    a = brain.remember(session, [{"kind": "semantic", "text": "A", "concepts": ["ＧｏｏｇｌｅドライブＡ"]}])
    b = brain.remember(session, [{"kind": "semantic", "text": "B", "concepts": ["googleドライブa"]}])
    ca = [e["dst"] for e in brain.edges_of(a["nodes"][0]["id"]) if e["kind"] == "about"]
    cb = [e["dst"] for e in brain.edges_of(b["nodes"][0]["id"]) if e["kind"] == "about"]
    assert ca == cb and brain.stats()["concept"] == 1


def test_link_weights(brain, session):
    r = report(brain, session, ELEMENTS)
    ep, proc, _ = (n["id"] for n in r["nodes"])
    kinds = {(e["kind"], e["weight"]) for e in brain.edges_of(ep)}
    assert ("about", W_ABOUT) in kinds and ("association", W_SAME_REPORT) in kinds
    # episode and procedural both point at the shared concept 進め方
    shared = {e["dst"] for e in brain.edges_of(ep) if e["kind"] == "about"} & \
             {e["dst"] for e in brain.edges_of(proc) if e["kind"] == "about"}
    assert len(shared) == 1


def test_used_memories_are_reinforced(brain, session):
    first = report(brain, session, ELEMENTS)
    ep, proc, sem = (n["id"] for n in first["nodes"])
    r = report(brain, session, [], used=[proc, sem, "n_missing"])
    assert r["reinforced_memory_ids"] == [proc, sem] and r["unknown_memory_ids"] == ["n_missing"]
    w = next(e["weight"] for e in brain.edges_of(proc) if sem in (e["src"], e["dst"]))
    assert w == pytest.approx(W_SAME_REPORT + HEBBIAN_RATE * (1 - W_SAME_REPORT))
    assert brain.node(proc)["access_count"] == 1 and brain.node(ep)["access_count"] == 0


def test_rebuild_reproduces_projections(brain, session):
    first = report(brain, session, ELEMENTS)
    report(brain, session, ELEMENTS[:1], used=[n["id"] for n in first["nodes"]])
    before = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == before


def test_profile_puts_rules_first_within_budget(brain, session):
    brain.remember(session, [{"kind": "semantic", "text": "会社は meeting", "importance": 1.0}])
    brain.remember(session, [{"kind": "procedural", "text": f"ルール{i}" * 20, "importance": 0.8}
                             for i in range(30)])
    profile = brain.start_session("Codex")["profile"]
    assert profile.splitlines()[0].startswith("- [ルール]")
    assert estimate_tokens(profile) <= 500


@pytest.mark.parametrize("bad, message", [
    ([{"kind": "note", "text": "x"}], "kind"),
    ([{"kind": "episode", "text": "  "}], "空"),
    ([{"kind": "episode", "text": "あ" * 301}], "分けて"),
    ([{"kind": "episode", "text": "x", "concepts": [str(i) for i in range(9)]}], "8 個"),
    ([{"kind": "episode", "text": "x", "importance": 2}], "0〜1"),
    ([{"kind": "episode", "text": "x"}] * 31, "30 件"),
])
def test_invalid_elements_are_explained(brain, session, bad, message):
    with pytest.raises(InvalidInput, match=message):
        brain.remember(session, bad)


def test_unknown_session_is_rejected(brain):
    with pytest.raises(InvalidInput, match="start_session"):
        brain.remember("s_nope", ELEMENTS)


def test_failed_report_leaves_no_file(brain, settings, session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(brain, "_add_elements", boom)
    with pytest.raises(RuntimeError):
        report(brain, session, ELEMENTS)
    assert list(settings.originals.rglob("*.md")) == []
    assert brain.stats()["sources"] == 0


def test_verify_notices_edited_original(brain, settings, session):
    r = report(brain, session, ELEMENTS)
    path = settings.drive_root / r["path"]
    path.write_text(path.read_text(encoding="utf-8") + "追記", encoding="utf-8")
    ok, msg = brain.verify()
    assert not ok and r["source_id"] in msg


def test_open_source(brain, session):
    r = report(brain, session, ELEMENTS, body="原文" * 10)
    got = brain.open_source(r["source_id"], max_chars=5)
    assert got["body"] == "原文原文原" and got["truncated"] and got["intact"]


def test_two_processes_share_one_brain(settings):
    """Claude Desktop and Codex each start their own server process on the same files."""
    a, b = Brain(settings), Brain(settings)
    sa, sb = a.start_session("Claude Desktop")["session_id"], b.start_session("Codex")["session_id"]

    def write(br, sid, tag):
        for i in range(10):
            br.remember(sid, [{"kind": "episode", "text": f"{tag}{i}", "concepts": ["共有"]}])

    threads = [threading.Thread(target=write, args=(a, sa, "A")), threading.Thread(target=write, args=(b, sb, "B"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert a.stats()["episode"] == 20 and a.stats()["concept"] == 1
    assert b.verify()[0]
    a.close()
    b.close()
