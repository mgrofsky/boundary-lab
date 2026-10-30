# Integration

This page shows how to move from the sample app to yours. The starter project has all of it in one place. Run `boundary-latch init my-app` to copy it.

## Replace the sample login

Build a `UserContext` from your own session after your app has authenticated the user.

    ctx = UserContext(
        user_id=session.user_id,
        org_id=session.org_id,
        permissions=frozenset(session.permissions),
        run_id=conversation_id,
    )

All four values come from your app. None of them may come from the model. Use one `run_id` for each conversation, so the run limit counts the right calls. The demo profiles in `boundary_latch.demo.profiles` are labeled sample identities and are not a login system.

## Replace the database

Latch keeps its operations, approvals and audit rows in SQLite.

    store = SQLiteStore("/var/lib/myapp/latch.sqlite")

Point it at a path your app keeps between restarts. If the file is lost, a repeated operation id is no longer caught. Use `":memory:"` only in tests.

A SQLite error becomes a `StorageError`. A failure before dispatch blocks the call. A failure while recording the result can happen after the handler changed something. Keep the original operation id, check the backend and stored status, and do not retry automatically. See [uncertain outcomes](limits.md).

## Add a tool

1. Write an arguments model. It must set `extra="forbid"`, or `register` raises `ConfigError`.
2. Write a handler that takes `(args, info)`. Raise `ToolFailed` only when you know nothing happened.
3. Write an access hook if the call touches a record that belongs to one user. It takes `(ctx, args)` and returns a bool.
4. Register a `ToolSpec`.
5. Add a `[tools.<name>]` table to the policy with at least `require_permission`.

Here is a refund tool.

    class RefundArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_id: str
        amount_minor: int = Field(ge=1)

    def refund(args, info):
        return backend.refund(args["account_id"], args["amount_minor"], info.operation_id)

    latch.register(ToolSpec(
        name="issue_refund", version="1", args_model=RefundArgs, handler=refund,
        access_hook=lambda ctx, args: backend.can_see(ctx.user_id, args["account_id"]),
    ))

Pass `info.operation_id` to your backend, and have the backend refuse a repeat of it. That is your second line of defense. See [limits](limits.md).

Register fails when the tool has no policy entry, and `prepare` blocks a tool with no entry as `NO_POLICY`. Check the policy file at any time with `boundary-latch check policy.toml`.

## Prepare and execute

    prepared = latch.prepare(ctx, "issue_refund", args, operation_id=action_id)

Give each action the model asks for its own operation id, and keep it with the action. A repeat call with the same id and the same request in the same run is fine. A different request or run is `OPERATION_CONFLICT`.

If `prepare` returns a `Decision`, the call was blocked and `decision.code` says why. If it returns a `PreparedAction` with `needs_approval` true, wait for an approver. Then run it.

    result = latch.execute(prepared, ctx)

Use `await latch.aexecute(prepared, ctx)` when the handler is async. `execute` refuses an async handler.

## What you should see

The nine built in scenarios are the expected results. Run `boundary-latch scenarios` to print them. Two examples:

    other_customer          ACCESS_DENIED
    duplicate_refund        ALLOWED, EXECUTED, ALREADY_RECORDED

In `duplicate_refund` the balance moves from 100.00 to 80.00 once. The second run returns the recorded outcome and does not call the handler. Your own tests can assert on the same codes.
