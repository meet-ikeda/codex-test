import json
import plistlib
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exobrain import safety, sleep
from exobrain.bookshelf import read_body
from exobrain.brain import InvalidInput
from exobrain.macos import sleep_agent

from .conftest import report


def remember(brain, session, kind, text, concepts=(), importance=0.5):
    return brain.remember(session, [{"kind": kind, "text": text, "concepts": list(concepts),
                                     "importance": importance}])["nodes"][0]["id"]


def no_ai(brain, run_id):
    return "テストでは AI を使わない"


# ---- stage A ------------------------------------------------------------------------


def test_replay_strengthens_what_a_conversation_recalled(brain, session):
    a = remember(brain, session, "semantic", "exobrain の本棚は Google ドライブ", ["exobrain"])
    b = remember(brain, session, "semantic", "exobrain の脳は PC 内に置く", ["exobrain"])
    brain.recall(session, "exobrain の置き場所")
    before = next(e["weight"] for e in brain.edges_of(a) if b in (e["src"], e["dst"]))
    stats = sleep.stage_a(brain, "0000")
    after = next(e["weight"] for e in brain.edges_of(a) if b in (e["src"], e["dst"]))
    assert stats["replayed_links"] >= 1 and after > before


def test_unused_memories_fall_asleep_but_important_ones_stay(brain, session, monkeypatch):
    trivia = remember(brain, session, "episode", "昼はそばを食べた", importance=0.3)
    rule = remember(brain, session, "procedural", "作る前に要件を確認する", importance=0.9)
    monkeypatch.setattr(sleep, "DORMANT_AFTER_DAYS", -1)  # pretend a long time has passed
    assert sleep.stage_a(brain, "0000")["dormant"] == 1
    assert brain.node(trivia)["status"] == "dormant" and brain.node(rule)["status"] == "active"
    assert trivia not in brain.recall(session, "昼はそばを食べた")["memory_ids"]


def test_dormant_memory_wakes_when_corrected(brain, session, monkeypatch):
    old = remember(brain, session, "semantic", "取引先の担当は佐藤さん", ["取引先"], importance=0.3)
    monkeypatch.setattr(sleep, "DORMANT_AFTER_DAYS", -1)
    sleep.stage_a(brain, "0000")
    t = brain.trace_correction(session, "取引先の担当は佐藤さんだよ", "")
    cand = next(c for c in t["brain_candidates"] if c["id"] == old)
    assert cand["dormant"]
    r = brain.apply_correction(session, t["trace_id"], "reinforce", target_id=old)
    assert "眠っていた記憶" in r["message_to_user"] and brain.node(old)["status"] == "active"


def test_new_links_between_memories_sharing_concepts(brain, session):
    a = remember(brain, session, "semantic", "採用サイトは原文を大切にする", ["採用サイト", "原文"])
    b = remember(brain, session, "semantic", "社員インタビューは原文で載せる", ["採用サイト", "原文"])
    c = remember(brain, session, "semantic", "無関係な話", ["天気"])
    stats = sleep.stage_a(brain, "0000")
    assert stats["new_links"] == 1
    link = next(e for e in brain.edges_of(a) if b in (e["src"], e["dst"]) and e["kind"] == "association")
    assert link["origin"] == "sleep" and link["weight"] == sleep.NEW_LINK_WEIGHT
    assert not any(c in (e["src"], e["dst"]) for e in brain.edges_of(a))


# ---- stage B ------------------------------------------------------------------------


def work_through(brain, answer):
    run_id, _ = sleep.start(brain)
    state = sleep.SleepState(run_id)
    seen = []
    while True:
        batch = sleep.next_batch(brain, state)
        if batch["done"]:
            break
        seen += [i["type"] for i in batch["items"]]
        sleep.apply(brain, state, batch["batch_id"], [answer(i) for i in batch["items"]])
    return run_id, seen


