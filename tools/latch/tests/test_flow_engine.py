"""The flow rule in prepare, approve and execute."""

import json
import threading
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict

from boundary_latch.engine import Latch
from boundary_latch.errors import ToolFailed
from boundary_latch.models import Decision, PreparedAction, ToolSpec, UserContext
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code
from boundary_latch.store import SQLiteStore

POLICY = """
[flow]
all_three = "{mode}"

[tools.get_issue]
require_permission = "use"
approve_permission = "approve"
labels = ["untrusted"]

[tools.get_file]
require_permission = "use"
approve_permission = "approve"
labels = ["sensitive"]

[tools.create_pull_request]
require_permission = "use"
approve_permission = "approve"
labels = ["external"]

[tools.read_and_send]
require_permission = "use"
approve_permission = "approve"
labels = ["sensitive", "external"]

[tools.send_reviewed]
require_permission = "use"
approve_permission = "approve"
labels = ["external"]
approval.mode = "always"

[tools.plain]
require_permission = "use"
labels = []
"""

TOOLS = ("get_issue", "get_file", "create_pull_request", "read_and_send", "send_reviewed",
         "plain")
REASON = ("This run read untrusted input (get_issue) and private data (get_file). "
          "This call acts outside (create_pull_request).")


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = "x"


def ctx(run_id: str = "run1", user_id: str = "sam") -> UserContext:
    return UserContext(user_id=user_id, org_id="acme", permissions=frozenset({"use"}),
                       run_id=run_id)


APPROVER = UserContext(user_id="jordan", org_id="acme", permissions=frozenset({"approve"}),
                       run_id="approver")


def build(clock, mode: str, store: SQLiteStore | None = None, events=None):
    handlers = {name: MagicMock(return_value=name) for name in TOOLS}
    engine = Latch(parse_policy(POLICY.format(mode=mode)), store or SQLiteStore(), clock=clock,
                   on_event=events.append if events is not None else None)
    for name in TOOLS:
        engine.register(ToolSpec(name=name, version="1", args_model=Args,
                                 handler=handlers[name]))
    return engine, handlers


def prepare(engine, tool, op, run="run1", **kwargs):
    result = engine.prepare(ctx(run), tool, {}, op, **kwargs)
    assert isinstance(result, PreparedAction), result
    return result


def run(engine, tool, op, run_id="run1", **kwargs):
    prepared = prepare(engine, tool, op, run_id, **kwargs)
    return engine.execute(prepared, ctx(run_id))


def read_issue_and_file(engine, run_id="run1"):
    assert run(engine, "get_issue", "i1", run_id).code == Code.EXECUTED
    assert run(engine, "get_file", "f1", run_id).code == Code.EXECUTED


# Deny


def test_deny_blocks_the_call_that_completes_the_chain_at_prepare(clock):
    events = []
    engine, handlers = build(clock, "deny", events=events)
    read_issue_and_file(engine)
    decision = engine.prepare(ctx(), "create_pull_request", {}, "pr1")
    assert isinstance(decision, Decision)
    assert decision.code == Code.FLOW_BLOCKED
    assert decision.explanation.endswith(REASON)
    assert handlers["create_pull_request"].call_count == 0
    last = events[-1]
    assert (last["event"], last["code"]) == ("prepare", "FLOW_BLOCKED")
    assert last["labels"] == ["external"]
    assert last["flow_reason"] == REASON


def test_deny_closes_an_operation_at_execute_when_the_chain_completed_after_prepare(clock):
    engine, handlers = build(clock, "deny")
    pr = prepare(engine, "create_pull_request", "pr1")
    assert pr.flow_reason == "" and not pr.needs_approval
    read_issue_and_file(engine)
    result = engine.execute(pr, ctx())
    assert (result.outcome, result.code) == ("denied", Code.FLOW_BLOCKED)
    assert result.explanation.endswith(REASON)
    assert handlers["create_pull_request"].call_count == 0
    status = engine.operation_status("acme", "pr1")
    assert (status.state, status.code, status.flow_reason) == ("denied", Code.FLOW_BLOCKED,
                                                               REASON)
    again = engine.prepare(ctx(), "create_pull_request", {}, "pr1")
    assert again.code == Code.OPERATION_CLOSED


def test_the_order_of_the_three_does_not_matter(clock):
    engine, _handlers = build(clock, "deny")
    assert run(engine, "create_pull_request", "pr1").code == Code.EXECUTED
    assert run(engine, "get_issue", "i1").code == Code.EXECUTED
    blocked = engine.prepare(ctx(), "get_file", {}, "f1")
    assert blocked.code == Code.FLOW_BLOCKED
    assert blocked.explanation.endswith(
        "This run read untrusted input (get_issue) and acted outside (create_pull_request). "
        "This call reads private data (get_file)."
    )


