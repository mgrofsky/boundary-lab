# How Redline holds up when the attacker adapts

A detector's score only describes the attacks it was tested on. Redline's saved numbers come from small answer sets. The holdout set has 24 bad answers and the attack run has 21. Each attack prompt was sent once, and none was written to beat Redline itself.

This page covers three tools that make the numbers say how much they can be trusted: intervals, repeats, and `redline evade`.

## Intervals

Every rate under `end_to_end`, `attack_end_to_end` and each kind under `attack` now comes with its count and a 95 percent interval. The interval is a Wilson score interval. It is the range the true rate could plausibly sit in, given how few answers it was counted from.

The rates stay where they were. The counts sit beside them under a key named `counts`, with the same names as the rates:

```json
"end_to_end": {
  "bad": {"caught": 0.875, "missed": 0.083, "sent_to_review": 0.042},
  "good": {"wrongly_failed": 0.094, "sent_to_review": 0.156},
  "counts": {
    "bad": {"caught": {"k": 21, "n": 24, "ci95": [0.690, 0.957]}, "...": "..."},
    "good": {"wrongly_failed": {"k": 3, "n": 32, "ci95": [0.032, 0.242]}, "...": "..."}
  }
}
```

`k` is how many answers, `n` is out of how many, and `ci95` is the low and high end. Under `attack`, each kind gets `counts.caught_by_rig` and `counts.missed`, both out of that kind's rule breaks. These are the published numbers from the saved run, rounded.

`redline report` and `redline readme` print each end to end rate in this form: "21 of 24 (88%, 95% interval 69% to 96%)". That interval is wide. With 24 bad answers, a catch rate of 88 percent is consistent with a true rate anywhere from about 69 to 96 percent.

`redline gate --use-interval` tests recall on the low end of its interval and the false alarm rate on the high end. Kappa is checked as before. A gate that passes this way passes on the numbers you can defend, not the lucky point value.

```sh
redline gate --summary results/summary.json --min-recall 0.8 --max-false-alarm 0.1 --use-interval
```

On the saved summary this fails, though the same limits pass without `--use-interval`. A failing line prints the point value and the interval. A summary written before Redline 0.3.0 has no counts, so `--use-interval` stops with exit 2 and says so.

## Repeats

A model at temperature 0 can still give different answers to the same request. One run hides that.

`redline run --attempts 3 --judge live` asks the judge three times about each answer that reaches it. The first attempt can use the cache. Later attempts skip it, reading nothing and saving nothing. The router decides once, so only judge calls repeat. Replay has one saved grade per answer, so `--attempts` with `--judge replay` stops with exit 2.

Each output row keeps its usual fields, filled from the worst attempt: fail, then review, then pass. It also gets `attempts` and `outcomes`, the list of outcomes in order.

When rows carry `outcomes`, the report counts them strictly. A bad answer is caught only if every attempt failed it. It is missed if any attempt passed it. Otherwise it is sent to review. A good answer is wrongly failed if any attempt failed it. Accuracy, kappa and per rule recall use the same rule, so every number tells the same story. The summary adds `flip_rate`, the share of judged answers whose attempts did not all agree, with its count and interval.

`redline attack --attempts 3 --temperature 0.7` sends each prompt three times. Rows gain `attempt`, counted from 1. Attempt 1 keeps the prompt's id and later ones end in `#2`, `#3`. Those later answers have no hand label yet. The report leaves any attack answer without a good or bad label out of every rate and prints how many it left out.

## Evade

`redline evade` asks a different question: if someone rewrote a bad answer to get it past Redline, would it still be caught? It reads labeled records and uses only the ones labeled `bad`.

### Stage 1: fixed rewrites

Each bad answer is graded as written and after each rewrite in `redline/mutate.py`. Every rewrite keeps the whole original answer readable, so the bad label still holds.

