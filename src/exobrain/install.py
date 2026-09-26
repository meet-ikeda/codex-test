"""Set exobrain up on a Mac (design §10): one command wires everything together.

- ~/.exobrain/config.json      where the Google Drive folder is, where Claude Code is
- Claude Desktop               ~/Library/Application Support/Claude/claude_desktop_config.json
- Codex                        ~/.codex/config.toml  ([mcp_servers.exobrain])
- launchd agents               the screen (always running) and sleep (at login / on a timer)

Every config file that already exists is copied aside before it is changed, and
uninstall() removes only what install() added. Memories are never touched.
"""

from __future__ import annotations

import json
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import macos
from .config import Settings

SERVER_NAME = "exobrain"
APP_LABEL = "jp.exobrain.app"
APP_PORT = 8765
SHARED_DRIVE_NAMES = {"Shared drives", "共有ドライブ", "Other computers", "パソコン", ".shortcut-targets-by-id"}

Runner = Callable[[list[str]], int]  # runs a command, returns its exit code


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, capture_output=True).returncode


@dataclass
class Report:
    steps: list[tuple[str, str, str]] = field(default_factory=list)  # (status, what, detail)

    def add(self, status: str, what: str, detail: str = "") -> None:
        self.steps.append((status, what, detail))

    def text(self) -> str:
        mark = {"ok": "✓", "changed": "✓", "skip": "–", "warn": "!", "fail": "✗"}
        return "\n".join(f" {mark.get(s, '?')} {w}" + (f"\n     {d}" if d else "") for s, w, d in self.steps)

    @property
    def ok(self) -> bool:
        return not any(s == "fail" for s, _, _ in self.steps)


# ---- locations -------------------------------------------------------------------


