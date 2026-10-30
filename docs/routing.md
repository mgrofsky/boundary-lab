# Choosing how answers find a rule

The router decides which rule an answer gets checked against. Start with the default, then try another setting on the same answers and see what changes. You can do that with the saved examples before connecting a chatbot.

## Center matching

`--router center` is the default. Redline turns each prompt and answer into numbers used to compare text. It averages the examples for each rule into one center, also called a centroid. There's a center for off-topic examples too.

A new answer goes to the closest center. Good and bad examples both help pick the rule. Being close to a rule doesn't tell you whether the answer follows it, so the judge normally makes that decision.

## K-means clusters

`--router kmeans --k 12` sorts similar examples into 12 groups, called clusters. Each cluster gets the rule most of its examples belong to. More than one cluster can have the same rule, and a cluster can contain examples from different rules.

A new answer goes to the closest cluster center. That gives a rule room to appear in several groups, but it doesn't guarantee a better match. In the saved runs, both 4 and 12 clusters did worse than the default on overall accuracy.

For either router, an answer too far from every center gets `review`. A match to `none`, the off-topic label, gets `pass` without asking the judge. Redline records reviews for you to check; it doesn't assign them to anyone.

## Letting the router make a decision

Add `--short-circuit` to let the router pass or fail some answers without the judge. This shortcut is off by default, and each router uses it differently.

With **center matching**, Redline also builds separate centers for good and bad examples under each rule and user type. Both groups need at least 5 examples by default. The answer must be close enough to the winning group and closer to it by at least the `--margin` setting. Otherwise, it goes to the judge.

With **k-means**, Redline counts good and bad examples for the user's type in the matched cluster. By default, it needs at least 5 examples, all with the same label. Those counts include every rule represented in the cluster. A mix of labels goes to the judge unless you lower `--purity`.

Even when the examples agree, the shortcut can get a new answer wrong. Check its wrong decisions as well as how often it skips the judge.

| Setting | Default | What it changes |
| --- | --- | --- |
| `--router` | `center` | Choose `center` or `kmeans`. |
| `--k` | `12` | Number of clusters for k-means. It must be positive and no larger than the number of training examples. |
| `--seed` | `0` | Starting choice for k-means. Keep it the same when repeating a run. |
| `--short-circuit` | Off | Let the router decide some answers without the judge. |
| `--min-members` | `5` | For center matching, examples needed in each good and bad group. For k-means, total examples for that user type in the matched cluster. |
| `--margin` | `0.10` | Center matching only. Minimum gap between the good and bad match scores. A larger gap makes the shortcut harder to use. |
| `--purity` | `1.0` | K-means only. Share of examples for that user type that must agree. `1.0` means all; `0.9` means at least 90 percent. A tie never decides. |

The last three settings only matter when the shortcut is on. Leave them at their defaults for your first comparison.

## Try a complete comparison

Run these from the repository root after following the [README setup](../redline/README.md#try-it-with-the-saved-answers). They use saved answers and write to a separate folder.

```sh
redline run --data data/holdout.jsonl --judge replay --router kmeans --k 12 --out results/local/kmeans12
redline run --data data/runs/attack_responses.jsonl --judge replay --router kmeans --k 12 --out results/local/kmeans12
redline report --holdout results/local/kmeans12/holdout_outputs.jsonl --attack results/local/kmeans12/attack_responses_outputs.jsonl --out results/local/kmeans12
redline compare --runs results results/local/kmeans12 --out results/local/comparison.md
```

Open `results/local/comparison.md` for the side-by-side numbers. Open `results/local/kmeans12/report.md` for the details, including which answers the shortcut got wrong when it's enabled. The output JSONL files have each answer and its decision.

To try the shortcut, add `--short-circuit` to both run commands and use a new folder such as `results/local/kmeans12_sc`. Point the report command at that folder, then add it to `--runs` in the comparison command.

Use the same router settings for the holdout and attack runs in each folder. The comparison labels a folder using its holdout settings; it doesn't check that the attack settings match.

The saved cache covers the included examples and tested settings: center matching and k-means with 4 or 12 clusters, with the shortcut on or off at its defaults. Other settings may need a grade that isn't in the cache. Replay stops if that happens.

For new examples, rules, or missing grades, set up a live judge using the [README](../redline/README.md#try-a-live-chatbot). Run with `--judge live` and a separate `--cache` path. Keep that cache path with the run so you can replay it later. Changing the judge model needs a fresh cache.

## Use your own answers and labels

`redline run` needs `id`, `user_type`, `prompt`, and `response` in each input record. It can grade answers without hand labels. Labels are needed when you want to measure how often the decisions were right.

For reports, each labeled answer also needs `rule_id`, `label`, and `reason_code`, as shown in [writing rules](writing-rules.md). For attack answers, `rule_id` is the rule the prompt targeted. Keep the prompt, response, and id with the label so you can tell exactly what was checked.

If you generate fresh answers, label those answers. Reusing an attack id doesn't make an old label apply to a new response. The report matches records by id and doesn't catch that mistake for you.

Pass your own rules and setup examples when grading, too:

```sh
redline run --data my-data/holdout.jsonl --rules my-data/rules.yaml --train my-data/train.jsonl --judge live --cache results/local/my-run/judge_cache.jsonl --out results/local/my-run
redline run --data my-data/attack_responses.jsonl --rules my-data/rules.yaml --train my-data/train.jsonl --judge live --cache results/local/my-run/judge_cache.jsonl --out results/local/my-run
```

These commands use center matching. Add the same router options to both if you want a different setting. Passing `--train` to the report command only records that file's count; it doesn't change how earlier answers were graded.

When using custom files, pass all the matching paths to `redline report`:

```sh
redline report \
  --train my-data/train.jsonl \
  --holdout-labels my-data/holdout.jsonl \
  --attacks my-data/attacks.jsonl \
  --attack-labels my-data/attack_labels.jsonl \
  --holdout results/local/my-run/holdout_outputs.jsonl \
  --attack results/local/my-run/attack_responses_outputs.jsonl \
  --cache results/local/my-run/judge_cache.jsonl \
  --out results/local/my-run
```

Those are example paths; replace them with your files. The report needs both holdout and attack outputs. `--out` changes where it writes the report, not which inputs it reads. The attack output filename comes from the input filename used with `redline run`.

`redline compare` expects `holdout_meta.json` and `attack_responses_meta.json` in each run folder. Keep the input filenames `holdout.jsonl` and `attack_responses.jsonl`, as above, if you want to use that command. Other input names work for grading and reporting when you pass their output paths.

## Read the results before choosing

Start with three questions: how many real violations did it catch, how often did it flag a good answer, and how many answers still need review? Then read the answers behind the misses. A single score can hide a tradeoff you care about.

In the [saved comparison](../results/routers/comparison.md), center matching with the shortcut caught one more attack violation, but its false alarm rate rose from 15 percent to 31 percent. K-means with 4 clusters made no shortcut decisions at all at the default settings. Turning the option on doesn't mean it will be used.

The comparison's judge count includes saved grades and replay lookups. It isn't a count of network requests. The times exclude router setup, and the saved files don't record whether grades came from live calls or the cache. Use those columns to understand the recorded run, not to claim one router makes live models faster.

For the code path behind these choices, see [how Redline works](how-it-works.md).