def test_memo_is_decomposed_and_shelved(brain, session):
    memo = brain.add_memo("9/20 打ち合わせ", "写真は全て社内撮影にする\n外部素材は使わない\n")

    def answer(item):
        if item["type"] == "decompose":
            return {"item_id": item["item_id"], "elements": [
                {"kind": "procedural", "text": "採用サイトの写真は全て社内撮影", "concepts": ["採用サイト", "写真"]}]}
        if item["type"] == "shelve":
            return {"item_id": item["item_id"], "assignments": {s["source_id"]: ["採用サイト"] for s in item["sources"]}}
        return {"item_id": item["item_id"], "keep": [], "drop": []} if item["type"] == "verify_links" else \
            {"item_id": item["item_id"], "elements": []}

    run_id, seen = work_through(brain, answer)
    assert seen[0] == "decompose" and "shelve" in seen
    node = brain._conn.execute("SELECT * FROM nodes WHERE source_id = ?", (memo["source_id"],)).fetchone()
    assert node["created_by"] == "sleep" and node["kind"] == "procedural"
    fresh = brain.start_session("Codex")["session_id"]
    assert node["id"] in brain.recall(fresh, "採用サイトの写真")["memory_ids"]
    # Nothing is handed out twice.
    assert sleep.next_batch(brain, sleep.SleepState(run_id))["done"]


def test_episodes_consolidate_into_a_rule(brain, session):
    eps = [remember(brain, session, "episode", f"結論を先に書いてと言われた（{i} 回目）", ["文章"]) for i in range(3)]

    def answer(item):
        if item["type"] == "consolidate":
            assert {e["id"] for e in item["episodes"]} == set(eps)
            return {"item_id": item["item_id"], "elements": [
                {"kind": "procedural", "text": "文章は結論を先に書く", "concepts": ["文章"], "importance": 0.9}]}
        return {"item_id": item["item_id"], "keep": [], "drop": []} if item["type"] == "verify_links" else \
            {"item_id": item["item_id"], "elements": []}

    work_through(brain, answer)
    rule = brain._conn.execute("SELECT id FROM nodes WHERE body = '文章は結論を先に書く'").fetchone()["id"]
    assert {e["dst"] for e in brain.edges_of(rule) if e["kind"] == "derived_from"} == set(eps)
    assert "文章は結論を先に書く" in brain.start_session("Codex")["profile"]


def test_near_duplicates_are_reconciled(brain, session):
    old = remember(brain, session, "semantic", "本棚は iCloud ドライブに置く")
    new = remember(brain, session, "semantic", "本棚は Google ドライブに置く")

    def answer(item):
        if item["type"] == "reconcile":
            assert {item["a"]["id"], item["b"]["id"]} == {old, new}
            return {"item_id": item["item_id"], "action": "supersede", "keep_id": new}
        return {"item_id": item["item_id"], "keep": [], "drop": []}

    work_through(brain, answer)
    assert brain.node(old)["status"] == "superseded" and brain.node(new)["status"] == "active"


def test_proposed_links_are_checked(brain, session):
    a = remember(brain, session, "semantic", "A の話", ["x", "y"])
    b = remember(brain, session, "semantic", "B の話", ["x", "y"])

    def answer(item):
        assert item["type"] == "verify_links"
        return {"item_id": item["item_id"], "keep": [], "drop": [[l["src"], l["dst"]] for l in item["links"]]}

    work_through(brain, answer)
    link = next(e for e in brain.edges_of(a) if b in (e["src"], e["dst"]) and e["kind"] == "association")
    assert link["weight"] == 0.0 and link["origin"] == "sleep_rejected"


def test_bad_results_are_explained(brain, session):
    brain.add_memo("メモ", "内容")
    run_id, _ = sleep.start(brain)
    state = sleep.SleepState(run_id)
    batch = sleep.next_batch(brain, state)
    with pytest.raises(InvalidInput, match="この束にありません"):
        sleep.apply(brain, state, batch["batch_id"], [{"item_id": "i99"}])
    with pytest.raises(InvalidInput, match="batch_id"):
        sleep.apply(brain, state, "b_nope", [])


def test_batch_limit(brain, session, monkeypatch):
    for i in range(5):
        brain.add_memo(f"メモ{i}", f"内容{i}")
    monkeypatch.setattr(sleep, "BATCH_ITEMS", 1)
    monkeypatch.setattr(sleep, "MAX_BATCHES", 2)
    run_id, _ = sleep.start(brain)
    state = sleep.SleepState(run_id)
    assert not sleep.next_batch(brain, state)["done"]
    assert not sleep.next_batch(brain, state)["done"]
    assert "上限" in sleep.next_batch(brain, state)["reason"]


