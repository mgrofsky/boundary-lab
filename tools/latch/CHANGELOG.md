# Changelog

## 0.3.0

### Breaking changes

Each change below lists the one line fix.

- The 0.2.0 policy key that blocked marked arguments is now `copied_arguments`. Fix: rename that key in your policy file to `copied_arguments`.
- The 0.2.0 reason code for marked arguments is now `COPIED_ARGUMENTS`. Fix: compare against `Code.COPIED_ARGUMENTS`.
- `UserContext` no longer holds the marked field paths. Fix: drop that field and pass the paths to `prepare(..., copied=paths)`.
- Preparing a denied operation again used to return a `PreparedAction` that could never run. It now returns a `Decision` with the new code `OPERATION_CLOSED`. Fix: give the new request a new operation ID.
- `OperationStatus` has new required fields, so code that builds one by hand must set them. Fix: add `needs_approval`, `user_id` and `run_id` where you build it.

### Added

- `copied_arguments = "approve"` makes a call with a copied value wait for approval.
- `ToolSpec.describe` turns the arguments into a one line summary for the approver. A failing describe blocks the call with `DESCRIBE_FAILED`.
- `approve` and `deny` take a `reference` that names the evidence for the decision.
- `pending(org_id, run_id=None)` lists the operations waiting for approval.
- `cancel(prepared, by)` stops an operation before it runs, with the code `CANCELLED`.
