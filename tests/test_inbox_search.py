import os
import time

from exobrain.bookshelf import read_body
from exobrain.search import extract_terms


def drop(settings, name, text, age=10, raw=None):
    path = settings.inbox / name
    path.write_bytes(raw if raw is not None else text.encode("utf-8"))
    past = time.time() - age
    os.utime(path, (past, past))
    return path


def test_inbox_memo_goes_to_the_shelf_verbatim(brain, settings):
    drop(settings, "採用サイトの気づき.md", "# 気づき\n社員の生の声は\r\n原文のまま載せる。\n")
    drop(settings, "notes.txt", "BOM 付き", raw="﻿BOM 付き".encode("utf-8"))
    drop(settings, "写真.png", "", raw=b"\x89PNG")
    drop(settings, "書きかけ.md", "まだ同期中", age=0)
    brain.start_session("Claude Desktop")  # starting a conversation takes in the inbox

    rows = brain._conn.execute("SELECT title, path, author, kind FROM sources ORDER BY title").fetchall()
    assert [(r["title"], r["author"], r["kind"]) for r in rows] == [
        ("notes", "human", "memo"), ("採用サイトの気づき", "human", "memo")]
    memo = settings.drive_root / rows[1]["path"]
    assert read_body(memo) == "# 気づき\n社員の生の声は\r\n原文のまま載せる。\n"
    assert sorted(p.name for p in settings.inbox.iterdir() if p.is_file()) == ["写真.png", "書きかけ.md"]
    assert brain.verify()[0]


def test_unreadable_file_is_set_aside(brain, settings):
    drop(settings, "shift_jis.txt", "", raw="日本語".encode("shift_jis"))
    brain.start_session("Codex")
    assert (settings.inbox / "読めなかった" / "shift_jis.txt").exists()
    assert brain.stats()["sources"] == 0


def test_same_memo_twice_is_filed_once(brain):
    assert brain.add_memo("メモ", "同じ内容") is not None
    assert brain.add_memo("メモ", "同じ内容") is None
    assert brain.stats()["sources"] == 1


def test_extract_terms():
    terms = extract_terms("本棚の原文は Google ドライブに置く、と決めたはず")
    assert "本棚" in terms and "原文" in terms and "Google" in terms and "ドライブ" in terms


def test_search_finds_passages(brain):
    brain.add_memo("採用メモ", "前置き。" * 50 + "採用サイトでは社員の生の声を原文のまま載せる。" + "後書き。" * 50)
    brain.add_memo("別件", "今日は雨だった。")
    hits = brain.search_bookshelf("採用サイトに社員の声を載せる件")
    assert [h["title"] for h in hits] == ["採用メモ"]
    assert "社員の生の声" in hits[0]["passage"] and len(hits[0]["passage"]) < 300
    assert brain.search_bookshelf("雨")[0]["title"] == "別件"  # two-letter and shorter terms use LIKE
    assert brain.search_bookshelf("存在しない話題") == []


def test_search_index_survives_rebuild(brain):
    brain.add_memo("メモ", "trigram で検索できる本文")
    brain.rebuild()
    assert brain.search_bookshelf("trigram")[0]["title"] == "メモ"
