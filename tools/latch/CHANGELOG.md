# Changelog

## 0.4.0

### Added

- `boundary-latch mcp` is a stdio proxy between an MCP client and one MCP server. Every `tools/call` goes through Latch `prepare` and `execute`. See docs/mcp.md.
- The proxy hides tools the policy does not name, and a call to one returns `UNKNOWN_TOOL`.
- The proxy parses every client message and writes it again, so the server sees exactly what Latch checked. Invalid JSON, batches, and a `tools/call` with a repeated key get a JSON-RPC error and are not forwarded.
- A blocked call is a result with `isError` true and the text `Blocked by Latch: CODE. explanation`, so the model can read why.
- An approval page on 127.0.0.1 lists waiting calls and changed tools. It needs a random key, refuses other `Host` headers, takes decisions by POST only, and escapes everything it shows. It reloads by itself only while nothing waits; otherwise it shows a Refresh link and the time it was drawn.
- Pinned tool definitions. A tool whose definition changes after it was first seen returns the new code `TOOL_CHANGED` until a person accepts it. The pin covers the whole tool object, including title, annotations, and `outputSchema`. `--pin FILE` keeps the pins between runs.
- `boundary-latch mcp --print-policy` prints a starter policy for a server's tools, with approval on for every tool.
- Audit rows with event `tool_changed` and `tool_accepted`.
- The flow rule. `[flow] all_three = "approve"` or `"deny"` stops one run from reading untrusted input, reading private data, and acting outside. Tools say what they do with `labels`. See docs/flow.md.
- The reason code `FLOW_BLOCKED`.
- `PreparedAction.flow_reason` and `OperationStatus.flow_reason` hold one plain sentence that says which calls set each label and what this call adds.
- `Latch.mark_run(ctx, label, note="")` adds a label to the run itself, for example when a user uploads a file.
- A copied value counts as `untrusted` for the call that carries it, at `prepare` and at `execute`, and gives the run that label once the call is reserved. A tool with no labels is still never gated.
- Audit rows and `on_event` dicts gain `labels` and `flow_reason`. Fields are only added, so `schema` stays 1.
- `boundary-latch check` prints the flow mode and how many tools carry each label.
- `boundary-latch mcp --print-policy --flow` writes a starter policy with the rule on and every label on every tool.
- The demo has a `lookup_note` tool and a tenth scenario, `flow_chain`, that shows the rule.

### Changed

- A pin file from an earlier build of the proxy, which hashed only the name, description, and `inputSchema`, is moved to the new hash on load when its definition did not change.
- The database gains `runs.labels`, `operations.flow_reason`, `approvals.flow_reason`, `audit.labels`, and `audit.flow_reason`. A database from an older release gets them when opened.
- `ToolSpec.name` now accepts the names MCP servers use: a letter, then up to 63 letters, digits, underscores, dots, or hyphens. Upper case letters, dots, and hyphens are new. These names are stored with SQL parameters and shown as text, so they stay safe in the audit, the `audit` command, and the demo page.

### Fixed

- An `over_amount` policy could skip approval when the amount was a bool or NaN, since `True > 5000` and `NaN > 5000` are both false. `prepare` now refuses an amount that is not a finite number with `INVALID_ARGUMENTS`. That covers a bool, a string, a missing value, NaN, and infinity.
- A missing `amount_field` or `recipient_field` on the arguments model raised `AttributeError` from `prepare`. It now fails closed: `INVALID_ARGUMENTS` for the amount, `RECIPIENT_NOT_ALLOWED` for the recipient.
- `recipient_field` now needs a string value. A list or other value is refused with `RECIPIENT_NOT_ALLOWED`.
- A policy field can name a field's alias as well as its name.

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
