# Dye

Part of [Boundary Lab](https://github.com/mgrofsky/boundary-lab).

Dye finds tool call arguments that were copied from text your chatbot read. Mark the text that came from outside your app, such as a support ticket, an email, or a web page. When the model proposes a tool call, Dye checks each argument against that text and reports the ones it finds, with the source they came from.

A model that reads "refund account 88214471 the sum of $420.00" inside a ticket may propose exactly that refund. Dye tells you the account and the amount came from the ticket, so your app or [Latch](https://github.com/mgrofsky/boundary-lab/tree/main/tools/latch) can decide what happens next.

Dye needs no model, no network, and no packages beyond Python 3.11.

## Try the demo

    pip install boundary-dye
    boundary-dye demo

Five scenarios run against Northwind, a made up utility. They cover a ticket that tells the agent to refund an account it names, a signed in account the app trusts, a quoted order number, an email address spelled out in words, and a one digit plan tier.

`boundary-dye scenarios --json` prints the same results as JSON.

## Check a call from the command line

    boundary-dye check --source ticket=ticket.txt --trust NW-100482 --args '{"account_id": "88214471", "amount_minor": 42000}'

Pass `-` as the path to read the text from stdin. Here the account id inside the role ARN is reported with the matcher `token`.

    echo "please use account 111122223333" | boundary-dye check --source ticket=- --args '{"role": "arn:aws:iam::111122223333:role/admin"}'

`--source-json ticket=ticket.json` marks a JSON file value by value, so each finding names the field it came from. `--ignore PATTERN` skips values that match a regular expression, such as a region name.

The report lists each copied argument, the value, the source label, and the matcher that found it. A copied argument is one whose value appears in text the model read. The command exits 1 when anything was copied and 0 when nothing was. Repeat `--source`, `--source-json`, `--trust` and `--ignore` as needed.

## Use it in your app

    from boundary_dye import Tracker

    tracker = Tracker()
    tracker.mark("ticket 4471", ticket_body)
    tracker.mark("web page", page_text)
    tracker.trust(session.account_id)

    report = tracker.check(model_arguments)
    if not report.clean:
        for finding in report.findings:
            print(finding.path, finding.value, finding.source, finding.matcher)

Make one `Tracker` per conversation. Mark text as it arrives, and trust the values your app already knows, such as the signed in account, so they are never reported even when the customer repeats them. `report.copied` is the set of copied argument paths, such as `account_id` or `items[0].sku`. `report.to_dict()` gives JSON ready output.

When the untrusted input is structured, such as a ticket fetched as JSON, mark it with `mark_data`. Each string or number inside it is marked with its path, and a finding names that path in `source_path`. A report can then say that `account_id` was copied from ticket 4471 at `body`.

    tracker.mark_data("ticket 4471", {"subject": subject, "body": body})

## Keep a tracker across restarts

A long conversation can outlive the process that started it. `tracker.to_dict()` returns the marked sources, the trusted values, the ignore patterns and the source limit as JSON ready data. Store it beside the conversation and rebuild the tracker with `Tracker.from_dict(data)` after a restart.

    saved = json.dumps(tracker.to_dict())
    tracker = Tracker.from_dict(json.loads(saved))

`Tracker(max_sources=200)` keeps memory bounded. When a new mark would go past the limit, the oldest source is dropped. `tracker.forget(source)` drops one source, such as a ticket the customer withdrew.

## How matching works

Dye compares each argument value with the marked text in three ways.

The text match removes zero width characters, lowercases both sides, collapses spaces, and looks for the value as a whole term, so `2` matches "tier 2" and not "22", and `ORD-553` does not match "ORD-5531". Email addresses spelled out in words are matched too: "j dot doe at example dot com" matches `j.doe@example.com`.

The digits match applies to values made only of digits, with spaces, dots, commas or dashes between them. It compares the digits with each whole number in the text, so `42000` matches "$420.00" and `1107` does not match "2026-11-07". Digits spelled out as words count too, so `420` matches "four two zero".

The token match looks inside composite values. When the whole value does not match, Dye splits it on spaces and punctuation such as `: / , = ? &` and tests each piece. A piece is tested when it has at least four characters and holds a digit or an @. Dashes and dots do not split, so `i-0abc1234` and `10.0.0.0` stay whole. An instance id copied into an ARN, a network copied into a CIDR, or an email inside a URL is reported with the matcher `token` and the piece as the value.

## Words that are never evidence

Some values show up in a ticket and in a tool call because they are common, not because the model copied them. A ticket that says "our servers in us-east-2 are down" and a call that names `us-east-2` share a value, but the region was going to be in the call anyway. There are only a few dozen regions, and the app or the model would pick the same one without reading the ticket. Reporting it adds noise and hides the findings that matter. Dates are the same.

Pass regular expressions to skip such values. Each pattern must match the whole normalised value or token, which is lowercase.

    from boundary_dye import COMMON_IGNORE, Tracker

    tracker = Tracker(ignore=COMMON_IGNORE)
    tracker.ignore(r"ord-\d+")

`COMMON_IGNORE` holds two patterns: cloud regions shaped like `us-east-2` and ISO dates shaped like `2026-11-07`. It is opt in, so a plain `Tracker()` still reports them.

Values the app trusted, `None`, booleans and empty strings are skipped. See [how it works](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/how-it-works.md).

## Use it with Latch

Latch checks tool calls before they run. Pass Dye's report to `prepare` and set `copied_arguments = "deny"` for the tools that should never act on copied values. See [using Dye with Latch](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/with-latch.md).

See [Release](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/release.md) for how a version gets published.

Dye is licensed under AGPL-3.0-only. See [LICENSE](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/LICENSE).
