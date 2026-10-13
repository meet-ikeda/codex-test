"""Bedtime missed → sleep on return, and 最近の出来事 (2026-10-11)."""

import json
from datetime import datetime, timedelta, timezone

from exobrain import recent, screen, sleep


def set_bedtime(settings, hour=19, minute=0):
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "config.json").write_text(json.dumps({"sleep_timer": {"hour": hour, "minute": minute}}))


def slept(brain, at, summary="今夜の振り返り"):
    brain._conn.execute("INSERT INTO sleep_runs (id, started_at, start_event, stage_a, finished_at, summary)"
                        " VALUES (?, ?, 0, '{}', ?, ?)",
                        (f"run_{at.timestamp()}", at.isoformat(), (at + timedelta(minutes=20)).isoformat(), summary))


def local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


def test_a_night_up_late_sleeps_on_return(brain, settings):
    set_bedtime(settings)
    slept(brain, local(2026, 10, 10, 4, 10))  # a manual sleep in the small hours
    assert sleep.is_due(brain, local(2026, 10, 10, 19, 30))  # it does not cover that evening's bedtime
    assert not sleep.is_due(brain, local(2026, 10, 10, 18, 0))  # before bedtime: nothing missed
    assert sleep.is_due(brain, local(2026, 10, 11, 10, 0))  # the Mac was off all night: sleep in the morning
    assert sleep.should_wake_to_sleep(brain, local(2026, 10, 11, 10, 0))
    assert not sleep.should_wake_to_sleep(brain, local(2026, 10, 10, 19, 5))  # the timer gets the first go


def test_a_nap_before_bedtime_counts_and_a_timed_out_sleep_counts(brain, settings):
    set_bedtime(settings)
    slept(brain, local(2026, 10, 10, 17, 0))
    assert not sleep.is_due(brain, local(2026, 10, 10, 23, 0))
    slept(brain, local(2026, 10, 11, 19, 0), "AI による整理が時間内に終わりませんでした。途中までの結果は保存されています。")
    assert not sleep.is_due(brain, local(2026, 10, 11, 23, 0))


def test_a_failed_night_is_tried_again_only_after_some_hours(brain, settings):
    set_bedtime(settings)
    slept(brain, local(2026, 10, 9, 19, 0))
    slept(brain, local(2026, 10, 10, 19, 0), "AI による整理が異常終了しました（終了コード 1）")
    assert sleep.is_due(brain, local(2026, 10, 10, 21, 0))
    assert not sleep.should_wake_to_sleep(brain, local(2026, 10, 10, 21, 0))
    assert sleep.should_wake_to_sleep(brain, local(2026, 10, 11, 2, 0))


def test_without_a_timer_the_old_rule_holds(brain):
    slept(brain, datetime.now(timezone.utc) - timedelta(hours=2))
    assert not sleep.is_due(brain)
    assert not sleep.should_wake_to_sleep(brain)


def daily(brain, session, title, project, **kw):
    return brain.submit_daily_log(session, title, kw.get("events", []), [], [], kw.get("decisions", []),
                                  kw.get("unresolved", []), project=project, reasons=kw.get("reasons", []),
                                  more={"rejected": kw.get("rejected", []), "status": kw.get("status", [])})


def test_the_week_is_summed_up_by_project_and_handed_over_on_recall(brain, session):
    daily(brain, session, "ロゴの相談", "はしゃGO", decisions=["ロゴは黒一色にする（理由: 印刷費を抑えるため）"],
          rejected=["グラデーション案はボツ（理由: 小さいと潰れる）"], unresolved=["名刺のデータを作る"],
          reasons=["> 小さく刷っても読めることが大事"], status=["ロゴの方向が決まった"],
          events=["雑談で天気の話をした"])
    from exobrain.inbox import ingest

    ingest(brain)
    items = recent.gather(brain)
    assert [it["project"] for it in items] == ["はしゃGO"]
    page = recent.write(brain)["markdown"]
    assert "はしゃGO" in page and "ロゴは黒一色にする" in page and "グラデーション案はボツ" in page
    assert "名刺のデータを作る" in page and "> 小さく刷っても読めることが大事" in page
    assert "（※原文と一字一句は一致しません）" not in page
    got = brain.start_session("Codex", include_profile=True)
    assert "ロゴは黒一色にする" in got["recent"]
    assert "recent" not in brain.start_session("Codex", include_profile=False)


def test_lines_screening_left_out_stay_out(brain, session):
    daily(brain, session, "相談", "案件A", decisions=["Aに決めた"], events=["天気の話をした"])
    from exobrain.inbox import ingest

    ingest(brain)
    sid = recent.gather(brain)[0]["source_id"]
    pl = screen.plan(brain, brain._conn.execute("SELECT id, kind, path, title FROM sources WHERE id = ?",
                                                (sid,)).fetchone())
    judged = {f"{sid}:{n}": {"label": "chatter"} for n, _ in pl["judged"]}
    screen.apply(brain, pl, judged, "test")
    lines = [t for _, t in recent.gather(brain)[0]["lines"]]
    assert "Aに決めた" in lines and not any("天気" in t for t in lines)


def test_ids_and_quotes_are_checked():
    body, unknown, bad = recent._check("- 決めた [src_aa]\n- 何か [src_bb]\n> 言ってないこと [src_aa]\n> 言ったこと [src_aa]",
                                        {"src_aa"}, "- > 言ったこと\n- 決めた")
    assert unknown == ["src_bb"] and "[src_bb・見当たらない]" in body
    assert bad == 1 and "言ってないこと [src_aa]（※原文と一字一句は一致しません）" in body


def test_a_tendency_needs_two_days():
    body, _, bad = recent._check("## 何度も出た理由（この 1 週間の傾向。ルールではない）\n- 費用を抑える [src_a] [src_b]\n"
                                 "- 自然さ優先 [src_a] [src_c]", {"src_a": "2026-10-09", "src_b": "2026-10-09",
                                                                 "src_c": "2026-10-10"}, "")
    assert bad == 1 and "費用を抑える [src_a] [src_b]（※別の日の根拠が 2 つありません" in body
    assert body.endswith("[src_c]")


def test_names_that_came_up_on_several_days_are_counted():
    items = [{"day": "2026-10-09", "lines": [("決まったこと", "[[池田コピー課]]の声")]},
             {"day": "2026-10-10", "lines": [("あったこと", "[[池田コピー課|コピー課]]と[[Anam]]")]}]
    assert recent.recurring(items) == [("池田コピー課", 2)]


def test_the_newest_days_are_kept_word_for_word_and_always_handed_over(brain, session):
    daily(brain, session, "見積", "案件B", decisions=["単価は 1 本 38,500 円（税別）にする"])
    from exobrain.inbox import ingest

    ingest(brain)
    r = recent.write(brain)
    assert recent.TAIL_HEADING in r["markdown"] and "単価は 1 本 38,500 円（税別）にする" in r["markdown"]
    got = recent.hand_over(brain, budget=recent.TAIL_TOKENS + 20)  # the summary is cut first, the tail stays
    assert "38,500 円" in got.split(recent.TAIL_HEADING)[1]
