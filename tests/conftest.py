import pytest

from exobrain.brain import Brain
from exobrain.config import Settings


@pytest.fixture(autouse=True)
def no_real_launch_agents(monkeypatch, tmp_path):
    """Tests must never touch the owner's real ~/Library/LaunchAgents or launchctl.
    (2026-10-07: test_timer_is_saved rewrote the real sleep agent to log into a pytest temp folder;
    once pytest removed that folder, the nightly sleep stopped running.)"""
    import exobrain.macos as macos

    monkeypatch.setattr(macos, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(macos, "apply_sleep_timer", lambda settings, timer: False)


@pytest.fixture(autouse=True)
def no_real_claude_for_memory_md(monkeypatch):
    """memory.md is written by the real Claude Code after a sleep; tests never call it."""
    import exobrain.portrait as portrait

    def refuse(brain):
        raise RuntimeError("tests do not call Claude Code")

    monkeypatch.setattr(portrait, "write", refuse)


@pytest.fixture(autouse=True)
def no_real_claude_for_screening(monkeypatch):
    """Screening asks the real Claude Code during the day; in tests the AI cannot be asked (nothing is dropped)."""
    import exobrain.screen as screen

    monkeypatch.setattr(screen, "ask_claude", lambda brain, text: None)


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
