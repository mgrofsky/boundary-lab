# Changelog

## 0.2.0

### Breaking changes

- The set of copied argument paths is now `Report.copied`, and the 0.1.0 name for it is gone. The key in `Report.to_dict()` and in the scenario JSON is now `"copied"`. The CLI output says copied too. A copied value is a value copied from text the model read.
