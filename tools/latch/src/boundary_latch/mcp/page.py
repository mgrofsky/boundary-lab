"""The approval page, served on 127.0.0.1 from the standard library."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

logger = logging.getLogger("boundary_latch.mcp")

BODY_LIMIT = 64 * 1024
# The answer to a decision posted from a page drawn before the operation changed.
STALE_PAGE = "STALE_PAGE"
STALE_TEXT = "This page is out of date, refresh it."
LOOP_SECONDS = 10.0

STYLE = """
body { font: 15px/1.4 system-ui, sans-serif; margin: 24px; color: #1d1d1f; background: #fff; }
h1 { font-size: 20px; } h2 { font-size: 17px; margin-top: 28px; }
.item { border: 1px solid #ccc; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
pre { background: #f4f4f4; padding: 8px; overflow-x: auto; white-space: pre-wrap; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
form { display: inline-block; margin-right: 8px; }
button { font: inherit; padding: 4px 14px; }
.muted { color: #666; }
.flow { border-left: 4px solid #b26b00; padding-left: 8px; }
@media (prefers-color-scheme: dark) {
  body { color: #eee; background: #1b1b1d; } pre { background: #2a2a2d; }
  .item { border-color: #444; } .muted { color: #aaa; }
}
"""


def flow_note(reason: Any) -> str:
    """The flow reason as its own paragraph, or nothing when the rule did not apply."""
    if not reason:
        return ""
    return f'<p class="flow"><strong>Flow rule:</strong> {escape(str(reason))}</p>'


QUERY = re.compile(r"\?[^\s'\"]*")


def scrub(text: str, token: str) -> str:
    """A log line with every query string cut off and the token itself removed."""
    text = QUERY.sub("", text)
    for secret in {token, quote(token)}:
        text = text.replace(secret, "[token]")
    return text


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)


def render(state: dict[str, Any], token: str, nonce: str, now: float | None = None) -> str:
    """The page as HTML. Every value from the model or the server is escaped.

    The page reloads by itself only while nothing is shown, so an item never moves
    under the pointer. Otherwise it has a Refresh link and the time it was drawn.
    """
    def form(action: str, label: str, fields: dict[str, str]) -> str:
        hidden = "".join(
            f'<input type="hidden" name="{escape(k, quote=True)}" value="{escape(v, quote=True)}">'
            for k, v in {"token": token, "action": action, **fields}.items()
        )
        return f'<form method="post" action="/decide">{hidden}<button>{label}</button></form>'

    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Latch approvals</title>",
        f'<style nonce="{escape(nonce, quote=True)}">{STYLE}</style></head><body>',
        "<h1>Latch approvals</h1>",
        "<h2>Calls waiting for a person</h2>",
    ]
    if not state["pending"]:
        parts.append('<p class="muted">No calls are waiting.</p>')
    for item in state["pending"]:
        op = str(item["operation_id"])
        parts.append(
            '<div class="item">'
            f"<p><strong>{escape(str(item['tool']))}</strong> "
            f'<span class="muted">waited {int(item["waited"])} s, operation {escape(op)}</span></p>'
            f"<p>{escape(str(item['summary']))}</p>"
            + flow_note(item.get("flow_reason"))
            + f"<pre>{escape(pretty(item['arguments']))}</pre>"
            + form("approve", "Approve", {"operation_id": op, "digest": str(item["digest"])})
            + form("deny", "Deny", {"operation_id": op, "digest": str(item["digest"])})
            + "</div>"
        )
    parts.append("<h2>Changed tool definitions</h2>")
    if not state["changed"]:
        parts.append('<p class="muted">No tool definitions changed.</p>')
    for item in state["changed"]:
        old = "(not kept)" if item["old"] is None else pretty(item["old"])
        parts.append(
            '<div class="item">'
            f"<p><strong>{escape(str(item['tool']))}</strong> "
            f'<span class="muted">new version {escape(str(item["hash"]))}</span></p>'
            '<div class="pair">'
            f"<div><p>First seen</p><pre>{escape(old)}</pre></div>"
            f"<div><p>Now</p><pre>{escape(pretty(item['new']))}</pre></div></div>"
            + form("accept", "Accept definition",
                   {"tool": str(item["tool"]), "hash": str(item["hash"])})
            + "</div>"
        )
    if state["pending"] or state["changed"]:
        drawn = datetime.fromtimestamp(time.time() if now is None else now, UTC)
        parts.append(
            f'<p class="muted"><a href="/?token={escape(quote(token), quote=True)}">Refresh</a>'
            f" Drawn at {drawn:%Y-%m-%d %H:%M:%S} UTC.</p>"
        )
    else:
        parts.append(
            f'<script nonce="{escape(nonce, quote=True)}">'
            "setTimeout(function () { location.reload(); }, 2000);</script>"
        )
    parts.append("</body></html>")
    return "".join(parts)


class ApprovalPage:
    """Serves the page in a thread. Every action runs on the proxy's event loop."""

    def __init__(self, proxy: Any, loop: asyncio.AbstractEventLoop, port: int = 0,
                 token: str | None = None):
        self.proxy, self.loop = proxy, loop
        self.token = token or secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?token={quote(self.token)}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def on_loop(self, fn: Callable[[], Any]) -> Any:
        async def run() -> Any:
            return fn()

        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(LOOP_SECONDS)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        page = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "latch"
            sys_version = ""

            def log_message(self, fmt: str, *args: Any) -> None:
                logger.info("approval page: %s", scrub(fmt % args, page.token))

            def _send(self, status: int, body: str = "", ctype: str = "text/plain",
                      extra: dict[str, str] | None = None, nonce: str = "") -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                scripts = f"'nonce-{nonce}'" if nonce else "'none'"
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'none'; script-src {scripts}; style-src {scripts}; "
                    "img-src 'none'; connect-src 'none'; form-action 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'",
                )
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _host_ok(self) -> bool:
                host = self.headers.get("Host", "")
                return host in (f"127.0.0.1:{page.port}", f"localhost:{page.port}")

            def _token_ok(self, given: str | None) -> bool:
                return given is not None and hmac.compare_digest(
                    given.encode(), page.token.encode()
                )

            def _guard(self, given: str | None) -> bool:
                if not self._host_ok():
                    self._send(403, "wrong host")
                    return False
                if not self._token_ok(given):
                    self._send(403, "missing or wrong token")
                    return False
                return True

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                if not self._guard((query.get("token") or [None])[0]):
                    return
                if url.path != "/":
                    self._send(404, "not found")
                    return
                try:
                    state = page.on_loop(page.proxy.page_state)
                except Exception:
                    logger.exception("approval page could not read the state")
                    self._send(500, "could not read the state")
                    return
                nonce = secrets.token_urlsafe(16)
                self._send(200, render(state, page.token, nonce), "text/html", nonce=nonce)

            def do_POST(self) -> None:
                if not self._host_ok():
                    self._send(403, "wrong host")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if length < 0 or length > BODY_LIMIT:
                    self._send(413, "body too large")
                    return
                form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                given = self.headers.get("X-Latch-Token") or (form.get("token") or [None])[0]
                if not self._guard(given):
                    return
                if urlsplit(self.path).path != "/decide":
                    self._send(404, "not found")
                    return
                action = (form.get("action") or [""])[0]
                try:
                    answer = self._act(action, form)
                except Exception:
                    logger.exception("approval page action failed")
                    self._send(500, "the action failed")
                    return
                if answer is None:
                    self._send(400, "unknown action")
                    return
                if answer == STALE_PAGE:
                    self._send(409, STALE_TEXT)
                    return
                logger.info("approval page %s: %s", action, answer)
                self._send(303, answer, extra={"Location": f"/?token={quote(page.token)}"})

            def _act(self, action: str, form: dict[str, list[str]]) -> str | None:
                if action in ("approve", "deny"):
                    op = (form.get("operation_id") or [""])[0]
                    digest = (form.get("digest") or [""])[0]
                    return page.on_loop(
                        lambda: page.proxy.decide(op, action == "approve", digest))
                if action == "accept":
                    tool = (form.get("tool") or [""])[0]
                    shown = (form.get("hash") or [""])[0]
                    ok = page.on_loop(lambda: page.proxy.accept(tool, shown))
                    return "ACCEPTED" if ok else "NOT_ACCEPTED"
                return None

        return Handler
