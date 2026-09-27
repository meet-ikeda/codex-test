"""v0.5 milestone 2: nightly daily logs, promotion signals, /good, the cortex copy, erase of quotes."""

import json

from exobrain import safety, sleep, transcripts
from exobrain.brain import Brain
from exobrain.config import Settings
from exobrain.cortex_export import export
from exobrain.promote import encode_nodes

from .test_hippocampus import FakeEmbedder


def jl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def codex_home(tmp_path):
    root = tmp_path / ".codex"
    jl(root / "session_index.jsonl", [{"id": "T1", "thread_name": "古い名前"}, {"id": "T1", "thread_name": "採用サイト"}])
    msg = lambda ts, role, text: {"timestamp": ts, "type": "response_item",  # noqa: E731
                                   "payload": {"type": "message", "role": role,
                                               "content": [{"type": "input_text" if role == "user" else "output_text",
                                                            "text": text}]}}
    jl(root / "sessions/2026/09/27/rollout-a-T1.jsonl", [
        {"timestamp": "2026-09-27T01:00:00Z", "type": "session_meta", "payload": {"id": "T1", "source": "vscode"}},
        {"timestamp": "2026-09-27T01:00:00Z", "type": "turn_context", "payload": {"model": "gpt-6-astra"}},
        msg("2026-09-27T01:00:01Z", "user", "<environment_context>秘密の設定</environment_context>"),
        msg("2026-09-27T01:00:02Z", "user", "採用サイトの写真は全部社内で撮ることにしたい。" * 6),
        msg("2026-09-27T01:00:09Z", "assistant", "了解です。社内撮影で進めます。" * 5),
    ])
    jl(root / "sessions/2026/09/27/rollout-b-G.jsonl", [
        {"timestamp": "2026-09-27T01:00:00Z", "type": "session_meta",
         "payload": {"id": "G", "source": {"subagent": {"other": "guardian"}}}},
        msg("2026-09-27T01:00:01Z", "user", "安全確認の中身" * 50),
    ])
    return root / "sessions"


