# How Dye works

Part of [Dye](../README.md).

A `Tracker` holds the text you marked for one conversation. Each `mark(label, text)` call stores the text three ways: lowercased with runs of whitespace collapsed to one space, the same text with spelled out "dot" and "at" turned into "." and "@", and the set of whole numbers in it. A whole number is a digit run after the spaces, dots, commas and dashes between digits are removed. "$420.00" becomes "42000". "4471 2201" becomes "44712201".

`mark_data(label, data)` walks a dict or list and marks each scalar as its own source with the same label and a path such as `body` or `comments[0].text`. It skips `None`, booleans and empty strings. A finding carries the matching source's path in `source_path`, which is empty for text marked with `mark`.

`trust(*values)` records values the app supplied itself, such as the signed in account. A trusted value is never reported.

`Tracker(ignore=...)` and `ignore(*patterns)` record regular expressions. A value or token whose normalised form matches one of them in full is skipped and never reported. `COMMON_IGNORE` is an opt in tuple with patterns for cloud regions and ISO dates.

`Tracker(max_sources=n)` holds at most n sources. When a new mark would go past n, the oldest source and its stored forms are dropped. `forget(source)` drops one source. `to_dict()` returns `{"schema": 1, "sources": [...], "trusted": [...], "ignore": [...], "max_sources": n}`, where each source has its label, text and path. `Tracker.from_dict(data)` rebuilds the tracker by marking each source again, so the stored forms are never saved. An app can store the dict beside the conversation and restore it after a restart.

`check(arguments)` walks the arguments, including nested dicts and lists, and gives each scalar a path such as `account_id`, `recipient.email` or `items[0].sku`. It skips `None`, booleans, empty strings, trusted values and ignored values. Each remaining value is lowercased and compared with every source in the order the sources were marked. The first source that matches wins and the value is reported once.

- Text match: the value appears in the plain or the spelled out text as a whole term, with no letter or digit touching either end.

- Digits match: the value is only digits and joiners, and its digits equal one whole number from the text.

- Token match: tried only when the whole value does not match. The value is split on runs of whitespace and these characters: `, = : ; / ? & ( ) [ ] { } < >`, both quote marks and the backtick. Dashes and dots do not split. Each piece that has at least four characters and holds a digit or an @ is tested with the text and digits matches. Pieces equal to the whole value, trusted pieces and ignored pieces are skipped. The first piece that matches is reported with the matcher `token`, and the finding's value is that piece.

Each path gets at most one finding.

A `Report` holds the findings. `report.copied` is the set of copied argument paths. `report.clean` is true when there are none. `report.to_dict()` returns `{"copied": [...], "findings": [...]}` with the paths sorted.
