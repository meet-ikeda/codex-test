"""Opens the real screen in Chromium. Skipped when Playwright or a browser is unavailable."""

import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from exobrain.app import App, make_handler  # noqa: E402

CHROMIUM = os.environ.get("EXOBRAIN_TEST_CHROMIUM", "/opt/pw-browsers/chromium")


@pytest.fixture
def page(brain, session):
    brain.submit_daily_report(session, "日報", "本文", [
        {"kind": "procedural", "text": "作る前に要件を確認する", "concepts": ["進め方"], "importance": 0.9},
        {"kind": "semantic", "text": "本棚は Google ドライブ", "concepts": ["exobrain"]},
        {"kind": "episode", "text": "要件定義をやり直した", "concepts": ["exobrain", "進め方"]},
    ])
    app = App(brain)
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with playwright.sync_playwright() as p:
        kwargs = {"executable_path": CHROMIUM} if Path(CHROMIUM).exists() else {}
        try:
            browser = p.chromium.launch(**kwargs)
        except Exception as e:  # no browser installed
            pytest.skip(f"Chromium unavailable: {e}")
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(f"http://127.0.0.1:{port}/")
        pg.locator("#graph canvas").first.wait_for()
        yield pg, errors, brain
        browser.close()
    server.shutdown()
    server.server_close()


def test_graph_renders_and_tabs_work(page):
    pg, errors, brain = page
    assert "003 memories" in pg.locator("#graph-stats").inner_text()
    assert pg.locator("#m-nodes").inner_text() == "003"  # the rail counter
    pg.check("#g-table")
    assert pg.locator("#graph-table tbody tr").count() == 3
    pg.uncheck("#g-table")

    pg.click("button[data-tab=memo]")
    pg.fill("#memo-title", "画面から渡したメモ")
    pg.fill("#memo-text", "写真は社内撮影にする")
    pg.click("#memo-form button[type=submit]")
    pg.locator("#toast").filter(has_text="本棚に保管しました").wait_for()
    assert brain.search_bookshelf("社内撮影")[0]["title"] == "画面から渡したメモ"

    pg.click("button[data-tab=shelf]")
    pg.locator("#s-list li").filter(has_text="画面から渡したメモ").click()
    pg.locator("#s-reader pre").filter(has_text="写真は社内撮影にする").wait_for()

    pg.click("button[data-tab=safety]")
    pg.click("label.switch")  # the visible switch, as a person would
    pg.locator("#paused-badge").wait_for(state="visible")
    assert brain.paused
    assert errors == []


def test_text_is_never_interpreted_as_html(page):
    pg, errors, brain = page
    brain.add_memo("<img src=x onerror=alert(1)>", "<script>alert(1)</script>")
    pg.click("button[data-tab=shelf]")
    item = pg.locator("#s-list li").filter(has_text="<img src=x onerror=alert(1)>")
    item.click()
    pg.locator("#s-reader pre").filter(has_text="<script>alert(1)</script>").wait_for()
    assert pg.locator("#s-reader img, #s-reader script").count() == 0
    assert errors == []
