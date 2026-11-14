"""The pure parts of the flow rule: when it applies and the sentence it gives."""

import pytest

from boundary_latch.flow import applies, reason
from boundary_latch.reasons import Code, explain

U, S, E = "untrusted", "sensitive", "external"


@pytest.mark.parametrize(
    ("held", "call", "gated"),
    [
        ((), (U, S, E), True),
        ((U, S), (E,), True),
        ((U,), (S, E), True),
        ((U, E), (S,), True),
        ((U,), (S,), False),
        ((U, S), (U,), False),
        ((U, S), (), False),
        ((U, S, E), (E,), True),
        ((U, S, E), (U, S), False),
        ((U, S, E), (), False),
    ],
)
def test_when_the_rule_applies(held, call, gated):
    assert applies("deny", held, call) is gated
    assert applies("approve", held, call) is gated
    assert applies("allow", held, call) is False


def held(**sources):
    return {label: {"tool": tool, "operation_id": "op", "at": 1.0}
            for label, tool in sources.items()}


def test_the_reason_names_each_source_and_what_the_call_adds():
    run = held(untrusted="get_issue", sensitive="get_file")
    text = reason(run, {E}, "create_pull_request")
    assert text == ("This run read untrusted input (get_issue) and private data (get_file). "
                    "This call acts outside (create_pull_request).")


def test_the_reason_when_one_call_carries_every_label():
    assert reason({}, {U, S, E}, "do_all") == (
        "This call reads untrusted input, reads private data, and acts outside (do_all)."
    )


def test_the_reason_names_an_app_mark_and_copied_evidence():
    run = {"untrusted": {"source": "upload of notes.txt", "operation_id": None, "at": 1.0},
           "sensitive": {"tool": "send", "copied": True, "operation_id": "op", "at": 2.0}}
    text = reason(run, {E}, "send")
    assert text == ("This run read untrusted input (marked by the app) and private data "
                    "(copied value in send). This call acts outside (send).")
    # The app's note is stored as the source, never put in the sentence.
    assert "notes.txt" not in text


def test_the_reason_once_the_run_holds_every_label():
    run = held(untrusted="a", sensitive="b", external="c")
    assert reason(run, {S, E}, "d") == (
        "This run read untrusted input (a) and private data (b) and acted outside (c). "
        "This call acts outside (d)."
    )


def test_the_same_facts_give_the_same_sentence_in_any_order():
    one = {"sensitive": {"tool": "b"}, "untrusted": {"tool": "a"}}
    two = {"untrusted": {"tool": "a"}, "sensitive": {"tool": "b"}}
    assert reason(one, [E], "c") == reason(two, (E,), "c")


def test_flow_blocked_has_an_explanation():
    assert Code.FLOW_BLOCKED == "FLOW_BLOCKED"
    assert explain(Code.FLOW_BLOCKED) == (
        "This run has read untrusted input and private data and can act outside. "
        "The policy blocks the call that would complete that chain."
    )
