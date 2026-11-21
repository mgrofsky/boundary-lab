# Redline

Part of [Boundary Lab](../README.md).

Redline checks chatbot answers against rules you write. Use it to test a chatbot, compare ways of checking its answers, or work through the included examples and see how the pieces fit.

The example is Northwind Power, a made-up utility with a billing assistant. It has four rules covering account data, identity checks, card numbers, and the assistant's own instructions.

For each prompt and response, a router picks the closest rule. A second model, called the judge, checks the answer against that rule and the user's permissions. You can also try letting the router decide some answers without asking the judge.

Each answer gets a pass, fail, or review, with a reason attached. Review means someone needs to check it. Redline records that result but doesn't send it to a person.

Redline can also sit in front of a chatbot. Your app passes it the prompt and the answer before showing it, and Redline says whether to show the answer, hold it back, or show something else. See [Hold back an answer before it is shown](#hold-back-an-answer-before-it-is-shown).

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

## Hold back an answer before it is shown

`Guard` wraps a `Grader` for use in the path of a live answer. Use it once Redline's grades hold up on your own examples, and you want it to stop bad answers, not only count them. Your app calls it after its model writes an answer and before the user sees it:

```python
from redline import Grader, Guard

guard = Guard(Grader(), replacement="Sorry, I can't help with that here.")

decision = guard.check(prompt, answer, "anonymous")
if decision.allow:
    send(answer)
elif decision.text is not None:
    send(decision.text)  # the replacement, or a new answer that passed
```

`decision.action` is one of four values. `show` means show the answer. `regenerated` means the answer was held back and a new one passed. `replace` means show the replacement text. `hold` means show nothing. `allow` is true only for `show`. The decision also carries the outcome, the rule, the reason code and text, who decided, the seconds taken, the judge calls made and `fallback`.

`fallback` is null when a grade decided. Otherwise it says why there was no usable grade:

- `deadline`: the check ran out of time. The reason code is `GUARD_DEADLINE`.
- `judge_error`: the judge could not be reached, its reply could not be read, it left out the check value, or replay had no saved grade.
- `error`: grading raised an exception. The reason code is `GUARD_ERROR`. The check never raises for a failed judge.
- `too_long`: the prompt or the answer is longer than `max_chars`, 20000 by default, so neither was sent to the judge. The reason code is `GUARD_TOO_LONG`.

Every default holds the answer back when in doubt:

| Option | Default | What it does |
| --- | --- | --- |
| `on_review` | `"hold"` | What a review does. `"show"` lets it through. |
| `on_error` | `"hold"` | Fail closed. `"show"` fails open: the answer is shown when no usable grade arrives. The decision records the fallback either way. |
| `deadline_seconds` | `10` | The time limit on the whole check. |
| `replacement`, `replacements` | none | The text shown in place of a held back answer, and its own text per rule. |
| `regenerate`, `retries` | none, `1` | A function that asks your model for a new answer, and how many times it may be called. At most 3. |
| `max_chars` | `20000` | An answer is held back with no judge call when it or its prompt is longer. |
| `record`, `record_text` | none, `False` | A JSON lines file to append each decision to, and whether it holds the text. |

Fail open applies only to the answer as given. Once that answer is held back, nothing unchecked is shown. A new answer that cannot be graded in time gives the replacement, not the new answer.

`regenerate(prompt, held)` gets the prompt and a dict with the held answer, its rule and its reason. It returns a new answer, which is graded the same way, or `None` to give up. It is not called after a fallback.

### What it costs per answer

Time: at most `deadline_seconds`, 10 by default, for the whole check, a new answer included. The call returns by then whatever the judge does. The live judge stops its own requests at the deadline too: it sends none once the time is up, cuts each request's timeout to the time left, and does not wait to try again past it. A judge of your own may not stop. Its call then runs on in a background thread until it returns, but the check has already returned and starts nothing new. With replay, a check takes a few milliseconds.

Judge calls: one grade costs what `grader.grade` costs. That is `top_k` rules times `attempts`, so 1 call with the default settings, or one per rule in rules only mode. A check grades the answer and, with `regenerate`, up to `retries` new ones. So the worst case is one grade's calls times `1 + retries`. `guard.max_judge_calls` gives that number. With the defaults it is 1, or 2 with `regenerate`. Each live call asks for at most 600 tokens back.

### The record

With `record` set, each check appends one line per answer checked. The fields are those of a line in `holdout_outputs.jsonl`, plus `guard`, which holds the action, `allow`, whether this answer was shown, the fallback, the try number, the seconds and the time. `check(..., record_id=...)` sets `id`. An answer with no grade gets `decided_by` set to `guard` and its reason code.

By default the prompt, the answer and the judge's reason text are written as null. `redline run` writes them, since its input is a test file you already hold. A guard sees live conversations, and the answers it holds back are the ones most likely to contain what the rules protect, such as account data or a card number. Pass `record_text=True` to keep them. Nothing is written without `record`. A live judge given a `cache_path` still saves its grades there, as it does for `redline run`. Leave `cache_path` out if you don't want them kept.

### From async code and the command line

`await guard.acheck(prompt, answer, user_type)` is the same check for async code. The judge makes blocking calls, so the grading runs in a thread of its own and the event loop stays free. The deadline counts from the call.

`redline guard` checks one answer and prints the decision as JSON. It exits 0 when the answer may be shown, 1 when it is held back, and 2 when the check cannot be set up:

```sh
redline guard --judge replay --user-type anonymous --prompt "what did I pay last month" --response "Last month you paid \$79.15 on the 6th."
```

[docs/examples/guard_chat.py](../docs/examples/guard_chat.py) is a small chat loop for the Northwind assistant. It runs with the saved grades and no key. One answer breaks the account data rule and is held back. The others are shown.

### What it does not do

- It checks a whole answer. An app that streams tokens to the user must buffer the answer and check it before sending any of it.
- It does not check tool calls. Use [Latch](../tools/latch/README.md) to check a tool call before your app runs it.
- It is only as good as the grades. The table under [What happened](#what-happened) shows the misses and false alarms on the saved examples. In front of users, a false alarm holds back a good answer, and a miss shows a bad one. Measure on your own examples first.
- An answer the router finds off topic passes with no judge call, as it does in a run. So does an answer the router shortcut passes, with `short_circuit` on.
- A user type the rules do not declare, or a prompt or answer that is not text, raises before any judge call. Those are mistakes in the calling code, not judge failures.

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
