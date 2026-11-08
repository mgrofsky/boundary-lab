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

The report lists each tainted argument, the value, the source label, and the matcher that found it. The command exits 1 when anything is tainted and 0 when nothing is. Repeat `--source` for more files and `--trust` for more values.

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

Make one `Tracker` per conversation. Mark text as it arrives, and trust the values your app already knows, such as the signed in account, so they are never reported even when the customer repeats them. `report.tainted` is the set of argument paths, such as `account_id` or `items[0].sku`. `report.to_dict()` gives JSON ready output.

## How matching works

Dye compares each argument value with the marked text in two ways.

The text match lowercases both sides, collapses spaces, and looks for the value as a whole term, so `2` matches "tier 2" and not "22", and `ORD-553` does not match "ORD-5531". Email addresses spelled out in words are matched too: "j dot doe at example dot com" matches `j.doe@example.com`.

The digits match applies to values made only of digits, with spaces, dots, commas or dashes between them. It compares the digits with each whole number in the text, so `42000` matches "$420.00" and `1107` does not match "2026-11-07".

Values the app trusted, `None`, booleans and empty strings are skipped. See [how it works](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/how-it-works.md).

## Use it with Latch

Latch checks tool calls before they run. Pass Dye's result in the `UserContext` and set `tainted_arguments = "deny"` for the tools that should never act on copied values. See [using Dye with Latch](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/with-latch.md).

Dye is licensed under AGPL-3.0-only. See [LICENSE](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/LICENSE).
