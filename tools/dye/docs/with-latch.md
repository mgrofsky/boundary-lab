# Using Dye with Latch

Part of [Dye](../README.md). Latch has its own [README](../../latch/README.md).

Dye tells you where argument values came from. Latch decides whether a tool call may run. Together, a call whose arguments were copied from a ticket can be stopped before it runs, with a reason code and an audit row.

Dye does not import Latch and Latch does not import Dye. The link between them is one argument. This page uses the Latch 0.3.0 API.

## The app side

    from boundary_dye import Tracker
    from boundary_latch import Decision, Latch, SQLiteStore, UserContext, load_policy

    latch = Latch(load_policy("policy.toml"), SQLiteStore("app.sqlite"))

    tracker = Tracker()
    tracker.mark("ticket 4471", ticket_body)
    tracker.trust(user.account_id)

    report = tracker.check(model_arguments)
    ctx = UserContext(
        user_id=user.id,
        org_id=user.org,
        permissions=frozenset(user.permissions),
        run_id=conversation_id,
    )
    prepared = latch.prepare(
        ctx, "refund", model_arguments, operation_id=action_id, copied=report
    )
    if isinstance(prepared, Decision):
        print(prepared.code, prepared.explanation)
    else:
        result = latch.execute(prepared, ctx)

`prepare` accepts any object with a `.copied` attribute, so a Dye `Report` works as it is. `UserContext` no longer carries the copied paths. Pass the same `ctx` to `prepare` and `execute`.

## The policy side

    [tools.refund]
    require_permission = "refunds:create"
    copied_arguments = "deny"

    [tools.change_plan]
    require_permission = "plans:write"
    copied_arguments = "approve"

    [tools.lookup_order]
    require_permission = "orders:read"

`refund` is blocked with the code `COPIED_ARGUMENTS` when any copied path names one of its arguments. `change_plan` uses `approve`, so a call with a copied argument waits for a human yes. `lookup_order` keeps the default, `allow`, because a customer quoting an order number is normal and the tool only reads.
