"""The receiving box (預かりBOX): everything the brain takes in (spec v0.5 §5).

Whatever arrives — a memo, a #remember note from Obsidian, an AI daily log — is
filed on the bookshelf verbatim, chunked for search, and enters the hippocampus
to wait for sleep. Nothing here writes to the cortex.

Sources, in the order `ingest()` looks at them:
- the receiving box folder on Google Drive (memos; AI daily logs, also in its AI日報/ subfolder)
- extra inboxes such as OUTBRAIN's (read only: logs are copied, never moved)
- ~/Downloads (only files marked as AI daily logs, e.g. saved from Gemini; they are moved in)
`scan_vault()` separately copies #remember notes from the owner's Obsidian vault.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import daily
from .bookshelf import body_sha256, write_original

if TYPE_CHECKING:
    from .brain import Brain

ACCEPTED = {".md", ".markdown", ".txt"}
SETTLE_SECONDS = 2.0  # a file still being written or synced is left for next time
UNREADABLE_DIR = "読めなかった"
DOWNLOADS_MAX_AGE_DAYS = 14
VAULT_NOTE_MAX = 50_000
SKIP_KINDS_FOR_HIPPOCAMPUS = {"dream"}
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def _tag_present(text: str, tag: str, fm: dict[str, Any]) -> bool:
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if any(str(t).lstrip("#") == tag for t in tags):
        return True
    return re.search(rf"(?<![\w/#&]){re.escape('#' + tag)}(?![\w/-])", _CODE.sub("", text)) is not None


def add_source(brain: Brain, *, kind: str, author: str, ai_name: str | None, title: str, body: str,
               actor: str = "human", origin_file: str | None = None, meta: dict | None = None,
               checkpoint: daily.DailyMeta | None = None) -> dict | None:
    """File an original on the bookshelf and let it enter the hippocampus. None if already filed."""
    from .brain import TITLE_MAX, InvalidInput, new_id

    title = " ".join(title.split())[:TITLE_MAX] or "メモ"
    if not body.strip():
        raise InvalidInput("本文が空です。")
    sha = body_sha256(body)
    if brain._conn.execute("SELECT 1 FROM sources WHERE sha256 = ? AND erased = 0", (sha,)).fetchone():
        return None
    source_id = new_id("src")
    original = write_original(brain.settings.originals, source_id, kind, author, ai_name, title, body)
    rel = original.path.relative_to(brain.settings.drive_root).as_posix()
    try:
        with brain._tx():
            brain._emit(actor, "source_added", {
                "id": source_id, "kind": kind, "author": author, "ai_name": ai_name, "title": title,
                "path": rel, "sha256": original.sha256, "created_at": original.created_at,
                **({"origin_file": origin_file} if origin_file else {}), **({"meta": meta} if meta else {}),
            })
            if kind not in SKIP_KINDS_FOR_HIPPOCAMPUS:
                expires = datetime.now(timezone.utc) + timedelta(days=brain.settings.hippocampus_days)
                brain._emit(actor, "hippocampus_entered", {"source_id": source_id,
                                                           "expires_at": expires.isoformat(timespec="seconds")})
            if checkpoint is not None:
                f = checkpoint.fields
                brain._emit(actor, "ai_checkpoint_set", {
                    "key": checkpoint.key, "source": f["source"], "thread_id": f["thread_id"],
                    "cursor": f["cursor"], "entry_date": f.get("entry_date"), "source_id": source_id,
                })
    except BaseException:
        original.path.unlink(missing_ok=True)
        raise
    return {"source_id": source_id, "kind": kind, "title": title, "path": rel}


def add_memo(brain: Brain, title: str, text: str, actor: str = "human") -> dict | None:
    """The owner hands over a memo. Filed on the bookshelf as is."""
    from .brain import InvalidInput

    if not text.strip():
        raise InvalidInput("メモが空です。")
    return add_source(brain, kind="memo", author="human", ai_name=None, title=title, body=text, actor=actor)


def current_cursor(brain: Brain, key: str) -> str | None:
    row = brain._conn.execute("SELECT cursor FROM ai_checkpoints WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def file_daily(brain: Brain, text: str, origin_file: str | None = None, actor: str | None = None) -> dict | None:
    """File an AI daily log after checking that it continues its thread. Raises DailyRejected."""
    meta = daily.parse(text)
    if meta is None:
        raise daily.DailyRejected("AI 日報の形式ではありません（先頭に outbrain_kind: ai_daily がありません）。")
    with brain._lock:
        if not daily.check_continuity(meta, current_cursor(brain, meta.key)):
            return None
        f = meta.fields
        return add_source(
            brain, kind="ai_daily", author="ai", ai_name=meta.ai_name,
            title=f"AI日報 · {meta.thread_title} · {f['entry_date']}", body=text,
            actor=actor or f"ai:{meta.ai_name}", origin_file=origin_file, checkpoint=meta,
            meta={k: f[k] for k in ("source", "ai_provider", "ai_product", "ai_model", "thread_id", "entry_date",
                                    "period_start", "period_end", "generated_at") if f.get(k)},
        )


# ---- folders ----------------------------------------------------------------------------------


def _settled(path: Path, now: float) -> bool:
    return (path.is_file() and not path.name.startswith((".", "~")) and path.suffix.lower() in ACCEPTED
            and now - path.stat().st_mtime >= SETTLE_SECONDS)


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _needs_check(inbox: Path, path: Path, reason: str) -> None:
    """Move a refused file aside with a note, so the owner can see why."""
    target_dir = inbox / "確認が必要"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target.with_name(f"{target.stem}_{int(time.time())}{target.suffix}")
    path.rename(target)
    target.with_suffix(target.suffix + ".理由.txt").write_text(reason + "\n", encoding="utf-8")


def _take_from_inbox(brain: Brain, path: Path, now: float) -> dict | None:
    """Claim one file in the receiving box, file it, and remove it (the original stays on the bookshelf)."""
    inbox = brain.settings.inbox
    claimed = path.with_name(f".claimed-{os.getpid()}-{path.name}")
    try:
        path.rename(claimed)  # two exobrain processes never file it twice
    except FileNotFoundError:
        return None
    text = _read(claimed)
    if text is None:
        _set_aside(claimed, path.name)
        return None
    try:
        if daily.front_matter(text).get("outbrain_kind") == "ai_daily":
            result = file_daily(brain, text, origin_file=path.relative_to(inbox).as_posix())
        else:
            result = add_memo(brain, path.stem, text)
    except daily.DailyRejected as e:
        claimed.rename(path)
        _needs_check(inbox, path, str(e))
        return None
    except BaseException:
        claimed.rename(path)  # put it back for next time
        raise
    claimed.unlink()
    return result


def _copy_from_extra(brain: Brain, root: Path, path: Path, seen: dict[str, str]) -> dict | None:
    """Extra inboxes are read only: remember what was looked at so it is not re-read every minute."""
    stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
    key = str(path)
    if seen.get(key) == stamp:
        return None
    text = _read(path)
    result = None
    if text is not None and daily.front_matter(text).get("outbrain_kind") == "ai_daily":
        try:
            result = file_daily(brain, text, origin_file=f"{root.name}/{path.relative_to(root).as_posix()}")
        except daily.DailyRejected as e:
            _log(brain, f"取り込めなかった AI 日報: {path} — {e}")
    seen[key] = stamp
    return result


def _move_from_downloads(brain: Brain, path: Path, now: float) -> None:
    if now - path.stat().st_mtime > DOWNLOADS_MAX_AGE_DAYS * 86400:
        return
    text = _read(path)
    if text is None or daily.front_matter(text).get("outbrain_kind") != "ai_daily":
        return
    month = datetime.now().strftime("%Y-%m")
    target_dir = brain.settings.ai_daily_inbox / month
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target.with_name(f"{target.stem}_{int(now)}{target.suffix}")
    path.rename(target)
    os.utime(target, (now - SETTLE_SECONDS, now - SETTLE_SECONDS))  # ready to be taken right away


def ingest(brain: Brain) -> list[dict]:
    """Take in everything that has arrived. Returns the newly filed sources."""
    s = brain.settings
    added: list[dict] = []
    now = time.time()
    if s.downloads_dir and s.downloads_dir.is_dir():
        for path in sorted(s.downloads_dir.iterdir()):
            if _settled(path, now) and path.suffix.lower() == ".md":
                _move_from_downloads(brain, path, now)
    if s.inbox.exists():
        paths = [p for p in sorted(s.inbox.iterdir()) if _settled(p, now)]
        if s.ai_daily_inbox.exists():
            # Oldest first, so a thread's logs are filed in the order their cursors follow each other.
            paths += sorted((p for p in s.ai_daily_inbox.rglob("*") if _settled(p, now)), key=lambda p: p.name)
        for path in paths:
            r = _take_from_inbox(brain, path, now)
            if r:
                added.append(r)
    if s.extra_inboxes:
        seen_path = s.home / "extra_inboxes_seen.json"
        seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
        for root in s.extra_inboxes:
            if not root.is_dir():
                continue
            for path in sorted((p for p in root.rglob("*.md") if _settled(p, now)), key=lambda p: p.name):
                r = _copy_from_extra(brain, root, path, seen)
                if r:
                    added.append(r)
        seen_path.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    return added


def scan_vault(brain: Brain) -> list[dict]:
    """Copy #remember notes (without #forget) from the owner's vault. The vault itself is never written."""
    vault = brain.settings.vault_root
    if not vault or not vault.is_dir():
        return []
    seen_path = brain.settings.home / "vault_seen.json"
    seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    added = []
    now = time.time()
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts) or not _settled(path, now):
            continue
        stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
        if seen.get(rel.as_posix()) == stamp:
            continue
        seen[rel.as_posix()] = stamp
        text = _read(path)
        if text is None:
            continue
        fm = daily.front_matter(text)
        if not _tag_present(text, "remember", fm) or _tag_present(text, "forget", fm):
            continue
        if len(text) > VAULT_NOTE_MAX:
            _log(brain, f"#remember のノートが {len(text)} 文字あります。{VAULT_NOTE_MAX} 文字以下に分けてください: {rel}")
            continue
        r = add_source(brain, kind="remember_note", author="human", ai_name=None, title=path.stem, body=text,
                       origin_file=rel.as_posix(), meta={"vault": vault.name})
        if r:
            added.append(r)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    return added


def pending_inbox(brain: Brain) -> list[dict]:
    """What is waiting in the receiving box (for the screen)."""
    out = []
    inbox = brain.settings.inbox
    if not inbox.exists():
        return out
    for p in sorted(inbox.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in ACCEPTED \
                and not p.name.endswith(".理由.txt"):
            rel = p.relative_to(inbox)
            out.append({"name": p.name, "path": rel.as_posix(),
                        "needs_check": rel.parts[0] == "確認が必要",
                        "reason": _reason(p)})
    return out


def _reason(p: Path) -> str | None:
    note = p.with_suffix(p.suffix + ".理由.txt")
    return note.read_text(encoding="utf-8").strip() if note.exists() else None


def _log(brain: Brain, message: str) -> None:
    with open(brain.settings.home / "ingest.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def _set_aside(claimed: Path, name: str) -> None:
    target_dir = claimed.parent / UNREADABLE_DIR
    target_dir.mkdir(exist_ok=True)
    target = target_dir / name
    if target.exists():
        target = target.with_name(f"{target.stem}_{int(time.time())}{target.suffix}")
    claimed.rename(target)
