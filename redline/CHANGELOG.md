# Changelog

## 0.4.0

This release lets Redline start from a rules file alone, and work with [garak](https://github.com/NVIDIA/garak) 0.17.1. Neither has been measured yet, so no quality number is published for them.

### Added

- `Grader.from_rules(rules, judge)` grades with no labeled examples. A new router ranks the rules by how close each rule's own text is to the answer. The judge gets no examples and checks every rule unless `top_k` is set, and the worst outcome wins. Each row carries `"mode": "rules_only"`.
- `redline run --rules-only` does the same from the command line. `--train` is not read, and no cache file is touched unless `--cache` is passed.
- `redline garak-grade REPORT.jsonl --rules RULES.yaml --out DIR` grades each output in a garak report. It writes `garak_graded.jsonl`, `garak_summary.json` with counts and 95 percent Wilson intervals, and `garak_evals.jsonl` in the shape of garak's own eval entries.
- `redline.garak.detector.PolicyRules` is a garak detector. A rule break scores 1.0, a pass 0.0, a review 0.5 by default and an output with no text scores none. The judge settings are read from environment variables and are never parameters.
- `redline.garak.probe.PromptFile` is a garak probe that sends the prompts in a file you supply.
- `redline garak-install` writes one small file each for the detector and the probe into the installed garak package, since garak loads plugins only from there. `redline garak-uninstall` removes them. Neither changes a file that is not byte for byte the one Redline writes.
- The guide [using Redline with garak](../docs/garak.md), and a CI job that runs the garak tests against garak 0.17.1.

### Changed

- Rows and results from the existing mode are unchanged. garak is not a dependency: Redline imports and runs every other command with garak absent.

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
