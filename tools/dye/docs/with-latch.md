# Using Dye with Latch

Part of [Dye](../README.md). Latch has its own [README](../../latch/README.md).

Dye tells you where argument values came from. Latch decides whether a tool call may run. Together, a call whose arguments were copied from a ticket can be blocked before it runs, with a reason code and an audit row.

Dye does not import Latch and Latch does not import Dye. The link between them is one field.

## The app side

    from boundary_dye import Tracker
    from boundary_latch import Decision, Latch, SQLiteStore, UserContext, load_policy

    latch = Latch(load_policy("policy.toml"), SQLiteStore("app.sqlite"))

    tracker = Tracker()
    tracker.mark("ticket 4471", ticket_body)

    report = tracker.check(model_arguments)
    ctx = UserContext(
        user_id=user.id,
        org_id=user.org,
        permissions=frozenset(user.permissions),
        run_id=conversation_id,
        tainted_fields=report.tainted,
    )
    prepared = latch.prepare(ctx, "refund", model_arguments, operation_id=action_id)
    if isinstance(prepared, Decision):
        print(prepared.code, prepared.explanation)
    else:
        result = latch.execute(prepared, ctx)

Pass the same `ctx` to `prepare` and `execute`. Latch runs its checks again before the tool runs, and the taint mark travels with the context.

## The policy side

    [tools.refund]
    require_permission = "refunds:create"
    tainted_arguments = "deny"

    [tools.lookup_order]
    require_permission = "orders:read"

`refund` is blocked with `TAINTED_ARGUMENTS` when any tainted path names one of its arguments. `lookup_order` keeps the default, `allow`, because a customer quoting an order number is normal and the tool only reads.

## What this does not cover

Latch only has `allow` and `deny` for tainted arguments. There is no mode that sends a tainted call to an approver. The check uses the paths Dye reports, not the values, so a tainted `items[0].sku` counts as the `items` field.
