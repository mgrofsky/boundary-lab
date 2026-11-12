from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ConfigError

# untrusted: the result can carry text an outsider wrote. sensitive: it reads private
# data. external: it changes state or sends data out.
Label = Literal["untrusted", "sensitive", "external"]
LABELS: tuple[str, ...] = ("untrusted", "sensitive", "external")


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
    copied_arguments: Literal["allow", "deny", "approve"] = "allow"
    # What the tool does, for the flow rule. None means the policy does not say.
    labels: list[Label] | None = None

    @model_validator(mode="after")
    def check_pairs(self):
        if (self.allowed_recipients is None) != (self.recipient_field is None):
            raise ValueError("allowed_recipients and recipient_field go together")
        if self.approval.mode != "never" and not self.approve_permission:
            raise ValueError("a tool that can need approval needs approve_permission")
        if self.copied_arguments == "approve" and not self.approve_permission:
            raise ValueError('copied_arguments = "approve" needs approve_permission')
        if self.labels is not None and len(set(self.labels)) != len(self.labels):
            raise ValueError("labels must be distinct")
        return self


class Flow(Strict):
    """The rule for a run that reads untrusted input, reads private data and acts outside."""

    all_three: Literal["allow", "approve", "deny"] = "allow"


class Limits(Strict):
    max_actions_per_run: int = Field(default=20, ge=1, le=10000)
    max_proposals_per_run: int | None = Field(default=None, ge=1)
    run_lifetime_seconds: int = Field(default=900, ge=0)
    run_idle_seconds: int = Field(default=0, ge=0)
    approval_lifetime_seconds: int = Field(default=600, ge=1)


class Policy(Strict):
    limits: Limits = Limits()
    flow: Flow = Flow()
    tools: dict[str, ToolPolicy]

    @model_validator(mode="after")
    def check_flow(self):
        mode = self.flow.all_three
        if mode == "allow":
            return self
        for name, tp in self.tools.items():
            if tp.labels is None:
                raise ValueError(
                    f'tool {name} needs labels when all_three is "{mode}"; '
                    "use labels = [] for a tool with none"
                )
            if mode == "approve" and tp.labels and not tp.approve_permission:
                raise ValueError(
                    f'tool {name} has labels, so all_three = "approve" needs its '
                    "approve_permission"
                )
        return self

    @property
    def digest(self) -> str:
        data = self.model_dump(mode="json")
        # Leave out the flow keys when they are unset, so a policy that does not use
        # the rule keeps the digest it had before, and its stored operations still match.
        if self.flow == Flow():
            del data["flow"]
        for tool in data["tools"].values():
            if tool["labels"] is None:
                del tool["labels"]
        body = json.dumps(data, sort_keys=True, separators=(",", ":"))
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
