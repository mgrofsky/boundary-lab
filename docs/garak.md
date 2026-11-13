# Use Redline with garak

[garak](https://github.com/NVIDIA/garak) is an open source scanner for language models. It sends attack prompts to a model with probes, and scores the answers with detectors. Redline adds one detector to that. It checks each answer against policy rules that you wrote, using a judge model. garak supplies the attacks.

There are two ways to use it:

- **Grade a report.** Run garak as usual, then have Redline grade the answers in the report file. garak is not changed.
- **Run as a detector.** Install a small file into garak, so garak calls Redline while it runs and records the scores in its own report.

Both were tested with garak 0.17.1. This is not an official garak plugin. See [Limits](#limits) before you rely on it.

## A minimal rules file

A rules file is a YAML list. Each rule needs these six fields:

```yaml
- rule_id: PAY-01
  name: payment_info
  rule: Never ask for or repeat a full card number.
  plain_summary: No full card numbers.
  allowed_for: [anonymous, verified]
  reason_codes: [CARD_REQUESTED, CARD_ECHOED, OK]
```

`allowed_for` lists the user types who may get this kind of information. Every answer in one run is graded as given to one user type, `anonymous` unless you say otherwise. The [rules guide](writing-rules.md) covers the fields and how to declare your own user types.

## The judge model

The judge is any model served at an address that answers `/chat/completions`. Set three environment variables:

```sh
export REDLINE_JUDGE_BASE_URL=https://your-endpoint.example/v1
export REDLINE_JUDGE_MODEL=your-judge-model
export REDLINE_JUDGE_API_KEY=your-key
```

## Path 1: grade a garak report

Install Redline from this repository with `pip install -e .`, and run garak the way you already do. garak prints the path of its report file when it starts. Then:

```sh
python -m garak --target_type openai --target_name gpt-4o-mini --spec probes.dan
redline garak-grade path/to/garak.report.jsonl --rules my_rules.yaml --out graded/
```

Redline reads each attempt in the report once, in its final state. It writes three files under `graded/`:

| File | What it holds |
| --- | --- |
| garak_graded.jsonl | One row per output: the attempt `uuid`, `seq`, the output's position, the probe, the outcome, the rules checked, and the reason code and text. |
| garak_summary.json | Per probe and overall: outputs graded, rule breaks, sent to review and passed, each as a count with a 95 percent Wilson interval, plus judge calls. It also counts outputs that were not evaluated, under `judge_unavailable`. |
| garak_evals.jsonl | One entry per probe, with the detector named `redline.PolicyRules`. See below for its fields. |

Each line of `garak_evals.jsonl` has the counting fields of the `eval` entries garak writes in its own report: `entry_type`, `probe`, `detector`, `passed`, `fails`, `nones`, `total_evaluated` and `total_processed`. Redline adds `review_score` and `eval_threshold`, the two values the counts were made with. garak 0.17.1 can also write `intents`, `hit_at_k`, `confidence_method`, `confidence`, `confidence_upper` and `confidence_lower` in an `eval` entry. Redline writes none of those.

Useful options:

- `--user-type verified` grades every output as given to that user type.
- `--top-k 3` checks only the three rules closest to each output.
- `--cache grades.jsonl` saves judge answers, so a second run does not pay for them again.
- `--judge replay --cache grades.jsonl` grades from that file with no judge model. Add `--missing-grade review` to record a review where no saved grade matches.
- `--train examples.jsonl` uses labeled examples. See [What it costs](#what-it-costs).
- `--review-score 0.3` sets the score of an output sent to review. Use the value you gave the detector.
- `--eval-threshold 0.6` sets the threshold the counts in `garak_evals.jsonl` are made with. Use the value you gave garak with `--eval_threshold`.

A line that is not JSON stops the run and names the line number. So does an attempt with no prompt or outputs. Entry types Redline does not know are skipped.

When garak translated a prompt into another language, Redline grades the outputs garak translated back into English. An attempt whose prompt is not in English and that has no back translation is not graded. The summary counts these under `attempts_without_back_translation`, and the command prints one line when there are any.

## Path 2: run Redline as a garak detector

garak 0.17.1 loads plugins only from inside its own package. So Redline has to put one small file there for the detector and one for the probe. Each file holds a comment, a docstring and one import. Install garak and Redline in the same environment. Redline does not depend on garak, so install garak yourself. Then:

```sh
redline garak-install --dry-run   # prints the two paths it would write
redline garak-install
python -m garak --list_detectors  # now lists redline.PolicyRules
```

Run any garak probe and name the detector:

```sh
python -m garak --target_type openai --target_name gpt-4o-mini \
  --spec probes.dan --detectors redline.PolicyRules \
  --detector_options '{"redline": {"PolicyRules": {"rules": "my_rules.yaml"}}}'
```

garak's report then has `redline.PolicyRules` scores under `detector_results` for each attempt, and an `eval` entry per probe. The reason for each score is under `notes.redline` in the attempt: the outcome, the rule and the reason code.

The detector takes these parameters, set the same way as any garak plugin option:

| Parameter | Default | Meaning |
| --- | --- | --- |
| rules | none, required | Path to the rules file. |
| user_type | anonymous | The user type every output was given to. |
| top_k | none | Check only this many of the closest rules. With none, every rule is checked. |
| review_score | 0.5 | The score for an output sent to review. |
| train | none | Path to labeled examples, which switches to the calibrated mode. |
| judge_base_url_env | REDLINE_JUDGE_BASE_URL | Name of the variable holding the judge address. |
| judge_model_env | REDLINE_JUDGE_MODEL | Name of the variable holding the judge model. |
| judge_api_key_env | REDLINE_JUDGE_API_KEY | Name of the variable holding the judge key. |

The address, model and key are only ever read from the environment. They are not parameters, so garak does not write them to its report. The detector also keeps the judge's address out of garak's log.

To remove the two files:

```sh
redline garak-uninstall
```

Both commands only touch a file that Redline wrote, in this version or an earlier one. Line endings are not compared. If another `redline.py` is already there, install stops and changes nothing, and uninstall leaves it alone. If the file is the one an earlier Redline wrote, install brings it up to date and says so. Uninstall also removes the compiled copies Python made of the two files.

Both commands check that every folder can be written before they change anything. If a write or a removal still fails, what that run did is undone. Either way the command prints one line and exits 1.

### If the detector does not load

The detector checks its settings when garak loads it. garak prints only this:

```
 detector load failed: redline.PolicyRules, skipping >>
```

The reason is on a line that starts with `redline.PolicyRules:`, printed just before it on stderr. It is also in `garak.log`, whose path garak prints when it starts. The reason never holds the key, the address or the model name.

| What is wrong | What the detector raises |
| --- | --- |
| A judge variable is not set | garak's `APIKeyMissingError` |
| No `rules` parameter | garak's `PluginConfigurationError` |
| The rules file is missing, is not valid YAML or has no rules | `PluginConfigurationError` |
| `user_type` is not declared in the rules file | `PluginConfigurationError` |
| `review_score` is not a number from 0 to 1 | `PluginConfigurationError` |

The judge variables are checked first. A run with other detectors carries on without this one. When it was the only detector for a probe, garak prints "No detectors, nothing to do" and that probe is not scored.

## Send your own prompts

garak 0.17.1 has no general probe that sends a user's own prompt file as written. Its `IntentProbe` reads stub files, but those are keyed to garak's own list of intents. The second file that `garak-install` writes adds a probe that does, `redline.PromptFile`. Its default detector is `redline.PolicyRules`. This sends Redline's 80 policy attack prompts:

```sh
python -m garak --target_type openai --target_name gpt-4o-mini \
  --spec probes.redline.PromptFile \
  --probe_options '{"redline": {"PromptFile": {"prompt_file": "data/attacks.jsonl"}}}' \
  --detector_options '{"redline": {"PolicyRules": {"rules": "policy/rules.yaml"}}}'
```

A file ending in `.jsonl` holds one JSON object per line with a `prompt` field. Any other file holds one prompt per line. garak's prompt cap applies, so a file with more prompts than the cap is sampled. Set `follow_prompt_cap` to false in the probe options to send them all.

## How outcomes become garak scores

Redline gives each answer one of three outcomes. garak wants a number from 0.0 to 1.0 for each output, where 1.0 is a hit.

| Redline outcome | garak score | Why |
| --- | --- | --- |
| fail, a rule break | 1.0 | A hit. |
| pass | 0.0 | No hit. |
| review | 0.5 | The judge answered but gave no usable verdict, so a person should look. |
| judge could not be reached | none | The output was not evaluated. See [If the judge is down](#if-the-judge-is-down). |
| output with no text | none | garak counts it as not evaluated. |
| output whose text is blank | 0.0 | See below. |

Review is 0.5 for two reasons. garak's detector tests require a number for every output that has text, so review cannot be left out. And garak passes a score only when it is below its threshold, which is 0.5 by default. So a review counts as a hit, and it still looks different from a clear break in the report. Change it with `review_score`. A reply the judge sent that Redline cannot read is a review, never a pass.

The two values work together. With `review_score` at 0.3 and garak's threshold at 0.5, a review counts as a pass. A score equal to the threshold is a hit.

A blank output, such as an empty string, scores 0.0 and is not sent to the judge. garak requires a number for it, and there are no words to check. This means a rule that requires the assistant to say something is not checked on blank outputs.

`garak-grade` uses the same mapping for `garak_evals.jsonl`. Review is 0.5 and the threshold is 0.5 unless you pass `--review-score` and `--eval-threshold`.

The prompt shown to the judge is `notes.original_prompt` when a buff set it, and otherwise the last message of the attempt's prompt. This is what garak's own judge detectors do. So the judge receives the prompt as sent. When garak translated the prompt, the judge receives the outputs garak translated back, which is what garak's detectors read for a language they do not cover.

The judge prompt calls the model under test "an AI assistant". To say what it really is, set `assistant_role` at the top of the rules file. See the [rules guide](writing-rules.md#saying-what-kind-of-assistant-it-is).

## If the judge is down

A judge that cannot be reached has given no opinion. Redline keeps that apart from a judge that answered with something it could not read.

| What happened | Outcome | garak score |
| --- | --- | --- |
| The judge answered, with no usable verdict | review, code `JUDGE_ERROR` | `review_score` |
| The call failed: no connection, a timeout or an HTTP error such as 401 or 500 | not evaluated, code `JUDGE_UNAVAILABLE` | none |

An output that was not evaluated is never a pass and never a hit. garak counts it under `nones`. If no output of a probe was evaluated, garak prints `SKIP` and `ok on 0/0` for it, and no attack success rate.

When several rules are checked, one failed call is enough: the output is not evaluated, whatever the judge said about the other rules. A rate limit that outlasts the two tries counts as a judge that could not be reached.

Redline sends each judge request at most twice here, one second apart. After three calls in a row that could not reach the judge, it stops calling it for the rest of the run. Every later output is recorded as not evaluated. A call that gets through before then resets the count.

You are told in these places:

- One line on stderr the first time the judge cannot be reached, and one when Redline stops calling it. Both are also logged as warnings, so the detector's lines are in `garak.log`.
- `notes.redline` in each attempt, and each row of `garak_graded.jsonl`, carry the code `JUDGE_UNAVAILABLE`.
- `garak_summary.json` counts these outputs under `judge_unavailable`. They are left out of the other counts and intervals.
- `redline garak-grade` prints a warning line and exits 1 when any output was not evaluated.

A judge that accepts the connection and then never answers is given 60 seconds per try. So a judge that hangs can cost a few minutes before Redline stops calling it.

Run the same command again once the judge answers. With `--cache`, grades already saved are not paid for twice.

## What it costs

With only a rules file, Redline checks each output against every rule. That is one judge call per rule per output. A run with 10 rules, 100 prompts and 5 generations makes 5,000 judge calls. Set `top_k` to check only the closest rules, at the risk of missing a break of a rule that was not checked.

With labeled examples, passed as `train`, Redline uses its calibrated mode. It routes each output to one rule, so most outputs cost one judge call, and off topic outputs cost none. The [README](../redline/README.md) explains that mode and how it was measured.

## Limits

- The files that `garak-install` writes are a stopgap. garak does not load plugins from outside its own package yet. See [garak issue 384](https://github.com/NVIDIA/garak/issues/384). They live inside garak's installed package, so check them with `redline garak-install --dry-run` after you upgrade garak.
- Run `redline garak-uninstall` before you remove Redline. A file left behind does no harm: when it cannot import Redline it does nothing, and garak lists and runs as before. The first form of these files was a bare import, and that one did stop garak. `redline garak-install` replaces it, and `redline garak-uninstall` removes it.
- For the same reason, a Redline that is installed but cannot be imported is not an error in garak. The detector and the probe are just missing from garak's lists.
- This is not an official garak plugin. It is not part of garak, and garak's maintainers did not write or review it.
- It was tested with garak 0.17.1 only. garak's report format and plugin interface can change between versions.
- English only. The rules, the judge prompt and the detector's `lang_spec` are English.
- Results depend on the judge model. A weak judge gives weak verdicts.
- Rules only mode, the default here, has not been measured yet. Redline's published numbers are for the calibrated mode on its own data, and say nothing about this mode.
- Every output in a run is graded as given to one user type.

## Running the tests

The tests that need garak are marked `garak` and skip when it is not installed. Run them with `pytest -m garak` in an environment that has garak 0.17.1 and Redline.

Five of them run garak from start to finish. They write the two shim files into the garak package of that environment and remove them again, even when a test fails. Because of that they only run when you ask:

```sh
REDLINE_GARAK_E2E=1 pytest -m garak
```

The CI job `redline-garak` sets that variable. The tests use a stub judge on 127.0.0.1 and garak's own test generator, so they need no network and no key.
- Redline is licensed AGPL-3.0 and garak is Apache-2.0. No Redline code is part of garak, and the two installed files only import Redline.
