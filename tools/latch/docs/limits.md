# Limits and uncertain outcomes

## Run limits

The defaults are 20 actions per run, a run lifetime of 900 seconds and an approval lifetime of 600 seconds. Change them in the `[limits]` table of the policy.

A run groups calls that share a `run_id` within one organization. A new operation ID counts toward the run's limit. Once Latch stores the operation, submitting it again in the same run does not count twice. An operation stays tied to its original run; moving it to another run gives `OPERATION_CONFLICT`.

When you exceed the call limit, Latch returns `RUN_LIMIT`. When the run expires, it returns `RUN_EXPIRED`. A new conversation can start a new run, but that does not let an old pending action run again. Use `operation_status` to look up an earlier operation without submitting it again.

## What happens at the time limit

Each tool has a `timeout_seconds` setting, 30 seconds by default. Latch waits that long for the handler, your function that does the work, to return.

Reaching the time limit does not guarantee that the work stops. Synchronous handlers use a pool of four threads. When time runs out, the thread keeps running, and a handler that never returns keeps one of those four threads occupied. Latch cancels async handlers at the time limit, but that cannot undo work they already sent elsewhere. Give your own network calls a shorter timeout than the tool's limit.

## Uncertain outcomes

A handler can finish in three ways.

- It returns a value. The outcome is `succeeded` with code `EXECUTED`.
- It raises `ToolFailed`. Use this only when you know nothing changed. The outcome is `failed` with code `FAILED`.
- It raises any other error or reaches the time limit. The outcome is `uncertain` with code `UNCERTAIN`.

Latch cannot tell whether an uncertain call changed anything, so it records the outcome and refuses to run that operation ID again. In the `timeout_after_refund` scenario, the refund goes through before the handler exceeds its time limit. A second call to `execute` returns the recorded `UNCERTAIN` outcome. The refund count stays at 1.

Recording the outcome can also fail after the handler has run. That raises `StorageError`, and the change may already have happened. Latch records the attempt before calling the handler, which blocks a second attempt while that database record remains. Keep the operation ID and check the system the handler called before deciding what to do next.

## Before trying again

Latch has no retry command and no command to clear an uncertain operation. First check the real system to find out what happened. If another attempt is needed, submit a new operation ID and a new request.

Raise `ToolFailed` only when you are sure nothing changed. If you are not sure, let the original error through.

## Returned values are not stored

The handler's return value is available only from the call that ran it. Repeating `execute` returns the recorded outcome with `value` set to `None` and `replayed` set to `True`. If you need the value later, store it in your own system.

## Check for duplicates in your own system too

Before calling a handler, Latch records the attempt with a status of `reserved`. That record prevents another attempt, but it cannot tell Latch what happened in a remote system. If the process dies after your handler sends a request and before Latch records the outcome, the status stays `reserved`. A later call to `execute` reports `ALREADY_RECORDED`.

For refunds and other calls that change something, have the system receiving the call reject repeated operation IDs too. The Northwind demo's refund method tracks operation IDs and raises `ToolFailed` on a repeat. Pass `info.operation_id` through to the system you call.

## One process on one host

Latch uses one SQLite connection and a lock so threads take turns using it. Tests cover threads in one process and restarting with the same database file. Running several processes at once is not supported.
