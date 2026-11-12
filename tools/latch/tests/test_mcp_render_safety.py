"""The approval page escapes everything that comes from the model or the server."""

import re

import pytest

from boundary_latch.mcp.page import render

PAYLOAD = "<img src=x onerror=alert(1)>"
QUOTE = '"><script>alert(1)</script>'


def state():
    return {
        "pending": [{"operation_id": QUOTE, "tool": PAYLOAD, "summary": PAYLOAD,
                     "arguments": {PAYLOAD: QUOTE}, "waited": 3, "flow_reason": PAYLOAD,
                     "digest": QUOTE}],
        "changed": [{"tool": QUOTE, "hash": QUOTE,
                     "old": {"name": PAYLOAD, "description": QUOTE},
                     "new": {"name": PAYLOAD, "description": PAYLOAD}}],
    }


def test_markup_in_every_field_is_escaped():
    html = render(state(), "token", "nonce")
    assert PAYLOAD not in html
    assert QUOTE not in html
    assert "<script>alert" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_the_only_script_is_the_refresh_with_the_nonce_and_only_when_nothing_is_shown():
    html = render({"pending": [], "changed": []}, "token", "nonce")
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts == ['<script nonce="nonce">']
    assert "innerHTML" not in html
    assert "location.reload()" in html


@pytest.mark.parametrize("shown", ["pending", "changed"])
def test_while_something_is_shown_the_page_does_not_reload_by_itself(shown):
    full = state()
    page = {"pending": [], "changed": [], shown: full[shown]}
    html = render(page, "tok en", "nonce", now=1_800_000_000.0)
    assert "<script" not in html
    assert "location.reload" not in html
    assert '<a href="/?token=tok%20en">Refresh</a>' in html
    assert "Drawn at 2027-01-15 08:00:00 UTC" in html


def test_the_flow_reason_is_shown_above_the_arguments():
    item = {"operation_id": "op", "tool": "send", "summary": "s", "arguments": {"a": 1},
            "waited": 0, "flow_reason": "This run read private data (get_file).", "digest": "d"}
    html = render({"pending": [item], "changed": []}, "token", "nonce")
    reason = html.index("This run read private data (get_file).")
    assert reason < html.index("&quot;a&quot;: 1")
    plain = dict(item, flow_reason="")
    assert "Flow rule" not in render({"pending": [plain], "changed": []}, "token", "nonce")


def test_attribute_values_cannot_break_out():
    html = render(state(), 'tok"en', "nonce")
    for value in re.findall(r'value="([^"]*)"', html):
        assert "<" not in value and ">" not in value
    assert 'value="tok&quot;en"' in html


def test_an_empty_page_says_nothing_is_waiting():
    html = render({"pending": [], "changed": []}, "token", "nonce")
    assert "No calls are waiting." in html
    assert "No tool definitions changed." in html
