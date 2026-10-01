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


def _settings(a) -> int:
    """Change exobrain's own settings file only. Other apps' settings are never touched here."""
    import json
    import os

    from .config import load_settings

    home = Path(os.environ.get("EXOBRAIN_HOME", "~/.exobrain")).expanduser()
    path = home / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    changed = False
    if a.vault is not None:
        vault = Path(a.vault).expanduser()
        if a.vault and not vault.is_dir():
            print(f"フォルダが見つかりません: {vault}")
            return 1
        cfg["vault_root"] = str(vault) if a.vault else None
        changed = True
    extras = list(cfg.get("extra_inboxes", []))
    for e in a.add_extra_inbox:
        e = str(Path(e).expanduser())
        if not Path(e).is_dir():
            print(f"フォルダが見つかりません: {e}")
            return 1
        if e not in extras:
            extras.append(e)
            changed = True
    for e in a.remove_extra_inbox:
        e = str(Path(e).expanduser())
        if e in extras:
            extras.remove(e)
            changed = True
    cfg["extra_inboxes"] = extras
    if a.embed_model is not None:
        cfg["embed_model"] = a.embed_model
        changed = True
    if a.downloads is not None:
        cfg["downloads_dir"] = a.downloads or None
        changed = True
    if a.daily_logs_from is not None:
        if a.daily_logs_from:
            from datetime import date

            try:
                date.fromisoformat(a.daily_logs_from)
            except ValueError:
                print("日付は 2026-09-27 の形で指定してください。")
                return 1
        cfg["daily_logs_since"] = a.daily_logs_from or None
        changed = True
    if changed:
        home.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    st = load_settings()
    _print({"保管庫（#remember）": str(st.vault_root) if st.vault_root else None,
            "追加の受け口（読むだけ）": [str(x) for x in st.extra_inboxes],
            "ダウンロードの見張り": str(st.downloads_dir) if st.downloads_dir else None,
            "埋め込みモデル": st.embed_model or "（使わない）", "海馬の保持日数": st.hippocampus_days,
            "毎晩の日報（Codex・Claude Code）": f"{st.daily_logs_since} 以降の会話" if st.daily_logs_since else "作らない",
            "Google ドライブ": str(st.drive_root)})
    if changed:
        print("\n変更しました。常駐している画面には、次に起動したときから反映されます。")
    return 0


def _usage(brain, days: int) -> dict:
    import json
    from datetime import datetime, timedelta

    path = brain.settings.home / "sleep-usage.jsonl"
    rows = []
    if path.exists():
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("at", "") >= since:
                rows.append(r)
    total = lambda k: sum(r.get(k) or 0 for r in rows)  # noqa: E731
    return {"期間": f"直近 {days} 日", "睡眠の回数": len(rows),
            "入力トークン": total("input_tokens"), "出力トークン": total("output_tokens"),
            "キャッシュ読み込み": total("cache_read_tokens"), "キャッシュ書き込み": total("cache_write_tokens"),
            "金額の目安（API 料金換算、USD）": round(total("cost_usd"), 2),
            "注": "Claude のサブスクリプションで動かしている場合、実際の請求ではなく利用枠の消費です。",
            "各回": rows}


