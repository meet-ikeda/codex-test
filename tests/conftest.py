import pytest

from exobrain.brain import Brain
from exobrain.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(home=tmp_path / "home", drive_root=tmp_path / "drive", embed_model="")


@pytest.fixture
def brain(settings):
    b = Brain(settings)
    yield b
    b.close()


@pytest.fixture
def session(brain):
    return brain.start_session("Claude Desktop")["session_id"]


def report(brain, session, elements, title="テスト日報", body="# 日報\n本文です。\n", used=()):
    return brain.submit_daily_report(session, title, body, elements, list(used))