def test_prepare_alone_adds_no_label(clock):
    engine, _ = build(clock, "deny")
    prepare(engine, "get_issue", "i1")
    prepare(engine, "get_file", "f1")
    assert engine.store.run_labels("acme", "run1") == {}
    assert run(engine, "create_pull_request", "pr1").code == Code.EXECUTED


def test_a_tool_with_no_labels_is_never_gated(clock):
    engine, _ = build(clock, "deny")
    read_issue_and_file(engine)
    assert run(engine, "create_pull_request", "pr0", "run2").code == Code.EXECUTED
    for label in ("untrusted", "sensitive", "external"):
        engine.mark_run(ctx(), label)
    assert run(engine, "plain", "p1").code == Code.EXECUTED
    assert run(engine, "plain", "p2", copied=["ref"]).code == Code.EXECUTED


def test_once_the_run_holds_all_three_every_external_call_is_gated(clock):
    engine, _ = build(clock, "deny")
    for label in ("untrusted", "sensitive", "external"):
        engine.mark_run(ctx(), label, note=f"set {label}")
    assert engine.prepare(ctx(), "create_pull_request", {}, "pr1").code == Code.FLOW_BLOCKED
    blocked = engine.prepare(ctx(), "read_and_send", {}, "rs1")
    assert blocked.code == Code.FLOW_BLOCKED
    assert blocked.explanation.endswith(
        "This run read untrusted input (marked by the app) and private data (marked by the "
        "app) and acted outside (marked by the app). This call acts outside (read_and_send)."
    )
    # A call that does not act outside is not gated once the chain is complete.
    assert run(engine, "get_file", "f1").code == Code.EXECUTED
    assert run(engine, "get_issue", "i1").code == Code.EXECUTED


def test_runs_do_not_share_labels(clock):
    engine, _ = build(clock, "deny")
    read_issue_and_file(engine)
    assert run(engine, "create_pull_request", "pr1", "run2").code == Code.EXECUTED


def test_failed_and_uncertain_calls_keep_their_labels(clock):
    engine, handlers = build(clock, "deny")
    handlers["get_issue"].side_effect = ToolFailed("down")
    handlers["get_file"].side_effect = RuntimeError("lost")
    assert run(engine, "get_issue", "i1").code == Code.FAILED
    assert run(engine, "get_file", "f1").code == Code.UNCERTAIN
    labels = engine.store.run_labels("acme", "run1")
    assert labels["untrusted"]["tool"] == "get_issue"
    assert labels["sensitive"]["operation_id"] == "f1"
    assert engine.prepare(ctx(), "create_pull_request", {}, "pr1").code == Code.FLOW_BLOCKED


def test_the_first_thing_to_set_a_label_stays(clock):
    engine, _ = build(clock, "allow")
    run(engine, "get_issue", "i1")
    clock.now += 5
    engine.mark_run(ctx(), "untrusted", "upload")
    run(engine, "read_and_send", "rs1")
    labels = engine.store.run_labels("acme", "run1")
    assert labels["untrusted"] == {"tool": "get_issue", "operation_id": "i1", "at": 1000.0}
    assert labels["sensitive"]["tool"] == "read_and_send"


def test_with_the_rule_off_labels_are_recorded_and_nothing_is_gated(clock):
    events = []
    engine, _ = build(clock, "allow", events=events)
    read_issue_and_file(engine)
    pr = prepare(engine, "create_pull_request", "pr1")
    assert pr.flow_reason == "" and not pr.needs_approval
    assert engine.execute(pr, ctx()).code == Code.EXECUTED
    assert set(engine.store.run_labels("acme", "run1")) == {"untrusted", "sensitive", "external"}
    assert all(event["flow_reason"] is None for event in events)
    assert events[-1]["labels"] == ["external"]


# Copied values


def test_a_copied_value_gives_the_run_the_untrusted_label_when_reserved(clock):
    engine, _ = build(clock, "deny")
    prepared = prepare(engine, "get_file", "f1", copied=["ref", "other"])
    assert engine.store.run_labels("acme", "run1") == {}
    engine.execute(prepared, ctx())
    labels = engine.store.run_labels("acme", "run1")
    assert labels["untrusted"]["copied"] is True
    blocked = engine.prepare(ctx(), "create_pull_request", {}, "pr1")
    assert blocked.explanation.endswith(
        "This run read untrusted input (copied value in get_file) and private data "
        "(get_file). This call acts outside (create_pull_request)."
    )


