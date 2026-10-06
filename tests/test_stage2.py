"""v0.5 milestone 2: nightly daily logs, promotion signals, /good, the cortex copy, erase of quotes."""

import json

from exobrain import daily, inbox, promote, safety, sleep, transcripts
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


def test_long_transcript_is_split_without_losing_the_middle_and_keeps_prior_context():
    messages = [
        transcripts.Message(f"2026-09-27T01:00:0{i}Z", "owner" if i % 2 == 0 else "ai", f"marker-{i}-" + "長" * 80)
        for i in range(8)
    ]
    parts = transcripts.excerpt_parts(messages[2:], max_chars=260)
    joined = "\n".join(parts)
    assert len(parts) > 1 and all(f"marker-{i}" in joined for i in range(2, 8))
    assert "途中を省略" not in joined
    context = transcripts.context_before(messages, messages[2].at)
    assert "marker-0" in context and "marker-1" in context and "marker-2" not in context


def test_nightly_daily_logs_are_written_and_continue_their_thread(tmp_path):
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", daily_logs_since="2026-09-01",
                 codex_sessions=codex_home(tmp_path), claude_projects=claude_home(tmp_path))
    with Brain(s) as b:
        def answer(item):
            if item["type"] == "write_daily":
                conversation = item.get("conversation") or "\n".join(item["conversation_parts"])
                assert "秘密の設定" not in conversation and "オーナー" in conversation
                assert not ({"conversation", "conversation_parts"} <= item.keys()) and not item["truncated"]
                return {"item_id": item["item_id"], "events": [f"{item['thread']['title']} の相談"],
                        "decisions": ["決めたこと"]}
            if item["type"] == "promote":
                kind = b._conn.execute("SELECT kind FROM sources WHERE id = ?", (item["source_id"],)).fetchone()[0]
                if kind == "ai_daily":
                    assert item["evidence"]["source_id"]
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
        excerpts = b._conn.execute("SELECT id FROM sources WHERE kind = 'conversation_excerpt'").fetchall()
        assert len(excerpts) == 2
        assert b._conn.execute(
            "SELECT COUNT(*) FROM hippocampus h JOIN sources s ON s.id = h.source_id"
            " WHERE s.kind = 'conversation_excerpt'").fetchone()[0] == 0
        meta = json.loads(b._conn.execute(
            "SELECT meta_json FROM sources WHERE kind = 'ai_daily' ORDER BY title LIMIT 1").fetchone()[0])
        assert meta["conversation_source_id"] in {r[0] for r in excerpts}
        assert b._conn.execute("SELECT cursor FROM ai_checkpoints WHERE key = 'codex:T1'").fetchone()[0] \
            == "2026-09-27T01:00:09Z"
        # The next night there is nothing new.
        run2, _ = sleep.start(b)
        batch = sleep.next_batch(b, sleep.SleepState(run2))
        assert batch["done"] or all(i["type"] != "write_daily" for i in batch["items"])


def test_daily_quality_check_rejects_unsupported_identifiers_and_near_miss_katakana():
    import pytest
    from exobrain.brain import InvalidInput

    conversation = "オーナーはクライアントの方針を確認した。件数は12件。"
    with pytest.raises(InvalidInput, match="確認できない"):
        sleep._daily_quality_check({"events": ["件数は95件だった"]}, conversation)
    with pytest.raises(InvalidInput, match="文字崩れ"):
        sleep._daily_quality_check({"events": ["クリエントの方針を確認した"]}, conversation)


