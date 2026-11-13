"""Load rules from a file or from data, and check that each rule is well formed."""

import re
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


# What a rule_id and a reason code may hold: letters, digits, underscore and hyphen.
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_TEXT_FIELDS = ("name", "rule", "plain_summary")
# The code of each problem validate_policy_data can report, and what it means.
PROBLEM_CODES = {
    "wrong_type": "the value is not the kind of value needed, such as text or a list",
    "missing": "a needed field is not there, or a rule has no rule_id",
    "empty": "text with no words, or a list that needs at least one entry and has none",
    "bad_characters": "a rule_id or reason code with a character that is not allowed",
    "duplicate": "a rule_id or a user type that is listed more than once",
    "unknown_user_type": "allowed_for names a user type that is not declared",
    "not_one_line": "assistant_role runs over more than one line",
}


class _Problems:
    """Collect problems in the order found. kind is what load_policy_data raises."""

    def __init__(self):
        self.found = []

    def add(self, where: str, code: str, message: str, kind=ValueError) -> None:
        self.found.append(({"where": where, "code": code, "message": message}, kind))


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_user_types(data, problems) -> list[str] | None:
    """Read the optional user_types list. None means it is there and cannot be used."""
    if not (isinstance(data, dict) and "user_types" in data):
        return list(USER_TYPES)
    declared = data["user_types"]
    if not isinstance(declared, list):
        problems.add(
            "user_types", "wrong_type", "user_types must be a list with at least one name"
        )
        return None
    if not declared:
        problems.add("user_types", "empty", "user_types must be a list with at least one name")
        return None
    good = True
    for index, name in enumerate(declared):
        where = f"user_types[{index}]"
        if not isinstance(name, str):
            problems.add(where, "wrong_type", "every entry in user_types must be a name")
        elif not name.strip():
            problems.add(where, "empty", "every entry in user_types must be a name")
        elif name in declared[:index]:
            problems.add(where, "duplicate", "user_types lists a name more than once")
        else:
            continue
        good = False
    return list(declared) if good else None


def _check_assistant_role(data, problems) -> str | None:
    """Read the optional assistant_role text, such as "a customer support assistant"."""
    if not (isinstance(data, dict) and "assistant_role" in data):
        return None
    role = data["assistant_role"]
    message = "assistant_role must be one line of text"
    if not isinstance(role, str):
        problems.add("assistant_role", "wrong_type", message)
    elif not role.strip():
        problems.add("assistant_role", "empty", message)
    elif "\n" in role.strip():
        problems.add("assistant_role", "not_one_line", message)
    else:
        return role.strip()
    return None


def _check_rule_id(item, where, label, seen, problems) -> None:
    rule_id = item.get("rule_id")
    if not isinstance(rule_id, str):
        problems.add(f"{where}.rule_id", "wrong_type", f"{label}: rule_id must be text")
    elif not _NAME.fullmatch(rule_id):
        problems.add(
            f"{where}.rule_id",
            "bad_characters",
            f"{label}: rule_id may hold only letters, digits, underscore and hyphen",
        )
    elif rule_id in seen:
        problems.add(f"{where}.rule_id", "duplicate", f"{label}: duplicate rule_id")
    else:
        seen.add(rule_id)


def _check_allowed_for(item, where, label, user_types, problems) -> None:
    allowed_for = item["allowed_for"]
    if allowed_for is None:
        return
    if not isinstance(allowed_for, list):
        problems.add(
            f"{where}.allowed_for", "wrong_type", f"{label}: allowed_for must be a list of names"
        )
        return
    bad = False
    for index, name in enumerate(allowed_for):
        if not isinstance(name, str):
            bad = True
            problems.add(
                f"{where}.allowed_for[{index}]",
                "wrong_type",
                f"{label}: every entry in allowed_for must be a name",
            )
    if bad or user_types is None:
        return
    unknown = [name for name in allowed_for if name not in user_types]
    if unknown:
        problems.add(
            f"{where}.allowed_for",
            "unknown_user_type",
            f"{label}: unknown user type in allowed_for: {', '.join(unknown)}",
        )


