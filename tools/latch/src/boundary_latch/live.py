"""Optional live model adapter. It needs boundary-latch[live] for httpx.

The model only names tools and gives arguments. Latch decides, and approval is never a tool.
Model text is passed back as an event and never read for instructions.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from .engine import Latch
from .errors import ConfigError, LatchError
from .reasons import Code

SYSTEM_PROMPT = (
    "You help Northwind support agents. Use the tools to look up accounts, send messages "
    "and issue refunds. Amounts are in cents. Some actions wait for a supervisor."
)


class MalformedResponse(LatchError):
    """The model server sent a response Latch will not guess about."""


class ModelRequestFailed(LatchError):
    """The model server could not be reached or answered with an error status."""


@dataclass(frozen=True)
class LiveConfig:
    base_url: str
    model: str
    api_key: str | None
    timeout_seconds: float = 60.0
    max_requests: int = 10

    @classmethod
    def from_env(cls) -> LiveConfig:
        base = os.environ.get("LATCH_BASE_URL")
        model = os.environ.get("LATCH_MODEL")
        if not base or not model:
            raise ConfigError("live mode needs LATCH_BASE_URL and LATCH_MODEL")
        return cls(base.rstrip("/"), model, os.environ.get("LATCH_API_KEY"))


class Session(Protocol):
    """What run_turn needs from the demo session."""

    def propose(
        self, profile: str, tool: str, arguments: dict[str, Any], operation_id: str | None = None
    ) -> dict[str, Any]: ...

    def run(self, profile: str, operation_id: str) -> dict[str, Any]: ...


class Client(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


def tool_schemas(latch: Latch) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.args_model.model_json_schema(),
            },
        }
        for spec in latch.tool_specs()
    ]


class ChatClient:
    """One plain request to an OpenAI style chat completions endpoint."""

    def __init__(self, config: LiveConfig):
        self.config = config

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            response = httpx.post(
                f"{self.config.base_url}/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                },
                headers=headers,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelRequestFailed(f"model request failed: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise MalformedResponse("response is not JSON") from exc


def _message(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise MalformedResponse("response is not a JSON object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise MalformedResponse("response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise MalformedResponse("response has no message")
    return message


def parse_tool_calls(response: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (call_id, name, arguments) for each tool call. Raise on anything unclear."""
    message = _message(response)
    calls = message.get("tool_calls")
    if calls is None:
        return []
    if not isinstance(calls, list):
        raise MalformedResponse("tool_calls is not a list")
    parsed = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise MalformedResponse("tool call has no function")
        function = call["function"]
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise MalformedResponse("tool call has no function name")
        raw = function.get("arguments")
        if not isinstance(raw, str):
            raise MalformedResponse("tool call arguments are not a JSON string")
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedResponse("tool call arguments are not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise MalformedResponse("tool call arguments are not a JSON object")
        call_id = call.get("id")
        parsed.append((call_id if isinstance(call_id, str) else "", name, arguments))
    return parsed


def _handle_call(session: Session, profile: str, name: str, args: dict[str, Any]) -> dict:
    proposed = session.propose(profile, name, args)
    operation_id = proposed.get("operation_id")
    if proposed.get("code") == Code.NEEDS_APPROVAL:
        content = f"Held for approval by a supervisor. Operation id {operation_id}."
        code = proposed["code"]
    elif proposed.get("code") == Code.ALLOWED:
        result = session.run(profile, operation_id)
        content = json.dumps(result)
        code = result["code"]
    else:
        code = proposed["code"]
        content = f"{code}: {proposed['explanation']}"
    return {"event": "tool", "tool": name, "operation_id": operation_id, "code": str(code),
            "content": content}


def run_turn(
    session: Session, profile: str, user_text: str, client: Client, config: LiveConfig,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Send one user message and act on the tool calls that come back, within a request budget."""
    if tools is None:
        latch = getattr(session, "latch", None)
        tools = tool_schemas(latch) if latch is not None else []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    events: list[dict[str, Any]] = []
    for _ in range(config.max_requests):
        response = client.complete(messages, tools)
        calls = parse_tool_calls(response)
        message = _message(response)
        text = message.get("content")
        if isinstance(text, str) and text:
            events.append({"event": "assistant", "text": text})
        if not calls:
            return events
        messages.append(
            {"role": "assistant", "content": text if isinstance(text, str) else None,
             "tool_calls": message["tool_calls"]}
        )
        for call_id, name, args in calls:
            event = _handle_call(session, profile, name, args)
            events.append(event)
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": event["content"]}
            )
    events.append({"event": "budget", "code": "RUN_LIMIT"})
    return events


def event_line(event: dict[str, Any]) -> str:
    """One plain line for one run_turn event."""
    if event["event"] == "assistant":
        return "assistant: " + " ".join(event["text"].split())
    if event["event"] == "tool":
        return f"tool {event['tool']} {event['code']}, operation {event['operation_id']}"
    return f"budget {event['code']}, the request budget is spent"


def run_live(args: Any) -> int:
    """`boundary-latch demo --live`: read requests from stdin and act as Sam until EOF."""
    from .demo.profiles import PROFILES
    from .demo.session import DemoSession

    try:
        config = LiveConfig.from_env()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    profile = getattr(args, "profile", "sam")
    session = DemoSession(args.db)
    client = ChatClient(config)
    print(f"live mode, model {config.model}, acting as {PROFILES[profile]['label']}. "
          "Type a request per line, end with Ctrl-D.", flush=True)
    try:
        while True:
            if sys.stdin.isatty():
                print("you: ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                return 0
            text = line.strip()
            if not text:
                continue
            try:
                events = run_turn(session, profile, text, client, config)
            except MalformedResponse as exc:
                print(f"model response refused: {exc}", file=sys.stderr, flush=True)
                continue
            except ModelRequestFailed as exc:
                print(exc, file=sys.stderr, flush=True)
                continue
            for event in events:
                print(event_line(event), flush=True)
    finally:
        session.close()
