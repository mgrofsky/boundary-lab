"""Turn a rule's allowed user types and the user's type into one sentence the judge must apply."""

from dataclasses import dataclass


@dataclass
class Stance:
    """What the rule says about this user: allowed or not, a reason code, and a sentence."""

    allowed: bool
    reason_code: str
    text: str


def _article(user_type: str) -> str:
    return "an" if user_type[:1] in "aeiou" else "a"


def check(rule, user_type: str) -> Stance:
    """Return the stance for this user type under this rule. The gate never decides alone."""
    if not rule.allowed_for:
        return Stance(
            False,
            "NOT_ALLOWED_FOR_USER",
            "This rule allows this information for nobody, whatever the user type.",
        )
    who = f"{_article(user_type)} {user_type} user"
    if user_type in rule.allowed_for:
        return Stance(
            True,
            "OK",
            f"The user is {user_type}. Under this rule, {who} may be given this kind of "
            "information about their own account.",
        )
    return Stance(
        False,
        "NOT_ALLOWED_FOR_USER",
        f"The user is {user_type}. Under this rule, nothing in this category may be given to "
        f"{who}, so any such detail in the answer breaks the rule.",
    )
