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

- The assistant role, from the rules' `assistant_role` or, when they set none, the judge's own setting. A `Grader` passes its role with each call, so two tenants with different roles can share one judge and one file.
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

`ReplayJudge(cache_path, missing="stop")` is the default. A lookup with no saved grade raises `redline.judge.MissingSavedGrade`, which is a `KeyError`, and the message holds the key. `redline garak-grade` turns that into one line and exits.

`ReplayJudge(cache_path, missing="review")` records a review with the reason code `NO_SAVED_GRADE` and goes on. It counts each such lookup in `missing_lookups`. The commands take this as `--missing-grade review`.

`ChatEndpointJudge` with a `cache_path` calls the judge model on a miss. It appends the grade to the file when the verdict is pass or fail. With `save=False` it reads the file and never writes to it.

## A line that cannot be read

`redline.judge.read_saved_grades(path)` reads a file, and both judges use it. A line it cannot use raises `ValueError`, and the message names the file and the line number. These are the cases:

- The line is not valid JSON. A last line cut short by a crash reads this way. Remove that line and the rest of the file is good.
- The line is not a JSON object, or it has no `key` or no `answer`, or `answer` is not an object.
- `version` is not a whole number. The text `"1"` is refused, and the message says the number 1 is needed.
- `version` is a number this Redline does not know.

## Versions

Redline 0.5.0 knows version 1 only. A line with a later version is refused, as above.

The lines in `data/judge_cache.jsonl` were written before the field existed. They have no `version`, they are read as version 1, and the file has not changed.

Redline 0.4.0 can read a file that 0.5.0 wrote. Its reader takes `key` and `answer` from each line and skips any field it does not know, so the new `version` field does not trouble it. A test in this repository reads a new file with a copy of the 0.4.0 reading loop. It was also checked by hand: a grade saved by 0.5.0 was replayed by the 0.4.0 code itself, with `ReplayJudge` and with `ChatEndpointJudge`, and gave the same row.
