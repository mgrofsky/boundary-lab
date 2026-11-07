# How Dye works

Part of [Dye](../README.md).

A `Tracker` holds the text you marked for one conversation. Each `mark(label, text)` call stores the text two ways: lowercased with runs of whitespace collapsed to one space, and as a list of digit runs. A digit run is a group of digits after the spaces, dots, commas and dashes between digits are removed. "$420.00" becomes the run "42000". "4471 2201" becomes "44712201".

`check(arguments)` walks the arguments, including nested dicts and lists, and gives each scalar a path such as `account_id`, `recipient.email` or `items[0].sku`. It skips `None` and booleans. It turns each remaining value into text the same way, and skips values shorter than `min_length` characters, four by default.

For each value, the sources are tried in the order they were marked. The first source that matches wins, and the value is reported once.

- Text match: the normalized value is a substring of the normalized source.

- Digits match: the value is only digits and joiners, and its digits are a substring of one digit run from the source. This is how 42000 in minor units matches "$420.00" in a ticket.

A `Report` holds the findings. `report.tainted` is the set of paths. `report.clean` is true when there are none. `report.to_dict()` returns `{"tainted": [...], "findings": [...]}` with the paths sorted.

## Settings

`Tracker(min_length=4)` sets the shortest value Dye will look for. Lower it to track short codes and expect more false alarms. There are no other settings.

## What it does not do

Dye does not detect instructions hidden in text. It does not rewrite or block anything. It does not know which values are legitimate. It answers one question: did this argument value appear in text the app did not trust?
