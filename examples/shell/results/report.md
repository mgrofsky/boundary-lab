# Results report

This report uses the results saved at 2026-11-21T02:19:28Z. The router used `sentence-transformers/all-MiniLM-L6-v2` to turn text into numbers it could compare. `redline report` writes this file.

The judge model was recorded as us.anthropic.claude-haiku-5-5. These names come from the cache file. They haven't been checked separately. The cache file's hash, a fingerprint of its contents, is in `summary.json`.

## The data

The holdout set contains answers kept back for checking the tool. The attack run contains the chatbot's answers to prompts that tried to make it break a rule. Both sets use labels written by hand.

- examples/shell/train.jsonl has 80 records.
- examples/shell/holdout.jsonl has 50 records.
- /dev/null has 0 records.
- examples/shell/results/holdout_outputs.jsonl has 50 records.

## Routing

The router picked the right rule for 74 percent of the 50 holdout answers, including 0 off-topic answers. Keep these answers separate from the examples used to set up the router and judge. The report doesn't check that separation for you.

If the router can't find a close enough match, it marks the answer for review. That counts as a wrong match for an answer about a rule, and a correct match for an off-topic answer.

The router marked 8 percent of the answers for review. Redline records that result; it doesn't send it to a person.

## Routing and the shortcut

This run used center matching. The shortcut was off. Off-topic matches still passed without the judge.

The `--short-circuit` option lets the router pass or fail some answers using the labeled examples, without asking the judge. Center matching averages examples for each rule and compares separate good and bad groups. K-means groups similar examples into clusters, then counts labels in the matched cluster for the user's type.

On the holdout set, the router did not use the shortcut. 230 answers went to the judge, including saved-grade lookups. Grading took 0.34 seconds.

On the attack run, the router did not use the shortcut.

These times cover grading after router setup. The saved settings don't say which grades came from live calls or the cache. The judge count includes both, so it isn't a count of network requests or a measure of live-model speed.

## Final decisions on the holdout set

Redline's final decision matched the hand label for 92 percent of the holdout answers. Each decision is pass, fail, or review. Review counts as a miss because the answer still needs a decision.

Kappa is 0.88. It measures agreement after accounting for agreement expected by chance. A score of 0 matches that chance level; 1 means complete agreement.

Accuracy and kappa handle review differently. Accuracy counts a review as a miss. Kappa compares fail against pass or review, and checks that against the bad or good hand label. Both scores include the router's decisions.

For FMT-01, when Redline said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For ROLE-01, when Redline said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 60 percent.

For STATE-01, when Redline said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For SYS-01, when Redline said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For TURN-01, when Redline said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 80 percent.

## Final decisions on the attack run

Redline's final decision matched the hand label for 0 percent of the attack answers. Each decision is pass, fail, or review. Review counts as a miss here too.

Kappa is 0.00. It measures agreement after accounting for agreement expected by chance. A score of 0 matches that chance level; 1 means complete agreement.

Accuracy and kappa handle review differently. Accuracy counts a review as a miss. Kappa compares fail against pass or review, and checks that against the bad or good hand label. Both scores include the router's decisions.

## Violations, false alarms, and reviews on the holdout set

Of the answers that broke a rule, Redline caught 88 percent. It passed 0 percent by mistake and marked 12 percent for review.

Of the answers that followed the rules, Redline failed 0 percent by mistake. That's the false alarm rate. It marked 4 percent of them for review and passed the other 96 percent.

Each line gives the count, the share, and a 95 percent interval. With this few answers the true rate could sit anywhere in that interval.

- Rule breaks caught: 22 of 25 (88%, 95% interval 70% to 96%).
- Rule breaks passed by mistake: 0 of 25 (0%, 95% interval 0% to 13%).
- Rule breaks sent to review: 3 of 25 (12%, 95% interval 4% to 30%).
- Good answers wrongly failed: 0 of 25 (0%, 95% interval 0% to 13%).
- Good answers sent to review: 1 of 25 (4%, 95% interval 1% to 20%).

## The attack run

The model under test got 0 attack prompts, and 0 of them got it to break a rule, going by the hand labels. Redline caught 0 of those.

Each line gives the count, the share, and a 95 percent interval. With this few answers the true rate could sit anywhere in that interval.

- Rule breaks caught: 0 of 0 (no answers).
- Rule breaks passed by mistake: 0 of 0 (no answers).
- Rule breaks sent to review: 0 of 0 (no answers).
- Good answers wrongly failed: 0 of 0 (no answers).
- Good answers sent to review: 0 of 0 (no answers).

The results are grouped by the rule each attack targeted. The router picked a different rule or no rule for 0 of the 0 attack answers. When the judge is used, it checks the rule the router picked. A wrong match can hide a violation, such as leaked instructions in an answer about payments.

## Results for each rule

Precision is how often a fail was right. Recall is how many of the real rule breaks were caught. F1 combines the two into one score from 0 to 1. Off-topic answers are not in the table because there is no rule for them to break.

| Set | Rule | Breaks | Precision | Recall | F1 | Answers |
| --- | --- | --- | --- | --- | --- | --- |
| holdout | FMT-01 | 5 | 100 percent | 100 percent | 1.00 | 10 |
| holdout | ROLE-01 | 5 | 100 percent | 60 percent | 0.75 | 10 |
| holdout | STATE-01 | 5 | 100 percent | 100 percent | 1.00 | 10 |
| holdout | SYS-01 | 5 | 100 percent | 100 percent | 1.00 | 10 |
| holdout | TURN-01 | 5 | 100 percent | 80 percent | 0.89 | 10 |
