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
