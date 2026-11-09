# Approvals

Some calls should wait for a person to approve them. Set which calls need approval in the [policy](policy.md).

## What an approval covers

An approval covers one exact request. Latch records a fingerprint, called a request hash, that includes the operation ID, run, tool, tool version, policy, user, organization, and arguments. If any of those change, the old approval does not apply.

Latch checks that the request still matches before approving or running it. If you load a changed policy or change a tool's version, old pending actions return `OPERATION_CONFLICT`. Check what happened to the original action before creating a replacement.

The original 0.1.0 release used an older request hash. When upgrading from that release to code with the new hash, old pending actions are rejected. Keep the database and use `operation_status` to look up earlier work. Do not delete the records that prevent repeat calls to make an old action run again.

The `changed_after_approval` scenario shows what happens when arguments change. Sam asks for a $60.00 refund and Jordan approves it. Then Sam asks for $70.00 under the same operation ID. Latch returns `OPERATION_CONFLICT`, and the original $60.00 request still runs once.

## Who can approve

The approver must meet all three rules:

- They are in the same organization as the requester.
- They have the tool's `approve_permission`.
- They are not the requester.

There is no switch to allow someone to approve their own request. A requester approving their own call, or a user without the required permission, gets `APPROVER_NOT_ALLOWED`. An approver from another organization gets `IDENTITY_MISMATCH`.

The model cannot approve. Approval is never offered to it as a tool.

## When approval expires

An approval lasts for `approval_lifetime_seconds` from the moment it is given, or until the run ends, whichever comes first. An expired approval gives `APPROVAL_EXPIRED` while the run is still active. An expired run gives `RUN_EXPIRED`. Submit a new operation ID to ask for a new approval, and use a new run if the old one has expired.

## Single use

Before calling the handler, Latch marks the approval as used and records the attempt in one database transaction. Both records change together. Repeating the same request in the same run returns the recorded outcome without calling the handler again. If Latch recorded the attempt but has no outcome yet, the code is `ALREADY_RECORDED`. Once an outcome is recorded, its code is returned instead. The tool's returned value is not stored.

## What the approver sees

Give each tool a `describe` function to show the approver what the call will do. It takes the checked arguments and returns a string.

    ToolSpec(name="issue_refund", version="1", args_model=RefundArgs, handler=refund,
             describe=lambda args: f"Refund {args.amount_minor} cents to {args.account_id}")

Latch turns the text into one line: control characters and line breaks become spaces, runs of spaces become one, and the text stops at 300 characters. The result is `PreparedAction.summary`. Latch saves it with the operation and in the prepare audit row. It is not part of the request hash. If `describe` raises an error or returns something other than a string, Latch blocks the call with `DESCRIBE_FAILED`. Without `describe`, the summary is empty.

The summary is saved in the audit table, so leave out secrets and message text.

## Waiting requests

`pending(org_id)` returns an `OperationStatus` for every prepared operation that needs approval and has no decision yet, oldest first. Pass `run_id` to see one conversation. Each status has the requester's `user_id`, the `run_id`, the `summary`, and the `copied` argument paths, so the approver can see what the model copied.

    for item in latch.pending("northwind"):
        print(item.operation_id, item.user_id, item.summary, sorted(item.copied))

## Recording the evidence

`approve` and `deny` take an optional `reference` that names the evidence for the decision, such as a chat message ID or a ticket number. It holds at most 128 printable characters. Line breaks and other control characters raise `ValueError`, and nothing is recorded.

    latch.approve(prepared, approver, reference="TICKET-1042")

Latch saves the reference with the approval and in the audit row. It comes back as `ExecutionResult.reference` and `OperationStatus.reference`.

## Denial

An approval can't be replaced. Once a pending action is approved, another approval or denial returns `APPROVAL_USED`. You can't change that approval to a denial.

`deny` records that a pending request was denied. That operation can no longer run, and `execute` returns `DENIED_BY_APPROVER`. Preparing the same operation ID again returns `OPERATION_CLOSED`. Use a new operation ID for a new request.

A denial cannot cancel an action once `execute` has recorded the attempt. The handler may already be running. Denying it at that point returns `ALREADY_RECORDED` and changes nothing. Latch does not stop work in the system your handler called.

## Cancel

`cancel(prepared, by)` stops an operation before it runs. The requester can cancel their own request. A user in the same organization with the tool's `approve_permission` can cancel it too. Anyone else gets `APPROVER_NOT_ALLOWED`, and a user from another organization gets `IDENTITY_MISMATCH`.

    decision = latch.cancel(prepared, ctx)
    if decision.code == Code.CANCELLED:
        print("cancelled")

Cancel works while the operation is prepared or approved. A cancelled operation never runs: `execute` returns outcome `denied` with `CANCELLED`, and an approval gets `CANCELLED` too. `Decision.allowed` is false, the same as for a denial, so check the code. Once `execute` has recorded the attempt, cancel returns `ALREADY_RECORDED` and changes nothing. Cancelling a denied operation returns its denial code.

## Closed operations

A denied, cancelled, or run limit refused operation is closed. Preparing the same request with that operation ID again returns `OPERATION_CLOSED`. A request that does not match the saved record still returns `OPERATION_CONFLICT`. Either way, use a new operation ID.

## Decision history

Latch records decisions from `prepare`, `approve`, `deny`, `cancel`, and `execute` in an audit table. Each row contains IDs, a reason code, timing information, and an outcome when one is available. Rows also hold the summary, the copied argument paths, and the approval reference where they apply. They do not contain arguments. See [the audit](audit.md).
