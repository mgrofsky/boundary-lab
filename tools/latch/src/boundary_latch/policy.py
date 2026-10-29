from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ConfigError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Approval(Strict):
    mode: Literal["never", "always", "over_amount"] = "never"
    amount_field: str | None = None
    threshold_minor: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_amount_fields(self):
        missing = self.amount_field is None or self.threshold_minor is None
        if self.mode == "over_amount" and missing:
            raise ValueError("over_amount needs amount_field and threshold_minor")
        if self.mode != "over_amount" and (self.amount_field or self.threshold_minor is not None):
            raise ValueError("amount_field and threshold_minor only apply to over_amount")
        return self


class ToolPolicy(Strict):
    require_permission: str = Field(min_length=1)
    approve_permission: str | None = None
    allowed_recipients: list[str] | None = None
    recipient_field: str | None = None
    approval: Approval = Approval()

    @model_validator(mode="after")
    def check_pairs(self):
        if (self.allowed_recipients is None) != (self.recipient_field is None):
            raise ValueError("allowed_recipients and recipient_field go together")
        if self.approval.mode != "never" and not self.approve_permission:
            raise ValueError("a tool that can need approval needs approve_permission")
        return self


class Limits(Strict):
    max_actions_per_run: int = Field(default=20, ge=1, le=10000)
    run_lifetime_seconds: int = Field(default=900, ge=1)
    approval_lifetime_seconds: int = Field(default=600, ge=1)


class Policy(Strict):
    limits: Limits = Limits()
    tools: dict[str, ToolPolicy]

    @property
    def digest(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


def parse_policy(text: str) -> Policy:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"policy is not valid TOML: {exc}") from exc
    try:
        return Policy.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"policy rejected: {exc}") from exc


def load_policy(path: str | Path) -> Policy:
    return parse_policy(Path(path).read_text())
