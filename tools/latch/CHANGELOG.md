# Changelog

## 0.3.0

### Breaking changes

Each change below lists the one line fix.

- The 0.2.0 policy key that blocked marked arguments is now `copied_arguments`. Fix: rename that key in your policy file to `copied_arguments`.
- The 0.2.0 reason code for marked arguments is now `COPIED_ARGUMENTS`. Fix: compare against `Code.COPIED_ARGUMENTS`.
- `UserContext` no longer holds the marked field paths. Fix: drop that field and pass the paths to `prepare(..., copied=paths)`.

### Added

- `copied_arguments = "approve"` makes a call with a copied value wait for approval.
