# My Latch app

This small app has one tool, `create_ticket`. Latch checks every call before
running it. Copy the app, then replace the sample user, database path, and tool
with your own.

## Run it

    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/python app.py
    .venv/bin/pytest -q

`app.py` prints `EXECUTED` and the new ticket. The tests print `2 passed`.

## What is in here

- `policy.toml` says who may call each tool and when a call needs approval.
- `app.py` defines the tool's accepted arguments and its handler, the function
  that does the work. Its `build()` function registers the tool with Latch.
- `tests/test_app.py` checks one allowed call and one denied call.

## Replace the sample user

`main()` builds a sample `UserContext` by hand. In your app, build it from the
user's login session. The user id, organization id, permissions, and `run_id`
must come from your app, never from the model. Use one `run_id` per conversation
so its calls count toward the same limit.

## Replace the database

`build()` uses `SQLiteStore("app.sqlite")`. Use a file path your app keeps, such
as a data folder. Latch stores requested actions, approvals, and decision history
there. Keep the file between restarts so Latch can still catch repeated operation IDs.

## Add a tool

1. Write a pydantic model for the accepted arguments, with `extra="forbid"` to
   reject extra fields.
2. Write a handler that takes `(args, info)`. Raise `ToolFailed` only when you
   know nothing happened.
3. Add an `access_hook` if the call uses data that belongs to one user. This
   function checks whether the user can access that data.
4. Register a `ToolSpec` in `build()`.
5. Add a `[tools.<name>]` table to `policy.toml` with `require_permission`.

Check the policy file at any time:

    boundary-latch check policy.toml

## Approvals

For a tool that should wait for a person, set `approve_permission` and an
approval mode of `always` or `over_amount`. The approver must be another user
in the same organization who has that permission. An approval covers one exact
request, expires, and works once.

## Operation ids

Give each requested action its own operation ID, and keep that id with the
action. Repeating the same request with the same id in the same run returns the
recorded outcome without calling the handler again. The original return value
is not stored. Changing the request or run gives `OPERATION_CONFLICT`.
