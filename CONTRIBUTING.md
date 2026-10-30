# Contributing

I want these tools to stay small, useful, and easy to follow. If you want to help, start here.

## Before you start

Open an issue before starting anything bigger than a typo fix. Say what you'd change and why it would help. That gives us a chance to agree on it before you write the code.

## Setting up

You need Python 3.11 or newer.

```sh
pip install -e ".[dev]"
ruff check .
pytest -q
redline readme --check
```

Those commands check Redline. Its results table lives in [Redline's README](redline/README.md). The main README introduces Boundary Lab and links to each project.

For Latch, follow the [Latch setup](tools/latch/README.md#install) and run these checks from `tools/latch`:

```sh
.venv/bin/ruff check .
.venv/bin/pytest -q
```

GitHub runs these checks for both projects. It also installs Latch in a fresh environment to check the package. All of these checks need to pass before a pull request is merged.

## What a good pull request looks like

- Keep each pull request focused. A new attack prompt, a bug fix, and a docs update are usually separate changes.
- Add tests for code changes. The existing tests show how to write them.
- Keep docs and comments plain and direct. Use short sentences. Don't use em dashes or en dashes, including in code and commit messages. The style tests check for these and for words I don't use.
- The results table in [Redline's README](redline/README.md) comes from `results/summary.json`. Don't edit the table by hand. To change its wording, edit `redline/report.py`, then run `redline readme`.
- Keep saved data and measurements in `data/` and `results/` unchanged unless the pull request is about those results. To change report wording, edit `redline/report.py` and rebuild the Markdown from the saved summary files.

## Adding attack prompts or labeled examples

Put new attack prompts in `data/attacks.jsonl`, with an ID, a kind, and the rule they try to break. Put answers you've checked and labeled in `data/train.jsonl` or `data/holdout.jsonl`. Start with [writing rules and examples](docs/writing-rules.md).

Each label needs a reason code from the rule you're checking. A label belongs to the answer you reviewed. If you collect a different answer, check and label that answer too. See [the attack examples](docs/attacks.md) for details.

If you're changing how Redline picks a rule, read [the routing guide](docs/routing.md). Save each run in its own folder so you can tell which settings produced the results. The [promptfoo setup](docs/promptfoo.md) uses the default center matching and adds its own checks.

Use test card numbers and made-up names. Keep real account data out of the examples.

## Merging

Each pull request is merged as one commit. Its title becomes the commit message, so write a plain sentence that says what changed. The branch is deleted after the merge.

## License

By contributing you agree that your work is released under the GNU Affero General Public License, version 3 only (AGPL-3.0-only), in this repository.
