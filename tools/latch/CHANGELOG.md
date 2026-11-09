# Changelog

## 0.3.0

### Breaking changes

Each change below lists the one line fix.

- The policy key `tainted_arguments` is now `copied_arguments`. Fix: rename that key in your policy file.
- The reason code `TAINTED_ARGUMENTS` is now `COPIED_ARGUMENTS`. Fix: compare against `Code.COPIED_ARGUMENTS`.
- `UserContext.tainted_fields` is gone. Fix: drop that field and pass the paths to `prepare(..., copied=paths)`. The old identifiers appear in this file only, so a search for them finds the fix.
- Preparing a denied operation again used to return a `PreparedAction` that could never run. It now returns a `Decision` with the new code `OPERATION_CLOSED`. Fix: give the new request a new operation ID.
- `OperationStatus` has new required fields, so code that builds one by hand must set them. Fix: add `needs_approval`, `user_id` and `run_id` where you build it.

- The policy fingerprint now includes the new policy keys, so it changes for every policy file, even one you did not edit. An operation prepared or approved under 0.2.0 that has not run returns `OPERATION_CONFLICT` on 0.3.0. Fix: finish pending work before upgrading, or prepare it again under a new operation ID.
- `max_actions_per_run` now counts the operations that run, checked at `execute`, instead of the requests made at `prepare`. Fix: set `max_proposals_per_run` to your old value to keep a limit at `prepare`.

### Added

- `copied_arguments = "approve"` makes a call with a copied value wait for approval.
- `ToolSpec.describe` turns the arguments into a one line summary for the approver. A failing describe blocks the call with `DESCRIBE_FAILED`.
- `approve` and `deny` take a `reference` that names the evidence for the decision.
- `pending(org_id, run_id=None)` lists the operations waiting for approval.
- `cancel(prepared, by)` stops an operation before it runs, with the code `CANCELLED`.
- `max_proposals_per_run` limits how many requests a run can make at `prepare`.
- `ExecutionResult` gains `error`, `recorded`, and `reference`. A storage error after the handler ran no longer raises; the result has `recorded` set to `False`.
- `Latch(..., on_event=callback)` gets every audit row as a dict, and `boundary-latch audit` prints the audit rows. See docs/audit.md.
- `run_lifetime_seconds = 0` means a run has no time limit, and `run_idle_seconds` expires a run after a quiet period.
