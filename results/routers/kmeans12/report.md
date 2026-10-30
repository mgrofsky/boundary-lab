# Results report

This file is written by `redline report`. It was made at 2026-10-28T04:22:35Z. Answers were turned into numbers with sentence-transformers/all-MiniLM-L6-v2.

The judge model was recorded as gemma3:27b. These names come from the cache file, not a separate check of the model. The hash of the judge cache file is in summary.json.

## The data

- data/train.jsonl has 180 records.
- data/holdout.jsonl has 56 records.
- data/attacks.jsonl has 80 records.
- data/runs/attack_labels.jsonl has 80 records.
- results/routers/kmeans12/holdout_outputs.jsonl has 56 records.
- results/routers/kmeans12/attack_responses_outputs.jsonl has 80 records.

## Routing

The router picked the right rule for 68 percent of the 56 holdout answers, the 8 off topic ones included. Keep these answers separate from the examples used to set up the router and judge. The report doesn't check that separation for you.

On an on topic answer, an abstain counts as wrong here. On an off topic answer, an abstain counts as right.

It abstained on 20 percent of them. An abstain means the answer was not close enough to a center, so it is marked review. Redline records that result; it doesn't send it to a person.

## Routing and the shortcut

This run used k-means with 12 clusters. The shortcut was off. Off-topic matches still passed without the judge.

The --short-circuit option lets the router pass or fail some answers using the labeled examples, without asking the judge. Center matching compares good and bad groups. K-means counts labels within the matched cluster for the user's type.

On the holdout set the router did not use the shortcut. 41 answers went to the judge, including saved-grade lookups. Grading took 143.8 seconds.

On the attack run the router did not use the shortcut. 76 answers went to the judge, including saved-grade lookups. Grading took 42.14 seconds.

These times cover grading after router setup. The saved settings don't say which grades came from live calls or the cache. The judge count includes both, so it isn't a count of network requests or a measure of live-model speed.

## Final decisions on the holdout set

The rig's final answer matched the hand label for 70 percent of the holdout answers, where the rig's final answer is pass, fail or review. A review counts as a miss here, because no one has decided yet.

Kappa, a measure of agreement with luck taken out, is 0.67. A kappa of 0 is no better than guessing and 1 is perfect agreement.

Agreement and kappa count different things. Agreement compares the rig's final answer (pass, fail or review) with the hand label, and a review is a miss. Kappa compares a yes or no on whether the rig failed the answer with a yes or no on whether the hand label said bad, so a review counts as did not fail.

For ACCT-01, when the rig said an answer broke the rule, it was right 83 percent of the time. Of the answers that really broke the rule, it caught 83 percent.

For IDV-01, when the rig said an answer broke the rule, it was right 57 percent of the time. Of the answers that really broke the rule, it caught 67 percent.

For PAY-01, when the rig said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For SYS-01, when the rig said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 67 percent.

## Final decisions on the attack run

The rig's final answer matched the hand label for 72 percent of the attack answers, where the rig's final answer is pass, fail or review. Review counts as a miss, same as above.

Kappa, a measure of agreement with luck taken out, is 0.39. A kappa of 0 is no better than guessing and 1 is perfect agreement.

Agreement and kappa count different things. Agreement compares the rig's final answer (pass, fail or review) with the hand label, and a review is a miss. Kappa compares a yes or no on whether the rig failed the answer with a yes or no on whether the hand label said bad, so a review counts as did not fail.

For ACCT-01, when the rig said an answer broke the rule, it was right 20 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For IDV-01, when the rig said an answer broke the rule, it was right 25 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For PAY-01, when the rig said an answer broke the rule, it was right 50 percent of the time. Of the answers that really broke the rule, it caught 50 percent.

For SYS-01, when the rig said an answer broke the rule, it was right 90 percent of the time. Of the answers that really broke the rule, it caught 56 percent.

## End to end on the holdout set

Of the answers that broke a rule, the rig caught 79 percent. It passed 8 percent by mistake and marked 12 percent for review.

Of the answers that followed the rules, the rig failed 12 percent by mistake. That is the cry wolf rate. It sent 25 percent of them to review and passed the other 63 percent.

## The attack run

The model under test got 80 attack prompts, and 21 of them got it to break a rule, going by the hand labels. The rig caught 13 of those.

The per rule numbers are grouped by the rule each attack targeted. The router picked a different rule or no rule for 25 of the 80 attack answers. When the judge is used, it checks the rule the router picked. A wrong match can hide a violation, such as leaked instructions in an answer about payments.

The card_elicitation kind sent 20 prompts, and 1 of them got the model to break a rule. The rig caught 0 of those and missed 1.

The prompt_extraction kind sent 20 prompts, and 12 of them got the model to break a rule. The rig caught 7 of those and missed 5.

The roleplay_injection kind sent 20 prompts, and 7 of them got the model to break a rule. The rig caught 5 of those and missed 2.

The verification_bypass kind sent 20 prompts, and 1 of them got the model to break a rule. The rig caught 1 of those and missed 0.

## Per rule numbers

Precision is how often a fail was right. Recall is how many of the real rule breaks were caught. F1 blends the two into one number from 0 to 1. The off topic answers are not in the table because there is no rule for them to break.

| Set | Rule | Breaks | Precision | Recall | F1 | Answers |
| --- | --- | --- | --- | --- | --- | --- |
| holdout | ACCT-01 | 6 | 83 percent | 83 percent | 0.83 | 12 |
| holdout | IDV-01 | 6 | 57 percent | 67 percent | 0.62 | 12 |
| holdout | PAY-01 | 6 | 100 percent | 100 percent | 1.00 | 12 |
| holdout | SYS-01 | 6 | 100 percent | 67 percent | 0.80 | 12 |
| attack | ACCT-01 | 1 | 20 percent | 100 percent | 0.33 | 15 |
| attack | IDV-01 | 2 | 25 percent | 100 percent | 0.40 | 15 |
| attack | PAY-01 | 2 | 50 percent | 50 percent | 0.50 | 25 |
| attack | SYS-01 | 16 | 90 percent | 56 percent | 0.69 | 25 |
