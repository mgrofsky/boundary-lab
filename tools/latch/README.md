# Latch

Latch checks tool calls before they run. The model asks to use one of your app's functions and supplies the arguments. Your app tells Latch who the user is, and Latch checks whether that user can make that exact call.

Latch is part of [Boundary Lab](https://github.com/mgrofsky/boundary-lab/blob/main/README.md). It is a separate package from [Redline](https://github.com/mgrofsky/boundary-lab/blob/main/redline/README.md) and does not share any code with it. Redline grades answers after they are written. Latch blocks calls before they happen.

## What it checks

- The arguments match what the tool accepts, with no extra fields.

- The user has the permission the tool needs.

- The recipient is on the allowed list, if the tool has one.

- Your app's access check says this user can use the requested record.

- Whether any argument value was copied from text the model read, such as an email or a web page, if your app passes those paths to `prepare`. The policy can allow such a call, block it, or make it wait for approval. Dye, another Boundary Lab project, finds copied values.

- The run has not used up its call limit, its lifetime, or its idle time.

- One run does not read untrusted input, read private data, and act outside, if the policy turns on the flow rule. The call that would complete that chain is refused or waits for a person, with a reason.

- Calls that need approval wait for another user. The approval covers that exact request, expires, and works once.

- Repeating the same request with the same operation ID in the same run does not run the tool again. This still works after a restart if you keep the SQLite database.

A run groups the calls for one conversation. An operation ID identifies one requested action within your organization.

Every decision comes back with a reason code and a short explanation.

## Install

The core package needs only pydantic, which checks the tool arguments.

    pip install boundary-latch

Install an optional extra to use the browser demo or connect a model.

    pip install "boundary-latch[demo]"    # the browser demo (fastapi, uvicorn)
    pip install "boundary-latch[live]"    # the live model adapter (httpx)

To work on Latch itself, from this folder:

    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev,demo,live]"
    .venv/bin/pytest -q

## Try the demo

    boundary-latch demo

Ten scenarios run one at a time. Each step shows who asked for what, the reason code, and what changed in the sample support system for Northwind, a made-up company. Sam and Priya are support agents, and Jordan is a supervisor. These are example users.

For the same scenarios in a browser:

    boundary-latch serve

Open [the local demo](http://127.0.0.1:8765). Run a scenario from a button, or try the steps yourself: act as Sam, propose a refund over $50, then switch to Jordan and approve it. Switch back to Sam and click Run. The balance changes once.

The server only listens on 127.0.0.1. To print the scenario results, run `boundary-latch scenarios`, or add `--json` to get JSON output.

Check a policy file with `boundary-latch check policy.toml`. Copy a starter project with `boundary-latch init my-app`. Print the decision history with `boundary-latch audit latch-demo.sqlite`.

## Use it in your app

Here is how the starter project connects Latch to an app. The full example is in [app.py](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/src/boundary_latch/starter/app.py).

    from boundary_latch import Decision, Latch, SQLiteStore, ToolSpec, UserContext, load_policy

    latch = Latch(load_policy("policy.toml"), SQLiteStore("app.sqlite"))
    latch.register(ToolSpec(name="create_ticket", version="1", args_model=TicketArgs,
                            handler=create_ticket))
    ctx = UserContext(user_id=user.id, org_id=user.org, permissions=frozenset(user.perms),
                      run_id=conversation_id)
    prepared = latch.prepare(ctx, "create_ticket", model_args, operation_id=action_id,
                             copied=report)
    if not isinstance(prepared, Decision):
        result = latch.execute(prepared, ctx)

`prepare` returns a `Decision` when the call is blocked. Otherwise it returns a `PreparedAction`. Check `needs_approval` before calling `execute`; if it is true, wait for an authorized person to approve the request. The handler is your function that does the work.

`copied` is optional. It lists the argument paths whose values the model copied from text it read, or takes a Dye report. Latch saves the paths with the operation and checks them against the tool's `copied_arguments` policy.

An approver finds waiting requests with `latch.pending(org_id)`. Each one carries a one line `summary` from the tool's `describe` function. The approver calls `latch.approve(prepared, approver, reference="TICKET-1042")` or `latch.deny(...)`, and either the requester or an approver can `latch.cancel(prepared, by)`.

`execute` never raises after your handler has run. If Latch could not save the outcome, the result has `recorded` set to `False`. Then check the real system before doing anything else.

Every decision is saved in an audit table. Read it with `boundary-latch audit app.sqlite`, or pass `on_event` to `Latch` to get each row as it is written.

## Put it in front of an MCP server

`boundary-latch mcp` checks every tool call between an MCP client, such as Claude Code or Cursor, and one MCP server. The client starts the proxy instead of the server.

    boundary-latch mcp --print-policy -- <server command> > policy.toml
    boundary-latch mcp --policy policy.toml -- <server command>

Calls the policy blocks never reach the server. Calls that need approval wait on a page in your browser on 127.0.0.1. The proxy also refuses a tool whose definition changed until you accept it. See [docs/mcp.md](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/mcp.md).

## What Latch does not do

- It doesn't log anyone in. Your app checks who the user is.

- You set the rules in one TOML file. There's no separate service for managing them.

- It doesn't isolate your functions from the rest of your system. They run with your app's permissions.

- It doesn't detect text that tries to trick a model into following unwanted instructions. It checks the tool calls the model asks to make. Pair it with Dye to see which arguments came from that text.

- It can't confirm what happened in another system after a timeout or lost connection. It records the attempt and won't try it again automatically.

- It doesn't clear records of attempted calls or offer a retry command.

- The live example doesn't show partial model replies as they arrive.

- It's tested with one app process on one machine. Running several app processes against the same database isn't supported.

## Docs

- [Use Latch in your app](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/integration.md): replace the sample login, database, and tools.

- [Policy](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/policy.md): set permissions, approvals, and limits.

- [Approvals](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/approvals.md): what an approval covers, summaries, references, the pending list, and cancel.

- [Limits](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/limits.md): call limits, time limits, and what to do when the outcome is unclear.

- [Flow rule](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/flow.md): stop one run from reading untrusted input, reading private data, and acting outside.

- [Audit](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/audit.md): the audit fields, the `audit` command, and `on_event`.

- [MCP proxy](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/mcp.md): check every tool call an MCP client makes, with an approval page and pinned tool definitions.

- [Changelog](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/CHANGELOG.md): what changed in each release.

- [Live model](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/live.md): point the demo at a real model.

- [Release](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/docs/release.md): how a version gets published.

Latch is licensed under AGPL-3.0-only. See [LICENSE](https://github.com/mgrofsky/boundary-lab/blob/main/tools/latch/LICENSE).
