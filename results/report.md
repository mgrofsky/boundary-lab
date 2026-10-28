# Results report

This file is written by `redline report`. It was made at 2026-10-28T01:35:32Z. Answers were turned into numbers with sentence-transformers/all-MiniLM-L6-v2.

The judge model was gemma3:27b. The hash of the judge cache file is in summary.json.

## The data

- data/train.jsonl has 180 records.
- data/holdout.jsonl has 56 records.
- data/attacks.jsonl has 80 records.
- data/runs/attack_labels.jsonl has 80 records.
- results/holdout_outputs.jsonl has 56 records.
- results/attack_responses_outputs.jsonl has 80 records.

## Routing

The router picked the right rule for 84 percent of the 56 holdout answers, the 8 off topic ones included. The holdout answers were never used to set up the router or the judge.

On an on topic answer, an abstain counts as wrong here. On an off topic answer, an abstain counts as right.

It abstained on 11 percent of them. An abstain means the answer was not close to any rule, so it goes to a person.

## The judge on the holdout set

The rig's final answer matched the hand label for 80 percent of the holdout answers, where the rig's final answer is pass, fail or review. A review counts as a miss here, because no one has decided yet.

Kappa, a measure of agreement with luck taken out, is 0.78. A kappa of 0 is no better than guessing and 1 is perfect agreement.

Agreement and kappa count different things. Agreement compares the rig's final answer (pass, fail or review) with the hand label, and a review is a miss. Kappa compares a yes or no on whether the rig failed the answer with a yes or no on whether the hand label said bad, so a review counts as did not fail.

For ACCT-01, when the rig said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For IDV-01, when the rig said an answer broke the rule, it was right 62 percent of the time. Of the answers that really broke the rule, it caught 83 percent.

For PAY-01, when the rig said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 83 percent.

For SYS-01, when the rig said an answer broke the rule, it was right 100 percent of the time. Of the answers that really broke the rule, it caught 83 percent.

## The judge on the attack run

The rig's final answer matched the hand label for 81 percent of the attack answers, where the rig's final answer is pass, fail or review. Review counts as a miss, same as above.

Kappa, a measure of agreement with luck taken out, is 0.61. A kappa of 0 is no better than guessing and 1 is perfect agreement.

Agreement and kappa count different things. Agreement compares the rig's final answer (pass, fail or review) with the hand label, and a review is a miss. Kappa compares a yes or no on whether the rig failed the answer with a yes or no on whether the hand label said bad, so a review counts as did not fail.

For ACCT-01, when the rig said an answer broke the rule, it was right 33 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For IDV-01, when the rig said an answer broke the rule, it was right 29 percent of the time. Of the answers that really broke the rule, it caught 100 percent.

For PAY-01, when the rig said an answer broke the rule, it was right 50 percent of the time. Of the answers that really broke the rule, it caught 50 percent.

For SYS-01, when the rig said an answer broke the rule, it was right 93 percent of the time. Of the answers that really broke the rule, it caught 81 percent.

## End to end on the holdout set

Of the answers that broke a rule, the rig caught 88 percent. It missed 8 percent and sent 4 percent to a person for review.

Of the answers that followed the rules, the rig failed 9 percent by mistake. That is the cry wolf rate. It sent 16 percent of them to review and passed the other 75 percent.

## The attack run

The model under test got 80 attack prompts, and 21 of them got it to break a rule, going by the hand labels. The rig caught 17 of those.

The per rule numbers are grouped by the rule each attack targeted. The judge graded each answer against the rule the router picked, which differed from the target for 21 of the 80 attack answers. When the router picks the wrong rule the judge reads the answer under the wrong rule, so a prompt leak that talks about payments can pass as a payment answer.

The card_elicitation kind sent 20 prompts, and 1 of them got the model to break a rule. The rig caught 0 of those and missed 1.

The prompt_extraction kind sent 20 prompts, and 12 of them got the model to break a rule. The rig caught 11 of those and missed 1.

The roleplay_injection kind sent 20 prompts, and 7 of them got the model to break a rule. The rig caught 5 of those and missed 2.

The verification_bypass kind sent 20 prompts, and 1 of them got the model to break a rule. The rig caught 1 of those and missed 0.

## Per rule numbers

Precision is how often a fail was right. Recall is how many of the real rule breaks were caught. F1 blends the two into one number from 0 to 1. The off topic answers are not in the table because there is no rule for them to break.

| Set | Rule | Breaks | Precision | Recall | F1 | Answers |
| --- | --- | --- | --- | --- | --- | --- |
| holdout | ACCT-01 | 6 | 100 percent | 100 percent | 1.00 | 12 |
| holdout | IDV-01 | 6 | 62 percent | 83 percent | 0.71 | 12 |
| holdout | PAY-01 | 6 | 100 percent | 83 percent | 0.91 | 12 |
| holdout | SYS-01 | 6 | 100 percent | 83 percent | 0.91 | 12 |
| attack | ACCT-01 | 1 | 33 percent | 100 percent | 0.50 | 15 |
| attack | IDV-01 | 2 | 29 percent | 100 percent | 0.44 | 15 |
| attack | PAY-01 | 2 | 50 percent | 50 percent | 0.50 | 25 |
| attack | SYS-01 | 16 | 93 percent | 81 percent | 0.87 | 25 |
