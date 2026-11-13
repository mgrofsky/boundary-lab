# The saved grades file

A saved grades file holds answers a judge model gave, so a later run can read them with no judge. `ChatEndpointJudge` writes one when it has a `cache_path`. `ReplayJudge` reads one. The file `data/judge_cache.jsonl` in this repository is one, and the published results were made from it.

This page describes format version 1.

## One line per grade

The file is JSON lines: one JSON object per line. Blank lines are skipped. The order of lines does not matter. When two lines share a key, the later one is used.

```json
{"key": "5f2c...", "model": "your-judge-model", "rule_id": "CARD-01", "answer": {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "No card number is asked for.", "raw": "{...}"}, "version": 1}
```

| Field | Meaning |
| --- | --- |
| `key` | What the grade is looked up by. See below. Needed. |
| `answer` | The grade. Needed. |
| `model` | The judge model's name. It is a note for a reader and is not part of the key. A line without it reads as `unknown`. |
| `rule_id` | The rule the answer was graded against. Also a note, and the cache check in the test suite reads it. |
| `version` | The format version, 1. Lines written from Redline 0.5.0 on have it. A line without it is version 1. |

`answer` holds five fields:

| Field | Meaning |
| --- | --- |
| `verdict` | `pass` or `fail`. A reply the judge got wrong is a review, and a review is never saved. |
| `severity` | A number from 0.0 to 1.0, or null when the judge gave none. |
| `reason_code` | One of the rule's reason codes, `NOT_ALLOWED_FOR_USER`, or `OTHER` when the judge named a code the rule does not have. |
| `reason_text` | The judge's one sentence. |
| `raw` | The judge's reply as it was sent. |

## How the key is made

The key is the SHA-256 of the judge prompt, as UTF-8 text, written in hex. The prompt is built by `redline.judge.build_prompt`. A saved grade is found only when the whole prompt is the same, character for character.

These are in the prompt, so a change to any of them is a miss:

- The assistant role, from the rules file's `assistant_role` or the judge's own setting.
- The rule's `rule_id`, `name` and `rule` text.
- The rule's `allowed_for` list and its `reason_codes`, in order.
- The user type the answer was given to, and the sentence that says whether that user type is allowed.
- The examples shown to the judge: up to six good and six bad for the rule, each with its prompt and answer. Rules only mode shows none.
- The user's message and the answer being graded. A closing data tag inside either one loses its first character before the prompt is built.
- The fixed wording of the prompt itself, which can change between Redline versions.

These are not in the prompt, so they do not change the key:

- The judge model's name and address.
- The rule's `plain_summary`.
- `top_k`, the router and the embedder. They decide which rules are checked, not what is asked about each.
- The check value a live judge is asked to copy into its reply. The key is made from the prompt without it.

So the same file can be replayed after you change `top_k`. It cannot be replayed after you reword a rule, add a reason code or change the labeled examples.

## What happens on a miss

`ReplayJudge(cache_path, missing="stop")` is the default. A lookup with no saved grade raises `KeyError`, and the message holds the key. The commands turn that into one line and exit.

`ReplayJudge(cache_path, missing="review")` records a review with the reason code `NO_SAVED_GRADE` and goes on. It counts each such lookup in `missing_lookups`. The commands take this as `--missing-grade review`.

`ChatEndpointJudge` with a `cache_path` calls the judge model on a miss. It appends the grade to the file when the verdict is pass or fail. With `save=False` it reads the file and never writes to it.

## Versions

A reader that finds a `version` it does not know stops with `ValueError` and names the line. Redline 0.5.0 knows version 1 only.

The lines in `data/judge_cache.jsonl` were written before the field existed. They have no `version`, they are read as version 1, and the file has not changed.
