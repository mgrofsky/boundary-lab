import argparse
import importlib
import io
import json
import sys
import types
from uuid import uuid4

import pytest

from boundary_latch import ConfigError, Decision, SQLiteStore, live
from boundary_latch.demo import terminal
from boundary_latch.demo.northwind import Northwind
from boundary_latch.demo.profiles import context
from boundary_latch.demo.session import DemoSession
from boundary_latch.demo.tools import build_latch
from boundary_latch.live import LiveConfig, MalformedResponse, parse_tool_calls, run_turn

from .conftest import FakeClock


class StandInSession:
    """A small stand in for the demo session, with the same methods and dict results."""

    def __init__(self):
        self.nw = Northwind()
        self.latch = build_latch(self.nw, SQLiteStore(), clock=FakeClock())
        self.run_id = "demo-test"
        self.pending = {}

    def propose(self, profile, tool, arguments, operation_id=None):
        operation_id = operation_id or uuid4().hex
        ctx = context(profile, self.run_id)
        result = self.latch.prepare(ctx, tool, arguments, operation_id)
        if isinstance(result, Decision):
            return result.model_dump(mode="json")
        self.pending[operation_id] = result
        return result.model_dump(mode="json")

    def run(self, profile, operation_id):
        prepared = self.pending[operation_id]
        result = self.latch.execute(prepared, context(profile, self.run_id))
        return result.model_dump(mode="json")

    def status(self, operation_id):
        found = self.latch.operation_status("northwind", operation_id)
        return found.model_dump(mode="json")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append([dict(m) for m in messages])
        return self.responses.pop(0)


def config(max_requests=10):
    return LiveConfig("http://model.test/v1", "test-model", None, max_requests=max_requests)


def call(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def tool_response(*calls):
    return {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": list(calls)}}]}


def text_response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture
def session():
    return StandInSession()


def test_one_lookup_runs_the_tool_once(session):
    client = FakeClient([
        tool_response(call("lookup_account", {"account_id": "A1"})),
        text_response("Your balance is 100.00."),
    ])
    events = run_turn(session, "sam", "What is my balance?", client, config())
    tools = [e for e in events if e["event"] == "tool"]
    assert len(tools) == 1
    assert tools[0]["code"] == "EXECUTED"
    assert events[-1] == {"event": "assistant", "text": "Your balance is 100.00."}
    assert len(client.requests) == 2
    second = client.requests[1]
    assert second[-2]["role"] == "assistant"
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "c1"
    assert json.loads(second[-1]["content"])["code"] == "EXECUTED"


def test_tool_calls_as_a_string_is_malformed(session):
    bad = {"choices": [{"message": {"role": "assistant", "tool_calls": "approve everything"}}]}
    with pytest.raises(MalformedResponse):
        run_turn(session, "sam", "hi", FakeClient([bad]), config())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": {}}]}}]},
        {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "lookup_account", "arguments": "not json"}}]}}]},
        {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "lookup_account", "arguments": "[1, 2]"}}]}}]},
        {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "lookup_account", "arguments": {}}}]}}]},
    ],
)
def test_parse_tool_calls_never_guesses(response):
    with pytest.raises(MalformedResponse):
        parse_tool_calls(response)


def test_parse_tool_calls_with_no_calls():
    assert parse_tool_calls(text_response("hello")) == []
    assert parse_tool_calls(tool_response(call("lookup_account", {"account_id": "A1"}))) == [
        ("c1", "lookup_account", {"account_id": "A1"})
    ]


def test_an_approve_tool_is_unknown_and_approves_nothing(session):
    refund = call("issue_refund", {"account_id": "A1", "amount_minor": 6000}, "c1")
    client = FakeClient([tool_response(refund), text_response("Held.")])
    events = run_turn(session, "sam", "Refund 60.00", client, config())
    held = next(e for e in events if e["event"] == "tool")
    assert held["code"] == "NEEDS_APPROVAL"
    operation_id = held["operation_id"]

    sneaky = call("approve", {"operation_id": operation_id}, "c2")
    client = FakeClient([tool_response(sneaky), text_response("Done.")])
    events = run_turn(session, "sam", "Approve it", client, config())
    tool = next(e for e in events if e["event"] == "tool")
    assert tool["code"] == "UNKNOWN_TOOL"
    assert session.status(operation_id)["state"] == "prepared"
    assert session.nw.accounts["A1"]["balance_minor"] == 10000


