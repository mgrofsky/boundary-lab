# Changelog

## 0.2.0

### Breaking changes

- `Report.tainted` is now `Report.copied`. The old name appears in this file only, so a search for it finds the fix. The key in `Report.to_dict()` and in the scenario JSON is now `"copied"`. The CLI output says copied too. A copied value is a value copied from text the model read.
- Each finding in `Report.to_dict()` and the CLI JSON now has a `source_path` key. It is empty unless the source was marked with `mark_data`.

### New

- `Tracker.mark_data(label, data)` marks each scalar in a dict or list with its path. `Source.path` and `Finding.source_path` record where a value sat.
- The token matcher finds a value copied into a longer composite value, such as an id inside an ARN. It reports the matcher `token`.
- `Tracker(ignore=...)`, `Tracker.ignore(*patterns)` and `COMMON_IGNORE` skip values that are never evidence, such as cloud regions and ISO dates.
- `Tracker(max_sources=...)` drops the oldest source past a limit, and `Tracker.forget(source)` drops one source.
- `Tracker.to_dict()` and `Tracker.from_dict(data)` save and restore a tracker, with a `"schema": 1` key.
- Zero width spaces, joiners and other Unicode format characters are removed before matching.
- Runs of two or more digit words, such as "four two zero", are read as digits by the digits matcher.
- `boundary-dye check` reads a source from stdin with `--source label=-`, marks a JSON file with `--source-json label=file.json`, and takes `--ignore PATTERN` as often as needed.
- Two Northwind scenarios: a meter id copied into a longer resource name, and a region name skipped with `COMMON_IGNORE`. The scenario JSON gains an `"ignore"` key.
