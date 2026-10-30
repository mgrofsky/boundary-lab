# How Redline works

Redline reads an answer a chatbot gave, picks a rule to check, and usually asks a second model to grade it. You can also let the router decide some answers on its own. It compares the results with labels written by hand so you can see where the checks work and where they miss.

For commands you can copy, see the [Redline README](../redline/README.md) and [routing guide](routing.md).

## The data flow

```text
    rules.yaml            labeled examples (train)        attack prompts
    (human written)       good + bad per rule, by hand    80, four kinds
          |                       |                              |
          |                       v                              v
          |              embed each (prompt + answer)     send to target model
          |              build rule centers or clusters
          |                       |                              |
          v                       v                              v
    +-------------------------------------------------------------------+
    |  for each answer to grade:                                        |
    |   1 route   pick a rule, abstain, or optionally decide an answer   |
    |   2 gate    rule + user type -> sentence for answers needing judge |
    |   3 judge   second model, ONE rule, train examples only, JSON      |
    |   4 record  outcome pass|fail|review, decided_by, reason_code       |
    +-------------------------------------------------------------------+
          |
          v
    compare with hand labels (holdout set and labeled attack run)
    -> results/summary.json -> redline/README.md table (generated, never typed)
```

Three things go in. The rules live in `policy/rules.yaml`. The labeled examples live in `data/train.jsonl`. Each example has a user message, the assistant's answer, and a label saying whether it followed its rule. The attack prompts live in `data/attacks.jsonl`. Sending them to the chatbot under test gives you answers to grade.

## Step 1: route

The router turns the user message and answer together into an embedding, a list of numbers used to compare texts. A high similarity score means the model placed two texts close together. It does not prove that they mean the same thing or follow the same rule.

There are two ways to build the router:

- **Average centers (`center`, the default):** group the train examples by their rule and average each group's embeddings. That average is called a centroid, or center. A new answer goes to the closest center. Examples labeled `none` provide a center for off-topic answers.
- **K-means (`kmeans`):** divide the train embeddings into a chosen number of clusters based on how close they are. Each cluster gets the rule carried by the most members. Several clusters can get the same rule, and a cluster can mix rules. A tie goes to the rule that sorts first. A new answer takes the rule of the closest cluster center.

Both routers use a similarity cutoff picked from the train file alone. At least 95 percent of the train examples meet that cutoff when compared with their closest center. A new answer below it gets `review`. This records a need for review; it does not contact a person or create a review task.

An answer routed to `none` passes without calling the judge. That is a routing decision, so a wrong off-topic match can miss a rule break. Either router can also pick the wrong rule and send the judge down the wrong path. Only one rule is checked per answer.

### Optional decisions without the judge

By default, a routed answer goes to the judge unless the router abstains or picks `none`. Turn on `--short-circuit` to let the router pass or fail some answers using the train labels.

With average centers, the router also builds separate good and bad centers for each rule and user type. Both groups need at least five examples by default. The answer must be closer to one group by the configured margin, and the winning score must meet the routing cutoff. A closer good group gives a pass; a closer bad group gives a fail.

With k-means, the router counts good and bad labels in the chosen cluster for the current user type. By default, it needs at least five members for that user type, all sharing one label. The purity setting can lower that required share, but an even split never decides. These counts include members across the cluster's rules, so agreement on a label does not mean agreement on a rule.

If those conditions are not met, the answer goes to the judge. These shortcuts can save judge calls, but a group of similarly labeled examples can still give a wrong decision on a new answer. Check both missed violations and false alarms when trying them. The [routing guide](routing.md) covers the settings and saved comparisons.

## Step 2: gate

Some rules depend on who is asking. The account data rule lets a verified customer hear their own balance, but not an anonymous one. The gate turns the rule and user type into one sentence for the judge. For an anonymous user on the account data rule, it says that nothing in this category may be given to them.

The gate does not read the answer or decide a verdict. It only supplies context for the judge. Answers already decided by the router skip this step and the judge.

## Step 3: judge

The judge is a second model that grades one answer against one rule. Its prompt holds the rule sentence, the gate's sentence, up to six good and six bad train examples for that rule, the user message, and the answer to grade. The answer is wrapped in tags and labeled as data. This helps separate it from the instructions, but does not guarantee the judge will ignore instructions inside it.

The judge is asked for JSON with a pass or fail verdict, severity, reason code, and a short explanation. Invalid JSON, a reply that is not an object, or an unknown verdict becomes `review`. An unknown reason code becomes `OTHER`, and an invalid severity becomes empty without changing an otherwise valid verdict.

Live judge call failures become `review`. Valid live grades can be saved to a cache, keyed by a hash of the exact grading prompt. Replay looks up those saved grades. A missing replay entry raises an error, so a new routing choice may need grades that the saved cache does not contain.

## Step 4: record

Each completed answer gets one output record and one final outcome: pass, fail or review. The record includes the selected rule, router name, center or cluster number, similarity score, and whether a short circuit decided it. It says who decided, either the router or the judge, and gives a reason code and explanation. Router decisions have no severity score.

The report compares these outcomes with the hand labels and writes the numbers to `results/summary.json`. It also reports how often short circuits decided answers and how many of those decisions matched the labels.

## Choosing the judge

The saved run used `llama3.2:3b` as the chatbot and `gemma3:27b` as the judge, both running locally on Ollama. You can change either model through environment variables. Trying a judge from another model family gives you another comparison, but does not establish that its grades are right. The hand labels are what let you check that.

## Keep some examples back

The router centers, clusters, cutoffs, and shortcut counts all come from the train file. The judge's examples also come only from that file. A fixed seed makes its example selection repeatable, and `judge_examples.json` records the selected examples in the output folder.

The holdout file is a separate set of labeled answers for checking the result. Keeping it out of setup lets you try the checks on examples they have not used. If you keep changing the settings to improve the same holdout score, check the final choice on another fresh set too.
