"""Maintenance CLI. The main human interface will be the app screen (M5);
until then, everything the owner may do is reachable here."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import safety
from .brain import InvalidInput, open_brain
from .inbox import ingest


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exobrain", description="exobrain 外部脳の保守コマンド")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="改ざんと本棚の原文をチェックする")
    sub.add_parser("rebuild", help="イベント記録から脳の表を作り直す")
    sub.add_parser("stats", help="記憶の件数を表示する")
    sub.add_parser("ingest", help="受け取り箱のメモを本棚に取り込む")

    s = sub.add_parser("memo", help="メモを渡す（本文は標準入力またはファイル）")
    s.add_argument("title")
    s.add_argument("file", nargs="?", help="省略すると標準入力から読む")

    s = sub.add_parser("search", help="本棚を全文検索する")
    s.add_argument("query")

    s = sub.add_parser("erase", help="記憶を完全に消去する（元に戻せません）")
    s.add_argument("--source", action="append", default=[], help="原文の id（そこから生まれた記憶も消す）")
    s.add_argument("--node", action="append", default=[], help="要素の id")
    s.add_argument("--since", help="この日時以降（例: 2026-09-01）")
    s.add_argument("--until", help="この日時より前")
    s.add_argument("--confirm", default="", help=f"実行するには「{safety.CONFIRM_PHRASE}」と指定する")

    sub.add_parser("pause", help="AI からの書き込みを一時停止する")
    sub.add_parser("resume", help="一時停止を解除する")
    sub.add_parser("backup", help="脳のバックアップを作る")
    s = sub.add_parser("restore", help="バックアップから脳を戻す")
    s.add_argument("snapshot", nargs="?", help="省略すると一覧を表示する")

    a = p.parse_args(argv)
    try:
        with open_brain() as brain:
            if a.cmd == "verify":
                ok, msg = brain.verify()
                print(msg)
                return 0 if ok else 1
            if a.cmd == "rebuild":
                print(f"rebuilt from {brain.rebuild()} events")
            elif a.cmd == "stats":
                _print({**brain.stats(), "paused": brain.paused})
            elif a.cmd == "ingest":
                _print(ingest(brain))
            elif a.cmd == "memo":
                text = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
                _print(brain.add_memo(a.title, text) or "同じメモがすでに本棚にあります")
            elif a.cmd == "search":
                _print(brain.search_bookshelf(a.query))
            elif a.cmd == "erase":
                plan = safety.plan_erase(brain, a.source, a.node, a.since, a.until)
                _print({"消去される原文": plan["sources"], "消去される要素": plan["nodes"]})
                if a.confirm != safety.CONFIRM_PHRASE:
                    print(f"\n確認のため --confirm {safety.CONFIRM_PHRASE} を付けて、もう一度実行してください。")
                    return 1
                _print(safety.erase(brain, plan, a.confirm))
            elif a.cmd == "pause":
                brain.set_paused(True)
                print("一時停止しました。AI は記憶を追加できません（思い出すことはできます）。")
            elif a.cmd == "resume":
                brain.set_paused(False)
                print("一時停止を解除しました。")
            elif a.cmd == "backup":
                print(safety.backup(brain))
            elif a.cmd == "restore":
                if not a.snapshot:
                    for b in safety.list_backups(brain.settings.backups):
                        print(b)
                    return 0
                _print(safety.restore(brain, Path(a.snapshot)))
    except InvalidInput as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