def test_daily_promotion_requires_and_links_raw_conversation_evidence(tmp_path):
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="")
    with Brain(s) as b:
        raw = inbox.add_source(
            b, kind="conversation_excerpt", author="conversation", ai_name="Codex",
            title="会話抜粋 · 採用サイト", body="[オーナー] 採用写真は社内で撮ることにした。",
            origin_file="codex/T1", hippocampus=False,
        )
        fields = {
            "source": "codex", "ai_provider": "openai", "ai_product": "Codex", "ai_model": "test",
            "generated_at": "2026-09-27T10:00:00+09:00", "thread_id": "T1", "entry_date": "2026-09-27",
            "period_start": "2026-09-27T09:00:00+09:00", "period_end": "2026-09-27T10:00:00+09:00",
            "previous_cursor": "", "cursor": "2026-09-27T01:00:09Z",
        }
        report = daily.render(fields, "採用サイト", {"decisions": ["採用写真は社内で撮ることにした。"]})
        filed = inbox.file_daily(
            b, report, origin_file="codex/T1", extra_meta={"conversation_source_id": raw["source_id"]})
        body = (b.settings.drive_root / b._conn.execute(
            "SELECT path FROM sources WHERE id = ?", (filed["source_id"],)).fetchone()[0]).read_text()
        heading, start, end = next(x for x in promote._daily_sections(body) if x[0] == "決まったこと")
        seg = promote.Segment(filed["source_id"], start, end, "explicit")
        item = promote.make_item(b, seg, "\n".join(body.splitlines()[start - 1:end]))
        decision_line = next(n for n, text in item["lines"] if "社内で撮る" in text)
        atom = {"kind": "semantic", "text": "採用写真は社内で撮ることにした。", "derivation": "paraphrase",
                "lines": [decision_line, decision_line], "confidence": 0.9, "concepts": ["採用写真"]}
        import pytest
        from exobrain.brain import InvalidInput

        with pytest.raises(InvalidInput, match="evidence_lines"):
            promote.validate(b, item, {"atoms": [atom]})
        evidence_line = next(n for n, text in item["evidence"]["lines"] if "社内で撮る" in text)
        atom["evidence_lines"] = [evidence_line, evidence_line]
        atoms = promote.validate(b, item, {"atoms": [atom]})
        with b._tx():
            promote.apply(b, "test", item, atoms)
        node_id = b._conn.execute("SELECT id FROM nodes WHERE body = ?", (atom["text"],)).fetchone()[0]
        source_ids = {r[0] for r in b._conn.execute(
            "SELECT source_id FROM node_sources WHERE node_id = ?", (node_id,))}
        assert source_ids == {filed["source_id"], raw["source_id"]}


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


# ---- whose memory: interview / client / confidential notes (spec v0.6 §5.1) --------------------------


