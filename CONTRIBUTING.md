# Contributing

Thanks for taking a look. This project is small on purpose, and I want to keep it that way. Here is how to help without wasting your time or mine.

## Before you start

Open an issue first for anything bigger than a typo. Say what you want to change and why. That way we agree on the idea before you write the code.

## Setting up

You need Python 3.11 or newer.

```sh
pip install -e ".[dev]"
ruff check .
pytest -q
redline readme --check
```

Those three checks are what CI runs. A pull request has to pass all of them.

## What a good pull request looks like

- One change per pull request. A new attack prompt, a bug fix, a doc fix. Not all three.
- Tests for code changes. The tests folder shows the style.
- Plain writing in docs and comments. Short sentences. No em dashes or en dashes anywhere, including code and commit messages. There is a style test that checks for them and for a list of words I do not use. Run pytest and it will tell you.
- Do not edit the results table in the README by hand. It is generated from results/summary.json and CI checks that they match.
- Do not change saved data under data/ or results/ unless the pull request is about the data. The published numbers come from those files.

## Adding attack prompts or labeled examples

New attack prompts go in data/attacks.jsonl with an id, a kind and the rule they go after. New labeled answers go in data/train.jsonl or data/holdout.jsonl. Read docs/writing-rules.md first. Any answer you label by hand needs a reason code from the rule it is labeled against.

Use test card numbers, made up names and no real account data in any example. The whole scenario is fictional and has to stay that way.

## Merging

Pull requests are squash merged, so the pull request title becomes the commit message. Write it as a plain sentence that says what changed. Branches are deleted after merge.

## License

By contributing you agree that your work is released under the GNU Affero General Public License, version 3 only (AGPL-3.0-only), in this repository.
