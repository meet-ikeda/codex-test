"""macOS launchd agents (design §10). Installed by `exobrain install` (M6)."""

from __future__ import annotations

import plistlib
from pathlib import Path

SLEEP_LABEL = "jp.exobrain.sleep"


def sleep_agent(exobrain: str, hour: int | None = None, minute: int = 0, log_dir: Path | None = None) -> bytes:
    """Run `exobrain sleep --if-due` at login (RunAtLoad) and, optionally, every day at hour:minute.

    launchd runs a calendar job that was missed while the Mac slept once it wakes
    (https://www.launchd.info), and --if-due keeps that from sleeping twice.
    """
    plist: dict = {
        "Label": SLEEP_LABEL,
        "ProgramArguments": [exobrain, "sleep", "--if-due"],
        "RunAtLoad": True,
    }
    if hour is not None:
        plist["StartCalendarInterval"] = {"Hour": int(hour), "Minute": int(minute)}
    if log_dir is not None:
        plist["StandardOutPath"] = str(log_dir / "sleep.out.log")
        plist["StandardErrorPath"] = str(log_dir / "sleep.err.log")
    return plistlib.dumps(plist)


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def exobrain_executable() -> str | None:
    import shutil
    import sys

    beside = Path(sys.executable).with_name("exobrain")
    return str(beside) if beside.exists() else shutil.which("exobrain")


def apply_sleep_timer(settings, timer: dict | None) -> bool:
    """Write the sleep agent and (re)load it with launchctl. Returns False off macOS.

    Uses `launchctl bootout` / `bootstrap gui/<uid>`, the replacements for the
    deprecated load/unload verbs.
    """
    import os
    import subprocess
    import sys

    if sys.platform != "darwin":
        return False
    exe = exobrain_executable()
    if exe is None:
        return False
    plist = launch_agents_dir() / f"{SLEEP_LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    hour = timer["hour"] if timer else None
    minute = timer["minute"] if timer else 0
    plist.write_bytes(sleep_agent(exe, hour, minute, settings.home))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    done = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True)
    return done.returncode == 0
