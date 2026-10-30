# Policy

The policy is one TOML file. It sets the rules for each tool. Latch rejects unknown keys, so a misspelled key causes an error. Load the file with `load_policy(path)`, or pass its text to `parse_policy(text)`. Check a file with `boundary-latch check policy.toml`.

Latch makes a fingerprint of the loaded policy settings, called a digest. It includes that fingerprint when it records each request. If you change the settings and load the new policy, a pending request prepared under the old policy returns `OPERATION_CONFLICT`. Check what happened to the old request, then submit a new operation ID and get a new approval if needed. Changing one tool's policy changes the fingerprint for all tools. Comments and formatting do not change it.

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
| require_permission | string | required | The permission the user must have |
| approve_permission | string | none | The permission an approver must have |
| recipient_field | string | none | The argument that holds the recipient |
| allowed_recipients | list of strings | none | The only values allowed in that argument |
| approval | table | mode never | When the call waits for approval |

`recipient_field` and `allowed_recipients` go together. Set both or neither. A tool whose approval mode is not `never` must set `approve_permission`.

## Approval modes

- `never`: the call needs no approval. This is the default.
- `always`: every call waits for an approver.
- `over_amount`: the call waits when an amount is above the limit you set.

`over_amount` needs two more keys, and no other mode accepts them.

    [tools.issue_refund.approval]
    mode = "over_amount"
    amount_field = "amount_minor"
    threshold_minor = 5000

## Minor units

Amounts use whole numbers in the currency's smallest unit, called a minor unit. For dollars, that means cents. A `threshold_minor` of 5000 means $50.00: exactly 5000 needs no approval, while 5001 does. Use integers for money instead of floating-point numbers, and end the argument name with `_minor` so readers know how to interpret it.
