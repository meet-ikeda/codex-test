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

    s = sub.add_parser("sleep", help="睡眠（記憶の整理）を実行する")
    s.add_argument("--if-due", action="store_true", help="前回の睡眠から一定時間たっているときだけ眠る")
    s.add_argument("--no-ai", action="store_true", help="AI を使わない整理だけを行う")

    s = sub.add_parser("app", help="画面（グラフ・本棚・睡眠・安全装置）を開く")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")

    s = sub.add_parser("install", help="Mac に導入する（Claude Desktop・Codex・常駐・睡眠）")
    s.add_argument("--drive", help="exobrain を置く Google ドライブのフォルダ（省略すると候補から選ぶ）")
    s.add_argument("--sleep-at", help="毎日眠る時刻（例: 03:00）。省略するとログイン時のみ")
    s.add_argument("--no-agents", action="store_true", help="常駐と睡眠の自動起動を設定しない")
    s.add_argument("--yes", action="store_true", help="確認せずに進める")
    sub.add_parser("uninstall", help="Claude Desktop・Codex・常駐から外す（記憶は残す）")
    sub.add_parser("doctor", help="導入の状態を点検する")
    sub.add_parser("open", help="画面をブラウザで開く")

    sub.add_parser("pause", help="AI からの書き込みを一時停止する")
    sub.add_parser("resume", help="一時停止を解除する")
    sub.add_parser("backup", help="脳のバックアップを作る")
    s = sub.add_parser("restore", help="バックアップから脳を戻す")
    s.add_argument("snapshot", nargs="?", help="省略すると一覧を表示する")

    a = p.parse_args(argv)
    if a.cmd in ("install", "uninstall", "open"):
        return _setup_commands(a)
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
            elif a.cmd == "sleep":
                from . import sleep

                if a.if_due and not sleep.is_due(brain):
                    print("まだ眠る時間ではありません。")
                    return 0
                try:
                    _print(sleep.run(brain, use_ai=not a.no_ai))
                except sleep.SleepBusy as e:
                    print(e)
                    return 0
            elif a.cmd == "app":
                from .app import serve

                serve(brain, a.port, open_browser=not a.no_browser)
            elif a.cmd == "doctor":
                from .install import doctor

                report = doctor(brain.settings, Path.home(), brain)
                print(report.text())
                return 0 if report.ok else 1
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


def _setup_commands(a) -> int:
    import socket
    import webbrowser

    from . import install as inst
    from .config import load_settings

    if a.cmd == "open":
        url = f"http://127.0.0.1:{inst.APP_PORT}/"
        with socket.socket() as s:
            running = s.connect_ex(("127.0.0.1", inst.APP_PORT)) == 0
        if running:
            webbrowser.open(url)
            print(url)
            return 0
        from .app import serve
        from .brain import open_brain

        with open_brain() as brain:
            serve(brain, inst.APP_PORT)
        return 0
    home = Path.home()
    if a.cmd == "uninstall":
        print(inst.uninstall(home).text())
        return 0
    settings = load_settings()
    drive = Path(a.drive).expanduser() if a.drive else _choose_drive(inst.drive_candidates(home), a.yes)
    if drive is None:
        return 2
    sleep_at = None
    if a.sleep_at:
        try:
            h, m = (int(x) for x in a.sleep_at.split(":"))
            assert 0 <= h < 24 and 0 <= m < 60
            sleep_at = (h, m)
        except (ValueError, AssertionError):
            print("error: --sleep-at は 03:00 のように指定してください", file=sys.stderr)
            return 2
    report = inst.install(settings, home, drive, sleep_at, agents=not a.no_agents)
    print(report.text())
    if report.ok:
        print("\n次に、Claude Desktop と Codex を一度終了してから開き直してください（設定を読み込み直すため）。")
        print(f"画面: http://127.0.0.1:{inst.APP_PORT}/  （exobrain open でも開けます）")
    return 0 if report.ok else 1


def _choose_drive(candidates: list, yes: bool):
    if not candidates:
        print("Google ドライブのフォルダが見つかりませんでした（~/Library/CloudStorage/GoogleDrive-…）。")
        print("Google ドライブ for desktop を入れてから実行するか、--drive で場所を指定してください。")
        return None
    if yes or len(candidates) == 1:
        print(f"Google ドライブ: {candidates[0]}")
        return candidates[0]
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. {c}")
    choice = input(f"どこに置きますか？ [1-{len(candidates)}]（Enter で 1）: ").strip() or "1"
    try:
        return candidates[int(choice) - 1]
    except (ValueError, IndexError):
        print("error: 番号で選んでください", file=sys.stderr)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
