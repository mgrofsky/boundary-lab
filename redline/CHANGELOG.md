# Changelog

## 0.3.0

This release makes the numbers say how far they can be trusted. The answer sets are small, each attack was tried once, and none was written to beat Redline.

### Added

- Each rate under `end_to_end`, `attack_end_to_end` and each kind under `attack` in `summary.json` has its count and a 95 percent Wilson interval beside it, under `counts`. Every existing key keeps its value.
- `redline report` and `redline readme` print each end to end rate as a count with its share and interval, such as "21 of 24 (88%, 95% interval 69% to 96%)".
- `redline gate --use-interval` tests recall on the low end of its interval and the false alarm rate on the high end. A summary with no counts stops with exit 2.
- `redline run --attempts N` judges each answer N times with a live judge. Later attempts skip the cache. Rows gain `attempts` and `outcomes`, and the report counts a bad answer as caught only if every attempt failed it. The summary gains `flip_rate`.
- `redline attack --attempts N --temperature T` sends each prompt N times. Later answers get ids ending `#2`, `#3`.
- `redline evade` grades each bad answer after a set of fixed rewrites in `redline/mutate.py`, and with `--attacker` asks a chat model to reword the answers still caught. `--max-judge-calls` stops a run cleanly.
- `redline report --evade-labels` counts labeled attacker rewrites that passed as missed, in a `confirmed_adaptive` block.
- `Grader` takes `attempts`. `ChatEndpointJudge.without_cache` returns a judge on the same endpoint that saves nothing.
- The guide [adaptive attacks](../docs/adaptive-attacks.md).

### Changed

- The report leaves any attack answer with no good or bad hand label out of every rate and prints how many it left out. Before, an attack prompt with no label stopped the report with an error. The summary gains `attack_unlabeled`.
- `metrics.attack_summary` counts labeled answers per kind, so with repeats `prompts` counts answers, not distinct prompts.
- `results/summary.json`, `results/report.md` and the README table were regenerated from the saved outputs. Only `produced_at` and the new keys changed.