def test_a_copied_path_for_another_tool_adds_nothing(clock):
    engine, _ = build(clock, "deny")
    run(engine, "plain", "p1", copied=["not_a_field"])
    assert engine.store.run_labels("acme", "run1") == {}


def test_an_unlabelled_tool_with_a_copied_value_still_marks_the_run(clock):
    engine, _ = build(clock, "deny")
    assert run(engine, "plain", "p1", copied=["ref"]).code == Code.EXECUTED
    assert engine.store.run_labels("acme", "run1")["untrusted"]["tool"] == "plain"


def test_a_copied_value_in_a_labelled_call_counts_as_untrusted_for_the_gate(clock):
    engine, _ = build(clock, "deny")
    run(engine, "get_file", "f1")
    blocked = engine.prepare(ctx(), "create_pull_request", {}, "pr1", copied=["ref"])
    assert blocked.code == Code.FLOW_BLOCKED
    assert blocked.explanation.endswith(
        "This run read private data (get_file). "
        "This call uses a copied value and acts outside (create_pull_request)."
    )


# mark_run


def test_mark_run_writes_an_audit_row(clock):
    events = []
    engine, _ = build(clock, "deny", events=events)
    engine.mark_run(ctx(), "untrusted", "user uploaded notes.txt")
    event = events[-1]
    assert (event["event"], event["code"], event["tool"]) == ("mark_run", "ALLOWED", None)
    assert event["labels"] == ["untrusted"]
    assert event["summary"] == "user uploaded notes.txt"
    labels = engine.store.run_labels("acme", "run1")
    assert labels["untrusted"] == {"source": "user uploaded notes.txt", "operation_id": None,
                                   "at": 1000.0}


@pytest.mark.parametrize(
    ("label", "note"),
    [("secret", ""), ("untrusted", "two\nlines"), ("untrusted", "x" * 201),
     ("untrusted", 5), ("external", "tab\there")],
)
def test_mark_run_checks_its_input(clock, label, note):
    engine, _ = build(clock, "deny")
    with pytest.raises(ValueError):
        engine.mark_run(ctx(), label, note)
    assert engine.store.run_labels("acme", "run1") == {}


def test_mark_run_starts_a_new_run(clock):
    engine, _ = build(clock, "deny")
    engine.mark_run(ctx("fresh"), "sensitive")
    assert "sensitive" in engine.store.run_labels("acme", "fresh")
    assert run(engine, "get_issue", "i1", "fresh").code == Code.EXECUTED


# Approve


def test_approve_mode_asks_for_approval_with_the_reason(clock):
    engine, handlers = build(clock, "approve")
    read_issue_and_file(engine)
    pr = prepare(engine, "create_pull_request", "pr1")
    assert pr.needs_approval and pr.code == Code.NEEDS_APPROVAL
    assert pr.flow_reason == REASON
    [waiting] = engine.pending("acme")
    assert waiting.flow_reason == REASON
    # A gated call with no approval says why it waits, and stays in the list.
    early = engine.execute(pr, ctx())
    assert early.code == Code.NEEDS_APPROVAL and early.explanation.endswith(REASON)
    assert len(engine.pending("acme")) == 1
    assert engine.approve(pr, APPROVER).code == Code.APPROVED
    assert engine.execute(pr, ctx()).code == Code.EXECUTED
    assert handlers["create_pull_request"].call_count == 1


def test_a_call_gated_only_at_execute_waits_for_approval_with_the_reason(clock):
    engine, handlers = build(clock, "approve")
    pr = prepare(engine, "create_pull_request", "pr1")
    assert not pr.needs_approval
    read_issue_and_file(engine)
    result = engine.execute(pr, ctx())
    assert (result.outcome, result.code) == ("denied", Code.NEEDS_APPROVAL)
    assert result.explanation.endswith(REASON)
    assert handlers["create_pull_request"].call_count == 0
    [waiting] = engine.pending("acme")
    assert (waiting.operation_id, waiting.state, waiting.flow_reason) == ("pr1", "prepared",
                                                                          REASON)
    # The old request copy still runs into the same refusal.
    assert engine.execute(pr, ctx()).code == Code.NEEDS_APPROVAL
    fresh = prepare(engine, "create_pull_request", "pr1")
    assert fresh.needs_approval and fresh.flow_reason == REASON
    assert engine.approve(fresh, APPROVER).code == Code.APPROVED
    assert engine.execute(fresh, ctx()).code == Code.EXECUTED


