# My Latch app

This is a small app with one tool, `create_ticket`. Latch checks every call to it
before it runs. Copy it, then swap in your own users, database and tools.

## Run it

    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/python app.py
    .venv/bin/pytest -q

`app.py` prints `EXECUTED` and the new ticket. The tests print `2 passed`.

## What is in here

- `policy.toml` says who may call each tool and when a call needs approval.
- `app.py` holds the tool's arguments model, its handler and `build()`, which
  registers the tool with Latch.
- `tests/test_app.py` checks one allowed call and one denied call.

## Replace the sample user

`main()` builds a `UserContext` by hand. Build it from your own login session.
The user id, org id and permissions must come from your app, never from the model.
Use one `run_id` per conversation, so the run limit counts the right calls.

## Replace the database

`build()` uses `SQLiteStore("app.sqlite")`. Point it at a path your app keeps,
such as a data folder. Latch writes its operations, approvals and audit rows there.
Keep the file between restarts, so a repeated operation id is still caught.

## Add a tool

1. Write a pydantic arguments model with `extra="forbid"`.
2. Write a handler that takes `(args, info)`. Raise `ToolFailed` only when you
   know nothing happened.
3. Add an access hook if the call touches data that belongs to one user.
4. Register a `ToolSpec` in `build()`.
5. Add a `[tools.<name>]` table to `policy.toml` with `require_permission`.

Check the policy file at any time:

    boundary-latch check policy.toml

## Approvals

For a tool that should wait for a person, set `approve_permission` and an
approval mode of `always` or `over_amount`. The approver must be another user
in the same org who holds that permission. An approval is bound to the exact
request, expires, and works once.

## Operation ids

Give each action the model asks for its own operation id, and keep it with the
action. Running the same id twice returns the first result and does not call the
handler again.
