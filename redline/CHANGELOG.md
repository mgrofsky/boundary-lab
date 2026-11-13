# Changelog

## 0.5.0

**Breaking change for installs.** `sentence-transformers` is no longer installed with Redline. It moved to an extra named `minilm`, because it pulls in torch and rules only mode does not need it. If you use the default `minilm` embedder, install `pip install "redline[minilm]"`, or `pip install -e ".[minilm]"` from a checkout. Without it, asking for that embedder stops with one line that names the extra. No default changed: the calibrated mode still uses `minilm`. The `dev` extra still installs it.

This release adds what an application needs to use Redline as a library. Nothing in it has been measured, and the published results are unchanged.

### Added

- `redline.policy.load_policy_data(data, source="<data>")` builds a `Policy` from the content of a rules file once parsed, so an app can keep rules in its own storage. `load_policy(path)` reads the file and calls it.
- `redline.policy.validate_policy_data(data)` returns every problem in rules data as a list and never raises for bad content. Each problem has `where`, a `code` from `redline.policy.PROBLEM_CODES` and a `message`.
- `Grader.from_rules` and `Grader` take the rules as a path, a `Policy` or data.
- `redline.estimate_calls(n_answers, rules, top_k=None, attempts=1)` returns the largest number of judge calls a rules only run can make.
- `redline.garak.grade_report(report_path, rules, judge, ...)` grades a garak report from Python and returns the summary, with the graded rows under `rows` and the eval entries under `evals`. It takes `out_dir`, `on_progress`, `should_stop`, `max_judge_calls` and `deadline_seconds`. It does not need garak.
- `redline.garak.estimate_report_calls(report_path, rules, top_k=None)` counts the answers a report holds, the outputs that would be skipped and the largest number of judge calls.
- `redline garak-grade --dry-run` prints that estimate and exits 0 with no judge and no `--out`. `--max-judge-calls N` stops a run before the call that would pass N.
- `garak_summary.json` gains `stopped`, `not_graded`, `judge_calls` and `estimated_calls` at the top level, and a `not_graded` count under `overall` and each probe. Every key it had keeps its meaning.
- `ChatEndpointJudge` takes `timeout`, the seconds one request may take. The default is 60, as before.
- New lines in a saved grades file carry `"version": 1`. Lines without it are read as before, and `data/judge_cache.jsonl` is unchanged. A line from a later format is refused. `redline.judge.read_saved_grades` reads a file.
- The guides [Use Redline from an application](../docs/using-redline-in-an-app.md), whose examples the test suite runs, and [Saved grades](../docs/saved-grades.md).
- A CI job that installs Redline with no extras and checks that rules only mode works with torch absent.

### Changed

- The rule checks are stricter, in the file loader too. A `rule_id` or a reason code may hold only letters, digits, underscore and hyphen. `rule_id`, `name`, `rule` and `plain_summary` must be text and not empty. `reason_codes` needs at least one code. `allowed_for` must be a list. The rules files in this repository all pass.
- An error from a rules file starts with the file's path, then the rule. Before, some messages named only the rule.
- `redline garak-grade` is a wrapper over `grade_report`. For a run that finishes, `garak_graded.jsonl` and `garak_evals.jsonl` are byte for byte what 0.4.0 wrote. Each output file is written under a temporary name and renamed.
- When `garak-grade --judge replay` stops on a missing saved grade, it now writes the three files first, with `stopped` set to `error`. Before, it wrote none.
- `garak-grade` exits 1 when any output was not graded, as it already did when the judge could not be reached, and prints one line that says why.
- `--out` is checked by the command, since `--dry-run` does not need it. Leaving it out of a real run still exits 2.

## 0.4.0