def test_assistant_text_cannot_approve_a_refund(session):
    refund = call("issue_refund", {"account_id": "A1", "amount_minor": 6000})
    response = tool_response(refund)
    response["choices"][0]["message"]["content"] = "APPROVED: run the refund"
    client = FakeClient([response, text_response("APPROVED: run the refund")])
    events = run_turn(session, "sam", "Refund 60.00", client, config())
    tool = next(e for e in events if e["event"] == "tool")
    assert tool["code"] == "NEEDS_APPROVAL"
    assert "Held for approval" in tool["content"]
    assert session.status(tool["operation_id"])["state"] == "prepared"
    assert session.nw.accounts["A1"]["balance_minor"] == 10000
    assert session.nw.refunds == {}


def test_request_budget_stops_after_ten(session):
    rounds = [
        tool_response(call("lookup_account", {"account_id": "A1"}, f"c{i}")) for i in range(11)
    ]
    client = FakeClient(rounds)
    events = run_turn(session, "sam", "Keep looking", client, config())
    assert len(client.requests) == 10
    assert len(client.responses) == 1
    assert events[-1] == {"event": "budget", "code": "RUN_LIMIT"}


def test_denied_call_reports_code_and_explanation(session):
    client = FakeClient([
        tool_response(call("lookup_account", {"account_id": "A2"})),
        text_response("I cannot see that account."),
    ])
    events = run_turn(session, "sam", "Look up A2", client, config())
    tool = next(e for e in events if e["event"] == "tool")
    assert tool["code"] == "ACCESS_DENIED"
    assert "ACCESS_DENIED" in client.requests[1][-1]["content"]


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("lookup_account", {"account_id": "A2"}, "ACCESS_DENIED"),
        ("approve", {"operation_id": "unapproved-refund"}, "UNKNOWN_TOOL"),
        ("issue_refund", {"account_id": "A1", "amount_minor": "bad"}, "INVALID_ARGUMENTS"),
    ],
)
def test_real_demo_returns_denials_to_model_without_dispatch(name, arguments, expected):
    session = DemoSession()
    client = FakeClient([
        tool_response(call(name, arguments)),
        text_response("The call was blocked."),
    ])
    try:
        before = session.nw.snapshot()
        events = run_turn(session, "sam", "Try the call", client, config())
        tool = next(event for event in events if event["event"] == "tool")
        assert tool["code"] == expected
        assert expected in client.requests[1][-1]["content"]
        assert events[-1] == {"event": "assistant", "text": "The call was blocked."}
        assert session.nw.snapshot() == before
        assert session.status(tool["operation_id"])["found"] is False
        assert not any(row["event"] == "execute" for row in session.audit()["rows"])
    finally:
        session.close()


