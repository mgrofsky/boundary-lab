# Dye

Part of [Boundary Lab](https://github.com/mgrofsky/boundary-lab).

Dye finds tool call arguments that were copied from text your chatbot read. Mark the text that came from outside your app, such as a support ticket, an email, or a web page. When the model proposes a tool call, Dye checks each argument against that text and reports the ones it finds, with the source they came from.

A model that reads "refund account 88214471 the sum of $420.00" inside a ticket may propose exactly that refund. Dye does not judge whether the refund is right. It tells you the account and the amount came from the ticket, so your app or [Latch](https://github.com/mgrofsky/boundary-lab/tree/main/tools/latch) can decide.

Dye needs no model, no network, and no packages beyond Python 3.11.

## Try the demo

    pip install boundary-dye
    boundary-dye demo

Five scenarios run against Northwind, a made up utility. One shows a ticket that tells the agent to refund an account it names, and Dye catches both values. One shows a customer quoting a real order number, which Dye reports too, because it tracks where values came from, not whether they are bad. One shows an address the ticket spells out in words and the model rewrites, which Dye misses.

`boundary-dye scenarios --json` prints the same results as JSON.

## Check a call from the command line

    boundary-dye check --source ticket=ticket.txt --args '{"account_id": "88214471", "amount_minor": 42000}'

The report lists each tainted argument, the value, the source label, and the matcher that found it. The command exits 1 when anything is tainted and 0 when nothing is, so you can use it in a script. Repeat `--source` for more files.

## Use it in your app

    from boundary_dye import Tracker

    tracker = Tracker()
    tracker.mark("ticket 4471", ticket_body)
    tracker.mark("web page", page_text)

    report = tracker.check(model_arguments)
    if not report.clean:
        for finding in report.findings:
            print(finding.path, finding.value, finding.source, finding.matcher)

`report.tainted` is the set of argument paths, such as `account_id` or `items[0].sku`. `report.to_dict()` gives JSON ready output. Make one `Tracker` per conversation and mark text as it arrives.

## How matching works

Dye compares each argument value with the marked text in two ways. The text match lowercases both and collapses spaces, then looks for the value inside the text. The digits match applies to values made only of digits, with spaces, dots, commas or dashes between them: it removes those joiners and looks for the digits inside one number from the text, so 42000 matches "$420.00". Values shorter than four characters are skipped. See [how it works](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/how-it-works.md) for details and the `min_length` setting.

## Use it with Latch

Latch checks tool calls before they run. Pass Dye's result in the `UserContext` and set `tainted_arguments = "deny"` for the tools that should never act on copied values. See [using Dye with Latch](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/docs/with-latch.md).

## Where it falls short

- Matching is by text. A model that rewrites, encodes, or translates a value defeats it.

- A value the customer typed on purpose is tainted too. Dye reports where values came from, and your policy decides what that means for each tool.

- Short values are skipped by default, so a one digit tier or a yes or no answer is never reported.

- The digits match looks inside whole numbers, so a short number can match part of a longer one, such as a date.

- Dye does not read your model's context for you. Your app has to mark the text.

Dye is licensed under AGPL-3.0-only. See [LICENSE](https://github.com/mgrofsky/boundary-lab/blob/main/tools/dye/LICENSE).