def _vault_brain(tmp_path, files):
    import os
    import time

    vault = tmp_path / "vault"
    for name, text in files.items():
        (vault / name).parent.mkdir(parents=True, exist_ok=True)
        (vault / name).write_text(text, encoding="utf-8")
        old = time.time() - 10
        os.utime(vault / name, (old, old))
    return Brain(Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", vault_root=vault))


def test_interview_notes_are_remembered_as_the_interviewees(tmp_path):
    from exobrain.brain import InvalidInput
    from exobrain.inbox import scan_vault

    b = _vault_brain(tmp_path, {"取材/佐藤さん.md": "#remember #取材\n入社前は建設会社を3社比べて迷った。\n"})
    try:
        scan_vault(b)
        run_id, _ = sleep.start(b)
        state = sleep.SleepState(run_id)
        batch = sleep.next_batch(b, state)
        item = next(i for i in batch["items"] if i["type"] == "promote")
        assert item["note_type"] == "interview" and "取材相手のもの" in item["instructions"]
        n = item["lines"][1][0]
        bad = {"kind": "episode", "text": "建設会社を3社比べて迷った", "derivation": "paraphrase", "lines": [n, n],
               "about": "interviewee"}
        import pytest

        with pytest.raises(InvalidInput, match="subject"):
            sleep.apply(b, state, batch["batch_id"], [{"item_id": item["item_id"], "atoms": [bad]}])
        good = dict(bad, text="取材相手の佐藤さんは入社前に建設会社を3社比べて迷った", subject="佐藤さん（取材相手）")
        sleep.apply(b, state, batch["batch_id"], [{"item_id": item["item_id"], "atoms": [good]}])
        node = b._conn.execute("SELECT about, subject FROM nodes WHERE kind = 'episode'").fetchone()
        assert tuple(node) == ("interviewee", "佐藤さん（取材相手）")
    finally:
        b.close()


def test_confidential_notes_stay_on_the_shelf_only(tmp_path):
    from exobrain.hippocampus import search
    from exobrain.inbox import scan_vault

    b = _vault_brain(tmp_path, {"A社.md": "#remember #クライアント #機密\nA社は来春に工場を移転する予定。\n"})
    try:
        src = scan_vault(b)[0]
        assert b._conn.execute("SELECT COUNT(*) FROM hippocampus").fetchone()[0] == 0  # never promoted
        meta = json.loads(b._conn.execute("SELECT meta_json FROM sources WHERE id = ?", (src["source_id"],)).fetchone()[0])
        assert meta == {"vault": "vault", "note_type": "client", "confidential": True}
        sid = b.start_session("Codex")["session_id"]
        r = b.recall(sid, "工場を移転する予定")
        assert r["evidence"][0]["confidential"] and "機密（外に出さない）" in r["context"]
        assert search(b, "工場を移転")[0].confidential
    finally:
        b.close()


# ---- backfill: past threads the owner chooses (never overlapping, whatever the order) -----------------


def test_backfill_takes_only_the_past_and_never_twice(tmp_path, monkeypatch):
    from exobrain import transcripts

    root = tmp_path / ".codex"
    msg = lambda ts, role, text: {"timestamp": ts, "type": "response_item",  # noqa: E731
                                   "payload": {"type": "message", "role": role,
                                               "content": [{"type": "input_text", "text": text}]}}
    rows = [{"timestamp": "2026-07-01T00:00:00Z", "type": "session_meta", "payload": {"id": "OLD", "source": "vscode"}}]
    for day in ("2026-07-01", "2026-08-01", "2026-09-01", "2026-09-28"):
        rows += [msg(f"{day}T03:00:00Z", "user", f"{day} の相談。" * 40), msg(f"{day}T03:00:05Z", "assistant", "了解。" * 40)]
    jl(root / "sessions/2026/07/01/rollout-OLD.jsonl", rows)
    jl(root / "session_index.jsonl", [{"id": "OLD", "thread_name": "古いスレッド"}])
    monkeypatch.setattr(transcripts, "EXCERPT_MAX", 1500)  # small pieces: one day per log
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", daily_logs_since="2026-09-27",
                 codex_sessions=root / "sessions", claude_projects=tmp_path / "none")
    with Brain(s) as b:
        sleep.save_backfill_queue(b, ["codex:OLD"])

        def night():
            run_id, _ = sleep.start(b)
            state, written = sleep.SleepState(run_id), []
            while not (batch := sleep.next_batch(b, state))["done"]:
                res = []
                for i in batch["items"]:
                    if i["type"] == "write_deposit":
                        written.append(("過去分", i["conversation"]))
                        res.append({"item_id": i["item_id"], "summary": "古い相談", "semantic": ["オーナーは相談していた"]})
                    elif i["type"] == "write_daily":
                        written.append((i["thread"]["title"], i["conversation"]))
                        res.append({"item_id": i["item_id"], "events": ["相談した"]})
                    else:
                        res.append({"item_id": i["item_id"], "atoms": [], "keep": [], "drop": [], "assignments": {}})
                sleep.apply(b, state, batch["batch_id"], res)
            return written

        first = night()
        past = "".join(w[1] for w in first if "過去分" in w[0])
        assert all(d in past for d in ("2026-07-01", "2026-08-01", "2026-09-01")) and "2026-09-28" not in past
        assert [w for w in first if "過去分" not in w[0]]  # the 28th came in as tonight's ordinary log
        sleep.save_backfill_queue(b, ["codex:OLD"])  # asked again later: nothing is taken twice
        assert [w for w in night() if "過去分" in w[0]] == []
        assert sleep.backfill_queue(b) == []  # finished threads leave the queue
        kinds = [r[0] for r in b._conn.execute("SELECT kind FROM sources")]
        assert kinds.count("ai_daily") == 1 and kinds.count("deposit") >= 2  # the past comes in as deposits
        assert b._conn.execute("SELECT COUNT(*) FROM hippocampus h JOIN sources s ON s.id = h.source_id"
                               " WHERE s.kind = 'deposit'").fetchone()[0] >= 2


def test_daily_log_command_is_refused_where_logs_are_automatic(tmp_path):
    import pytest

    from exobrain.brain import InvalidInput

    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", daily_logs_since="2026-09-27")
    with Brain(s) as b:
        codex = b.start_session("Codex")["session_id"]
        with pytest.raises(InvalidInput, match="自動的に日報"):
            b.submit_daily_log(codex, "題", ["x"], [], [], [], [])
        chat = b.start_session("Claude Desktop")["session_id"]
        assert b.submit_daily_log(chat, "題", ["x"], [], [], [], [])["filed"]  # chats are not on this Mac


# ---- deposits: /預けて in Claude's chat, or a file from ChatGPT / Gemini ---------------------------------


def test_deposit_from_chat_goes_through_the_hippocampus_as_explicit(brain, session):
    r = brain.deposit(session, "採用サイトの相談", "採用サイトの構成を相談した。",
                      ["見出しは短くする"], ["オーナーはA社の採用サイトの構成を考えている"], ["9/27 に構成案を2つ出した"])
    assert r["filed"] and "今夜の睡眠" in r["message_to_user"]
    run_id, _ = sleep.start(brain)
    batch = sleep.next_batch(brain, sleep.SleepState(run_id))
    items = [i for i in batch["items"] if i["type"] == "promote"]
    # The deposit is an AI's summary (one step weaker), except the owner's-words section (spec v0.8 §7.2).
    assert items and all(i["signal"] in ("explicit", "summarized") and i["max_atoms"] == 8 for i in items)
    assert "預けると決めた会話" in items[0]["instructions"]
    assert brain.deposit(session, "採用サイトの相談", "採用サイトの構成を相談した。", ["見出しは短くする"],
                         ["オーナーはA社の採用サイトの構成を考えている"], ["9/27 に構成案を2つ出した"])["filed"] is False