def test_a_repeat_prepare_that_becomes_gated_is_saved_and_listed(clock):
    engine, _handlers = build(clock, "approve")
    prepare(engine, "create_pull_request", "pr1")
    read_issue_and_file(engine)
    assert engine.pending("acme") == []
    fresh = prepare(engine, "create_pull_request", "pr1")
    assert fresh.needs_approval and fresh.flow_reason == REASON
    [waiting] = engine.pending("acme")
    assert waiting.flow_reason == REASON and waiting.code == Code.NEEDS_APPROVAL
    engine.approve(fresh, APPROVER)
    assert engine.execute(fresh, ctx()).code == Code.EXECUTED


def test_an_approval_given_before_the_reason_does_not_count(clock):
    engine, handlers = build(clock, "approve")
    send = prepare(engine, "send_reviewed", "s1")
    assert send.needs_approval and send.flow_reason == ""
    assert engine.approve(send, APPROVER).code == Code.APPROVED
    read_issue_and_file(engine)
    result = engine.execute(send, ctx())
    assert result.code == Code.NEEDS_APPROVAL
    assert handlers["send_reviewed"].call_count == 0
    status = engine.operation_status("acme", "s1")
    assert status.state == "prepared" and status.reference is None
    # The stale approval is gone, so a person can approve again after seeing the reason.
    fresh = prepare(engine, "send_reviewed", "s1")
    assert engine.approve(fresh, APPROVER).code == Code.APPROVED
    assert engine.execute(fresh, ctx()).code == Code.EXECUTED


def test_an_approval_for_an_older_reason_does_not_count(clock):
    engine, handlers = build(clock, "approve")
    run(engine, "get_issue", "i1")
    rs = prepare(engine, "read_and_send", "rs1")
    assert rs.flow_reason == ("This run read untrusted input (get_issue). "
                              "This call reads private data and acts outside (read_and_send).")
    engine.approve(rs, APPROVER)
    run(engine, "get_file", "f1")
    result = engine.execute(rs, ctx())
    assert result.code == Code.NEEDS_APPROVAL
    assert result.explanation.endswith(
        "This run read untrusted input (get_issue) and private data (get_file). "
        "This call acts outside (read_and_send)."
    )
    assert handlers["read_and_send"].call_count == 0


def test_approve_records_the_reason_the_approver_saw(clock):
    engine, _ = build(clock, "approve")
    read_issue_and_file(engine)
    pr = prepare(engine, "create_pull_request", "pr1")
    engine.approve(pr, APPROVER)
    approval = engine.store.get_approval("acme", "pr1")
    assert approval["flow_reason"] == REASON


def test_a_request_copy_that_claims_less_approval_than_stored_still_waits(clock):
    engine, handlers = build(clock, "approve")
    read_issue_and_file(engine)
    pr = prepare(engine, "create_pull_request", "pr1")
    forged = pr.model_copy(update={"needs_approval": False})
    forged._args_json = pr._args_json
    assert engine.execute(forged, ctx()).code == Code.NEEDS_APPROVAL
    assert handlers["create_pull_request"].call_count == 0


def test_audit_rows_carry_labels_and_the_reason(clock):
    events = []
    engine, _ = build(clock, "approve", events=events)
    read_issue_and_file(engine)
    pr = prepare(engine, "create_pull_request", "pr1")
    engine.approve(pr, APPROVER)
    engine.execute(pr, ctx())
    prep = next(e for e in events if e["operation_id"] == "pr1" and e["event"] == "prepare")
    done = next(e for e in events if e["operation_id"] == "pr1" and e["event"] == "execute")
    assert prep["labels"] == done["labels"] == ["external"]
    assert prep["flow_reason"] == done["flow_reason"] == REASON
    rows = engine.store.audit_rows("acme")
    assert json.loads(rows[-1]["labels"]) == ["external"]


# Two calls at once


@pytest.mark.parametrize("attempt", range(10))
def test_two_calls_at_once_cannot_both_pass_as_not_completing(tmp_path, clock, attempt):
    path = tmp_path / "latch.sqlite"
    one, h1 = build(clock, "deny", SQLiteStore(path))
    two, h2 = build(clock, "deny", SQLiteStore(path))
    one.mark_run(ctx(), "untrusted")
    file_op = prepare(one, "get_file", "f1")
    pr_op = prepare(one, "create_pull_request", "pr1")
    results = {}
    start = threading.Barrier(2)

    def go(engine, prepared, key):
        start.wait()
        results[key] = engine.execute(prepared, ctx())

    threads = [threading.Thread(target=go, args=(one, file_op, "file")),
               threading.Thread(target=go, args=(two, pr_op, "pr"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    codes = sorted(str(result.code) for result in results.values())
    assert codes == ["EXECUTED", "FLOW_BLOCKED"]
    ran = h1["get_file"].call_count + h2["create_pull_request"].call_count
    assert ran == 1