def claude_desktop_config(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def codex_config(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def drive_candidates(home: Path) -> list[Path]:
    """Google Drive for desktop keeps accounts under ~/Library/CloudStorage/GoogleDrive-<account>/."""
    out = []
    for account in sorted((home / "Library" / "CloudStorage").glob("GoogleDrive-*")):
        for sub in sorted(p for p in account.iterdir() if p.is_dir()):
            if sub.name not in SHARED_DRIVE_NAMES and not sub.name.startswith("."):
                out.append(sub / "exobrain")
    return out


def executables() -> tuple[str | None, str | None]:
    """Absolute paths of our commands: apps launched by the OS do not share your shell's PATH."""
    def find(name: str) -> str | None:
        beside = Path(sys.executable).with_name(name)
        return str(beside) if beside.exists() else shutil.which(name)

    return find("exobrain"), find("exobrain-mcp")


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    copy = path.with_name(f"{path.name}.before-exobrain-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, copy)
    return copy


# ---- Claude Desktop (JSON) -----------------------------------------------------------


def add_to_claude_desktop(path: Path, mcp_exe: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text().strip() else {}
    servers = data.setdefault("mcpServers", {})
    wanted = {"command": mcp_exe, "args": []}
    if servers.get(SERVER_NAME) == wanted:
        return "unchanged"
    backup = _backup(path)
    servers[SERVER_NAME] = wanted
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"backup: {backup}" if backup else "created"


def remove_from_claude_desktop(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if SERVER_NAME not in data.get("mcpServers", {}):
        return False
    _backup(path)
    del data["mcpServers"][SERVER_NAME]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# ---- Codex (TOML, edited as text so the rest of the file is kept exactly) -----------------


_SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$", re.M)


def _strip_section(text: str, name: str) -> str:
    """Remove [name] and its sub-tables ([name.env] etc.), keeping everything else byte for byte."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        m = _SECTION.match(line.strip())
        if m:
            header = m.group("name").strip()
            skipping = header == name or header.startswith(name + ".")
        if not skipping:
            out.append(line)
    return "".join(out)


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_to_codex(path: Path, mcp_exe: str) -> str:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    section = f"[mcp_servers.{SERVER_NAME}]\ncommand = {_toml_str(mcp_exe)}\nargs = []\n"
    if section in text:
        return "unchanged"
    backup = _backup(path)
    body = _strip_section(text, f"mcp_servers.{SERVER_NAME}").rstrip("\n")
    new = (body + "\n\n" if body else "") + section
    try:
        import tomllib

        tomllib.loads(new)  # never leave Codex with a file it cannot read
    except ImportError:  # Python 3.10
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return f"backup: {backup}" if backup else "created"


def remove_from_codex(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    stripped = _strip_section(text, f"mcp_servers.{SERVER_NAME}")
    if stripped == text:
        return False
    _backup(path)
    path.write_text(stripped.rstrip("\n") + "\n", encoding="utf-8")
    return True


# ---- launchd ---------------------------------------------------------------------------


def app_agent(exe: str, log_dir: Path) -> bytes:
    """Keep the screen running from login, restarting it if it stops."""
    return plistlib.dumps({
        "Label": APP_LABEL,
        "ProgramArguments": [exe, "app", "--no-browser", "--port", str(APP_PORT)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / "app.out.log"),
        "StandardErrorPath": str(log_dir / "app.err.log"),
    })


def _load_agent(home: Path, label: str, content: bytes, run: Runner, uid: int) -> str:
    plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(content)
    domain = f"gui/{uid}"
    run(["launchctl", "bootout", domain, str(plist)])  # fine if it was not loaded
    code = run(["launchctl", "bootstrap", domain, str(plist)])
    return str(plist) if code == 0 else f"launchctl bootstrap failed (exit {code}): {plist}"


def _unload_agent(home: Path, label: str, run: Runner, uid: int) -> bool:
    plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist.exists():
        return False
    run(["launchctl", "bootout", f"gui/{uid}", str(plist)])
    plist.unlink()
    return True


# ---- install / uninstall / doctor ---------------------------------------------------------


def install(settings: Settings, home: Path, drive: Path, sleep_at: tuple[int, int] | None = None,
            agents: bool = True, run: Runner = _run, uid: int | None = None, platform: str = sys.platform) -> Report:
    import os

    r = Report()
    uid = os.getuid() if uid is None else uid
    exe, mcp_exe = executables()
    if not exe or not mcp_exe:
        r.add("fail", "exobrain のコマンドが見つかりません", "uv tool install でインストールしてから実行してください。")
        return r

    # 1) config.json: the drive folder and Claude Code's location (the OS-launched sleep has no shell PATH).
    settings.home.mkdir(parents=True, exist_ok=True)
    cfg_path = settings.home / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg["drive_root"] = str(drive)
    claude = shutil.which("claude") or cfg.get("claude_path")
    if claude:
        cfg["claude_path"] = claude
    if sleep_at:
        cfg["sleep_timer"] = {"hour": sleep_at[0], "minute": sleep_at[1]}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    r.add("changed", f"設定を保存しました: {cfg_path}", f"Google ドライブのフォルダ: {drive}")
    if claude:
        r.add("ok", f"Claude Code: {claude}", "AI による睡眠に使います。")
    else:
        r.add("warn", "Claude Code が見つかりません", "AI による睡眠は動きません（AI なしの睡眠は動きます）。"
              "Claude Code を入れたあと、もう一度 exobrain install を実行してください。")

    # 2) folders
    Settings(settings.home, drive).ensure_dirs()
    r.add("ok", "本棚・受け取り箱・バックアップのフォルダを用意しました", str(drive))

    # 3) Claude Desktop and Codex
    for name, fn, path in (("Claude Desktop", add_to_claude_desktop, claude_desktop_config(home)),
                           ("Codex", add_to_codex, codex_config(home))):
        try:
            result = fn(path, mcp_exe)
            r.add("ok" if result == "unchanged" else "changed", f"{name} に exobrain を登録しました", f"{path}（{result}）")
        except Exception as e:  # keep going; report precisely
            r.add("fail", f"{name} の設定を書き換えられませんでした", f"{path}: {e}")

    # 4) launchd agents (macOS only)
    if not agents:
        r.add("skip", "常駐とタイマーの設定は行いませんでした（--no-agents）")
    elif platform != "darwin":
        r.add("skip", "常駐とタイマーは macOS でのみ設定します")
    else:
        for label, content, what in (
            (APP_LABEL, app_agent(exe, settings.home), f"画面を常駐させました（http://127.0.0.1:{APP_PORT}）"),
            (macos.SLEEP_LABEL, macos.sleep_agent(exe, *(sleep_at or (None, 0)), log_dir=settings.home),
             "睡眠を設定しました（ログイン時" + (f"と毎日 {sleep_at[0]:02d}:{sleep_at[1]:02d}" if sleep_at else "") + "）"),
        ):
            result = _load_agent(home, label, content, run, uid)
            r.add("fail" if "failed" in result else "changed", what, result)
    return r


def uninstall(home: Path, run: Runner = _run, uid: int | None = None) -> Report:
    import os

    r = Report()
    uid = os.getuid() if uid is None else uid
    r.add("changed" if remove_from_claude_desktop(claude_desktop_config(home)) else "skip", "Claude Desktop から外しました")
    r.add("changed" if remove_from_codex(codex_config(home)) else "skip", "Codex から外しました")
    for label in (APP_LABEL, macos.SLEEP_LABEL):
        r.add("changed" if _unload_agent(home, label, run, uid) else "skip", f"{label} を止めました")
    r.add("ok", "記憶（脳・本棚・バックアップ）はそのまま残しています")
    return r


def doctor(settings: Settings, home: Path, brain=None) -> Report:
    r = Report()
    v = sys.version_info
    r.add("ok" if v >= (3, 10) else "fail", f"Python {v.major}.{v.minor}.{v.micro}")
    r.add("ok", f"SQLite {sqlite3.sqlite_version}",
          "" if tuple(map(int, sqlite3.sqlite_version.split("."))) >= (3, 42, 0)
          else "3.42 未満のため、消去時は全文検索の索引を作り直して痕跡を消します。")
    exe, mcp_exe = executables()
    r.add("ok" if exe and mcp_exe else "fail", "exobrain のコマンド", f"{exe} / {mcp_exe}")
    drive = settings.drive_root
    r.add("ok" if drive.exists() else "fail", "Google ドライブのフォルダ", str(drive))
    desk = claude_desktop_config(home)
    registered = desk.exists() and SERVER_NAME in json.loads(desk.read_text(encoding="utf-8") or "{}").get("mcpServers", {})
    r.add("ok" if registered else "warn", "Claude Desktop への登録", str(desk))
    codex = codex_config(home)
    r.add("ok" if codex.exists() and f"[mcp_servers.{SERVER_NAME}]" in codex.read_text(encoding="utf-8") else "warn",
          "Codex への登録", str(codex))
    for label in (APP_LABEL, macos.SLEEP_LABEL):
        plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
        r.add("ok" if plist.exists() else "warn", f"launchd: {label}", str(plist))
    from .sleep import find_claude

    if brain is not None:
        claude = find_claude(brain)
        r.add("ok" if claude else "warn", "Claude Code（AI による睡眠）", claude or "見つかりません")
        ok, msg = brain.verify()
        r.add("ok" if ok else "fail", "改ざんチェック", msg)
        r.add("ok", "FTS5 secure-delete", "有効" if brain.fts_secure_delete else "使えない（代わりに索引を作り直す）")
    return r
