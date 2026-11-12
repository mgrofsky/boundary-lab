"""Run boundary-latch mcp as a subprocess against the fake server."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FAKE = Path(__file__).with_name("fake_mcp_server.py")

POLICY = """
[limits]
max_actions_per_run = 1000
run_lifetime_seconds = 0

[tools.echo]
require_permission = "mcp:use"

[tools.fail]
require_permission = "mcp:use"

[tools.rpc_error]
require_permission = "mcp:use"

[tools.sleep]
require_permission = "mcp:use"

[tools.mutate]
require_permission = "mcp:use"

[tools.crash]
require_permission = "mcp:use"

[tools.ask_roots]
require_permission = "mcp:use"

[tools."files.read-text"]
require_permission = "mcp:use"

[tools.guarded]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
approval.mode = "always"

[tools.pay]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
recipient_field = "to"
allowed_recipients = ["ok@example.com"]
approval.mode = "over_amount"
approval.amount_field = "amount"
approval.threshold_minor = 5000

[tools."bad name"]
require_permission = "mcp:use"

[tools.ask_input]
require_permission = "mcp:use"

[tools.reserved]
require_permission = "mcp:use"
"""

# The protocol fields a 2026-07-28 client puts in every request's _meta, as in the capture.
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
}


class Proxy:
    """One proxy process, with helpers to talk JSON-RPC to it."""

    def __init__(self, tmp: Path, *options: str, policy: str = POLICY, init: bool = True,
                 modern: bool = False):
        self.tmp = tmp
        self.modern = modern
        self.log = tmp / "server.log"
        self.policy = tmp / "policy.toml"
        self.policy.write_text(policy)
        self.err_path = tmp / "proxy.err"
        self.err = self.err_path.open("w")
        command = [sys.executable, "-m", "boundary_latch", "mcp", "--policy", str(self.policy),
                   "--no-open", "--approver", "person", *options,
                   "--", sys.executable, str(FAKE), str(self.log),
                   *(["--modern"] if modern else [])]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.err)
        self.lines: queue.Queue = queue.Queue()
        self.raw: list[bytes] = []
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 1000
        if init:
            self.initialize()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.raw.append(line)
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def send_raw(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, message: dict[str, Any]) -> None:
        """Send one message. In modern mode a request gets the protocol fields in _meta."""
        if self.modern and "method" in message and "id" in message:
            params = message.get("params", {})
            if isinstance(params, dict) and "_meta" not in params:
                message = {**message, "params": {**params, "_meta": META}}
        self.send_raw(json.dumps(message).encode() + b"\n")

    def recv(self, timeout: float = 10.0) -> dict[str, Any] | None:
        return self.lines.get(timeout=timeout)

    def recv_id(self, msg_id: Any, timeout: float = 10.0) -> dict[str, Any]:
        """Wait for the response with this id, keeping other messages for later."""
        deadline = time.monotonic() + timeout
        held = []
        try:
            while True:
                message = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
                if message is None:
                    raise AssertionError("the proxy closed stdout")
                if message.get("id") == msg_id and type(message.get("id")) is type(msg_id) \
                        and "method" not in message:
                    return message
                held.append(message)
        finally:
            for message in held:
                self.lines.put(message)

    def request(self, method: str, params: dict | None = None, msg_id: Any = None,
                timeout: float = 10.0) -> dict[str, Any]:
        if msg_id is None:
            self.next_id += 1
            msg_id = self.next_id
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        return self.recv_id(msg_id, timeout)

    def initialize(self) -> None:
        if self.modern:
            reply = self.request("server/discover", {})
            assert reply["result"]["supportedVersions"] == ["2026-07-28"], reply
            return
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}})
        assert "result" in reply, reply
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, arguments: Any = None, msg_id: Any = None,
             timeout: float = 10.0) -> dict[str, Any]:
        params: dict[str, Any] = {"name": tool}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request("tools/call", params, msg_id, timeout)

    def server_log(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def server_calls(self) -> list[dict[str, Any]]:
        return [m for m in self.server_log() if m.get("method") == "tools/call"]

    def stderr(self) -> str:
        if not self.err.closed:
            self.err.flush()
        return self.err_path.read_text()

    def page_url(self, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = re.search(r"approval page: (http://\S+)", self.stderr())
            if found:
                return found.group(1)
            time.sleep(0.05)
        raise AssertionError("no approval page URL on stderr")

    def close(self, timeout: float = 10.0) -> int:
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        self.err.close()
        return code


def text_of(reply: dict[str, Any]) -> str:
    return reply["result"]["content"][0]["text"]


def http(url: str, data: bytes | None = None, headers: dict | None = None
         ) -> tuple[int, dict, str]:
    request = urllib.request.Request(url, data=data, headers=headers or {})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def wait_for(predicate, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
