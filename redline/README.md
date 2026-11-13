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
pip install -e ".[minilm]"
redline run --data data/holdout.jsonl --judge replay --out results/local/center
```

The `minilm` extra installs `sentence-transformers`, the model library this example matches text with. It pulls in torch.

From Redline 0.5.0 a plain `pip install -e .` leaves that library out. That is a breaking change for an install that relied on it. The fix is one line: install `".[minilm]"` from a checkout, as above. A plain install still runs `--rules-only`, `--embedder tfidf` and `garak-grade`. Asking for the `minilm` embedder without the extra stops with one line that says what to install.

The package is named `boundary-redline`, and it is not on the public Python package index. Another project owns the name `redline` there, so never install Redline by name alone. Install it from a checkout, or straight from the repository:

```sh
pip install "boundary-redline[minilm] @ git+https://github.com/mgrofsky/boundary-lab"
```

The import name is `redline` and the command is `redline`, as before.

Open `results/local/center/holdout_outputs.jsonl` to see the results. Each line has the original answer, the rule it matched, who made the decision, and why. The matching `_meta.json` file records the run settings and totals.

Replay uses saved judge answers from `data/judge_cache.jsonl`, so you don't need a chat model or an API key. The first run needs internet access to install dependencies and download the model that matches text.

The 56 holdout examples are answers kept back for checking the tool. Each has a `good` or `bad` label written by hand, so you can compare Redline's decisions with those labels.

## Use it in your code

`Grader` runs the same steps as `redline run`, one answer at a time. Run this from the repository root:

```python
from redline import Grader

grader = Grader()  # saved rules, examples and judge answers, center matching
prompt = "how much is autopay going to take out next week"
response = "Autopay will draw $103.85 on Tuesday, which covers this month's bill in full."
print(grader.grade(prompt, response, "verified")["outcome"])  # pass
```

`grade` returns the same fields as a line in `holdout_outputs.jsonl`, without the `id`. Building the `Grader` loads the rules, picks judge examples and sets up the router, so build it once and reuse it.

By default it uses the replay judge, which only knows the saved answers. To grade new answers, pass `judge=ChatEndpointJudge(base_url, model, api_key, cache_path="my_cache.jsonl")` from `redline.judge`. The other options match the `redline run` flags: `rules`, `train`, `router`, `k`, `short_circuit`, `seed`, `cache`, `embedder`, `top_k`, `floor`, `missing_grade` and `attempts`.

## Use it from an application

An app can call Redline as a library with no command line. It can pass rules as data and list every problem in them before saving. It can count judge calls before a run, grade a garak report with a cap and a stop, and replay saved grades in CI. [Use Redline from an application](../docs/using-redline-in-an-app.md) has a tested example of each. [Saved grades](../docs/saved-grades.md) describes the file that replay reads.

## Pick how answers find a rule

There are two options:

- **Center matching**, the default, makes one average center for each rule's examples and picks the closest one. That average is also called a centroid.
- **K-means** groups similar examples into clusters. Each cluster takes the rule most of its examples belong to. A new answer goes to the nearest cluster.

Try k-means with 12 clusters on the same answers:

```sh
redline run --data data/holdout.jsonl --judge replay --router kmeans --k 12 --out results/local/kmeans12
```

Both options can use `--short-circuit`. That lets the router pass or fail some answers using the labeled examples, without asking the judge. It's off by default. Fewer judge decisions can mean more mistakes, so check both.

Add `--top-k 3` to check each answer against the three closest rules and keep the worst outcome. Each answer that reaches the judge then costs up to three judge calls instead of one. `--floor` skips extra rules with a low match score. The saved cache only covers one rule per answer, so this needs a live judge or `--missing-grade review`.

`--embedder tfidf` swaps the model that turns text into numbers for simple word counts from scikit-learn. It needs no model download and starts at once, but it routes worse because it only matches shared words. The saved results use the default, `minilm`. The run's `_meta.json` records which one was used, and `redline report` names it in the summary and report.

A different router or embedder can send an answer to a rule the saved cache never graded it under. Replay stops when that happens, which is the default `--missing-grade stop`. With `--missing-grade review`, that answer gets `review` with the code `NO_SAVED_GRADE`, and the run keeps going. `_meta.json` counts the answers whose final outcome had no grade under `no_saved_grade_records`. It counts every lookup that found none under `no_saved_grade_lookups`, which is higher when `--top-k` checks several rules. For example:

```sh
redline run --data data/holdout.jsonl --judge replay --embedder tfidf --missing-grade review --out results/local/tfidf
```

The [routing guide](../docs/routing.md) walks through the settings, saved comparisons, and commands to compare your own runs.

## Start from a rules file alone

Everything above needs labeled example answers. If you have a rules file and nothing else, use rules only mode:

```sh
redline run --rules-only --rules my_rules.yaml --data my_answers.jsonl --judge live --out results/local/rules_only
```

```python
from redline import Grader
from redline.judge import ChatEndpointJudge