def claude_home(tmp_path):
    root = tmp_path / ".claude" / "projects"
    jl(root / "-Users-me-work" / "S1.jsonl", [
        {"type": "user", "uuid": "u1", "timestamp": "2026-09-27T02:00:00Z",
         "message": {"role": "user", "content": "見積書アプリの税率は 10% 固定でいい。" * 10}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-09-27T02:00:05Z",
         "message": {"role": "assistant", "model": "claude-opus-5-5",
                     "content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "10% 固定にしました。"}]}},
        {"type": "user", "uuid": "u2", "timestamp": "2026-09-27T02:00:06Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "ファイルの中身" * 50}]}},
        {"type": "user", "uuid": "u3", "timestamp": "2026-09-27T02:00:07Z", "isMeta": True,
         "message": {"role": "user", "content": "メタ情報"}},
        {"type": "custom-title", "customTitle": "見積書アプリ"},
    ])
    jl(root / "-private-tmp-devhome" / "sleep2.jsonl", [
        {"type": "user", "uuid": "y", "timestamp": "2026-09-27T03:00:00Z",
         "message": {"role": "user", "content": sleep.SLEEP_PROMPT}}])
    jl(root / "-Users-me--exobrain" / "sleep.jsonl", [
        {"type": "user", "uuid": "x", "timestamp": "2026-09-27T03:00:00Z",
         "message": {"role": "user", "content": "睡眠の指示" * 100}}])
    return root


def test_transcripts_keep_only_the_owners_conversations(tmp_path):
    codex = list(transcripts.codex_threads(codex_home(tmp_path)))
    assert [t.thread_id for t in codex] == ["T1"]
    t = codex[0]
    assert t.title == "採用サイト" and t.model == "gpt-6-astra" and [m.role for m in t.messages] == ["owner", "ai"]
    assert "秘密の設定" not in "".join(m.text for m in t.messages)
    cc = list(transcripts.claude_code_threads(claude_home(tmp_path)))
    assert [(c.thread_id, c.title, c.model) for c in cc] == [("S1", "見積書アプリ", "claude-opus-5-5")]
    assert [m.role for m in cc[0].messages] == ["owner", "ai"]  # no tool output, no meta, not the sleep's own run
    pending = list(transcripts.pending(iter(codex), {}, "2026-09-27"))
    assert len(pending) == 1
    assert list(transcripts.pending(iter(codex), {"codex:T1": "2026-09-27T01:00:09Z"}, "2026-09-27")) == []
    assert list(transcripts.pending(iter(codex), {}, "2026-09-28")) == []  # before the owner's start date
    # The start date is local: 01:00Z on the 27th is 10:00 on the 27th in Tokyo, after midnight local.
    assert transcripts.since_utc("2026-09-27").endswith("Z")


def test_nightly_daily_logs_are_written_and_continue_their_thread(tmp_path):
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", daily_logs_since="2026-09-01",
                 codex_sessions=codex_home(tmp_path), claude_projects=claude_home(tmp_path))
    with Brain(s) as b:
        def answer(item):
            if item["type"] == "write_daily":
                assert "秘密の設定" not in item["conversation"] and "オーナー" in item["conversation"]
                return {"item_id": item["item_id"], "events": [f"{item['thread']['title']} の相談"],
                        "decisions": ["決めたこと"]}
            if item["type"] == "promote":
                return {"item_id": item["item_id"], "atoms": []}
            return {"item_id": item["item_id"], "keep": [], "drop": [], "assignments": {}}

        run_id, _ = sleep.start(b)
        state = sleep.SleepState(run_id)
        kinds = []
        while not (batch := sleep.next_batch(b, state))["done"]:
            kinds += [i["type"] for i in batch["items"]]
            sleep.apply(b, state, batch["batch_id"], [answer(i) for i in batch["items"]])
        assert kinds.count("write_daily") == 2 and "promote" in kinds  # tonight's logs are promoted tonight
        logs = b._conn.execute("SELECT title, ai_name FROM sources WHERE kind = 'ai_daily' ORDER BY title").fetchall()
        assert [tuple(r) for r in logs] == [("AI日報 · 採用サイト · 2026-09-27", "Codex"),
                                            ("AI日報 · 見積書アプリ · 2026-09-27", "Claude Code")]
        assert b._conn.execute("SELECT cursor FROM ai_checkpoints WHERE key = 'codex:T1'").fetchone()[0] \
            == "2026-09-27T01:00:09Z"
        # The next night there is nothing new.
        run2, _ = sleep.start(b)
        batch = sleep.next_batch(b, sleep.SleepState(run2))
        assert batch["done"] or all(i["type"] != "write_daily" for i in batch["items"])


def test_nothing_is_read_until_the_owner_sets_a_start_date(tmp_path):
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="",
                 codex_sessions=codex_home(tmp_path), claude_projects=claude_home(tmp_path))
    with Brain(s) as b:
        run_id, _ = sleep.start(b)
        assert sleep.next_batch(b, sleep.SleepState(run_id))["done"]


def test_good_strengthens_what_the_praised_answer_used(brain, session):
    a = brain.remember_explicit(session, "見出しは短くする")["node_id"]
    c = brain.remember_explicit(session, "結論から書く")["node_id"]
    before = brain.node(a)["importance"]
    r = brain.good(session, "結論から書き、見出しを短くしたメール", "/good 最高", [a, c, "n_unknown"])
    lesson = brain.node(r["node_id"])
    assert lesson["body"] == "オーナーが良いと評価したやり方: 結論から書き、見出しを短くしたメール"
    assert lesson["promoted_by"] == "explicit" and lesson["goods"] == 1 and lesson["kind"] == "procedural"
    assert brain.node(a)["goods"] == 1 and brain.node(a)["importance"] == before  # already at 1.0
    assert r["unknown_memory_ids"] == ["n_unknown"] and "覚えました" in r["message_to_user"]
    link = next(e for e in brain.edges_of(a) if c in (e["src"], e["dst"]) and e["kind"] == "association")
    assert link["weight"] >= 0.3
    again = brain.good(session, "結論から書き、見出しを短くしたメール", "/good", [a])
    assert again["node_id"] == r["node_id"] and brain.node(r["node_id"])["goods"] == 2


def test_repetition_on_another_day_is_a_signal(tmp_path, monkeypatch):
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="")
    with Brain(s) as b:
        b.embedder = FakeEmbedder()
        sid = b.start_session("Codex")["session_id"]
        from exobrain import daily
        from exobrain.inbox import file_daily

        base = {"source": "codex", "ai_provider": "openai", "ai_product": "Codex", "ai_model": "x",
                "thread_id": "t", "period_start": "2026-09-01T00:00:00+09:00",
                "period_end": "2026-09-01T23:00:00+09:00", "generated_at": "2026-09-01T23:50:00+09:00"}
        file_daily(b, daily.render({**base, "entry_date": "2026-09-01", "cursor": "c1"}, "t",
                                   {"events": ["納品物は PDF と PNG の両方で渡す"]}))
        file_daily(b, daily.render({**base, "entry_date": "2026-09-05", "cursor": "c2", "previous_cursor": "c1"},
                                   "t", {"events": ["納品物は PDF と PNG の両方で渡す"], "learnings": ["別の話"]}))
        from exobrain.hippocampus import encode_pending

        encode_pending(b)
        from exobrain.promote import candidates

        segs = [seg for seg, _ in candidates(b, set())]
        assert {seg.signal for seg in segs} == {"repetition"}
        assert len(segs) == 2  # each day's mention points at the other


def test_cortex_copy_on_drive_is_rewritten_and_forgets_superseded(brain, session, settings):
    keep = brain.remember_explicit(session, "資料は PDF で共有する", "procedural", ["資料"])["node_id"]
    old = brain.remember_explicit(session, "本棚は iCloud に置く", "semantic")["node_id"]
    counts = export(brain)
    root = settings.drive_root / "大脳皮質"
    assert counts == {"procedural": 1, "semantic": 1, "episode": 0}
    note = next((root / "手続き記憶").glob("*.md")).read_text(encoding="utf-8")
    assert "写し" in note and "資料は PDF で共有する" in note and "根拠" in note
    with brain._tx():
        brain._emit("sleep", "node_updated", {"id": old, "status": "superseded"})
    export(brain)
    assert not any((root / "意味記憶").glob("*.md")) and keep


