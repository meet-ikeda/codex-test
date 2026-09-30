from urllib.parse import unquote

from .conftest import report

from exobrain.shelves import write_shelf_index

LOG = """# AI日報 · 見積もり · 2026-09-29

## 今日の出来事

- [[A社|A社様]]の見積もりを [[田中さん]] と見直した

## 決まったこと

- [[A社]]には来週金曜に再提出する

## 未解決・次に続くこと

- [[田中さん]]に単価を確認する
"""


def links(text: str) -> list[str]:
    return [unquote(part.split(")")[0]) for part in text.split("](")[1:]]


def test_marked_names_become_topic_notes_that_open_the_originals(brain, settings, session):
    r = report(brain, session, [], title="見積もりの日報", body=LOG)
    names = write_shelf_index(brain)
    assert {"話題/A社.md", "話題/田中さん.md", "はじめに.md", "目次/決定.md", "目次/未解決.md"} <= set(names)

    note = (settings.bookshelf / "話題" / "A社.md").read_text(encoding="utf-8")
    assert 'aliases: ["A社様"]' in note  # Obsidian finds the note under the other name too
    assert "A社様の見積もりを 田中さん と見直した" in note  # the line, read as a person would
    assert "[田中さん](%E7%94%B0%E4%B8%AD%E3%81%95%E3%82%93.md)" in note  # topics seen together
    source_link = [t for t in links(note) if t.startswith("../")][0]
    assert (settings.bookshelf / "話題" / source_link).resolve() == (settings.drive_root / r["path"]).resolve()

    month = next(n for n in names if n.startswith("目次/月別/"))
    text = (settings.bookshelf / month).read_text(encoding="utf-8")
    assert "A社には来週金曜に再提出する" in text and "田中さんに単価を確認する" in text
    assert "田中さんに単価を確認する" in (settings.bookshelf / "目次" / "未解決.md").read_text(encoding="utf-8")


def test_confidential_lines_are_not_copied_into_the_indexes(brain, settings):
    brain.add_memo("社外秘の件", "#機密\n\n- [[B社]]の買収額は 3 億円\n\n## 決まったこと\n\n- [[B社]]と契約する\n")
    names = write_shelf_index(brain)
    note = (settings.bookshelf / "話題" / "B社.md").read_text(encoding="utf-8")
    assert "機密のため抜粋なし" in note and "3 億円" not in note
    for n in names:
        assert "3 億円" not in (settings.bookshelf / n).read_text(encoding="utf-8")
        assert "と契約する" not in (settings.bookshelf / n).read_text(encoding="utf-8")


def test_rebuilt_every_sleep_and_old_shelves_removed(brain, settings, session):
    legacy = settings.bookshelf / "棚"
    legacy.mkdir(parents=True)
    (legacy / "日付別.md").write_text("old")
    (legacy / "話題_古い.md").write_text("old")
    report(brain, session, [], title="一つ目", body="- [[続く話題]]の話\n")
    write_shelf_index(brain)
    topics = settings.bookshelf / "話題"
    assert (topics / "続く話題.md").exists() and not legacy.exists()

    made_by_us = (topics / "続く話題.md").read_text(encoding="utf-8")
    (topics / "消えた話題.md").write_text(made_by_us)  # a note from an earlier night whose topic is gone
    (topics / "手で置いた.md").write_text("オーナーのノート")
    write_shelf_index(brain)
    assert not (topics / "消えた話題.md").exists()
    assert (topics / "手で置いた.md").read_text(encoding="utf-8") == "オーナーのノート"  # a person's file is never removed
