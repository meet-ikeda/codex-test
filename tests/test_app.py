import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from exobrain.app import App, make_handler


@pytest.fixture
def served(brain, session):
    brain.remember(session, [
        {"kind": "procedural", "text": "作る前に要件を確認する", "concepts": ["進め方"], "importance": 0.9},
        {"kind": "semantic", "text": "本棚は Google ドライブ", "concepts": ["exobrain"]},
    ])
    app = App(brain)
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, port
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, token=None, host=None, ctype="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Exobrain-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = ctype
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    try:
        return res.status, json.loads(raw)
    except ValueError:  # not JSON (static files, fonts)
        return res.status, raw


def test_page_carries_the_token_and_strict_csp(served):
    app, port = served
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", "/")
    res = conn.getresponse()
    html = res.read().decode()
    assert res.status == 200 and f'data-token="{app.token}"' in html
    assert "script-src 'self'" in res.getheader("Content-Security-Policy")


def test_api_needs_token_and_local_host(served):
    app, port = served
    assert request(port, "GET", "/api/safety")[0] == 403
    assert request(port, "GET", "/api/safety", token="wrong")[0] == 403
    assert request(port, "GET", "/api/safety", token=app.token, host="evil.example")[0] == 403  # DNS rebinding
    assert request(port, "POST", "/api/pause", {"paused": True}, token=app.token, ctype="text/plain")[0] == 415
    assert request(port, "GET", "/api/safety", token=app.token)[0] == 200


def test_static_files_cannot_escape(served):
    _, port = served
    assert request(port, "GET", "/../../brain.py")[0] == 404
    assert request(port, "GET", "/vendor/graphology.umd.min.js")[0] == 200
    assert request(port, "GET", "/fonts/inter-tight-latin-300-normal.woff2")[0] == 200


def test_graph_and_node(served):
    app, port = served
    status, g = request(port, "GET", "/api/graph", token=app.token)
    assert status == 200
    kinds = sorted(n["kind"] for n in g["nodes"])
    assert kinds == ["concept", "concept", "procedural", "semantic"]
    assert all(0 < e["w"] <= 1 for e in g["edges"])
    rule = next(n for n in g["nodes"] if n["kind"] == "procedural")
    status, d = request(port, "GET", f"/api/node/{rule['id']}", token=app.token)
    assert d["node"]["body"] == "作る前に要件を確認する" and d["neighbors"]
    _, only_rules = request(port, "GET", "/api/graph?kinds=procedural", token=app.token)
    assert {n["kind"] for n in only_rules["nodes"]} == {"procedural", "concept"}


def test_memo_shelf_and_search(served):
    app, port = served
    status, r = request(port, "POST", "/api/memo", {"title": "打ち合わせ", "text": "写真は社内撮影にする"}, app.token)
    assert status == 200
    _, again = request(port, "POST", "/api/memo", {"title": "打ち合わせ", "text": "写真は社内撮影にする"}, app.token)
    assert again == {"duplicate": True}
    _, shelves = request(port, "GET", "/api/shelves", token=app.token)
    assert shelves["sources"][0]["title"] == "打ち合わせ"
    _, found = request(port, "GET", "/api/search?q=%E7%A4%BE%E5%86%85%E6%92%AE%E5%BD%B1", token=app.token)
    assert found["bookshelf"][0]["source_id"] == r["source_id"]
    _, src = request(port, "GET", f"/api/source/{r['source_id']}", token=app.token)
    assert src["body"] == "写真は社内撮影にする"


def test_erase_through_the_screen(served):
    app, port = served
    _, memo = request(port, "POST", "/api/memo", {"title": "消すメモ", "text": "秘密"}, app.token)
    _, plan = request(port, "POST", "/api/erase/plan", {"source_ids": [memo["source_id"]]}, app.token)
    assert [s["title"] for s in plan["sources"]] == ["消すメモ"]
    status, err = request(port, "POST", "/api/erase", {"plan_token": plan["plan_token"], "confirm": "はい"}, app.token)
    assert status == 400 and "消去する" in err["error"]
    status, err = request(port, "POST", "/api/erase", {"plan_token": plan["plan_token"], "confirm": "消去する"}, app.token)
    assert status == 400  # a plan is single-use: a failed attempt means confirming again
    _, plan = request(port, "POST", "/api/erase/plan", {"source_ids": [memo["source_id"]]}, app.token)
    status, done = request(port, "POST", "/api/erase", {"plan_token": plan["plan_token"], "confirm": "消去する"}, app.token)
    assert status == 200 and done["erased_sources"] == 1


def test_sleep_button(served):
    app, port = served
    status, s = request(port, "POST", "/api/sleep", {"use_ai": False}, app.token)
    assert status == 200 and s["running"]
    for _ in range(100):
        _, s = request(port, "GET", "/api/sleep", token=app.token)
        if not s["running"]:
            break
        time.sleep(0.05)
    assert s["result"]["journal"] and s["dreams"] and not s["due"]


def test_pause_verify_backup_restore(served):
    app, port = served
    assert request(port, "POST", "/api/pause", {"paused": True}, app.token)[1] == {"paused": True}
    assert app.brain.paused
    request(port, "POST", "/api/pause", {"paused": False}, app.token)
    assert request(port, "POST", "/api/verify", {}, app.token)[1]["ok"]
    name = request(port, "POST", "/api/backup", {}, app.token)[1]["backup"]
    assert request(port, "POST", "/api/restore", {"name": "../../etc/passwd"}, app.token)[0] == 400
    assert request(port, "POST", "/api/restore", {"name": name}, app.token)[1]["verified"]


def test_timer_is_saved(served):
    app, port = served
    _, r = request(port, "POST", "/api/sleep/timer", {"hour": 3, "minute": 5}, app.token)
    assert r["timer"] == {"hour": 3, "minute": 5}
    assert request(port, "POST", "/api/sleep/timer", {"hour": 25}, app.token)[0] == 400
    _, s = request(port, "GET", "/api/sleep", token=app.token)
    assert s["timer"] == {"hour": 3, "minute": 5}
