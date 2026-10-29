# Policy

The policy is one TOML file. Unknown keys are rejected, so a typo is an error and not a silent gap. Load it with `load_policy(path)`, or with `parse_policy(text)` for a string. Check a file with `boundary-latch check policy.toml`.

Latch hashes the policy contents into a digest. The digest is part of every request hash, so editing the file after a call is prepared turns that operation id into a conflict. Submit a new operation id and get a new approval. Editing any tool changes the digest for all of them.

## Limits

    [limits]
    max_actions_per_run = 20
    run_lifetime_seconds = 900
    approval_lifetime_seconds = 600

| Key | Type | Default | Range |
| --- | --- | --- | --- |
| max_actions_per_run | integer | 20 | 1 to 10000 |
| run_lifetime_seconds | integer | 900 | at least 1 |
| approval_lifetime_seconds | integer | 600 | at least 1 |

The whole `[limits]` table is optional.

## Tools

Each tool you register needs a `[tools.<name>]` table.

    [tools.send_message]
    require_permission = "messages:send"
    approve_permission = "messages:approve"
    recipient_field = "to"
    allowed_recipients = ["help@northwind.example"]
    [tools.send_message.approval]
    mode = "always"

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| require_permission | string | required | The permission the user must hold |
| approve_permission | string | none | The permission an approver must hold |
| recipient_field | string | none | The argument that holds the recipient |
| allowed_recipients | list of strings | none | The only values allowed in that argument |
| approval | table | mode never | When the call waits for an approver |

`recipient_field` and `allowed_recipients` go together. Set both or neither. A tool whose approval mode is not `never` must set `approve_permission`.

## Approval modes

- `never`: the call needs no approval. This is the default.
- `always`: every call waits for an approver.
- `over_amount`: the call waits when one argument is above a threshold.

`over_amount` needs two more keys, and no other mode accepts them.

    [tools.issue_refund.approval]
    mode = "over_amount"
    amount_field = "amount_minor"
    threshold_minor = 5000

## Minor units

Amounts are whole numbers in minor units, such as cents. A threshold of 5000 means 50.00. A call of exactly 5000 runs without approval. A call of 5001 waits. Keep money out of floats, and name the argument with a `_minor` suffix so nobody reads it as dollars.