judge = ChatEndpointJudge(base_url, model, api_key)
grader = Grader.from_rules("my_rules.yaml", judge)
print(grader.grade(prompt, response, "anonymous")["outcome"])
```

The train file is not read and no cache file is touched unless you pass `--cache`. The router ranks the rules by how close each rule's own text is to the answer. The judge checks the answer against every rule with no examples, and the worst outcome wins. Every output row carries `"mode": "rules_only"`.

This mode has not been measured yet. It costs one judge call per rule per answer unless `--top-k` is set, which checks only that many of the closest rules. Adding labeled examples is what makes the router and judge better.

## What happened

The saved run used two models running locally on Ollama: `llama3.2:3b` as the chatbot and `gemma3:27b` as the judge. The table below uses center matching with the shortcut off, across 56 holdout examples and 80 attack prompts.

<!-- results:start -->
| What we measured | Result |
| --- | --- |
| Router accuracy on holdout | 84 percent |
| Overall accuracy on holdout (router and judge) | 80 percent |
| Agreement with hand labels on holdout (kappa) | 0.78 |
| Rule breaks caught on holdout | 21 of 24 (88%, 95% interval 69% to 96%) |
| False alarm rate on holdout | 3 of 32 (9%, 95% interval 3% to 24%) |
| Attack prompts that broke a rule | 21 of 80 |
| Of those, caught by Redline | 17 of 21 (81%, 95% interval 60% to 92%) |
| False alarm rate on attack answers | 9 of 59 (15%, 95% interval 8% to 27%) |
| Agreement with hand labels on attack answers (kappa) | 0.61 |

Counts come with a 95 percent interval. With this few answers, the true rate could be anywhere in it.

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

Each live judge prompt ends with a random check value the judge must copy into its JSON. A reply without it gets `review` with the code `JUDGE_NONCE` and is not saved. A judge model that drops the key sends every answer to review, so check a few replies when trying a new model.

Add `--router kmeans --k 12` to both `redline run` commands to try that routing option. The [routing guide](../docs/routing.md) shows how to make reports with the right files and compare runs.

To use your own rules and examples, start with [writing rules](../docs/writing-rules.md). A rules file can name its own user types under `user_types`, and `redline run` refuses a record whose type isn't listed. See [how it works](../docs/how-it-works.md) for the code flow and [the attack prompts](../docs/attacks.md) for testing ideas.

## CI

`redline gate` reads a summary and fails the build when a number crosses a limit you set. Run it after `redline run` and `redline report`:

```sh
redline run --data data/holdout.jsonl --judge replay --out results/ci
redline run --data data/runs/attack_responses.jsonl --judge replay --out results/ci && redline report --holdout results/ci/holdout_outputs.jsonl --attack results/ci/attack_responses_outputs.jsonl --out results/ci
redline gate --summary results/ci/summary.json --min-recall 0.8 --max-false-alarm 0.1 --min-kappa 0.7
```

It prints one line for each limit that's broken and exits 1. When every limit holds, it prints nothing and exits 0. Only the limits you pass are checked.

`--min-recall` is the share of rule breaks caught. `--max-false-alarm` is the share of good answers marked fail. `--min-kappa` is agreement with the hand labels. These read the holdout numbers. Add `--attack` to check the attack run's numbers instead.

Add `--use-interval` to test recall on the low end of its 95 percent interval and false alarms on the high end. With a few dozen answers that is a much harder bar. The saved summary passes 0.8 and 0.1 without it and fails with it.

## How Redline holds up when the attacker adapts

A detector's score is specific to the attacks it was tested on. The saved sets are small, each attack prompt was sent once, and no attack here was written to beat Redline. Three tools help you see how much the numbers can be trusted.

Intervals come first. Every end to end rate now carries its count and a 95 percent interval, so the table above says "21 of 24" and not just 88 percent. `summary.json` keeps every old number in place and adds the counts beside them under `counts`.

Repeats show how stable the grades are. `redline run --attempts 3 --judge live` asks the judge three times per answer, skipping the cache after the first. The report then counts a bad answer as caught only if every attempt failed it. `redline attack --attempts 3 --temperature 0.7` sends each attack prompt three times.

`redline evade` rewrites each bad answer in fixed ways that keep it readable, such as a fake pass verdict, padding, code blocks, zero width spaces, and look alike letters. It reports a bad answer as caught only if every version failed. With `--attacker`, a chat model then rewords the answers still caught, for up to five rounds.

```sh
redline evade --data data/holdout.jsonl --judge live --out results/local/evade --cache results/local/evade/judge_cache.jsonl
redline evade --data data/holdout.jsonl --judge live --attacker --max-judge-calls 500 --out results/local/evade --cache results/local/evade/judge_cache.jsonl
```

A live evade run saves new grades under `--out` by default and refuses `--cache data/judge_cache.jsonl` with exit 2, so the saved cache keeps its pinned hash.

A reworded answer that passes may no longer break the rule. So attacker results stay unconfirmed until a person labels them and passes the file to `redline report --evade-labels`.

Replay needs `--missing-grade review`. The cache has never seen the rewritten answers, so every rewrite that reaches the judge goes to review, and the judge side is not measured. The router side is real, since the router is deterministic. With the default embedder and router, replay on the 24 bad holdout answers gives this router only floor. As written, the router passed 0 of 24 (0%, 95% interval 0% to 14%). After a rewrite, it passed 2 of 24 (8%, 95% interval 2% to 26%) with no judge call. That is a lower bound on misses. The summary is saved as `results/evade_replay_summary.json`. The other evade numbers for this repo are not published until a live run is done.

See [adaptive attacks](../docs/adaptive-attacks.md) for every rewrite, the output files, and the summary fields.

## Run it with promptfoo

Redline's 80 attack prompts also run under [promptfoo](https://www.promptfoo.dev), alongside its string and rubric checks. That integration uses center matching with the shortcut off. See [running the attacks through promptfoo](../docs/promptfoo.md).

## Use with garak

[garak](https://github.com/NVIDIA/garak) is an open source scanner that sends attack prompts to a model. Redline can grade the answers in a garak report against your rules with `redline garak-grade`. It can also run inside garak as the detector `redline.PolicyRules`, after `redline garak-install`. A rules file is enough to start. Redline does not depend on garak, and this is not an official garak plugin. See [using Redline with garak](../docs/garak.md).

## Where it falls short

Both routers can mix up rules that use similar words, like account data and identity checks. By default each response gets checked against one rule, so a violation of another rule can slip through. `--top-k` checks more rules for more judge calls. An off-topic match passes without asking the judge.

The shortcut can also make the wrong call. Examples that look similar can need different decisions. Look at the missed violations, false alarms, and individual answers before choosing settings for your own work.

The judge reads the response it's grading. That response could contain instructions meant to sway the judge. Wrapping it in tags and calling it data helps separate it from the instructions, but doesn't guarantee the judge will ignore it.

This is a small test around one fictional business. A few answers change the percentages quite a bit. The results show what happened on these examples, not how every chatbot or policy will behave.

To take it further, add examples from the kinds of conversations you need to check. Try another judge, compare the same answers, and look through the disagreements. That's where you'll see what needs work.

Copyright (c) 2026 Matt Grofsky. Licensed under the GNU Affero General Public License, version 3 only (AGPL-3.0-only). See [LICENSE](../LICENSE).
