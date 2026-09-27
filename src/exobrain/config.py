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


AI_DAILY_DIR = "AI日報"
NEEDS_CHECK_DIR = "確認が必要"
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
HIPPOCAMPUS_DAYS = 7  # how long information stays in the hippocampus waiting for sleep (spec v0.5 §3)


@dataclass(frozen=True)
class Settings:
    home: Path
    drive_root: Path
    # The owner's Obsidian vault: read only, scanned for #remember notes (spec v0.5 §5.1).
    vault_root: Path | None = None
    # Other folders whose AI daily logs are copied in, never moved (e.g. OUTBRAIN's inbox).
    extra_inboxes: tuple[Path, ...] = ()
    # ~/Downloads is watched for AI daily logs saved from Gemini etc. (spec v0.5 §5.2).
    downloads_dir: Path | None = None
    # Embedding model served by Ollama. "" turns semantic search off (lexical search still works).
    embed_model: str = DEFAULT_EMBED_MODEL
    ollama_url: str = DEFAULT_OLLAMA_URL
    hippocampus_days: int = HIPPOCAMPUS_DAYS
    # Nightly AI daily logs from conversations kept on this Mac (spec v0.5 §5.2). Off until the owner
    # says from when (ISO date), so the first night does not read years of history.
    daily_logs_since: str | None = None
    codex_sessions: Path | None = None
    claude_projects: Path | None = None
    cowork_sessions: Path | None = None

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

    @property
    def ai_daily_inbox(self) -> Path:
        return self.inbox / AI_DAILY_DIR

    def ensure_dirs(self) -> None:
        for d in (self.home, self.inbox, self.ai_daily_inbox, self.originals, self.bookshelf / SHELVES_DIR,
                  self.backups):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Resolve settings from env vars, then ~/.exobrain/config.json, then defaults."""
    home = Path(os.environ.get("EXOBRAIN_HOME", "~/.exobrain")).expanduser()
    cfg_path = home / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    drive = os.environ.get("EXOBRAIN_DRIVE") or cfg.get("drive_root")
    drive_root = Path(drive).expanduser() if drive else home / "drive"
    path_or_none = lambda v: Path(v).expanduser() if v else None  # noqa: E731
    return Settings(
        home=home,
        drive_root=drive_root,
        vault_root=path_or_none(cfg.get("vault_root")),
        extra_inboxes=tuple(Path(p).expanduser() for p in cfg.get("extra_inboxes", [])),
        downloads_dir=path_or_none(cfg.get("downloads_dir", "~/Downloads")),
        embed_model=os.environ.get("EXOBRAIN_EMBED_MODEL", cfg.get("embed_model", DEFAULT_EMBED_MODEL)),
        ollama_url=cfg.get("ollama_url", DEFAULT_OLLAMA_URL),
        hippocampus_days=int(cfg.get("hippocampus_days", HIPPOCAMPUS_DAYS)),
        daily_logs_since=cfg.get("daily_logs_since"),
        codex_sessions=path_or_none(cfg.get("codex_sessions", "~/.codex/sessions")),
        claude_projects=path_or_none(cfg.get("claude_projects", "~/.claude/projects")),
        cowork_sessions=path_or_none(cfg.get("cowork_sessions",
                                             "~/Library/Application Support/Claude/local-agent-mode-sessions")),
    )
