# How Dye works

Part of [Dye](../README.md).

A `Tracker` holds the text you marked for one conversation. Each `mark(label, text)` call stores the text three ways: lowercased with runs of whitespace collapsed to one space, the same text with spelled out "dot" and "at" turned into "." and "@", and the set of whole numbers in it. A whole number is a digit run after the spaces, dots, commas and dashes between digits are removed. "$420.00" becomes "42000". "4471 2201" becomes "44712201".

`trust(*values)` records values the app supplied itself, such as the signed in account. A trusted value is never reported.

`check(arguments)` walks the arguments, including nested dicts and lists, and gives each scalar a path such as `account_id`, `recipient.email` or `items[0].sku`. It skips `None`, booleans, empty strings and trusted values. Each remaining value is lowercased and compared with every source in the order the sources were marked. The first source that matches wins and the value is reported once.

- Text match: the value appears in the plain or the spelled out text as a whole term, with no letter or digit touching either end.

- Digits match: the value is only digits and joiners, and its digits equal one whole number from the text.

A `Report` holds the findings. `report.tainted` is the set of paths. `report.clean` is true when there are none. `report.to_dict()` returns `{"tainted": [...], "findings": [...]}` with the paths sorted.
