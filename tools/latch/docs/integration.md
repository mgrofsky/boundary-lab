# Use Latch in your app

Start with `boundary-latch init my-app` to copy the starter project. Then replace its sample user, database path, and tool with your own.

## Replace the sample login

After your app checks who the user is, build a `UserContext` from their login session.

    ctx = UserContext(
        user_id=session.user_id,
        org_id=session.org_id,
        permissions=frozenset(session.permissions),
        run_id=conversation_id,
    )

All four values come from your app. The model must not set any of them. Use one `run_id` for each conversation so its calls count toward the same limit. The profiles in `boundary_latch.demo.profiles` are example users; replace them with your own login system.

## Replace the database

Latch keeps requested actions, approvals, and decision history in a SQLite database.

    store = SQLiteStore("/var/lib/myapp/latch.sqlite")

Use a file path your app keeps between restarts. If you lose the file, Latch can no longer catch repeated operation IDs from that database. Use `":memory:"` only in tests.

A SQLite error becomes a `StorageError`. A failure before the tool starts blocks the call. A failure while recording the outcome can happen after the tool has changed something. Keep the original operation ID, check both the system the tool called and Latch's stored status, and do not retry automatically. See [uncertain outcomes](limits.md).

## Add a tool

1. Write a pydantic model that defines the accepted arguments. It must set `extra="forbid"` to reject extra fields, or `register` raises `ConfigError`.
2. Write the handler, the function that does the work. It takes `(args, info)`. Raise `ToolFailed` only when you know nothing happened.
3. Add an `access_hook` if the call uses a record that belongs to one user. This function checks access, takes `(ctx, args)`, and returns `True` or `False`.
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

Pass `info.operation_id` to the system your handler calls, and have that system refuse a repeated id too. This adds a duplicate check where the change actually happens. See [limits](limits.md).

`register` fails when the tool has no policy entry. If a registered tool later has no entry, `prepare` blocks it with `NO_POLICY`. Check the policy file at any time with `boundary-latch check policy.toml`.

## Prepare and execute

    prepared = latch.prepare(ctx, "issue_refund", args, operation_id=action_id)

Give each requested action its own operation ID, and keep that id with the action. Repeating the same request with the same id in the same run does not run the handler again. Changing the request or moving it to another run gives `OPERATION_CONFLICT`.

If `prepare` returns a `Decision`, the call was blocked and `decision.code` says why. Otherwise, it returns a `PreparedAction`. If `needs_approval` is true, wait for an authorized person to approve it before calling `execute`.

    result = latch.execute(prepared, ctx)

For an async handler, use `await latch.aexecute(prepared, ctx)`. `execute` rejects async handlers.

## What you should see

Run `boundary-latch scenarios` to print the results of the nine included scenarios. Two examples:

    other_customer          ACCESS_DENIED
    duplicate_refund        ALLOWED, EXECUTED, ALREADY_RECORDED

In `duplicate_refund`, the balance changes from $100.00 to $80.00 once. The second attempt returns the recorded outcome without calling the handler. The demo displays `ALREADY_RECORDED` for a repeated call unless its outcome is `UNCERTAIN`; the library returns the recorded outcome's code. Your own tests can check the outcome and `replayed` flag. The original returned value is not stored.
