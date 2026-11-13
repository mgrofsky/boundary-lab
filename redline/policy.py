"""Load rules from a file or from data, and check that each rule is well formed."""

import os
import re
import reprlib
import unicodedata
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
    "bad_characters": "a character that is not allowed there, or spaces around a name",
    "duplicate": "a rule_id, reason code or user type listed twice, letter case aside",
    "unknown_user_type": "allowed_for names a user type that is not declared",
    "not_one_line": "text that must be one line has a line break in it",
    "reserved_id": "the rule_id is none, the word Redline uses for no rule",
}
# The router's own word for "no rule applies". A rule may not take it, in any letter case.
RESERVED_ID = "none"
# Every character Python treats as the end of a line.
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
# How much of a caller's value a message shows.
SHOWN = 80


def show(value: object) -> str:
    """Return a caller's value the way a message shows it.

    Text is quoted with line breaks and control characters escaped, so it is one
    line. Any other value is written as Python writes it, with a long list or mapping
    shortened. The result is cut at 80 characters, with "... (cut)" after it when cut.
    """
    if isinstance(value, str):
        text = repr(value[: SHOWN + 1])
    else:
        # A long list or mapping is cut before it is written out in full.
        text = reprlib.Repr(maxlist=3, maxdict=3, maxstring=SHOWN, maxother=SHOWN).repr(value)
    return text if len(text) <= SHOWN else f"{text[:SHOWN]}... (cut)"


def _named(name: str) -> str:
    """A short plain name as it is, and any other text as show gives it."""
    return name if _NAME.fullmatch(name) and len(name) <= SHOWN else show(name)


def reserved(rule_id: object) -> bool:
    """True for a rule_id that is the word none, in any letter case, spaces aside.

    Redline uses that word for an answer no rule applies to, so no rule may take it.
    A value that is not text gives False.
    """
    return isinstance(rule_id, str) and _same(rule_id) == RESERVED_ID


def _same(name: str) -> str:
    """The form two names are compared in: no spaces around, no letter case."""
    return name.strip().casefold()


def _one_line(text: str) -> str | None:
    """The code of what stops text sitting on one line of the judge prompt, or None."""
    if any(char in _LINE_BREAKS for char in text):
        return "not_one_line"
    if any(unicodedata.category(char) == "Cc" for char in text):
        return "bad_characters"
    return None


_ONE_LINE_WORDS = {
    "not_one_line": "must be one line of text, with no line break",
    "bad_characters": "must not hold a control character",
}


# Said by the checks here and by a Grader given a Policy that holds no rule.
NO_RULES = "the rules list has no rules, and at least one is needed"


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
    good, seen = True, set()
    for index, name in enumerate(declared):
        where = f"user_types[{index}]"
        if not isinstance(name, str):
            problems.add(where, "wrong_type", "every entry in user_types must be a name")
        elif not name.strip():
            problems.add(where, "empty", "every entry in user_types must be a name")
        elif _one_line(name):
            code = _one_line(name)
            problems.add(where, code, f"user type {show(name)} {_ONE_LINE_WORDS[code]}")
        elif name != name.strip():
            problems.add(
                where, "bad_characters", f"user type {show(name)} must not have spaces around it"
            )
        elif _same(name) in seen:
            problems.add(
                where,
                "duplicate",
                f"user_types lists {show(name)} more than once. Letter case does not "
                "make two names different",
            )
        else:
            seen.add(_same(name))
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
    elif _one_line(role.strip()):
        # Space around the text is dropped first, so a last line break in a file is fine.
        code = _one_line(role.strip())
        problems.add("assistant_role", code, f"assistant_role {_ONE_LINE_WORDS[code]}")
    else:
        return role.strip()
    return None


def _check_rule_id(item, where, label, seen, problems) -> None:
    rule_id = item.get("rule_id")
    if not isinstance(rule_id, str):
        problems.add(f"{where}.rule_id", "wrong_type", f"{label}: rule_id must be text")
    elif reserved(rule_id):
        problems.add(
            f"{where}.rule_id",
            "reserved_id",
            f"{label}: rule_id {show(rule_id)} is reserved. Redline uses the word none, in "
            "any letter case, to mean that no rule applies",
        )
    elif not _NAME.fullmatch(rule_id):
        problems.add(
            f"{where}.rule_id",
            "bad_characters",
            f"{label}: rule_id {show(rule_id)} may hold only letters, digits, underscore "
            "and hyphen",
        )
    elif _same(rule_id) in seen:
        problems.add(
            f"{where}.rule_id",
            "duplicate",
            f"{label}: duplicate rule_id. Letter case does not make two ids different",
        )
    else:
        seen.add(_same(rule_id))


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
        more = f" and {len(unknown) - 3} more" if len(unknown) > 3 else ""
        problems.add(
            f"{where}.allowed_for",
            "unknown_user_type",
            f"{label}: unknown user type in allowed_for: "
            f"{', '.join(_named(name) for name in unknown[:3])}{more}",
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
    seen = set()
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
                f"{label}: reason code {show(code)} may hold only letters, digits, underscore "
                "and hyphen",
            )
        elif _same(code) in seen:
            problems.add(
                spot,
                "duplicate",
                f"{label}: reason code {show(code)} is listed more than once. Letter case "
                "does not make two codes different",
            )
        else:
            seen.add(_same(code))


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
        # A rule is named by its rule_id, as before, when that is a short plain name.
        # Any other value is named by its place, so no message carries odd text.
        plain = isinstance(rule_id, str) and _NAME.fullmatch(rule_id) and len(rule_id) <= SHOWN
        label = rule_id if plain else f"rule {position}"
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
        elif field == "name" and _one_line(item[field]):
            # The name sits on one line of the judge prompt. The other two may have more.
            code = _one_line(item[field])
            problems.add(f"{where}.name", code, f"{label}: name {_ONE_LINE_WORDS[code]}")
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
    if not items:
        problems.add("rules", "empty", NO_RULES)
    seen = set()
    for index, item in enumerate(items):
        _check_rule(item, index, user_types, seen, problems)
    return problems, user_types, assistant_role, items


def validate_policy_data(data: list | dict) -> list[dict]:
    """List every problem in rules data.

    data is what a rules file holds once parsed: a list of rule mappings, or a mapping
    with "rules" and the optional "user_types" and "assistant_role". Any other value
    is reported as a problem too.

    Returns a list with one mapping per problem, in the order found. Each has where,
    such as "rules[2].allowed_for", a code from PROBLEM_CODES and a plain message. A
    message shows a value from the data through show, so it is one line of bounded
    length. An empty list means load_policy_data takes the data and a Grader can be
    built from it. Any problem means load_policy_data refuses it.

    Bad content never raises. No file is read and no judge is called.
    """
    return [problem for problem, _ in _check(data)[0].found]


def load_policy_data(data: list | dict, source: str = "<data>") -> Policy:
    """Turn rules data into a Policy. The data is what a rules file holds once parsed.

    data is a list of rule mappings, or a mapping with "rules" and the optional
    "user_types" and "assistant_role". Without "user_types", the user types are
    anonymous and verified. source names the data in an error, such as a file path or
    a tenant. The data is not changed.

    Returns the Policy: the rules keyed by rule_id, the user types and the role.

    The first problem found raises ValueError, or TypeError when the data is not a
    list of mappings. The message starts with source and names the rule. A list with
    no rules is a problem, since nothing can be graded against it.
    validate_policy_data lists every problem and does not raise.
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


def load_policy(path: str | os.PathLike) -> Policy:
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
