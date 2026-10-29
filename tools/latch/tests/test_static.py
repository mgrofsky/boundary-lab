import importlib.util
import re
from importlib import resources

import pytest

from boundary_latch.demo import server
from boundary_latch.demo.session import DemoSession

STATIC = resources.files("boundary_latch.demo").joinpath("static")
INDEX = STATIC.joinpath("index.html").read_text()
APP = STATIC.joinpath("app.js").read_text()
STYLE = STATIC.joinpath("style.css").read_text()

needs_fastapi = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None, reason="needs boundary-latch[demo]"
)


def test_index_has_no_inline_script():
    assert not re.search(r"<script(?![^>]*src=)[^>]*>\s*\S", INDEX, re.IGNORECASE)
    assert re.search(r'<script src="/static/app.js"', INDEX)


def test_index_has_no_inline_style_or_handlers():
    assert "<style" not in INDEX.lower()
    assert not re.search(r"\sstyle\s*=", INDEX, re.IGNORECASE)
    assert not re.search(r"\son\w+\s*=", INDEX, re.IGNORECASE)
    assert "javascript:" not in INDEX.lower()


def test_index_loads_only_local_files():
    for url in re.findall(r'(?:src|href)="([^"]+)"', INDEX):
        assert url.startswith("/static/"), url


def test_app_never_writes_markup():
    for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function", "setTimeout(\"", "setInterval(\""):
        assert word not in APP, word


def test_app_sends_the_token_and_json():
    assert 'meta[name="latch-token"]' in APP
    assert '"X-Latch-Token"' in APP
    assert '"Content-Type": "application/json"' in APP


def test_every_element_the_app_uses_is_on_the_page():
    ids = set(re.findall(r'\sid="([\w-]+)"', INDEX))
    used = set(re.findall(r'byId\("([\w-]+)"\)', APP))
    assert used
    assert used <= ids, used - ids


def test_page_labels():
    assert "Sample identities, not real users" in INDEX
    for label in ("Propose", "Approve", "Deny", "Run", "Scenarios"):
        assert f">{label}<" in INDEX, label


def test_amount_is_parsed_without_floats():
    assert "parseFloat" not in APP
    assert "toMinor" in APP


@needs_fastapi
def test_files_are_served_with_their_types():
    from fastapi.testclient import TestClient

    app = server.create_app(DemoSession(), "t", port=8765)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert js.headers["content-type"] == "text/javascript; charset=utf-8"
    assert js.text == APP
    css = client.get("/static/style.css")
    assert css.headers["content-type"] == "text/css; charset=utf-8"
    assert css.text == STYLE
    page = client.get("/")
    assert page.headers["content-type"].startswith("text/html")
    assert 'content="LATCH_TOKEN"' not in page.text
