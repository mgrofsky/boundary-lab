"""Load the rules file and check that each rule is well formed."""

from dataclasses import dataclass
from pathlib import Path

import yaml

USER_TYPES = {"anonymous", "verified"}
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


def load(path) -> dict[str, Rule]:
    """Read a rules file and return the rules keyed by rule_id.

    The file holds a list of rules, either at the top level or under a "rules" key.
    A missing field, a repeated rule_id or an unknown user type raises ValueError.
    A file that is not a list of mappings raises TypeError.
    """
    data = yaml.safe_load(Path(path).read_text())
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
        unknown = [u for u in allowed_for if u not in USER_TYPES]
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
    return rules
