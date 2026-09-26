"""Staged recall and the hippocampus (spec v0.5 §3, §7.1)."""

import hashlib
from datetime import datetime, timedelta, timezone

from exobrain.brain import Brain
from exobrain.chunks import lexical_tokens
from exobrain.config import Settings
from exobrain.hippocampus import encode_pending, fade_expired, search
from exobrain.inbox import add_memo, add_source


class FakeEmbedder:
    """Bag of bigrams hashed into 64 dims: close texts get close vectors, deterministically."""

    model = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 64
            for g in lexical_tokens(t):
                v[int(hashlib.md5(g.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    def status(self):
        return True, "fake"


def test_records_answer_when_the_cortex_cannot(brain, session):
    add_memo(brain, "文章の方針", "# 文章\n文章は結論から書く。理由はあとに回す。\n")
    r = brain.recall(session, "結論から書く")
    assert r["searched"] == ["大脳皮質", "海馬"]
    ev = r["evidence"][0]
    assert ev["place"] == "海馬" and ev["writer"] == "オーナー" and ev["heading"] == "文章"
    assert "結論から書く" in ev["quote"] and ev["lines"] == [1, 2]
    assert "まだ記憶になっていない記録" in r["context"] and ev["source_id"] in r["context"]
    assert not r["no_record"]


def test_faded_information_is_still_on_the_bookshelf(brain, session):
    src = add_memo(brain, "昔の話", "去年の採用サイトでは社員が主役だった。\n")
    with brain._tx():
        brain._conn.execute("UPDATE hippocampus SET expires_at = ?",
                            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
        assert fade_expired(brain, "sleep") == 1
    r = brain.recall(session, "採用サイトでは社員が主役")
    assert r["searched"] == ["大脳皮質", "海馬", "本棚"] and r["evidence"][0]["place"] == "本棚"
    assert r["evidence"][0]["source_id"] == src["source_id"]


def test_vault_is_the_last_place_before_no_record(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "旅行.md").write_text("来年の旅行は北海道に行きたい。\n", encoding="utf-8")
    s = Settings(home=tmp_path / "h", drive_root=tmp_path / "d", embed_model="", vault_root=vault)
    with Brain(s) as b:
        sid = b.start_session("Claude Desktop")["session_id"]
        r = b.recall(sid, "北海道に行きたい")
        assert r["searched"][-1] == "Obsidian" and r["evidence"][0]["file"] == "旅行.md"
        r = b.recall(sid, "好きな食べ物は")
        assert r["no_record"] and "確認できる記録はありませんでした" in r["context"]


def test_semantic_search_uses_embeddings_when_present(brain):
    brain.embedder = FakeEmbedder()
    add_memo(brain, "a", "文章は結論から書く。\n")
    add_memo(brain, "b", "明日の会議は十五時から。\n")
    assert encode_pending(brain) == 2 and encode_pending(brain) == 0
    hits = search(brain, "結論から書く文章")
    assert hits and hits[0].title == "a" and hits[0].semantic > 0.5


def test_an_unembedded_chunk_is_still_found_by_words(brain):
    brain.embedder = FakeEmbedder()
    add_memo(brain, "a", "文章は結論から書く。\n")
    encode_pending(brain)
    add_memo(brain, "b", "北海道の旅行計画を立てる。\n")  # arrived while Ollama was busy: no vector yet
    assert [h.title for h in search(brain, "北海道の旅行計画")] == ["b"]


def test_dream_journals_are_not_evidence(brain):
    add_source(brain, kind="dream", author="ai", ai_name="睡眠", title="夢日記", body="結論から書くと決めた夢。\n",
               actor="sleep")
    assert search(brain, "結論から書く") == []
    assert brain._conn.execute("SELECT COUNT(*) FROM hippocampus").fetchone()[0] == 0


def test_remember_explicit_goes_straight_to_the_cortex(brain, session):
    r = brain.remember_explicit(session, "AI 特有の言い回しを使わない", "procedural", ["文章"], "文体の話")
    node = brain.node(r["node_id"])
    assert node["promoted_by"] == "explicit" and node["importance"] == 1.0 and node["source_id"] == r["source_id"]
    assert brain.remember_explicit(session, "AI 特有の言い回しを使わない")["already_remembered"]
    got = brain.recall(session, "言い回し")
    assert r["node_id"] in got["memory_ids"] and got["searched"] == ["大脳皮質"]


def test_rebuild_keeps_chunks_hippocampus_and_checkpoints(brain, session):
    add_memo(brain, "a", "# A\n本文\n")
    brain.submit_daily_log(session, "題", ["起きたこと"], [], [], ["決めたこと"], [])
    before = brain.snapshot()
    brain.rebuild()
    after = brain.snapshot()
    for t in ("chunks", "hippocampus", "ai_checkpoints", "sources"):
        assert before[t] == after[t], t
