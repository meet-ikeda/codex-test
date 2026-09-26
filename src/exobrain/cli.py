"""Maintenance CLI (the main human interface will be the app screen)."""

from __future__ import annotations

import argparse
import json

from .brain import open_brain


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exobrain", description="exobrain 外部脳の保守コマンド")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="改ざんと本棚の原文をチェックする")
    sub.add_parser("rebuild", help="イベント記録から脳の表を作り直す")
    sub.add_parser("stats", help="記憶の件数を表示する")
    a = p.parse_args(argv)

    with open_brain() as brain:
        if a.cmd == "verify":
            ok, msg = brain.verify()
            print(msg)
            return 0 if ok else 1
        if a.cmd == "rebuild":
            print(f"rebuilt from {brain.rebuild()} events")
        elif a.cmd == "stats":
            print(json.dumps(brain.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