def test_erasing_an_original_takes_its_quotes_and_lone_memories(brain, session):
    n = brain.remember_explicit(session, "取引先の電話番号は 06-1234-5678")
    plan = safety.plan_erase(brain, source_ids=[n["source_id"]])
    assert [x["id"] for x in plan["nodes"]] == [n["node_id"]]
    safety.erase(brain, plan, safety.CONFIRM_PHRASE)
    assert brain.node(n["node_id"]) is None
    leftover = brain._conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'node_sourced' AND payload_json LIKE '%06-1234%'").fetchone()[0]
    assert leftover == 0 and brain.verify()[0]


def test_cortex_is_recalled_by_meaning(brain, session):
    brain.embedder = FakeEmbedder()
    nid = brain.remember_explicit(session, "メールの文章は結論から書く")["node_id"]
    assert encode_nodes(brain) == 1
    r = brain.recall(session, "結論から書くメールの文章")
    assert nid in r["memory_ids"] and r["searched"] == ["大脳皮質"]


# ---- reconsolidation (spec v0.6) ------------------------------------------------------------------


def test_recalled_memory_is_rewritten_with_the_episode_of_why(brain, session, settings):
    old = brain.remember_explicit(session, "納品物は PDF と PNG の両方で渡す", "semantic", ["納品"])["node_id"]
    links_before = {(e["src"], e["dst"], e["kind"]) for e in brain.edges_of(old)}
    r = brain.revise_memory(session, old, "納品物は PDF だけで渡す", "オーナーが PNG は不要と言った",
                            owner_words="PNG はもう要らない、PDF だけでいい")
    n = brain.node(old)
    assert n["body"] == "納品物は PDF だけで渡す" and n["status"] == "active"  # same memory, same id
    assert links_before <= {(e["src"], e["dst"], e["kind"]) for e in brain.edges_of(old)}  # links kept
    ep = brain.node(r["episode_id"])
    assert ep["kind"] == "episode" and ep["promoted_by"] == "reconsolidation"
    assert "PDF と PNG の両方" in ep["body"] and "PDF だけ" in ep["body"] and "PNG は不要" in ep["body"]
    assert any(e["kind"] == "revised_in" and r["episode_id"] in (e["src"], e["dst"]) for e in brain.edges_of(old))
    rev = brain._conn.execute("SELECT old_body, new_body, reason FROM revisions WHERE node_id = ?", (old,)).fetchone()
    assert tuple(rev) == ("納品物は PDF と PNG の両方で渡す", "納品物は PDF だけで渡す", "オーナーが PNG は不要と言った")
    assert brain.open_source(r["source_id"])["body"].startswith("PNG はもう要らない")  # the evidence is on the shelf
    # The history survives a rebuild, and the cortex copy shows it.
    snap = brain.snapshot()
    brain.rebuild()
    assert brain.snapshot()["revisions"] == snap["revisions"] and brain.node(old)["body"] == "納品物は PDF だけで渡す"
    export(brain)
    note = next((settings.drive_root / "大脳皮質" / "意味記憶").glob("*.md")).read_text(encoding="utf-8")
    assert "書き換えの履歴" in note and "前: 納品物は PDF と PNG の両方で渡す" in note


def test_rewriting_needs_evidence_and_a_real_change(brain, session):
    import pytest

    from exobrain.brain import InvalidInput

    nid = brain.remember_explicit(session, "会議は月曜")["node_id"]
    with pytest.raises(InvalidInput, match="根拠"):
        brain.revise_memory(session, nid, "会議は火曜", "変わったらしい")
    with pytest.raises(InvalidInput, match="同じ"):
        brain.revise_memory(session, nid, "会議は月曜", "確認", owner_words="月曜だよ")
    with pytest.raises(InvalidInput, match="本棚にありません"):
        brain.revise_memory(session, nid, "会議は火曜", "原文にあった", source_id="src_nothere")


def test_erasing_a_rewritten_memory_takes_its_history_too(brain, session):
    nid = brain.remember_explicit(session, "取引先の担当は佐藤さん")["node_id"]
    r = brain.revise_memory(session, nid, "取引先の担当は鈴木さん", "担当が変わった", owner_words="担当は鈴木さんに変わった")
    plan = safety.plan_erase(brain, node_ids=[nid])
    assert {x["id"] for x in plan["nodes"]} >= {nid, r["episode_id"]}
    safety.erase(brain, plan, safety.CONFIRM_PHRASE)
    # The memory and the episode of its rewriting are gone; the owner's original words stay on the bookshelf
    # until the original itself is erased (erasing a memory never erases a record).
    left = brain._conn.execute("SELECT COUNT(*) FROM events WHERE type IN ('node_added', 'node_revised', 'node_sourced')"
                               " AND payload_json LIKE '%佐藤%'").fetchone()[0]
    assert left == 0 and brain.verify()[0]