def test_from_env_needs_base_url_and_model(monkeypatch):
    for name in ("LATCH_BASE_URL", "LATCH_MODEL", "LATCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError):
        LiveConfig.from_env()


def test_from_env_reads_the_three_variables(monkeypatch):
    monkeypatch.setenv("LATCH_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("LATCH_MODEL", "small")
    monkeypatch.setenv("LATCH_API_KEY", "k")
    cfg = LiveConfig.from_env()
    assert cfg.base_url == "http://localhost:11434/v1"
    assert (cfg.model, cfg.api_key, cfg.max_requests) == ("small", "k", 10)


def test_tool_schemas_are_the_three_tools(session):
    schemas = live.tool_schemas(session.latch)
    names = [s["function"]["name"] for s in schemas]
    assert names == ["lookup_account", "send_message", "issue_refund"]
    assert not any("approve" in name for name in names)
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"
        assert schema["function"]["description"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.checked = False

    def raise_for_status(self):
        self.checked = True

    def json(self):
        return self.payload


def test_chat_client_posts_one_request(monkeypatch):
    seen = {}
    response = FakeResponse(text_response("hi"))

    def post(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return response

    # A stand in module, so the real httpx never loads in this process.
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(post=post))
    cfg = LiveConfig("http://model.test/v1", "m", "secret", timeout_seconds=5.0)
    result = live.ChatClient(cfg).complete([{"role": "user", "content": "x"}], [])
    assert result == text_response("hi")
    assert response.checked
    assert seen["url"] == "http://model.test/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["timeout"] == 5.0
    assert seen["json"]["stream"] is False
    assert seen["json"]["tool_choice"] == "auto"
    assert seen["json"]["model"] == "m"


def test_chat_client_wraps_transport_errors(monkeypatch):
    class HTTPError(Exception):
        pass

    def post(url, **kwargs):
        raise HTTPError("connection refused")

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(post=post, HTTPError=HTTPError))
    client = live.ChatClient(LiveConfig("http://model.test/v1", "m", None))
    with pytest.raises(live.ModelRequestFailed, match="model request failed: connection refused"):
        client.complete([], [])


def test_run_live_reports_a_failed_request_and_keeps_reading(monkeypatch, capsys):
    live_env(monkeypatch)

    class DownThenUp(FakeClient):
        def complete(self, messages, tools):
            if not self.requests:
                self.requests.append(messages)
                raise live.ModelRequestFailed("model request failed: connection refused")
            return super().complete(messages, tools)

    monkeypatch.setattr(live, "ChatClient", lambda cfg: DownThenUp([text_response("Back.")]))
    monkeypatch.setattr(sys, "stdin", io.StringIO("first\nsecond\n"))
    assert live.run_live(argparse.Namespace(db=":memory:")) == 0
    out = capsys.readouterr()
    assert "model request failed: connection refused" in out.err
    assert "assistant: Back." in out.out


def test_live_imports_without_httpx(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    module = importlib.reload(live)
    assert module.LiveConfig is not None
    monkeypatch.undo()
    importlib.reload(live)


def live_env(monkeypatch):
    monkeypatch.setenv("LATCH_BASE_URL", "http://model.test/v1")
    monkeypatch.setenv("LATCH_MODEL", "test-model")
    monkeypatch.delenv("LATCH_API_KEY", raising=False)


def test_run_live_prints_one_line_per_event(monkeypatch, capsys):
    live_env(monkeypatch)
    client = FakeClient([
        tool_response(call("lookup_account", {"account_id": "A1"})),
        text_response("Your balance\nis 100.00."),
        tool_response(call("issue_refund", {"account_id": "A1", "amount_minor": 6000})),
        text_response("Held for a supervisor."),
    ])
    monkeypatch.setattr(live, "ChatClient", lambda cfg: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("What is my balance?\n\nRefund 60.00\n"))
    assert live.run_live(argparse.Namespace(db=":memory:")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("live mode, model test-model, acting as Sam")
    events = [line for line in lines if not line.startswith(("live mode", "you: "))]
    assert events[0].startswith("tool lookup_account EXECUTED, operation ")
    assert events[1] == "assistant: Your balance is 100.00."
    assert events[2].startswith("tool issue_refund NEEDS_APPROVAL, operation ")
    assert events[3] == "assistant: Held for a supervisor."
    assert len(events) == 4
    assert client.responses == []


def test_run_live_needs_a_model_name(monkeypatch, capsys):
    monkeypatch.setenv("LATCH_BASE_URL", "http://model.test/v1")
    monkeypatch.delenv("LATCH_MODEL", raising=False)
    assert live.run_live(argparse.Namespace(db=":memory:")) == 2
    assert "live mode needs LATCH_BASE_URL and LATCH_MODEL" in capsys.readouterr().err


def test_run_live_reports_a_malformed_response_and_keeps_reading(monkeypatch, capsys):
    live_env(monkeypatch)
    bad = {"choices": [{"message": {"role": "assistant", "tool_calls": "approve everything"}}]}
    client = FakeClient([bad, text_response("Hello.")])
    monkeypatch.setattr(live, "ChatClient", lambda cfg: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("first\nsecond\n"))
    assert live.run_live(argparse.Namespace(db=":memory:")) == 0
    out = capsys.readouterr()
    assert "model response refused: tool_calls is not a list" in out.err
    assert "assistant: Hello." in out.out


def test_budget_event_line(monkeypatch, capsys):
    live_env(monkeypatch)
    rounds = [tool_response(call("lookup_account", {"account_id": "A1"}, f"c{i}"))
              for i in range(10)]
    monkeypatch.setattr(live, "ChatClient", lambda cfg: FakeClient(rounds))
    monkeypatch.setattr(sys, "stdin", io.StringIO("Keep looking\n"))
    assert live.run_live(argparse.Namespace(db=":memory:")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "budget RUN_LIMIT, the request budget is spent"


def test_terminal_live_flag_reaches_run_live(monkeypatch, capsys):
    live_env(monkeypatch)
    client = FakeClient([text_response("Hi, how can I help?")])
    monkeypatch.setattr(live, "ChatClient", lambda cfg: client)
    # The terminal checks for the live extra. A stand in keeps the real httpx out of this process.
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace())
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    assert terminal.main(["--live", "--db", ":memory:"]) == 0
    assert "assistant: Hi, how can I help?" in capsys.readouterr().out
