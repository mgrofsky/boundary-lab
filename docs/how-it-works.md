# How Redline works

Redline reads an answer a chatbot gave, works out which rule the answer is about, and asks a second model to grade it against that one rule. Then it compares those grades with labels I wrote by hand, so you can see how often the rig is right.

## The data flow

```text
    rules.yaml            labeled examples (train)        attack prompts
    (human written)       good + bad per rule, by hand    80, four kinds
          |                       |                              |
          |                       v                              v
          |              embed each (prompt + answer)     send to target model
          |              average per rule = one center    (small open model)
          |                       |                              |
          v                       v                              v
    +-------------------------------------------------------------------+
    |  for each answer to grade:                                        |
    |   1 route   embed it, pick the closest center, abstain if far     |
    |   2 gate    rule + user type -> one sentence the judge must apply |
    |   3 judge   second model, ONE rule, train examples only, JSON     |
    |   4 record  outcome pass|fail|review, decided_by, reason_code     |
    +-------------------------------------------------------------------+
          |
          v
    metrics vs hand labels (holdout set and labeled attack run)
    -> results/summary.json -> README table (generated, never typed)
```

Three things go in. The rules live in policy/rules.yaml. The labeled examples live in data/train.jsonl, and each one is a user message, the assistant's answer, and my label saying whether the answer followed its rule. The attack prompts live in data/attacks.jsonl, and they get sent to the model under test so there are fresh answers to grade.

## Step 1: route

First the rig has to know which rule an answer is about. It turns the user message and the answer into an embedding, which is a list of numbers that captures what the text means. Texts about the same thing end up with similar numbers. For each rule, the rig averages the embeddings of that rule's train examples into one point called a center. There is also a center for off topic answers. A new answer goes to whichever center it is closest to. If it is far from every center, the router abstains and the answer goes to a person for review. The cutoff for "far" is picked from the train file alone: it is the highest value at which 95 percent of train examples still get routed. An answer that routes to off topic passes, because no rule applies to it. If the router picks the wrong rule, the judge grades the answer against the wrong rule, so a routing miss can hide a rule break.

## Step 2: gate

Some rules depend on who is asking. The account data rule lets a verified customer hear their own balance, but not an anonymous one. The rule says who may get this kind of information. The gate turns that and the user type into one sentence the judge must apply. For an anonymous user on the account data rule, the sentence says that nothing in this category may be given to them, so any such detail in the answer breaks the rule. For a rule that allows the information for nobody, it says so. The gate never decides on its own. It can't, because it never reads the answer. A careful refusal and a leak look the same to it, so it hands its sentence to the judge and lets the judge read the answer.

## Step 3: judge

The judge is a second model that grades one answer against one rule. Its prompt holds the rule sentence, the gate's sentence, a few good and bad train examples for that rule, the user message, and the answer to grade. The answer is wrapped in tags and labeled as data, so text inside it is not supposed to be read as instructions. The judge has to reply with JSON: a verdict of pass or fail, a severity from 0 to 1, a reason code from the rule's list, and one sentence saying why. If the reply is not valid JSON, or the verdict is anything other than pass or fail, the answer goes to review. A broken judge reply never counts as a pass. Every judge answer is saved to data/judge_cache.jsonl, keyed by a hash of the exact prompt text. That is what lets anyone replay the published run without a key.

## Step 4: record

Each answer ends up with one output record and one final outcome: pass, fail or review. The record says who decided, either the router (abstain or off topic) or the judge. It also carries the reason code, the judge's sentence, and the severity. There is never more than one verdict on a record, so there is nothing to reconcile later. Once every answer has a record, the rig compares the outcomes with my hand labels and writes the numbers to results/summary.json.

## Why the judge is a different kind of model

The model under test is a small open model. The judge comes from a different model family. Models from the same family tend to share blind spots: if one misses a certain kind of leak, its sibling may miss it too. A judge from another family is more likely to see what the target missed. Both are set with environment variables, so you can swap in any chat endpoint.

## Why the judge only sees train examples

The judge's examples come only from data/train.jsonl. They are picked once, with a fixed seed, and written to judge_examples.json in the output folder so you can read the exact prompt. The holdout file, data/holdout.jsonl, is a set of labeled answers kept back for testing. Nothing in it is used to build the centers or to pick judge examples. If the judge had seen holdout answers as examples, it could score well by matching them, and the holdout number would tell you nothing about new answers.
