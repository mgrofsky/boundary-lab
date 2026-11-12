"""Turn MCP tool definitions into Latch tools, and write a starter policy."""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, model_serializer

from ..models import Handler, ToolSpec
from .wire import canonical

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
COMMENT_LIMIT = 100


class _Missing:
    """The value of an optional argument the client left out. It is never forwarded."""

    def __repr__(self) -> str:
        return "MISSING"

    def __copy__(self) -> _Missing:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> _Missing:
        return self


MISSING = _Missing()


class McpArgs(BaseModel):
    """Base for the arguments model of a proxied tool.

    Each field is read by its alias, the property name in the tool's inputSchema.
    A dump uses those names and leaves out arguments the client did not send.
    """

    model_config = ConfigDict(extra="forbid", validate_by_alias=True, validate_by_name=False)

    @model_serializer(mode="plain")
    def _dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, field in type(self).model_fields.items():
            value = getattr(self, key)
            if value is not MISSING:
                out[key if field.alias is None else field.alias] = value
        return out


def fits(name: Any) -> bool:
    return isinstance(name, str) and NAME.fullmatch(name) is not None


def version(tool: dict[str, Any]) -> str:
    """The first 12 hex characters of the sha256 of the whole tool object.

    The proxy serves the whole object, so title, annotations, outputSchema and any
    other field a client sees are covered.
    """
    return hashlib.sha256(canonical(tool).encode()).hexdigest()[:12]


def old_version(tool: dict[str, Any]) -> str:
    """The hash before 0.4.0 pins covered every field: name, description and inputSchema.

    Only used to read a pin file written that way.
    """
    body = {"name": tool.get("name"), "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema")}
    return hashlib.sha256(canonical(body).encode()).hexdigest()[:12]


def _safe_field(name: str) -> bool:
    return (name.isidentifier() and not keyword.iskeyword(name) and not name.startswith("_")
            and not name.startswith("model_") and not hasattr(BaseModel, name))


def args_model(input_schema: Any) -> type[BaseModel]:
    """A model with one field of any type per top level property.

    Names that cannot be Python fields get a made up field name and keep the
    property name as the alias. The child server does its own deep validation.
    """
    schema = input_schema if isinstance(input_schema, dict) else {}
    properties = schema.get("properties")
    names = [str(n) for n in properties] if isinstance(properties, dict) else []
    required = schema.get("required")
    required = {r for r in required if isinstance(r, str)} if isinstance(required, list) else set()
    names += sorted(required - set(names))
    taken = set(names)
    fields: dict[str, Any] = {}
    for index, name in enumerate(names):
        field_name = name
        if not _safe_field(name):
            field_name = f"arg_{index}"
            while field_name in taken or field_name in fields:
                field_name = "x" + field_name
        if name in required:
            fields[field_name] = (Any, Field(alias=name))
        else:
            fields[field_name] = (Any, Field(default=MISSING, alias=name))
    return create_model("ToolArgs", __base__=McpArgs, **fields)


def describer(name: str) -> Callable[[BaseModel], str]:
    def describe(args: BaseModel) -> str:
        parts = [f"{key}={json.dumps(value, separators=(',', ':'), ensure_ascii=False)}"
                 for key, value in args.model_dump(mode="json").items()]
        return f"{name}({', '.join(parts)})"

    return describe


def build_spec(tool: dict[str, Any], handler: Handler, timeout: float) -> ToolSpec:
    name = tool["name"]
    description = tool.get("description")
    return ToolSpec(
        name=name,
        version=version(tool),
        args_model=args_model(tool.get("inputSchema")),
        handler=handler,
        description=description if isinstance(description, str) else "",
        timeout_seconds=timeout,
        describe=describer(name),
    )


def comment(text: Any) -> str:
    """One line of server text, safe to put after a # in TOML."""
    if not isinstance(text, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(text.split())[:COMMENT_LIMIT]


def toml_key(name: str) -> str:
    return name if BARE_KEY.fullmatch(name) else json.dumps(name)


STARTER_HEAD = """\
# Starter policy from boundary-latch mcp --print-policy.
# Every tool needs a person's approval. Loosen it from here. See docs/mcp.md.

[limits]
# One proxy process is one run. Long sessions need room, so the run has no
# fixed lifetime and ends after an hour with no tool calls.
max_actions_per_run = 1000
run_lifetime_seconds = 0
run_idle_seconds = 3600
"""


FLOW_HEAD = """\
# The flow rule is on. Every tool starts with all three labels, the safe starting
# point. Remove the labels that do not apply to each tool. See docs/flow.md.
[flow]
all_three = "approve"
"""

ALL_LABELS = 'labels = ["untrusted", "sensitive", "external"]'
HINTS = ("readOnlyHint", "destructiveHint", "openWorldHint")


def hints(tool: dict[str, Any]) -> str:
    """The server's own annotations as one comment, or "" when it sent none."""
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        return ""
    parts = [f"{name}={json.dumps(annotations[name])}" for name in HINTS if name in annotations]
    if not parts:
        return ""
    return comment(f"The server says: {', '.join(parts)}.")


def starter_policy(tools: list[dict[str, Any]], flow: bool = False) -> tuple[str, list[str]]:
    """The policy text, and the names left out because Latch cannot use them.

    With flow, the [flow] rule is on and every tool gets all three labels. The
    server's annotations only appear in a comment; they never choose a label.
    """
    lines = [STARTER_HEAD.rstrip("\n")]
    if flow:
        lines += ["", FLOW_HEAD.rstrip("\n")]
    skipped = []
    for tool in tools:
        name = tool.get("name")
        if not fits(name):
            skipped.append(str(name))
            continue
        lines.append("")
        note = comment(tool.get("description"))
        if note:
            lines.append(f"# {note}")
        lines.append(f"[tools.{toml_key(name)}]")
        lines.append('require_permission = "mcp:use"')
        lines.append('approve_permission = "mcp:approve"')
        lines.append('approval.mode = "always"')
        if flow:
            claim = hints(tool)
            if claim:
                lines.append(f"# {claim} That is its own claim; choose labels yourself.")
            lines.append(ALL_LABELS)
    return "\n".join(lines) + "\n", skipped
