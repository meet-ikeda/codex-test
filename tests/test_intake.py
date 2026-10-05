"""Taking things in (spec v0.5 §5): receiving box, AI daily logs, #remember notes, Downloads."""

import os
import time

import pytest

from exobrain import daily
from exobrain.brain import Brain
from exobrain.chunks import chunk_document
from exobrain.config import Settings
from exobrain.inbox import ingest, pending_inbox, scan_vault

FIELDS = {"source": "codex", "ai_provider": "openai", "ai_product": "Codex", "ai_model": "gpt-6",
          "thread_id": "t-1", "entry_date": "2026-09-24", "period_start": "2026-09-24T00:00:00+09:00",
          "period_end": "2026-09-24T23:59:59+09:00", "generated_at": "2026-09-24T23:50:00+09:00"}


def log(cursor, prev="", date="2026-09-24", **sections):
    f = {**FIELDS, "entry_date": date, "cursor": cursor, "previous_cursor": prev}
    return daily.render(f, "OUTBRAIN開発", sections or {"events": ["作業した"]})


def settle(path):
    old = time.time() - 10
    os.utime(path, (old, old))
    return path


def put(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    return settle(folder / name)if (folder / name).write_text(text, encoding="utf-8") else None


# ---- chunks -----------------------------------------------------------------------------------


def test_chunks_keep_their_place_in_the_original():
    body = "# 見出し1\n本文1\n" + "あ" * 50 + "\n## 見出し2\n本文2\n"
    chunks = chunk_document(body, max_chars=40)
    assert [c.heading for c in chunks][0] == "見出し1"
    for c in chunks:
        assert body[c.start : c.start + c.length] == c.text
    assert chunks[-1].heading == "見出し2" and chunks[-1].line_end == 5
    assert "".join(c.text for c in chunks) == body


# ---- AI daily logs --------------------------------------------------------------------------------


def test_protocol_v1_round_trip_matches_outbrain_format():
    text = log("c1", decisions=["md は写し"])
    meta = daily.parse(text)
    assert meta.key == "codex:t-1" and meta.thread_title == "OUTBRAIN開発" and meta.fields["ai_model"] == "gpt-6"
    assert "## 決まったこと\n\n- md は写し" in text and "## ボツになったこと\n\n- 特になし" in text and text.endswith("#remember\n")
    assert daily.filename({**FIELDS, "cursor": "c1"}, "OUTBRAIN開発").startswith("2026-09-24__codex__OUTBRAIN開発__")


def test_continuity_is_checked():
    assert daily.check_continuity(daily.parse(log("c1")), None)
    assert not daily.check_continuity(daily.parse(log("c1")), "c1")  # already filed
    assert daily.check_continuity(daily.parse(log("c2", prev="c1")), "c1")
    with pytest.raises(daily.DailyRejected, match="つながっていません"):
        daily.check_continuity(daily.parse(log("c3", prev="cX")), "c2")
    with pytest.raises(daily.DailyRejected, match="最初の日報"):
        daily.check_continuity(daily.parse(log("c2", prev="c1")), None)
    with pytest.raises(daily.DailyRejected, match="cursor"):
        daily.parse(log("c1").replace('outbrain_cursor: "c1"', 'outbrain_cursor: ""'))


def test_daily_logs_in_the_box_are_filed_in_order_and_enter_the_hippocampus(brain, settings):
    box = settings.ai_daily_inbox / "2026-09"
    put(box, "2026-09-25__codex__x.md", log("c2", prev="c1", date="2026-09-25"))
    put(box, "2026-09-24__codex__x.md", log("c1"))
    added = ingest(brain)
    assert [a["kind"] for a in added] == ["ai_daily", "ai_daily"]
    assert brain._conn.execute("SELECT cursor FROM ai_checkpoints WHERE key = 'codex:t-1'").fetchone()[0] == "c2"
    rows = brain._conn.execute("SELECT s.ai_name, h.status FROM sources s JOIN hippocampus h ON h.source_id = s.id")
    assert [tuple(r) for r in rows] == [("Codex", "waiting"), ("Codex", "waiting")]
    assert not any(box.iterdir())  # taken out of the box; the originals are on the bookshelf


def test_a_broken_chain_is_set_aside_with_the_reason(brain, settings):
    put(settings.ai_daily_inbox, "a.md", log("c1"))
    ingest(brain)
    put(settings.ai_daily_inbox, "b.md", log("c9", prev="c7"))
    assert ingest(brain) == []
    waiting = pending_inbox(brain)
    assert waiting[0]["needs_check"] and "つながっていません" in waiting[0]["reason"]


def test_extra_inbox_is_read_only(tmp_path):
    outbrain = tmp_path / "OUTBRAIN" / "海馬" / "Inbox" / "AI日報"
    put(outbrain / "2026-09", "2026-09-24__codex.md", log("c1"))
    put(outbrain, "README.md", "# これは説明です\n")
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", extra_inboxes=(outbrain,))
    with Brain(s) as b:
        assert [a["kind"] for a in ingest(b)] == ["ai_daily"]
        assert ingest(b) == []  # not re-read
        assert (outbrain / "2026-09" / "2026-09-24__codex.md").exists()  # never moved
        assert b.stats()["sources"] == 1  # the README is not an AI daily log


def test_downloads_only_moves_ai_daily_logs(tmp_path):
    dl = tmp_path / "Downloads"
    put(dl, "gemini-日報.md", log("g1").replace('"codex"', '"gemini"'))
    put(dl, "買い物メモ.md", "# 牛乳\n")
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", downloads_dir=dl)
    with Brain(s) as b:
        added = ingest(b)
        assert [a["kind"] for a in added] == ["ai_daily"]
        assert sorted(p.name for p in dl.iterdir()) == ["買い物メモ.md"]  # the owner's other files stay


# ---- #remember notes ---------------------------------------------------------------------------


def test_vault_remember_notes_are_copied_never_written(tmp_path):
    vault = tmp_path / "vault"
    put(vault / "00_Inbox", "覚える.md", "# 方針\n結論から書く。\n\n#remember\n")
    put(vault, "タグ.md", "---\ntags: [remember, 仕事]\n---\n本文\n")
    put(vault, "忘れる.md", "#remember #forget\nやめた\n")
    put(vault, "コード.md", "```\n#remember\n```\n説明\n")
    put(vault, "ふつう.md", "ただのメモ\n")
    put(vault / ".obsidian", "x.md", "#remember\n")
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", vault_root=vault)
    with Brain(s) as b:
        added = scan_vault(b)
        assert sorted(a["title"] for a in added) == ["タグ", "覚える"]
        assert scan_vault(b) == []
        origin = b._conn.execute("SELECT origin_file, author, kind FROM sources ORDER BY title").fetchall()
        assert [tuple(r) for r in origin] == [("タグ.md", "human", "remember_note"),
                                              ("00_Inbox/覚える.md", "human", "remember_note")]
        # Edited again: filed as a new version (the old one stays on the bookshelf).
        settle(vault / "00_Inbox" / "覚える.md").write_text("# 方針\n結論から書く。理由はあとで。\n#remember\n",
                                                            encoding="utf-8")
        settle(vault / "00_Inbox" / "覚える.md")
        assert len(scan_vault(b)) == 1 and b.stats()["sources"] == 3
    assert all(p.read_bytes() == before[p] for p in before if p.name != "覚える.md")
