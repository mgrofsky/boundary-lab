## What changed

Describe the change in one or two sentences. Give the pull request a clear title; that title becomes the commit message.

## Why

Explain why this helps. Link the issue if there is one.

## Checks

- [ ] Redline's checks pass: `ruff check .`, `pytest -q`, and `redline readme --check`.
- [ ] Latch's checks pass when changed: `ruff check .` and `pytest -q` from `tools/latch` with its dependencies installed.
- [ ] Any changes to saved data or results are explained above.
