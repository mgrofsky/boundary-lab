"""JSON-RPC 2.0 messages, one JSON object per line, as MCP stdio uses them."""

from __future__ import annotations

import json
import math
from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class BadMessage(Exception):
    """A client line that the proxy answers with a JSON-RPC error and never forwards."""

    def __init__(self, code: int, message: str, msg_id: Any = None):
        super().__init__(message)
        self.code, self.message, self.msg_id = code, message, msg_id


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number is too large")
    return value


def parse(line: bytes) -> tuple[Any, bool]:
    """Parse one line. Returns the value and whether any object had a duplicate key.

    NaN, Infinity and numbers too large for a float are refused, since they are
    not JSON and would compare in surprising ways.
    """
    duplicates = False

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicates
        obj: dict[str, Any] = {}
        for key, value in items:
            if key in obj:
                duplicates = True
            obj[key] = value
        return obj

    try:
        text = line.decode("utf-8")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=_no_constant,
                           parse_float=_finite_float)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BadMessage(PARSE_ERROR, "Parse error") from exc
    return value, duplicates


def valid_id(value: Any) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def id_key(value: Any) -> tuple[str, Any]:
    """A dict key for a request id that keeps 1 and "1" apart."""
    return (type(value).__name__, value)


def kind(message: dict[str, Any]) -> str:
    """"request", "notification" or "response". Raises BadMessage for anything else."""
    msg_id = message.get("id")
    if "id" in message and not valid_id(msg_id):
        raise BadMessage(INVALID_REQUEST, "Invalid Request")
    if isinstance(message.get("method"), str):
        return "request" if "id" in message else "notification"
    if "id" in message and ("result" in message or "error" in message) and "method" not in message:
        return "response"
    raise BadMessage(INVALID_REQUEST, "Invalid Request", msg_id)


def check_client(line: bytes) -> dict[str, Any]:
    """Parse a client line. Refuse a batch, a non object, and a tools/call with duplicate keys."""
    value, duplicates = parse(line)
    if not isinstance(value, dict):
        raise BadMessage(INVALID_REQUEST, "Invalid Request: batches and non objects are refused")
    kind(value)
    if value.get("method") == "tools/call" and duplicates:
        raise BadMessage(INVALID_REQUEST, "Invalid Request: duplicate keys", value.get("id"))
    return value


def dump(message: dict[str, Any]) -> bytes:
    """One line of JSON, written again from the parsed value."""
    return json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode(
    ) + b"\n"


def canonical(value: Any) -> str:
    """Sorted keys, no spaces, so the same value always gives the same text."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def result(msg_id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": value}


def text_result(text: str) -> dict[str, Any]:
    """A tools/call result the model can read, marked as an error."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def blocked(code: str, explanation: str) -> dict[str, Any]:
    return text_result(f"Blocked by Latch: {code}. {explanation}")
