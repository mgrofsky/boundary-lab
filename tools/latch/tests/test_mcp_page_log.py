"""The approval page never writes its token to the log after the startup line."""

import asyncio
import logging
import socket
import threading

import pytest

from boundary_latch.mcp.page import ApprovalPage

from .mcp_helpers import http

TOKEN = "SeCrEtToKeN0123456789"


class StubProxy:
    def page_state(self):
        return {"pending": [], "changed": []}

    def decide(self, op_id, approve, digest=""):
        return "UNKNOWN_OPERATION"

    def accept(self, tool, shown):
        return False


@pytest.fixture
def page():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    served = ApprovalPage(StubProxy(), loop, 0, token=TOKEN)
    served.start()
    yield served
    served.close()
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


def test_request_lines_in_the_log_have_no_query_and_no_token(page, caplog):
    base = f"http://127.0.0.1:{page.port}"
    with caplog.at_level(logging.INFO, logger="boundary_latch.mcp"):
        assert http(f"{base}/?token={TOKEN}&x=1")[0] == 200
        assert http(f"{base}/?token=wrong")[0] == 403
        assert http(f"{base}/missing?token={TOKEN}")[0] == 404
        http(f"{base}/decide", data=f"token={TOKEN}&action=nothing".encode())
    assert caplog.records
    assert TOKEN not in caplog.text
    assert "?" not in caplog.text
    assert "GET / " in caplog.text


def test_an_error_line_with_the_token_in_it_is_scrubbed(page, caplog):
    with caplog.at_level(logging.INFO, logger="boundary_latch.mcp"), \
            socket.create_connection(("127.0.0.1", page.port), timeout=5) as sock:
        sock.sendall(f"GET /?token={TOKEN} HTTP/9.9 extra\r\n\r\n".encode())
        sock.recv(4096)
    assert caplog.records
    assert TOKEN not in caplog.text
