"""Where things live.

- home (PC-local, default ~/.exobrain): the brain database. Never put this in a
  sync folder: a live SQLite file can be corrupted by sync clients.
- drive root (Google Drive sync folder): inbox, bookshelf and backups. These are
  write-once files, which are safe to sync.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

INBOX_DIR = "受け取り箱"
BOOKSHELF_DIR = "本棚"
ORIGINALS_DIR = "原文"
SHELVES_DIR = "棚"
DREAMS_DIR = "夢日記"
BACKUP_DIR = "バックアップ"


@dataclass(frozen=True)
class Settings:
    home: Path
    drive_root: Path

    @property
    def db_path(self) -> Path:
        return self.home / "brain.db"

    @property
    def inbox(self) -> Path:
        return self.drive_root / INBOX_DIR

    @property
    def bookshelf(self) -> Path:
        return self.drive_root / BOOKSHELF_DIR

    @property
    def originals(self) -> Path:
        return self.bookshelf / ORIGINALS_DIR

    @property
    def backups(self) -> Path:
        return self.drive_root / BACKUP_DIR

    def ensure_dirs(self) -> None:
        for d in (self.home, self.inbox, self.originals, self.bookshelf / SHELVES_DIR, self.backups):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Resolve settings from env vars, then ~/.exobrain/config.json, then defaults."""
    home = Path(os.environ.get("EXOBRAIN_HOME", "~/.exobrain")).expanduser()
    drive = os.environ.get("EXOBRAIN_DRIVE")
    if not drive:
        cfg = home / "config.json"
        if cfg.exists():
            drive = json.loads(cfg.read_text(encoding="utf-8")).get("drive_root")
    drive_root = Path(drive).expanduser() if drive else home / "drive"
    return Settings(home=home, drive_root=drive_root)
