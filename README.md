# Boundary Lab

<p align="center">
  <img src="assets/boundary-lab-logo-transparent.png" alt="Boundary Lab logo" width="360">
</p>

Tools for testing AI behavior and safety boundaries.

Boundary Lab is a growing collection of tools for testing AI systems.

## Redline

Redline tests whether chatbot answers follow defined rules and measures how reliably violations are detected. It also checks how often good answers get flagged by mistake, using responses with hand-written labels to check the grades.

The example is Northwind Power, a made-up utility with a billing assistant. It has four rules covering account data, identity checks, card numbers and the assistant's own instructions.

For each prompt and response, the code picks the closest rule and adds the permissions for that user type. A second model grades the response against that rule. The result is pass, fail or review, with a reason attached.

This checks responses after they're produced. It doesn't sit in front of a chatbot and block them.

### What happened

The saved run used two models running locally on Ollama: `llama3.2:3b` as the chatbot and `gemma3:27b` as the judge. There are 56 holdout examples and 80 attack prompts.

<!-- results:start -->
| What we measured | Result |
| --- | --- |
| Router accuracy on holdout | 84 percent |
| Rig accuracy on holdout (router and judge together) | 80 percent |
| Judge agreement with hand labels on holdout (kappa) | 0.78 |
| Rule breaks caught end to end | 88 percent |
| Cry wolf rate on holdout | 9 percent |
| Attack prompts that broke a rule | 21 of 80 |
| Of those, caught by the rig | 17 of 21 (81 percent) |
| Cry wolf rate on attack prompts | 15 percent |
| Judge agreement with hand labels on attack prompts (kappa) | 0.61 |

This table is written from results/summary.json by `redline readme`, so do not edit it by hand.
<!-- results:end -->

The holdout examples were kept out of router training and judge example selection. The attacks tried to extract instructions, bypass identity checks, get card numbers, or use roleplay to break the rules.

A few details matter when reading the table:

- Router accuracy measures whether the code picked the labeled rule. Off-topic examples are included.

- Rig accuracy measures the whole pipeline's final decision, including routing. Review counts as a miss, since it isn't a decision.

- Kappa measures agreement after adjusting for chance, also using the whole pipeline's decisions. It compares fail against pass or review, so it treats review differently from accuracy.

- Rule breaks caught is recall. The cry wolf rate is the share of good answers marked fail by mistake. The final four rows describe the attack run, including its false alarms and agreement with hand labels.

The [full report](results/report.md) includes results for each rule and how often answers went to review. The [saved summary](results/summary.json) is the source for the table above.

### Run it

You'll need Python 3.11 or newer. From the repository directory:

```sh
pip install -e .
redline run --data data/holdout.jsonl --judge replay
redline report
redline readme --check
```

Replay uses the saved judge answers in `data/judge_cache.jsonl`. It doesn't call a chat model or need an API key. Installing the dependencies and downloading the embedding model for the first run need an internet connection.

Each saved judge answer records its model name, and `results/summary.json` stores a SHA-256 hash of the cache file. Replay reproduces the calculations from those answers. It doesn't independently verify that the named model produced them.

The report command combines the holdout results with the saved attack results. It writes `results/summary.json` and `results/report.md`. The last command checks that the README table still matches the summary.

For live runs, use a chat endpoint that supports `/chat/completions` and JSON response mode. Set these environment variables, or put them in a local `.env` file:

| Model | Settings |
| --- | --- |
| Judge | `REDLINE_JUDGE_BASE_URL`, `REDLINE_JUDGE_MODEL`, `REDLINE_JUDGE_API_KEY` |
| Chatbot under test | `REDLINE_TARGET_BASE_URL`, `REDLINE_TARGET_MODEL`, `REDLINE_TARGET_API_KEY` |

Use the base URL before `/chat/completions`. The CLI requires an API key value even for a local server that doesn't check it.

```sh
redline attack --prompts data/attacks.jsonl --out results/live/attack_responses.jsonl
redline run --data results/live/attack_responses.jsonl --judge live --out results/live --cache results/live/judge_cache.jsonl
redline run --data data/holdout.jsonl --judge live --out results/live --cache results/live/judge_cache.jsonl
```

These commands keep new results separate from the saved run. Label new attack responses before comparing their grades; the saved labels only describe the original responses.

Live mode also reuses cached grades. Use a fresh cache file when changing the judge model, since cache keys contain the grading prompt but not the model name. New valid grades are saved for later replay.

For the details, see [how it works](docs/how-it-works.md), [writing your own rules](docs/writing-rules.md) and [the attack prompts](docs/attacks.md).

### Run it with promptfoo

Redline's 80 attack prompts also run under [promptfoo](https://www.promptfoo.dev), with its judge as a Python assertion next to promptfoo's own string and rubric checks. A replay config uses the saved answers and gives the same 80 outcomes as the command line tool. See [running the attacks through promptfoo](docs/promptfoo.md).

### Where it falls short

The router gives each rule one average embedding and picks the closest match. Rules that use similar words, like account data and identity checks, can get mixed up. It checks one rule per response, so it can miss violations of other rules.

The judge reads the response it's grading. That response could contain instructions meant to sway the judge. Wrapping it in tags and calling it data helps separate it from the instructions, but doesn't guarantee the judge will ignore it.

This is a small test around one fictional business. A few answers change the percentages quite a bit. The results show what happened on these examples, not how every chatbot or policy will behave.

Next I'd test a trained classifier for routing, add a judge from a different model family, and send disagreements for human review. More rules and examples would help show whether those changes hold up.

MIT license. See [LICENSE](LICENSE).
