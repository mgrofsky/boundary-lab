# Limits and uncertain outcomes

## Run limits

The defaults are 20 actions per run, a run lifetime of 900 seconds and an approval lifetime of 600 seconds. Change them in the `[limits]` table of the policy.

A run is everything that shares a `run_id` in one org. A new operation id counts against the run. Resubmitting it in that same run does not count again. An operation stays tied to its original run; moving it to another run gives `OPERATION_CONFLICT`.

Past the limit, Latch answers `RUN_LIMIT`. Past the lifetime, it answers `RUN_EXPIRED`. A new conversation can start a new run, but it cannot revive an old pending action. Use `operation_status` to look up an earlier operation without resubmitting it.

## What the deadline does

Each tool has a `timeout_seconds` setting, 30 seconds by default. Latch waits that long for the handler to return.

The deadline does not stop the handler. A sync handler runs on a small pool of four threads, and when the deadline passes the thread keeps going on its own. A handler that never returns holds one of the four threads for good. Async handlers are cancelled at the deadline, but work they already sent elsewhere is not undone. Give your own network calls a timeout shorter than the deadline.

## Uncertain outcomes

A handler can finish three ways.

- It returns a value. The outcome is `succeeded` with code `EXECUTED`.
- It raises `ToolFailed`. You are saying nothing happened. The outcome is `failed` with code `FAILED`.
- It raises any other error, or hits the deadline. The outcome is `uncertain` with code `UNCERTAIN`.

Latch cannot tell whether an uncertain call changed anything. So it records the outcome and never runs that operation id again. The `timeout_after_refund` scenario shows it. The refund commits, the handler runs past the deadline, and a second execute returns `UNCERTAIN` as a replay. The backend refund count stays at 1.

Recording the result can also fail after the handler ran. That raises `StorageError`; it does not mean nothing happened. The reservation blocks another dispatch. Keep the operation id and check the real backend before deciding what to do next.

## The no retry rule

Latch has no retry and no command to clear an uncertain operation. To try again, first look at the real system and find out what happened. Then submit a new operation id and a new request.

Raise `ToolFailed` only when you are sure nothing happened. If you are not sure, let the error through.

## Results are not stored

Latch returns a result in process only. A replayed execute returns the recorded outcome with `value` set to None and `replayed` set to true. If you need the value later, store it in your own system.

## The backend's own duplicate check

Latch records that it dispatched an operation once. It cannot see inside a remote system. If the process dies after your handler sent a request and before Latch recorded the result, the operation stays reserved and later runs report `ALREADY_RECORDED`.

For money and other real effects, have the backend refuse a repeated operation id as well. The Northwind demo does this. Its refund method keeps a record by operation id and raises `ToolFailed` on a repeat. Pass `info.operation_id` through to whatever you call.

## One host

Latch uses one SQLite connection with a lock around it. It is tested with threads in one process, and with a restart on a file. Several processes at once are not a supported setup.
