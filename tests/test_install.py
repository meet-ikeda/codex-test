import json
import plistlib
import tomllib

import pytest

from exobrain import install as inst
from exobrain.config import Settings

OTHER_TOML = '''model = "gpt-5"

[mcp_servers.github]
command = "gh-mcp"

[mcp_servers.exobrain]
command = "/old/exobrain-mcp"

[mcp_servers.exobrain.env]
X = "1"

[profiles.work]
model = "o3"
'''


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    drive = h / "Library" / "CloudStorage" / "GoogleDrive-ikeda@example.com"
    (drive / "マイドライブ").mkdir(parents=True)
    (drive / "共有ドライブ").mkdir()
    (drive / ".shortcut-targets-by-id").mkdir()
    desk = inst.claude_desktop_config(h)
    desk.parent.mkdir(parents=True)
    desk.write_text(json.dumps({"mcpServers": {"filesystem": {"command": "npx", "args": ["fs"]}}, "theme": "dark"}))
    codex = inst.codex_config(h)
    codex.parent.mkdir(parents=True)
    codex.write_text(OTHER_TOML)
    return h


@pytest.fixture
def calls():
    return []


def run_install(home, calls, **kw):
    settings = Settings(home=home / ".exobrain", drive_root=home / "unused")
    drive = inst.drive_candidates(home)[0]
    return inst.install(settings, home, drive, run=lambda c: calls.append(c) or 0, uid=501, platform="darwin", **kw)


def test_drive_candidates_skip_shared_and_hidden(home):
    assert [p.parent.name for p in inst.drive_candidates(home)] == ["マイドライブ"]


def test_read_only_computer_backups_are_never_chosen(tmp_path, monkeypatch):
    # the layout seen on a real Mac: "その他のパソコン" sorts before "マイドライブ" and is read-only (dr-x------)
    account = tmp_path / "Library" / "CloudStorage" / "GoogleDrive-ikeda@example.com"
    (account / "その他のパソコン" / "USB と外部デバイス").mkdir(parents=True)
    (account / "マイドライブ").mkdir()
    (account / "作業用").mkdir()
    monkeypatch.setattr("os.access", lambda p, mode: p.name != "その他のパソコン")  # root ignores chmod
    assert inst.drive_candidates(tmp_path) == [account / "マイドライブ" / "exobrain", account / "作業用" / "exobrain"]
    monkeypatch.setattr("os.access", lambda p, mode: False)
    assert inst.drive_candidates(tmp_path) == [account / "マイドライブ" / "exobrain"]


def test_unwritable_drive_fails_before_touching_anything(home, tmp_path):
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("")  # mkdir below it fails even for root, like the read-only folder did
    desk_before = inst.claude_desktop_config(home).read_text()
    settings = Settings(home=home / ".exobrain", drive_root=home / "x")
    r = inst.install(settings, home, blocked / "exobrain", run=lambda c: 0, uid=501, platform="darwin")
    assert not r.ok and "--drive" in r.text()
    assert not (home / ".exobrain" / "config.json").exists()
    assert inst.claude_desktop_config(home).read_text() == desk_before


def test_install_wires_everything(home, calls):
    r = run_install(home, calls, sleep_at=(3, 5))
    assert r.ok, r.text()

    cfg = json.loads((home / ".exobrain" / "config.json").read_text())
    assert cfg["drive_root"].endswith("マイドライブ/exobrain") and cfg["sleep_timer"] == {"hour": 3, "minute": 5}
    assert (inst.drive_candidates(home)[0] / "本棚" / "原文").is_dir()

    desk = json.loads(inst.claude_desktop_config(home).read_text())
    assert desk["mcpServers"]["filesystem"] == {"command": "npx", "args": ["fs"]} and desk["theme"] == "dark"
    assert desk["mcpServers"]["exobrain"]["command"].endswith("exobrain-mcp")

    codex = inst.codex_config(home).read_text()
    parsed = tomllib.loads(codex)
    assert parsed["mcp_servers"]["github"] == {"command": "gh-mcp"}
    assert parsed["profiles"]["work"] == {"model": "o3"} and parsed["model"] == "gpt-5"
    assert parsed["mcp_servers"]["exobrain"]["command"].endswith("exobrain-mcp")
    assert "env" not in parsed["mcp_servers"]["exobrain"] and "/old/" not in codex

    backups = list(inst.codex_config(home).parent.glob("config.toml.before-exobrain-*"))
    assert len(backups) == 1 and backups[0].read_text() == OTHER_TOML

    agents = home / "Library" / "LaunchAgents"
    app = plistlib.loads((agents / "jp.exobrain.app.plist").read_bytes())
    assert app["ProgramArguments"][1:] == ["app", "--no-browser", "--port", "8765"] and app["KeepAlive"]
    sleep = plistlib.loads((agents / "jp.exobrain.sleep.plist").read_bytes())
    assert sleep["StartCalendarInterval"] == {"Hour": 3, "Minute": 5} and sleep["RunAtLoad"]
    assert ["launchctl", "bootstrap", "gui/501", str(agents / "jp.exobrain.app.plist")] in calls


def test_install_twice_changes_nothing(home, calls):
    run_install(home, calls)
    desk, codex = inst.claude_desktop_config(home).read_text(), inst.codex_config(home).read_text()
    r = run_install(home, calls)
    assert inst.claude_desktop_config(home).read_text() == desk and inst.codex_config(home).read_text() == codex
    assert "exobrain を登録しました" in r.text()
    assert len(list(inst.codex_config(home).parent.glob("config.toml.before-exobrain-*"))) == 1


def test_fresh_machine_without_configs(tmp_path, calls):
    h = tmp_path / "fresh"
    (h / "Library" / "CloudStorage" / "GoogleDrive-a@b.c" / "My Drive").mkdir(parents=True)
    r = run_install(h, calls)
    assert r.ok
    assert json.loads(inst.claude_desktop_config(h).read_text())["mcpServers"]["exobrain"]
    assert tomllib.loads(inst.codex_config(h).read_text())["mcp_servers"]["exobrain"]


def test_launchctl_failure_is_reported(home):
    settings = Settings(home=home / ".exobrain", drive_root=home / "x")
    r = inst.install(settings, home, inst.drive_candidates(home)[0], run=lambda c: 5 if "bootstrap" in c else 0,
                     uid=501, platform="darwin")
    assert not r.ok and "exit 5" in r.text()


def test_uninstall_removes_only_ours(home, calls):
    run_install(home, calls)
    r = inst.uninstall(home, run=lambda c: calls.append(c) or 0, uid=501)
    desk = json.loads(inst.claude_desktop_config(home).read_text())
    assert "exobrain" not in desk["mcpServers"] and "filesystem" in desk["mcpServers"]
    parsed = tomllib.loads(inst.codex_config(home).read_text())
    assert "exobrain" not in parsed["mcp_servers"] and parsed["mcp_servers"]["github"]
    assert not (home / "Library" / "LaunchAgents" / "jp.exobrain.app.plist").exists()
    assert (home / ".exobrain" / "config.json").exists()  # memories and settings stay
    assert "そのまま残しています" in r.text()


def test_doctor(home, calls, brain):
    run_install(home, calls)
    settings = Settings(home=home / ".exobrain", drive_root=inst.drive_candidates(home)[0])
    r = inst.doctor(settings, home, brain)
    text = r.text()
    assert "Claude Desktop への登録" in text and "Codex への登録" in text and "改ざんチェック" in text
    assert r.ok