def _check_reason_codes(item, where, label, problems) -> None:
    codes = item["reason_codes"]
    if codes is not None and not isinstance(codes, list):
        problems.add(
            f"{where}.reason_codes", "wrong_type", f"{label}: reason_codes must be a list of codes"
        )
        return
    if not codes:
        problems.add(
            f"{where}.reason_codes", "empty", f"{label}: reason_codes needs at least one code"
        )
        return
    for index, code in enumerate(codes):
        spot = f"{where}.reason_codes[{index}]"
        if not isinstance(code, str):
            problems.add(spot, "wrong_type", f"{label}: every reason code must be text")
        elif not code.strip():
            problems.add(spot, "empty", f"{label}: a reason code has no text")
        elif not _NAME.fullmatch(code):
            problems.add(
                spot,
                "bad_characters",
                f"{label}: reason code {code!r} may hold only letters, digits, underscore "
                "and hyphen",
            )


def _check_rule(item, index, user_types, seen, problems) -> None:
    where, position = f"rules[{index}]", index + 1
    if not isinstance(item, dict):
        problems.add(where, "wrong_type", f"rule {position} is not a mapping", TypeError)
        return
    rule_id = item.get("rule_id")
    if rule_id is None or rule_id == "":
        problems.add(f"{where}.rule_id", "missing", f"rule {position} has no rule_id")
        label = f"rule {position}"
    else:
        # A rule is named by its rule_id, as before, or by its place when that is not text.
        label = rule_id if isinstance(rule_id, str) else f"rule {position}"
        _check_rule_id(item, where, label, seen, problems)
    for field in FIELDS[1:]:
        if field not in item:
            problems.add(f"{where}.{field}", "missing", f"{label}: missing field {field}")
    for field in _TEXT_FIELDS:
        if field not in item:
            continue
        if not isinstance(item[field], str):
            problems.add(f"{where}.{field}", "wrong_type", f"{label}: {field} must be text")
        elif not item[field].strip():
            problems.add(f"{where}.{field}", "empty", f"{label}: {field} has no text")
    if "allowed_for" in item:
        _check_allowed_for(item, where, label, user_types, problems)
    if "reason_codes" in item:
        _check_reason_codes(item, where, label, problems)


def _check(data) -> tuple[_Problems, list[str] | None, str | None, list]:
    """Find every problem in rules data. Returns them with the parts that were read."""
    problems = _Problems()
    user_types = _check_user_types(data, problems)
    assistant_role = _check_assistant_role(data, problems)
    items = data["rules"] if isinstance(data, dict) and "rules" in data else data
    if not isinstance(items, list):
        problems.add("rules", "wrong_type", "expected a list of rules", TypeError)
        return problems, user_types, assistant_role, []
    seen = set()
    for index, item in enumerate(items):
        _check_rule(item, index, user_types, seen, problems)
    return problems, user_types, assistant_role, items


def validate_policy_data(data) -> list[dict]:
    """List every problem in rules data. An empty list means load_policy_data takes it.

    data is what a rules file holds once parsed. See load_policy_data. Each problem is a
    mapping with where, such as "rules[2].allowed_for", a code from PROBLEM_CODES and a
    plain message. Bad content never raises. No file is read and no judge is called.
    """
    return [problem for problem, _ in _check(data)[0].found]


def load_policy_data(data, source: str = "<data>") -> Policy:
    """Turn rules data into a Policy. The data is what a rules file holds once parsed.

    data is a list of rule mappings, or a mapping with "rules" and the optional
    "user_types" and "assistant_role". Without "user_types", the user types are
    anonymous and verified. The first problem found raises ValueError, or TypeError
    when the data is not a list of mappings. The message starts with source and names
    the rule. validate_policy_data lists every problem and does not raise.
    """
    problems, user_types, assistant_role, items = _check(data)
    if problems.found:
        problem, kind = problems.found[0]
        raise kind(f"{source}: {problem['message']}")
    rules = {}
    for item in items:
        rules[item["rule_id"]] = Rule(
            rule_id=item["rule_id"],
            name=item["name"],
            rule=item["rule"],
            plain_summary=item["plain_summary"],
            allowed_for=list(item["allowed_for"] or []),
            reason_codes=list(item["reason_codes"]),
        )
    return Policy(rules, user_types, assistant_role)


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
    The checks are those of load_policy_data, which takes the parsed content.
    """
    try:
        data = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:
        # The parser's message runs over several lines. Callers print one.
        raise ValueError(f"{path}: is not valid YAML: {' '.join(str(exc).split())}") from exc
    return load_policy_data(data, source=str(path))


def load(path) -> dict[str, Rule]:
    """Read a rules file and return the rules keyed by rule_id. See load_policy."""
    return load_policy(path).rules