def test_deposit_file_from_chatgpt_is_picked_up_from_downloads(tmp_path):
    import os
    import time

    from exobrain import deposit
    from exobrain.inbox import ingest

    dl = tmp_path / "Downloads"
    dl.mkdir()
    text = deposit.render("chatgpt", "取材の振り返り", {"summary": "取材を振り返った", "episode": ["取材相手の佐藤さんは迷った"]},
                          note_type="interview", confidential=True)
    (dl / "exobrain-預け入れ.md").write_text(text, encoding="utf-8")
    old = time.time() - 10
    os.utime(dl / "exobrain-預け入れ.md", (old, old))
    with Brain(Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", downloads_dir=dl)) as b:
        added = ingest(b)
        assert [a["kind"] for a in added] == ["deposit"]
        row = b._conn.execute("SELECT ai_name, meta_json FROM sources").fetchone()
        assert row[0] == "ChatGPT" and json.loads(row[1])["note_type"] == "interview"
        assert b._conn.execute("SELECT COUNT(*) FROM hippocampus").fetchone()[0] == 0  # 機密: bookshelf only
        assert not any(dl.iterdir())


# ---- the Obsidian plugin: sent notes, and stamped notes left alone by the tag scan ------------------------


def test_note_sent_by_the_obsidian_plugin_is_filed_with_its_counter_answers(tmp_path):
    import os
    import time

    from exobrain.inbox import ingest, scan_vault

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "佐藤さん.md").write_text("---\nexobrain受領: 2026-09-27 15:10 全文\n---\n#remember\n迷った話\n", encoding="utf-8")
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", vault_root=vault)
    with Brain(s) as b:
        box = s.inbox / "Obsidian"
        box.mkdir(parents=True)
        sent = ('---\nexobrain_kind: obsidian_note\nexobrain_source_file: "取材/佐藤さん.md"\n'
                'exobrain_note_type: "interview"\nexobrain_subject: "佐藤さん（入社3年目）"\nexobrain_confidential: false\n'
                'exobrain_part: "差分（2026-09-27 15:10 以降）"\n---\n# 入社の理由\n- 建設会社を3社比べた\n')
        (box / "202609271520_佐藤さん_差分.md").write_text(sent, encoding="utf-8")
        old = time.time() - 10
        os.utime(box / "202609271520_佐藤さん_差分.md", (old, old))
        os.utime(vault / "佐藤さん.md", (old, old))
        added = ingest(b)
        assert [a["title"] for a in added] == ["佐藤さん（差分（2026-09-27 15:10 以降））"]
        meta = json.loads(b._conn.execute("SELECT meta_json FROM sources").fetchone()[0])
        assert meta["note_type"] == "interview" and meta["subject"] == "佐藤さん（入社3年目）" and meta["via"] == "obsidian-plugin"
        assert scan_vault(b) == []  # the stamped note is the plugin's: the #remember scan leaves it alone
        run_id, _ = sleep.start(b)
        item = next(i for i in sleep.next_batch(b, sleep.SleepState(run_id))["items"] if i["type"] == "promote")
        assert "佐藤さん（入社3年目）" in item["instructions"] and item["note_type"] == "interview"