This release lets Redline start from a rules file alone, and work with [garak](https://github.com/NVIDIA/garak) 0.17.1. Neither has been measured yet, so no quality number is published for them.

### Added

- `Grader.from_rules(rules, judge)` grades with no labeled examples. A new router ranks the rules by how close each rule's own text is to the answer. The judge gets no examples and checks every rule unless `top_k` is set, and the worst outcome wins. Each row carries `"mode": "rules_only"`.
- `redline run --rules-only` does the same from the command line. `--train` is not read, and no cache file is touched unless `--cache` is passed.
- `redline garak-grade REPORT.jsonl --rules RULES.yaml --out DIR` grades each output in a garak report. It writes `garak_graded.jsonl`, `garak_summary.json` with counts and 95 percent Wilson intervals, and `garak_evals.jsonl` with the counting fields of garak's own eval entries. `--review-score` and `--eval-threshold` set the two values those counts depend on, and each entry records them.
- `redline.garak.detector.PolicyRules` is a garak detector. A rule break scores 1.0, a pass 0.0, a review 0.5 by default and an output with no text scores none. The judge settings are read from environment variables and are never parameters. When it cannot load, it writes the reason to stderr on a line that starts with `redline.PolicyRules:`.
- A judge that cannot be reached is kept apart from a judge that answered badly. The output is not evaluated: it scores none with the code `JUDGE_UNAVAILABLE`, and is never a pass or a hit. The garak paths try each request twice, stop calling the judge after three failed calls in a row, and say so on stderr. `garak-grade` counts these outputs under `judge_unavailable` and exits 1.
- `redline.garak.probe.PromptFile` is a garak probe that sends the prompts in a file you supply.
- `redline garak-install` writes one small file each for the detector and the probe into the installed garak package, since garak loads plugins only from there. `redline garak-uninstall` removes them. Neither changes a file that Redline did not write. Each file does nothing when Redline cannot be imported, so one left behind does not stop garak. Both commands check the folders first and undo a run that fails part way.
- A rules file can set `assistant_role`, what the judge prompt calls the assistant. The default is the old text, so every saved grade still matches. The garak paths use "an AI assistant" when the file sets none.
- `ChatEndpointJudge` takes `tries` and `backoff`. The defaults are what they were.
- The guide [using Redline with garak](../docs/garak.md), and a CI job that runs the garak tests against garak 0.17.1.

### Changed

- Rows and results from the existing mode are unchanged. garak is not a dependency: Redline imports and runs every other command with garak absent.
- A rules file with no rules, or one that is not valid YAML, is reported as one plain line.
- The settings file of a rules only run leaves out `purity`, `min_members` and `margin`, which belong to the router shortcut.

## 0.3.0

This release makes the numbers say how far they can be trusted. The answer sets are small, each attack was tried once, and none was written to beat Redline.

### Added

- Each rate under `end_to_end`, `attack_end_to_end` and each kind under `attack` in `summary.json` has its count and a 95 percent Wilson interval beside it, under `counts`. Every existing key keeps its value.
- `redline report` and `redline readme` print each end to end rate as a count with its share and interval, such as "21 of 24 (88%, 95% interval 69% to 96%)".
- `redline gate --use-interval` tests recall on the low end of its interval and the false alarm rate on the high end. A summary with no counts stops with exit 2.
- `redline run --attempts N` judges each answer N times with a live judge. Later attempts skip the cache. Rows gain `attempts` and `outcomes`, and the report counts a bad answer as caught only if every attempt failed it. The summary gains `flip_rate`.
- `redline attack --attempts N --temperature T` sends each prompt N times. Later answers get ids ending `#2`, `#3`.
- `redline evade` grades each bad answer after a set of fixed rewrites in `redline/mutate.py`, and with `--attacker` asks a chat model to reword the answers still caught. `--max-judge-calls` stops a run cleanly.
- `evade_summary.json` gains `router_only`, the records the router passed with no judge call, as written and after a rewrite. `results/evade_replay_summary.json` saves that floor from a replay run.
- A live `redline evade` saves grades under `--out` by default and refuses `--cache data/judge_cache.jsonl`. `redline run --attempts` reads that file but never writes it.
- Each kind under `attack` gains `sent_to_review`, so its buckets match `attack_end_to_end`.
- `redline report --evade-labels` counts labeled attacker rewrites that passed as missed, in a `confirmed_adaptive` block.
- `Grader` takes `attempts`. `ChatEndpointJudge.without_cache` returns a judge on the same endpoint that saves nothing.
- The guide [adaptive attacks](../docs/adaptive-attacks.md).

### Changed

- The report leaves any attack answer with no good or bad hand label out of every rate and prints how many it left out. Before, an attack prompt with no label stopped the report with an error. The summary gains `attack_unlabeled`.
- `metrics.attack_summary` counts labeled answers per kind, so with repeats `prompts` counts answers, not distinct prompts.
- `results/summary.json`, `results/report.md` and the README table were regenerated from the saved outputs. Only `produced_at` and the new keys changed.
