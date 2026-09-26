from datetime import datetime

import pytest

from exobrain.bookshelf import body_sha256, read_body, write_original


def test_original_is_kept_verbatim(tmp_path):
    body = "# 日報\r\n改行コードも  空白も\tそのまま\n\n---\n区切り線も本文のうち\n"
    o = write_original(tmp_path, "src_1", "daily_report", "ai", "Claude Desktop", "要件/定義: v1", body,
                       created=datetime(2026, 9, 26, 14, 32).astimezone())
    assert read_body(o.path) == body
    assert o.sha256 == body_sha256(body)
    assert o.path.parent == tmp_path / "2026" / "09"
    assert o.path.name.startswith("20260926-1432_Claude_Desktop_要件_定義_")
    header = o.path.read_text(encoding="utf-8").split("---\n")[1]
    assert "id: src_1" in header and 'title: "要件/定義: v1"' in header


def test_never_overwrites(tmp_path):
    when = datetime(2026, 9, 26, 9, 0).astimezone()
    a = write_original(tmp_path, "src_aaaaaa", "memo", "human", None, "同じ題", "一つ目", created=when)
    b = write_original(tmp_path, "src_bbbbbb", "memo", "human", None, "同じ題", "二つ目", created=when)
    assert a.path != b.path
    assert read_body(a.path) == "一つ目" and read_body(b.path) == "二つ目"
    with pytest.raises(FileExistsError):
        open(a.path, "x").close()