# ---- finishing, journal, triggers ------------------------------------------------------


def test_run_without_ai_writes_a_journal(brain, settings, session):
    remember(brain, session, "semantic", "記憶", ["x", "y"])
    remember(brain, session, "semantic", "記憶2", ["x", "y"])
    assert sleep.is_due(brain)
    out = sleep.run(brain, runner=no_ai)
    journal = settings.drive_root / out["journal"]
    text = read_body(journal)
    assert journal.parent.name == "09" and "夢日記" in journal.parts
    assert "新しく結んだつながりの候補: 1 本" in text and "テストでは AI を使わない" in text
    assert not sleep.is_due(brain)
    assert sleep.is_due(brain, now=datetime.now(timezone.utc) + timedelta(hours=21))
    assert (settings.bookshelf / "棚" / "夢日記.md").exists()
    assert len(safety.list_backups(settings.backups)) == 1
    assert brain.verify()[0]


def test_shelf_index_links_open_the_originals(brain, settings, session):
    r = report(brain, session, [], title="要件定義の日報")
    brain._emit  # noqa: B018
    with brain._tx():
        brain._emit("sleep", "shelf_assigned", {"source_id": r["source_id"], "shelves": ["exobrain 開発"]})
    from exobrain.shelves import write_shelf_index
    from urllib.parse import unquote

    names = write_shelf_index(brain)
    assert "話題_exobrain 開発.md" in names and "日付別.md" in names and "出所別.md" in names
    text = (settings.bookshelf / "棚" / "話題_exobrain 開発.md").read_text(encoding="utf-8")
    target = unquote(text.split("](")[1].split(")")[0])
    assert (settings.bookshelf / "棚" / target).resolve() == (settings.drive_root / r["path"]).resolve()


def test_only_one_sleep_at_a_time(brain):
    entered = threading.Event()
    release = threading.Event()

    def slow(b, run_id):
        entered.set()
        release.wait(5)
        return ""

    t = threading.Thread(target=lambda: sleep.run(brain, runner=slow))
    t.start()
    entered.wait(5)
    with pytest.raises(sleep.SleepBusy):
        sleep.run(brain, runner=no_ai)
    release.set()
    t.join()


def test_rebuild_after_sleep(brain, session):
    remember(brain, session, "semantic", "記憶", ["x", "y"])
    remember(brain, session, "semantic", "記憶2", ["x", "y"])
    brain.add_memo("メモ", "内容")
    sleep.run(brain, runner=no_ai)
    before = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == before


def test_launchd_agent():
    plist = plistlib.loads(sleep_agent("/usr/local/bin/exobrain", hour=3, minute=5, log_dir=Path("/tmp")))
    assert plist["ProgramArguments"] == ["/usr/local/bin/exobrain", "sleep", "--if-due"]
    assert plist["RunAtLoad"] and plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert "StartCalendarInterval" not in plistlib.loads(sleep_agent("/x/exobrain"))


# ---- the real `claude -p` path, with a stand-in for Claude Code -------------------------


def test_sleep_through_claude_code(brain, settings, session, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\n" + (Path(__file__).parent / "fake_claude.py").read_text(encoding="utf-8"),
                    encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (settings.home / "config.json").write_text(json.dumps({"claude_path": str(fake)}), encoding="utf-8")
    brain.add_memo("打ち合わせ", "# 資料は PDF で共有する\n詳細は後日\n")
    for i in range(3):
        remember(brain, session, "episode", f"会議の出来事{i}", ["会議"])

    out = sleep.run(brain)
    assert out["note"] == "", (settings.home / "last-sleep.log").read_text(encoding="utf-8")
    journal = brain.open_source(out["journal_source_id"])["body"]
    assert "テストの夢を見ました。" in journal and "資料は PDF で共有する" in journal
    assert brain._conn.execute("SELECT COUNT(*) FROM shelves WHERE shelf = 'テスト棚'").fetchone()[0] >= 1
    assert brain.verify()[0]
