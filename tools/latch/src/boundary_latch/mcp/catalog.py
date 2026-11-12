"""The tools the proxy learns from the server, pinned by their first definition."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..reasons import Code
from . import wire
from .tools import build_spec, fits, uses_reserved, version

logger = logging.getLogger("boundary_latch.mcp")

MAX_PAGES = 1000
# How much of a server's error text goes into the log.
ERROR_TEXT = 200


@dataclass
class Changed:
    """A tool whose definition differs from its pin, waiting for a person."""

    new: dict[str, Any]
    hash: str
    old: dict[str, Any] | None = field(default=None)


SERVER_INFO = "io.modelcontextprotocol/serverInfo"


def list_fields(page: dict[str, Any]) -> dict[str, Any]:
    """The fields of the server's last tools/list page that the client gets, beside tools.

    Only these pass: ttlMs as an int of 0 or more, cacheScope as "public" or
    "private", and _meta holding only the server's info object. A server cannot put
    other text beside the tools, where a client might trust it.
    """
    fields: dict[str, Any] = {}
    ttl = page.get("ttlMs")
    if isinstance(ttl, int) and not isinstance(ttl, bool) and ttl >= 0:
        fields["ttlMs"] = ttl
    if page.get("cacheScope") in ("public", "private"):
        fields["cacheScope"] = page["cacheScope"]
    meta = page.get("_meta")
    if isinstance(meta, dict) and isinstance(meta.get(SERVER_INFO), dict):
        fields["_meta"] = {SERVER_INFO: meta[SERVER_INFO]}
    return fields


class ToolCatalog:
    """The Proxy's tool list. It expects the attributes Proxy sets in __init__."""

    async def ensure_tools(self, fresh: bool = False) -> None:
        """Fetch the server's list when there is none, or always when fresh is set."""
        async with self._tools_lock:
            if self._tools_known and not fresh:
                return
            changes = self._list_changes
            tools: list[dict[str, Any]] = []
            cursor = None
            for _ in range(MAX_PAGES):
                params = {} if cursor is None else {"cursor": cursor}
                page = self._page(await self.request("tools/list", params))
                tools += [tool for tool in page["tools"] if isinstance(tool, dict)]
                cursor = page.get("nextCursor")
                if not isinstance(cursor, str):
                    break
            else:
                raise ValueError("the server's tools/list did not end")
            self.learn(tools)
            self._list_fields = list_fields(page)
            # A list_changed that came in during the fetch may name tools this list
            # does not have yet, so the next use fetches again.
            self._tools_known = self._list_changes == changes

    @staticmethod
    def _page(reply: dict[str, Any]) -> dict[str, Any]:
        """One tools/list page, or an error that says what the server answered."""
        error = reply.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            text = error.get("message") if isinstance(error, dict) else None
            text = " ".join(str(text).split())[:ERROR_TEXT]
            raise ValueError(f"the server answered tools/list with error {code!r}: {text}")
        page = reply.get("result")
        if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
            raise TypeError("the server's tools/list answer has no tools list")
        if page.get("resultType", wire.COMPLETE) != wire.COMPLETE:
            raise ValueError("the server's tools/list answer is not complete")
        return page

    def learn(self, tools: list[dict[str, Any]]) -> None:
        """Pin new tools, find changed ones, and register what the client may call."""
        order: list[str] = []
        listed: set[str] = set()
        for tool in tools:
            name = tool.get("name")
            if not fits(name) or name not in self.latch.policy.tools or uses_reserved(tool):
                self._hide(name)
                continue
            if name in listed:
                continue
            listed.add(name)
            order.append(name)
            self._learn_one(name, tool)
        for name in list(self.served):
            if name not in listed:
                self.served.pop(name)
                self.changed.pop(name, None)
                self.latch._drop_tool(name)
        self._order = order

    def _learn_one(self, name: str, tool: dict[str, Any]) -> None:
        new_hash = version(tool)
        pin = self.pins.get(name)
        if pin is None:
            self.pins.pin(name, new_hash, tool)
            pin = self.pins.get(name)
        if pin["hash"] == new_hash:
            self.changed.pop(name, None)
            self._serve(name, tool)
            return
        old = pin.get("definition")
        old = old if isinstance(old, dict) and version(old) == pin["hash"] else None
        known = self.changed.get(name)
        if known is None or known.hash != new_hash:
            self.changed[name] = Changed(new=tool, hash=new_hash, old=old)
            self.latch._write_audit(
                "tool_changed", str(Code.TOOL_CHANGED), self.settings.org, run_id=self.run_id,
                user_id=self.settings.user, tool=name, now=self.latch.clock(),
                summary=f"pinned {pin['hash']}, server now has {new_hash}",
            )
            logger.warning("tool %s changed after it was first seen; calls are refused until "
                           "a person accepts it on the approval page", name)
        if old is None:
            # No pinned definition to show, so the client does not see the tool.
            self.served.pop(name, None)
            self.latch._drop_tool(name)
        else:
            self._serve(name, old)

    def _serve(self, name: str, tool: dict[str, Any]) -> None:
        current = self.served.get(name)
        self.served[name] = tool
        if current is None or version(current) != version(tool) or not any(
            spec.name == name for spec in self.latch.tool_specs()
        ):
            self.latch._set_tool(build_spec(tool, self.handler, self.settings.tool_timeout))

    def _hide(self, name: Any) -> None:
        label = repr(name)[:80]
        if label not in self._hidden_logged:
            self._hidden_logged.add(label)
            logger.warning("hid tool %s: the policy does not name it, the name does not fit, "
                           "or its inputSchema uses a reserved name", label)

    def accept(self, name: str, shown_hash: str) -> bool:
        """A person accepts the changed definition they saw. False when it is gone or newer."""
        changed = self.changed.get(name)
        if changed is None or changed.hash != shown_hash:
            return False
        self.pins.pin(name, changed.hash, changed.new)
        del self.changed[name]
        self.served.pop(name, None)
        self._serve(name, changed.new)
        self.latch._write_audit(
            "tool_accepted", str(Code.TOOL_CHANGED), self.settings.org, run_id=self.run_id,
            user_id=self.settings.approver, tool=name, now=self.latch.clock(),
            reference="approval-page", summary=f"accepted {changed.hash}",
        )
        return True

    async def answer_list(self, msg_id: Any) -> None:
        """Fetch the server's list again, so the pins check what the server has now.

        The answer is the served tools, resultType "complete", and the few fields of
        the server's last page that list_fields lets through.
        """
        try:
            await self.ensure_tools(fresh=True)
        except Exception as exc:  # noqa: BLE001 the client gets an error, never a partial list
            logger.warning("could not list the server's tools: %s", exc)
            self.to_client(wire.error(msg_id, wire.INTERNAL_ERROR, "could not list tools"))
            return
        tools = [self.served[name] for name in self._order if name in self.served]
        self.to_client(wire.result(msg_id, {**self._list_fields, "tools": tools,
                                            "resultType": wire.COMPLETE}))
