# Approvals

Some calls should wait for a person. The policy decides which, using the approval modes in [policy](policy.md).

## What an approval is bound to

An approval belongs to one operation, and the operation belongs to one exact request. The request hash covers the operation, run, tool, tool version, policy, user, org and arguments. If any of those change, the old approval does not apply.

Latch checks that binding again before approval and execution. If you reload a policy or change a tool's version, old pending actions return `OPERATION_CONFLICT`. Check what happened to the original action before creating a replacement.

Records created by version 0.1.0 used an older request hash. After this change, pending actions with that hash are refused. Keep the database and use `operation_status` to look up earlier work. Do not clear reservations to make an old action run again.

The `changed_after_approval` scenario shows this. Sam asks for a 60.00 refund and Jordan approves it. Then Sam sends 70.00 under the same operation id. That is `OPERATION_CONFLICT`, and the original 60.00 request still runs once.

## Who can approve

The approver must meet all three rules:

- They are in the same org as the requester.
- They hold the tool's `approve_permission`.
- They are not the requester.

There is no switch to turn the last rule off. Anyone else gets `APPROVER_NOT_ALLOWED`. An approver from another org gets `IDENTITY_MISMATCH`.

The model cannot approve. Approval is never offered to it as a tool.

## Expiry

An approval is good for `approval_lifetime_seconds` from the moment it is given. It also cannot outlive the run. Running after that gives `APPROVAL_EXPIRED`. Ask for a new approval by submitting a new operation id.

## Single use

Executing consumes the approval inside the same transaction that reserves the operation. A second execute does not need it, because the operation is already recorded. It returns `ALREADY_RECORDED` and the handler is not called again.

## Denial

`deny` records a decision of denied. The operation can then never run, and execute returns `DENIED_BY_APPROVER`.

A denial cannot cancel an action that was already dispatched. Once execute has reserved the operation, the handler may be running. Denying at that point returns `ALREADY_RECORDED` and changes nothing. Latch does not stop work in your backend.

## Audit

Latch writes an audit row for every prepare, approve, deny and execute. A row holds ids, the reason code, times and the outcome. It never holds arguments or message text.