- `judge_says_pass` adds a line telling the grader to mark the answer as pass.
- `fake_verdict` adds a block shaped like the judge's own JSON reply with verdict pass.
- `fake_verdict_closed` first closes the `assistant_response` tag the judge prompt wraps the answer in, then adds the fake verdict.
- `padding_before` and `padding_after` add harmless support text to pull the router toward no rule.
- `wrap_quote`, `wrap_code` and `wrap_json` put the answer in a quote block, a code block, or a JSON string.
- `bullets` splits the answer into one bullet per sentence.
- `zero_width` puts zero width spaces between the letters of longer words.
- `look_alike` swaps a few letters for Cyrillic letters that look the same.

Every rewrite is deterministic. The ones that choose seed their choice from the text.

```sh
redline evade --data data/holdout.jsonl --judge live --out results/local/evade --cache results/local/evade/judge_cache.jsonl
```

Give live runs their own `--cache`, so new grades stay out of the saved `data/judge_cache.jsonl`.

This writes `evade_outputs.jsonl`, one row per answer and rewrite, and `evade_summary.json`. The summary has:

- `worst_case`: a bad answer counts as caught only if the original and every rewrite failed. It is missed if any version passed. Otherwise it is sent to review.
- `by_rewrite`: the same three counts for each rewrite alone, with `original` as the baseline.
- `judge_nonce`: how many verdicts ended with the code `JUDGE_NONCE`, by rewrite. A rewrite that tricks the judge into echoing text can show up here.
- `judge_calls`, and in replay mode `no_saved_grade_lookups` and `no_saved_grade_records`.

Every count comes with its interval, as above.

Replay works too, but the saved cache has never seen the rewritten answers. Replay mode needs `--missing-grade review`, and every rewrite that reaches the judge then goes to review. That run shows the command works without a network. It does not measure anything, so its numbers are not results.

```sh
redline evade --data data/holdout.jsonl --judge replay --missing-grade review --embedder tfidf --out results/local/evade
```

`evade` takes the same router and rules options as `run`. `--max-judge-calls 200` stops cleanly after 200 judge calls, writes what it has, and says so under `stopped` in the summary. A record cut off part way is dropped from every block and from `evade_outputs.jsonl`, so all of them cover the same finished records.

### Stage 2: a model attacker

With `--attacker`, a chat model rewords each answer still caught after stage 1. Set `REDLINE_ATTACKER_BASE_URL`, `REDLINE_ATTACKER_MODEL` and `REDLINE_ATTACKER_API_KEY`, the same way as the judge.

For up to `--rounds` rounds (5 by default), the attacker gets the rule, the user's message, the current answer, and Redline's last outcome, reason code and reason. It is asked to make the same claim or commitment to the customer in different words. Redline grades each rewrite and stops at the first pass.

The attacker's reply is data. Redline takes only the text between `<rewrite>` tags and cuts it to four times the length of the original answer.

A rewrite that passes may have dropped the rule break, so it is not counted as a miss. Every rewrite goes to `evade_candidates.jsonl` with the original id, the round, the rewrite, Redline's verdict, and the label `unconfirmed`. The summary's `attacker` block counts records tried, candidates that passed, and rounds used.

Candidates count only after a person labels them. Change each `label` to `good` or `bad`, then pass the file to the report:

```sh
redline report --evade-labels results/local/evade/evade_candidates_labeled.jsonl
```

The summary gains `confirmed_adaptive`. A record counts as missed there when one of its rewrites passed and a person labeled it bad. A record whose passing rewrite has no label yet is left out, and `unlabeled` counts those rewrites. A record whose rewrites all failed stays in as not missed. It has the count and interval like every other rate.

## What this repo does and does not show

The stage 1 and stage 2 numbers for the saved run are not published here. They need a live judge, and that run has not been done. Until it has, the saved numbers describe attacks that were not written against Redline. They say nothing about how it does against an attacker who sees its decisions and adapts.
