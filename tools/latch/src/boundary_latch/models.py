from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .reasons import Code


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UserContext(Strict):
    user_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=128)
    permissions: frozenset[str]
    run_id: str = Field(min_length=1, max_length=128)


class CallInfo(Strict):
    operation_id: str
    run_id: str
    user_id: str
    org_id: str
    tool: str
    tool_version: str


AccessHook = Callable[[UserContext, dict[str, Any]], bool]
Handler = (
    Callable[[dict[str, Any], CallInfo], Any]
    | Callable[[dict[str, Any], CallInfo], Awaitable[Any]]
)


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    args_model: type[BaseModel]
    handler: Handler
    access_hook: AccessHook | None = None
    description: str = ""
    timeout_seconds: float = 30.0


class Decision(Strict):
    allowed: bool
    code: Code
    explanation: str
    operation_id: str | None = None


class PreparedAction(BaseModel):
    """A checked request waiting to run.

    The engine builds one with PreparedAction.model_construct(...) and saves the
    arguments as JSON in obj._args_json. The arguments property returns a new dict
    each time, so editing that dict does not change the saved request.
    """

    model_config = ConfigDict(frozen=True)
    operation_id: str
    run_id: str
    user_id: str
    org_id: str
    tool: str
    tool_version: str
    needs_approval: bool
    request_hash: str
    code: Code
    explanation: str
    _args_json: str = PrivateAttr()

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self._args_json)


class ExecutionResult(Strict):
    operation_id: str
    outcome: str  # "succeeded", "failed", "uncertain", "denied"
    code: Code
    explanation: str
    value: Any = None
    replayed: bool = False
    duration_ms: int = 0


class OperationStatus(Strict):
    operation_id: str
    state: str
    code: Code
    tool: str
    created_at: float
    updated_at: float
