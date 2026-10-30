# Latch

Latch checks tool calls before they run. It sits between the model and your app's functions, and the model only gets to name a tool and give arguments. Your app decides who the user is, and Latch decides whether that user may make this exact call right now.

Latch is part of [Boundary Lab](../../README.md). It is a separate package from [Redline](../../redline/README.md) and does not share any code with it. Redline grades answers after they are written. Latch blocks calls before they happen.

## What it checks

- The arguments match the tool, with no extra fields.

- The user holds the permission the tool needs.
- The recipient is on the allowed list, if the tool has one.

- Your own access hook says this user may touch this record.
- The run has not used up its call limit or its time.

- Big actions wait for another user to approve them. The approval is tied to the exact request, expires, and works once.
- The same operation id never dispatches twice, even after a restart.

Every decision comes back with a reason code and one plain sentence.

## Install

The core needs only pydantic.

    pip install boundary-latch

Two extras are optional.

    pip install "boundary-latch[demo]"    # the browser demo (fastapi, uvicorn)
    pip install "boundary-latch[live]"    # the live model adapter (httpx)

To work on Latch itself, from this folder:

    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev,demo,live]"
    .venv/bin/pytest -q

## Try the demo

    boundary-latch demo

Nine scenarios play out one at a time. Each step shows who asked for what, the reason code, and what changed in a fake backend for Northwind, a made-up company. The people are labeled sample identities. Sam and Priya are customer agents, and Jordan is a supervisor.

For the same scenarios in a browser:

    boundary-latch serve

Open http://127.0.0.1:8765. Run a scenario from a button, or try the steps yourself: act as Sam, propose a refund over $50, then switch to Jordan and approve it. Switch back to Sam and click Run. The balance moves once.

The server only listens on 127.0.0.1. To see the codes without any interface, run `boundary-latch scenarios`, or add `--json` for machine output.

Check a policy file with `boundary-latch check policy.toml`. Copy a starter project with `boundary-latch init my-app`.

## Use it in your app

This is the shape of the starter project. The full file is `src/boundary_latch/starter/app.py`.

    from boundary_latch import Decision, Latch, SQLiteStore, ToolSpec, UserContext, load_policy

    latch = Latch(load_policy("policy.toml"), SQLiteStore("app.sqlite"))
    latch.register(ToolSpec(name="create_ticket", version="1", args_model=TicketArgs,
                            handler=create_ticket))
    ctx = UserContext(user_id=user.id, org_id=user.org, permissions=frozenset(user.perms),
                      run_id=conversation_id)
    prepared = latch.prepare(ctx, "create_ticket", model_args, operation_id=action_id)
    if not isinstance(prepared, Decision):
        result = latch.execute(prepared, ctx)

`prepare` returns a `Decision` when the call is blocked. Otherwise it returns a `PreparedAction`, and `needs_approval` tells you whether to wait for a supervisor first.

## What Latch does not do

- It does not log anyone in. Your app owns identity.

- It has no policy server. The policy is one TOML file.
- It is not a sandbox. A handler runs with your app's own rights.

- It does not detect prompt injection. It limits what an injected call can do.
- It cannot make a remote action happen exactly once. It records the attempt and refuses to retry an uncertain one.

- It does not purge reservations or offer a retry command.
- It does not stream model responses.

- It supports one host. It is not tested across several processes.

## Docs

- [Integration](docs/integration.md): replace the sample login, database and tools.

- [Policy](docs/policy.md): every key in the TOML file.
- [Approvals](docs/approvals.md): what an approval is tied to and how it ends.

- [Limits](docs/limits.md): run limits, deadlines and uncertain outcomes.
- [Live model](docs/live.md): point the demo at a real model.

- [Release](docs/release.md): how a version gets published.

Latch is licensed under AGPL-3.0-only. See [LICENSE](LICENSE).
