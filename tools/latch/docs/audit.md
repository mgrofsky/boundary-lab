# The audit

Latch writes one audit row for every decision from `prepare`, `approve`, `deny`, `cancel`, and `execute`. The rows live in the `audit` table of the SQLite database. They never hold the tool's arguments, its return value, or the handler's error text.

## Read it from the command line

    boundary-latch audit latch.sqlite
    boundary-latch audit latch.sqlite --org northwind --last 20
    boundary-latch audit latch.sqlite --follow --json

| Option | Meaning |
| --- | --- |
| `--org ORG` | Only rows for this organization |
| `--last N` | Only the newest N rows |
| `--follow` | Check the database once a second and print new rows until you press Ctrl+C |
| `--json` | Print one JSON object per line instead of one plain line per row |

The command opens the database read only, so it never changes the file. It works on a database from an older release; fields that release did not record are `null`.

## Send it somewhere as it happens

Pass `on_event` to `Latch` to get each row as soon as it is written.

    def send(event):
        log_shipper.send(event)

    latch = Latch(policy, store, on_event=send)

Latch calls `on_event` after the row is saved, with one dict. If `on_event` raises, Latch logs a warning on the `boundary_latch` logger and carries on. The decision does not change. Keep `on_event` quick, because it runs before the Latch call returns. If the row could not be saved, Latch does not call `on_event`.

## Fields

The dict from `on_event` and each line of `--json` hold the same fields.

| Field | Type | Meaning |
| --- | --- | --- |
| schema | integer | The field layout version. It is 1. |
| event | string | `prepare`, `approve`, `deny`, `cancel`, or `execute` |
| code | string | The reason code, such as `ALLOWED` or `RUN_LIMIT` |
| org_id | string | The organization |
| run_id | string or null | The run the operation belongs to |
| operation_id | string or null | The operation ID |
| user_id | string or null | Who acted: the requester, the approver, or the user who cancelled |
| tool | string or null | The tool name |
| outcome | string or null | For `execute`: `succeeded`, `failed`, `uncertain`, or `denied` |
| duration_ms | integer or null | For an `execute` that ran the handler: how long it took |
| at | number | When the row was written, in seconds since 1970 (UTC) |
| reference | string or null | The approval reference, on `approve`, `deny`, and `execute` rows |
| summary | string or null | The one line summary from `describe`, on `prepare` rows |
| copied | list of strings or null | The copied argument paths, on `prepare` rows |

The summary comes from your tool's `describe` function. Leave secrets and message text out of it, because it is saved here.

## Schema numbers

Within one schema number, fields are only added. A field is never renamed or removed, and its meaning does not change. Code that reads schema 1 keeps working when new fields appear. A change that renames or removes a field gets a new schema number.
