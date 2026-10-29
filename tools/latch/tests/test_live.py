import importlib
import json
import sys
import types
from uuid import uuid4

import pytest

from boundary_latch import ConfigError, Decision, SQLiteStore, live
from boundary_latch.demo.northwind import Northwind
from boundary_latch.demo.profiles import context
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


def test_live_imports_without_httpx(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    module = importlib.reload(live)
    assert module.LiveConfig is not None
    monkeypatch.undo()
    importlib.reload(live)
