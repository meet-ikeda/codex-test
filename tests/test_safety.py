import pytest

from exobrain import safety
from exobrain.brain import InvalidInput

from .conftest import report

SECRET = "口座の暗証番号は 4321"


def db_bytes(settings):
    data = b""
    for suffix in ("", "-wal"):
        p = settings.db_path.with_name(settings.db_path.name + suffix)
        if p.exists():
            data += p.read_bytes()
    return data


def test_erase_leaves_no_trace(brain, settings, session):
    r = report(brain, session, [{"kind": "semantic", "text": SECRET, "concepts": ["銀行"]}],
               title="うっかり", body=f"日報: {SECRET}\n")
    keep = brain.remember(session, [{"kind": "semantic", "text": "会社は meeting"}])["nodes"][0]["id"]
    safety.backup(brain)
    plan = safety.plan_erase(brain, source_ids=[r["source_id"]])
    assert [s["id"] for s in plan["sources"]] == [r["source_id"]] and len(plan["nodes"]) == 1

    with pytest.raises(InvalidInput, match="消去する"):
        safety.erase(brain, plan, confirm="yes")
    out = safety.erase(brain, plan, confirm="消去する")
    assert out["erased_sources"] == 1 and "ゴミ箱" in out["notice"]

    assert not (settings.drive_root / r["path"]).exists()
    assert brain.node(r["nodes"][0]["id"]) is None and brain.node(keep) is not None
    assert brain.search_bookshelf("暗証番号") == []
    assert SECRET.encode() not in db_bytes(settings)
    assert all(SECRET.encode() not in b.read_bytes() for b in safety.list_backups(settings.backups))
    assert len(safety.list_backups(settings.backups)) == 1
    ok, msg = brain.verify()
    assert ok and "erased" in msg
    before = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot() == before


def test_erase_by_period(brain, session):
    brain.remember(session, [{"kind": "episode", "text": "消える出来事"}])
    plan = safety.plan_erase(brain, since="2000-01-01")
    safety.erase(brain, plan, "消去する")
    assert brain.stats()["episode"] == 0


def test_pause_blocks_ai_writes_but_not_recall(brain, session):
    brain.remember(session, [{"kind": "semantic", "text": "記憶はある", "concepts": ["テスト"]}])
    brain.set_paused(True)
    with pytest.raises(InvalidInput, match="一時停止"):
        brain.remember(session, [{"kind": "semantic", "text": "追加できない"}])
    with pytest.raises(InvalidInput, match="一時停止"):
        report(brain, session, [])
    events_before = brain.stats()["events"]
    assert brain.recall(session, "テスト")["memory_ids"]
    assert brain.stats()["events"] == events_before  # recall wrote nothing while paused
    brain.set_paused(False)
    brain.remember(session, [{"kind": "semantic", "text": "再開後は追加できる"}])


def test_backup_and_restore(brain, settings, session):
    brain.remember(session, [{"kind": "semantic", "text": "バックアップ前の記憶"}])
    snap = safety.backup(brain)
    brain.remember(session, [{"kind": "semantic", "text": "あとで入った記憶"}])
    out = safety.restore(brain, snap)
    assert out["verified"]
    assert brain.stats()["semantic"] == 1
    assert (settings.home / out["previous_brain"].split("/")[-1]).exists()


def test_backups_are_rotated(brain):
    for _ in range(safety.BACKUP_KEEP + 3):
        safety.backup(brain)
    assert len(safety.list_backups(brain.settings.backups)) == safety.BACKUP_KEEP


def test_restore_rejects_foreign_files(brain, tmp_path):
    other = tmp_path / "other.db"
    import sqlite3
    sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
    with pytest.raises(InvalidInput, match="バックアップではありません"):
        safety.restore(brain, other)
