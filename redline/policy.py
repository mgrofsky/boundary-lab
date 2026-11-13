"""Load the rules file and check that each rule is well formed."""

from dataclasses import dataclass
from pathlib import Path

import yaml

# The user types a rules file gets when it doesn't declare its own.
USER_TYPES = ("anonymous", "verified")
FIELDS = ("rule_id", "name", "rule", "plain_summary", "allowed_for", "reason_codes")


@dataclass
class Rule:
    """One rule: what it says, who may get answers about it, and its reason codes."""

    rule_id: str
    name: str
    rule: str
    plain_summary: str
    allowed_for: list[str]
    reason_codes: list[str]


@dataclass
class Policy:
    """A loaded rules file: the rules keyed by rule_id, and the user types it declares.

    assistant_role is what the judge prompt calls the assistant, or None when the file
    does not say.
    """

    rules: dict[str, Rule]
    user_types: list[str]
    assistant_role: str | None = None


def _user_types(path, data) -> list[str]:
    """Read the optional user_types list. Without one, use anonymous and verified."""
    if not (isinstance(data, dict) and "user_types" in data):
        return list(USER_TYPES)
    declared = data["user_types"]
    if not isinstance(declared, list) or not declared:
        raise ValueError(f"{path}: user_types must be a list with at least one name")
    if not all(isinstance(u, str) and u.strip() for u in declared):
        raise ValueError(f"{path}: every entry in user_types must be a name")
    if len(set(declared)) != len(declared):
        raise ValueError(f"{path}: user_types lists a name more than once")
    return list(declared)


def _assistant_role(path, data) -> str | None:
    """Read the optional assistant_role text, such as "a customer support assistant"."""
    if not (isinstance(data, dict) and "assistant_role" in data):
        return None
    role = data["assistant_role"]
    if not isinstance(role, str) or not role.strip() or "\n" in role.strip():
        raise ValueError(f"{path}: assistant_role must be one line of text")
    return role.strip()


def load_policy(path) -> Policy:
    """Read a rules file and return its rules and user types.

    The file holds a list of rules, either at the top level or under a "rules" key.
    With a "rules" key, an optional "user_types" list names the user types.
    Without it, the user types are anonymous and verified.
    An optional "assistant_role" text beside "rules" says what the judge prompt calls
    the assistant.
    A missing field, a repeated rule_id or an unknown user type raises ValueError.
    So does a file that is not valid YAML.
    A file that is not a list of mappings raises TypeError.
    """
    try:
        data = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:
        # The parser's message runs over several lines. Callers print one.
        raise ValueError(f"{path}: is not valid YAML: {' '.join(str(exc).split())}") from exc
    user_types = _user_types(path, data)
    assistant_role = _assistant_role(path, data)
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if not isinstance(data, list):
        raise TypeError(f"{path}: expected a list of rules")

    rules = {}
    for position, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise TypeError(f"{path}: rule {position} is not a mapping")
        rule_id = item.get("rule_id")
        if not rule_id:
            raise ValueError(f"{path}: rule {position} has no rule_id")
        missing = [f for f in FIELDS if f not in item]
        if missing:
            raise ValueError(f"{rule_id}: missing field {', '.join(missing)}")
        if rule_id in rules:
            raise ValueError(f"{rule_id}: duplicate rule_id")
        allowed_for = list(item["allowed_for"] or [])
        unknown = [u for u in allowed_for if u not in user_types]
        if unknown:
            raise ValueError(f"{rule_id}: unknown user type in allowed_for: {', '.join(unknown)}")
        rules[rule_id] = Rule(
            rule_id=rule_id,
            name=item["name"],
            rule=item["rule"],
            plain_summary=item["plain_summary"],
            allowed_for=allowed_for,
            reason_codes=list(item["reason_codes"] or []),
        )
    return Policy(rules, user_types, assistant_role)


def load(path) -> dict[str, Rule]:
    """Read a rules file and return the rules keyed by rule_id. See load_policy."""
    return load_policy(path).rules
