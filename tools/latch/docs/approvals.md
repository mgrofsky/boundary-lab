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

## Denial

An approval can't be replaced. Once a pending action is approved, another approval or denial returns `APPROVAL_USED`. You can't change that approval to a denial.

`deny` records that a pending request was denied. That operation can no longer run, and `execute` returns `DENIED_BY_APPROVER`.

A denial cannot cancel an action once `execute` has recorded the attempt. The handler may already be running. Denying it at that point returns `ALREADY_RECORDED` and changes nothing. Latch does not stop work in the system your handler called.

## Decision history

Latch records decisions from `prepare`, `approve`, `deny`, and `execute` in an audit table. Each row contains IDs, a reason code, timing information, and an outcome when one is available. These rows do not contain arguments or message text.
