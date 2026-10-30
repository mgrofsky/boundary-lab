# Redline

Part of [Boundary Lab](../README.md).

Redline checks chatbot answers against rules you write. Use it to test a chatbot, compare ways of checking its answers, or work through the included examples and see how the pieces fit.

The example is Northwind Power, a made-up utility with a billing assistant. It has four rules covering account data, identity checks, card numbers, and the assistant's own instructions.

For each prompt and response, a router picks the closest rule. A second model, called the judge, checks the answer against that rule and the user's permissions. You can also try letting the router decide some answers without asking the judge.

Each answer gets a pass, fail, or review, with a reason attached. Review means someone needs to check it. Redline records that result but doesn't send it to a person.

This checks responses after they're produced. It doesn't sit in front of a chatbot and block them.

## Try it with the saved answers

You'll need Python 3.11 or newer. From the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
redline run --data data/holdout.jsonl --judge replay --out results/local/center
```

Open `results/local/center/holdout_outputs.jsonl` to see the results. Each line has the original answer, the rule it matched, who made the decision, and why. The matching `_meta.json` file records the run settings and totals.

Replay uses saved judge answers from `data/judge_cache.jsonl`, so you don't need a chat model or an API key. The first run needs internet access to install dependencies and download the model that matches text.

The 56 holdout examples are answers kept back for checking the tool. Each has a `good` or `bad` label written by hand, so you can compare Redline's decisions with those labels.

## Pick how answers find a rule

There are two options:

- **Center matching**, the default, makes one average center for each rule's examples and picks the closest one. That average is also called a centroid.
- **K-means** groups similar examples into clusters. Each cluster takes the rule most of its examples belong to. A new answer goes to the nearest cluster.

Try k-means with 12 clusters on the same answers:

```sh
redline run --data data/holdout.jsonl --judge replay --router kmeans --k 12 --out results/local/kmeans12
```

Both options can use `--short-circuit`. That lets the router pass or fail some answers using the labeled examples, without asking the judge. It's off by default. Fewer judge decisions can mean more mistakes, so check both.

The [routing guide](../docs/routing.md) walks through the settings, saved comparisons, and commands to compare your own runs.

## What happened

The saved run used two models running locally on Ollama: `llama3.2:3b` as the chatbot and `gemma3:27b` as the judge. The table below uses center matching with the shortcut off, across 56 holdout examples and 80 attack prompts.

<!-- results:start -->
| What we measured | Result |
| --- | --- |
| Router accuracy on holdout | 84 percent |
| Overall accuracy on holdout (router and judge) | 80 percent |
| Agreement with hand labels on holdout (kappa) | 0.78 |
| Rule breaks caught on holdout | 88 percent |
| False alarm rate on holdout | 9 percent |
| Attack prompts that broke a rule | 21 of 80 |
| Of those, caught by Redline | 17 of 21 (81 percent) |
| False alarm rate on attack answers | 15 percent |
| Agreement with hand labels on attack answers (kappa) | 0.61 |

Run `redline readme` to update this table from `results/summary.json`. Don't edit the table by hand.
<!-- results:end -->

The holdout examples were kept out of router training and judge example selection. The attacks tried to extract instructions, bypass identity checks, get card numbers, or use roleplay to break the rules.

A few details matter when reading the table:

- Router accuracy measures whether the code picked the labeled rule. Off-topic examples are included.

- Overall accuracy measures Redline's final decision, including routing. Review counts as a miss because the answer still needs a decision.

- Kappa measures agreement with the hand labels after accounting for chance. It uses Redline's final decisions and groups pass and review together, so it treats review differently from accuracy.

- Rule breaks caught is also called recall. The false alarm rate is the share of good answers marked fail by mistake. The final four rows describe the attack run, including its false alarms and agreement with hand labels.

The [full report](../results/report.md) includes results for each rule and how often answers went to review. The [saved summary](../results/summary.json) is the source for the table above.

The [router comparison](../results/routers/comparison.md) includes k-means with 4 and 12 clusters, with the shortcut on and off. On these examples, both k-means settings got fewer final decisions right than center matching with the shortcut off.

With center matching, the shortcut caught 18 of 21 attack violations instead of 17. But false alarms rose from 15 percent to 31 percent. I'd start with the default and check the other settings against your own examples.

Each saved judge answer records its model name. `results/summary.json` stores a SHA-256 hash, a fingerprint of the cache file's contents. Replay repeats the calculations from those answers. It doesn't check whether the named model produced them.

## Try a live chatbot

For live runs, use a chat endpoint that supports `/chat/completions` and JSON response mode. Set these environment variables, or put them in a local `.env` file:

| Model | Settings |
| --- | --- |
| Judge | `REDLINE_JUDGE_BASE_URL`, `REDLINE_JUDGE_MODEL`, `REDLINE_JUDGE_API_KEY` |
| Chatbot under test | `REDLINE_TARGET_BASE_URL`, `REDLINE_TARGET_MODEL`, `REDLINE_TARGET_API_KEY` |

Use the base URL before `/chat/completions`. The CLI requires an API key value even for a local server that doesn't check it.

```sh
redline attack --prompts data/attacks.jsonl --out results/local/live/attack_responses.jsonl
redline run --data results/local/live/attack_responses.jsonl --judge live --out results/local/live --cache results/local/live/judge_cache.jsonl
redline run --data data/holdout.jsonl --judge live --out results/local/live --cache results/local/live/judge_cache.jsonl
```

These commands keep new results separate from the saved run. Label new attack responses before comparing their grades; the saved labels only describe the original responses.

Live mode also reuses saved grades. Use a fresh cache file when changing the judge model. Redline looks up grades using the grading prompt, and that lookup doesn't include the model name. New valid grades are saved for later replay.

Add `--router kmeans --k 12` to both `redline run` commands to try that routing option. The [routing guide](../docs/routing.md) shows how to make reports with the right files and compare runs.

To use your own rules and examples, start with [writing rules](../docs/writing-rules.md). See [how it works](../docs/how-it-works.md) for the code flow and [the attack prompts](../docs/attacks.md) for testing ideas.

## Run it with promptfoo

Redline's 80 attack prompts also run under [promptfoo](https://www.promptfoo.dev), alongside its string and rubric checks. That integration uses center matching with the shortcut off. See [running the attacks through promptfoo](../docs/promptfoo.md).

## Where it falls short

Both routers can mix up rules that use similar words, like account data and identity checks. Each response gets checked against one rule, so a violation of another rule can slip through. An off-topic match passes without asking the judge.

The shortcut can also make the wrong call. Examples that look similar can need different decisions. Look at the missed violations, false alarms, and individual answers before choosing settings for your own work.

The judge reads the response it's grading. That response could contain instructions meant to sway the judge. Wrapping it in tags and calling it data helps separate it from the instructions, but doesn't guarantee the judge will ignore it.

This is a small test around one fictional business. A few answers change the percentages quite a bit. The results show what happened on these examples, not how every chatbot or policy will behave.

To take it further, add examples from the kinds of conversations you need to check. Try another judge, compare the same answers, and look through the disagreements. That's where you'll see what needs work.

Copyright (c) 2026 Matt Grofsky. Licensed under the GNU Affero General Public License, version 3 only (AGPL-3.0-only). See [LICENSE](../LICENSE).
