"""The approval page escapes everything that comes from the model or the server."""

import re

from boundary_latch.mcp.page import render

PAYLOAD = "<img src=x onerror=alert(1)>"
QUOTE = '"><script>alert(1)</script>'


def state():
    return {
        "pending": [{"operation_id": QUOTE, "tool": PAYLOAD, "summary": PAYLOAD,
                     "arguments": {PAYLOAD: QUOTE}, "waited": 3}],
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


def test_the_only_script_is_the_refresh_with_the_nonce():
    html = render(state(), "token", "nonce")
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts == ['<script nonce="nonce">']
    assert "innerHTML" not in html
    assert "location.reload()" in html


def test_attribute_values_cannot_break_out():
    html = render(state(), 'tok"en', "nonce")
    for value in re.findall(r'value="([^"]*)"', html):
        assert "<" not in value and ">" not in value
    assert 'value="tok&quot;en"' in html


def test_an_empty_page_says_nothing_is_waiting():
    html = render({"pending": [], "changed": []}, "token", "nonce")
    assert "No calls are waiting." in html
    assert "No tool definitions changed." in html