def _threads(brain, a) -> int:
    from datetime import datetime

    from . import sleep, transcripts
    from .inbox import current_cursor

    st = brain.settings
    threads = transcripts.local_threads(st.codex_sessions, st.claude_projects, st.cowork_sessions)
    before = transcripts.since_utc(st.daily_logs_since) if st.daily_logs_since else "9999"
    queue = sleep.backfill_queue(brain)
    local = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")  # noqa: E731
    if a.cmd == "threads":
        rows = []
        for th in sorted(threads, key=lambda t: t.messages[-1].at, reverse=True):
            if a.search and a.search not in th.title:
                continue
            done = current_cursor(brain, th.key + transcripts.BACKFILL_SUFFIX) or ""
            left = [m for m in th.messages if done < m.at < before]
            rows.append({"id": th.thread_id[:13], "AI": th.product, "題名": th.title,
                         "期間": f"{local(th.messages[0].at)}〜{local(th.messages[-1].at)}",
                         "過去分の残り": f"{len(left)} 発言" + ("（取り込み待ち）" if th.key in queue else "")})
            if len(rows) >= a.limit:
                break
        _print(rows)
        return 0
    chosen = []
    for tid in a.thread_ids:
        hits = [t for t in threads if t.thread_id.startswith(tid)]
        if len(hits) != 1:
            print(f"{tid}: {'見つかりません' if not hits else '複数に当てはまります。もう少し長く指定してください'}")
            return 1
        chosen.append(hits[0])
    keys = [t.key for t in chosen]
    if a.cancel:
        sleep.save_backfill_queue(brain, [k for k in queue if k not in keys])
        print(f"取り込み待ちから外しました: {len(keys)} 件")
        return 0
    sleep.save_backfill_queue(brain, queue + [k for k in keys if k not in queue])
    for t in chosen:
        print(f"取り込み待ちに入れました: {t.title}（{t.product}）")
    print("次の睡眠から、毎晩の日報より前の発言を、ひと区切りずつ預け入れ（要約と3種類の記憶）にします（1晩に最大"
          f" {sleep.MAX_BACKFILL_ITEMS} 区切り）。同じスレッドを何度入れても重なりません。")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exobrain", description="exobrain 外部脳の保守コマンド")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="改ざんと本棚の原文をチェックする")
    sub.add_parser("rebuild", help="イベント記録から脳の表を作り直す")
    sub.add_parser("stats", help="記憶の件数を表示する")
    sub.add_parser("ingest", help="預かりBOX・Obsidian の #remember を取り込む")
    s = sub.add_parser("encode", help="意味検索の埋め込みを作る（Ollama）")
    s.add_argument("--all", action="store_true", help="待っているものを全部（既定は 200 件まで）")

    s = sub.add_parser("settings", help="exobrain 自身の設定を見る・変える（~/.exobrain/config.json）")
    s.add_argument("--vault", help="#remember を探す Obsidian の保管庫のフォルダ")
    s.add_argument("--add-extra-inbox", action="append", default=[],
                   help="AI 日報を読むだけで取り込む追加のフォルダ（例: OUTBRAIN の受け口）")
    s.add_argument("--remove-extra-inbox", action="append", default=[])
    s.add_argument("--embed-model", help="Ollama の埋め込みモデル（空文字で意味検索を止める）")
    s.add_argument("--downloads", help="AI 日報を拾うダウンロードフォルダ（空文字で見張らない）")
    s.add_argument("--daily-logs-from", help="Codex・Claude Code の会話から毎晩日報を作る。この日付以降の会話が対象"
                                              "（例: 2026-09-27。空文字で止める）")

    s = sub.add_parser("fade", help="原文を海馬から外す（大脳皮質への昇格の対象にしない。本棚には残る）")
    s.add_argument("source_ids", nargs="+", help="原文の id（src_...）")

    s = sub.add_parser("threads", help="この Mac に残っている Codex・Claude Code のスレッドを一覧にする")
    s.add_argument("--search", help="題名に含まれる語で絞る")
    s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("backfill", help="選んだスレッドの過去分（毎晩の日報より前）を、次の睡眠から預け入れにする")
    s.add_argument("thread_ids", nargs="*", help="exobrain threads の id（先頭の数文字でよい）")
    s.add_argument("--cancel", action="store_true", help="取り込み待ちから外す")

    sub.add_parser("export", help="大脳皮質の写しを Google ドライブに書き出す（睡眠のたびにも自動で書き出す）")
    s = sub.add_parser("usage", help="睡眠で使ったトークン数を見る")
    s.add_argument("--days", type=int, default=7)

    s = sub.add_parser("recall", help="思い出す（大脳皮質 → 海馬 → 本棚 → Obsidian）")
    s.add_argument("cue")

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

    s = sub.add_parser("ask", help="脳と話す: 中に何があるか、自分がどう見えているかを聞く（Claude Code を使う）")
    s.add_argument("question", nargs="*", help="省略すると、ターミナルで会話を始める")
    s = sub.add_parser("look", help="脳の中を AI なしで見る（全体像・記憶の一覧・1つの記憶）")
    s.add_argument("--kind", default="", help="procedural / semantic / episode")
    s.add_argument("--topic", default="", help="話題名で絞る（例: ハピホテ）")
    s.add_argument("--query", default="", help="本文に含む語で絞る")
    s.add_argument("--id", default="", help="1つの記憶を詳しく見る")
    s.add_argument("--limit", type=int, default=30)

    sub.add_parser("pause", help="AI からの書き込みを一時停止する")
    sub.add_parser("resume", help="一時停止を解除する")
    sub.add_parser("backup", help="脳のバックアップを作る")
    s = sub.add_parser("restore", help="バックアップから脳を戻す")
    s.add_argument("snapshot", nargs="?", help="省略すると一覧を表示する")

    a = p.parse_args(argv)
    if a.cmd in ("install", "uninstall", "open"):
        return _setup_commands(a)
    if a.cmd == "settings":
        return _settings(a)
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
                from .inbox import scan_vault

                _print({"預かりBOX": ingest(brain), "Obsidian #remember": scan_vault(brain)})
            elif a.cmd == "encode":
                from .hippocampus import encode_pending, unencoded_count

                if brain.embedder is None:
                    print("embed_model が空のため、埋め込みは作りません。")
                    return 1
                total = 0
                while True:
                    n = encode_pending(brain)
                    total += n
                    if not n or not a.all:
                        break
                print(f"埋め込みを {total} 件作りました。残り {unencoded_count(brain)} 件。")
            elif a.cmd == "fade":
                known = [sid for sid in a.source_ids if brain._conn.execute(
                    "SELECT 1 FROM hippocampus WHERE source_id = ? AND status = 'waiting'", (sid,)).fetchone()]
                if known:
                    with brain._tx():
                        brain._emit("human", "hippocampus_faded", {"source_ids": known})
                missing = sorted(set(a.source_ids) - set(known))
                print(f"海馬から外しました: {len(known)} 件（本棚には残っています）。"
                      + (f" 海馬に見つからなかったもの: {', '.join(missing)}" if missing else ""))
            elif a.cmd in ("threads", "backfill"):
                return _threads(brain, a)
            elif a.cmd == "export":
                from .cortex_export import cortex_root, export

                counts = export(brain)
                print(f"書き出しました: {cortex_root(brain)}（ルール {counts['procedural']}・事実 {counts['semantic']}"
                      f"・出来事 {counts['episode']} 件）")
            elif a.cmd == "usage":
                _print(_usage(brain, a.days))
            elif a.cmd == "recall":
                sid = brain.start_session("exobrain CLI")["session_id"]
                r = brain.recall(sid, a.cue)
                print(r["context"])
                print(f"\n（探した場所: {' → '.join(r['searched'])}、{r['tokens']} トークン）")
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
            elif a.cmd == "ask":
                from .ask import ask

                return ask(brain, " ".join(a.question).strip() or None)
            elif a.cmd == "look":
                from . import introspect

                if a.id:
                    _print(introspect.memory(brain, a.id))
                elif a.kind or a.topic or a.query:
                    _print(introspect.memories(brain, a.kind, a.query, a.topic, a.limit))
                else:
                    _print(introspect.overview(brain))
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