def test_gemini_daily_without_cursors_is_filed_once_and_promotes_its_decisions(tmp_path):
    import os
    import time

    from exobrain.inbox import ingest

    dl = tmp_path / "Downloads"
    dl.mkdir()
    text = ('---\nexobrain_kind: daily\nexobrain_source: "gemini"\nexobrain_thread_title: "採用サイトの相談"\n'
            'exobrain_sequence: "2"\nexobrain_entry_date: ""\n---\n\n# AI日報 · 採用サイトの相談\n\n## 今日の出来事\n\n'
            '- 構成案を比べた\n\n## 注意・訂正されたこと\n\n- 特になし\n\n## 工夫・学び\n\n- 特になし\n\n'
            '## 決まったこと\n\n- トップの見出しは社員の言葉にする\n\n## 未解決・次に続くこと\n\n- 特になし\n')
    for name in ("exobrain-日報-1.md", "exobrain-日報-1 (1).md"):  # downloaded twice by mistake
        (dl / name).write_text(text, encoding="utf-8")
        old = time.time() - 10
        os.utime(dl / name, (old, old))
    with Brain(Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", downloads_dir=dl)) as b:
        added = ingest(b)
        assert len(added) == 1 and added[0]["kind"] == "ai_daily"  # the same content only once
        row = b._conn.execute("SELECT ai_name, title FROM sources").fetchone()
        assert row[0] == "Gemini" and row[1].startswith("AI日報 · 採用サイトの相談 · 20")  # date filled on arrival
        run_id, _ = sleep.start(b)
        items = [i for i in sleep.next_batch(b, sleep.SleepState(run_id))["items"] if i["type"] == "promote"]
        texts = ["\n".join(t for _, t in i["lines"]) for i in items]
        assert len(items) == 1 and "社員の言葉" in texts[0]


def test_reasons_section_is_an_explicit_signal():
    """The owner's why (こだわり・理由) has its own section in daily logs and deposits, and sleep promotes it."""
    from exobrain import daily, deposit

    fields = {"source": "codex", "ai_provider": "openai", "ai_product": "Codex", "ai_model": "test",
              "generated_at": "2026-09-29T10:00:00+09:00", "thread_id": "T9", "entry_date": "2026-09-29",
              "period_start": "2026-09-29T09:00:00+09:00", "period_end": "2026-09-29T10:00:00+09:00",
              "previous_cursor": "", "cursor": "c1"}
    body = daily.render(fields, "UI", {"reasons": ["設定画面について「ユーザーに考えさせるUIは嫌」と言った"]})
    assert "## オーナーの言葉\n\n- 設定画面について" in body
    assert {"オーナーの言葉", "オーナーのこだわり・理由"} <= set(promote.DAILY_EXPLICIT_SECTIONS)  # v2 and old logs
    dep = deposit.render("chatgpt", "UI", {"summary": "s", "reasons": ["「直感的にしたい」と言った"]})
    assert "## こだわり・理由（オーナー本人の言葉）\n\n- 「直感的にしたい」と言った" in dep


def test_long_thread_is_split_and_capped_per_night():
    from exobrain import sleep, transcripts

    msgs = [transcripts.Message(role="user", text="あ" * 1000, at=f"2026-09-29T00:{i:02d}:00Z") for i in range(60)]
    groups = transcripts.split_messages(msgs, max_chars=5000)
    assert [m for g in groups for m in g] == msgs and len(groups) > sleep.MAX_DAILY_PARTS


def test_marked_names_become_typed_concepts_with_aliases(brain):
    """[[名前|別名]] in the original becomes a concept on its own; the AI's type is kept; the other name finds it."""
    from exobrain import inbox

    b = brain
    src = inbox.add_memo(b, "打ち合わせメモ", "- [[A社|A社様]]の採用サイトは写真を社内で撮る\n> 考えさせるUIは嫌\n")
    sid = src["source_id"]
    lines = [[i, x] for i, x in enumerate(b.settings.drive_root.joinpath(
        b._conn.execute("SELECT path FROM sources WHERE id = ?", (sid,)).fetchone()[0]).read_text().splitlines(), 1)]
    n = next(i for i, x in lines if "A社様" in x)
    q = next(i for i, x in lines if x.startswith(">"))
    item = {"source_id": sid, "signal": "explicit", "lines": lines, "similar_memories": [], "max_atoms": 8}
    atoms = promote.validate(b, item, {"atoms": [
        {"kind": "semantic", "text": "[[A社|A社様]]の採用サイトは写真を社内で撮る", "derivation": "verbatim",
         "lines": [n, n], "confidence": 0.9, "concepts": [{"name": "A社", "type": "organization"}]},
        {"kind": "semantic", "text": "考えさせるUIは嫌", "derivation": "verbatim", "lines": [q, q],
         "confidence": 0.9, "concepts": []}]})
    assert atoms[0]["text"] == "A社様の採用サイトは写真を社内で撮る" and atoms[0]["derivation"] == "verbatim"
    assert atoms[1]["derivation"] == "verbatim"
    assert atoms[0]["concepts"] == ["A社"] and atoms[0]["concept_meta"]["A社"] == {"type": "organization", "aliases": ["A社様"]}
    with b._tx():
        promote.apply(b, "test", item, atoms)
    c = b._conn.execute("SELECT id, concept_type FROM nodes WHERE kind = 'concept' AND label = 'A社'").fetchone()
    assert c["concept_type"] == "organization"
    assert b._concept_id("test", "A社様") == c["id"]  # the other name leads to the same thing
    assert c["id"] in b._recaller.seeds("A社様の件どうなった", None)
    assert b.rebuild() > 0 and b._conn.execute("SELECT concept_id FROM concept_aliases").fetchone()[0] == c["id"]
