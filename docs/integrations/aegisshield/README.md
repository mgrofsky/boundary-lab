# Using Redline in an application: AegisShield

This page shows how one application consumes Redline. AegisShield is a threat modeling and scanning web app. It calls Redline. Redline does not call it and knows nothing about it.

Status: this is a reference design. The Redline pieces it uses exist in Redline 0.4.0. The AegisShield side is planned and not built yet. Nothing on this page is a measured result.

## The two jobs Redline does for the app

**Job 1. Policy checks for a tenant's LLM scan.** AegisShield lets a tenant register an LLM endpoint and scan it. The scan runs [garak](https://github.com/NVIDIA/garak) probes. garak's own detectors answer general questions, such as whether a jailbreak worked. Redline adds one more question: did the answer break this tenant's written rules?

**Job 2. Checks on the app's own model output.** AegisShield's prompts already tell its model how to behave, for example "Base all claims strictly on the data provided". Redline grades saved reports against those same instructions. That shows whether the model follows them, including when the input data carries text an attacker wrote.

## What the app supplies

| The app supplies | Redline supplies |
| --- | --- |
| A rules file per tenant, or one for its own outputs | The grader: `Grader.from_rules`, `redline run --rules-only` |
| The answers to check, or a garak report | The garak detector `redline.PolicyRules` and `redline garak-grade` |
| A judge model endpoint and its key | A verdict per answer: pass, fail or review, with a rule and a reason code |
| A place to store and show results | Counts with intervals, and a count of answers it could not evaluate |

## Setup the app needs

1. **Install Redline** beside the app's scan worker, from a pinned commit. garak goes in the same environment as the worker that runs it.
2. **Pick a judge model.** It must not be the model being checked. Any endpoint that serves `/chat/completions` works.
3. **Set three environment variables** on the worker: `REDLINE_JUDGE_BASE_URL`, `REDLINE_JUDGE_MODEL`, `REDLINE_JUDGE_API_KEY`. The key never goes in a rules file, a report or a log.
4. **Give each tenant a rules file.** Start from the prefilled one below.
5. **For runs inside garak**, run `redline garak-install` once in the worker's environment. See [the garak page](../../garak.md) for what that does and how to undo it.

## Prefilled rules

Two rules files ship in this folder so the setup screen does not start empty.

- [rules-llm-endpoint-starter.yaml](rules-llm-endpoint-starter.yaml) is for Job 1. It holds seven rules a regulated business is likely to need: private setup, health information, account data, credentials, card numbers, medical direction, and instructions hidden in content. A tenant edits the wording, removes what does not apply and adds their own.
- [rules-aegisshield-outputs.yaml](rules-aegisshield-outputs.yaml) is for Job 2. Five of its six rules restate an instruction from an AegisShield prompt, and a comment names the prompt. The sixth, about instructions inside data, is new.

Both files load with `redline.policy.load_policy`. The rules are a starting point written for this page. No one has measured how well a judge applies them.

A rule works best when it is one sentence that a person could check by reading a single answer. [Writing rules](../../writing-rules.md) has more.

## Data flow for Job 1, a tenant's LLM scan

1. An analyst opens the setup screen. The app shows the starter rules. The analyst edits and saves them.
2. The analyst requests a scan of the tenant's LLM endpoint.
3. The app stores the request and hands it to the scan worker.
4. The worker starts garak with the tenant's endpoint as the target.
5. garak sends its probe prompts to the endpoint and collects the answers.
6. For each answer, the Redline detector asks the judge model whether the answer breaks one of the tenant's rules.
7. The detector returns a score to garak: 1.0 for a rule break, 0.0 for a pass, 0.5 for review. An answer the judge could not be reached for is recorded as not evaluated.
8. garak writes its report. The worker can also run `redline garak-grade` on that report to get a summary per probe with counts and intervals.
9. The worker stores the summary with the scan.
10. The analyst reads the results and reviews the answers marked review.

## Data flow for Job 2, the app's own output

1. A fixed set of synthetic inputs is prepared. Some carry an injected instruction in a field an attacker could control, such as a DNS name or a user agent.
2. The app's model writes a report for each input, as it does in normal use.
3. The reports are saved to a file.
4. `redline run --rules-only` grades each report against `rules-aegisshield-outputs.yaml`.
5. A person labels a sample of the reports. The labels show how often the judge was right.
6. The result goes in the app's compliance notes. A later step can replay the saved grades in CI.

This path runs offline. Redline is not in the app's request path.

## Trust boundaries

- **The tenant's LLM endpoint is untrusted.** Its answers are data. They go to the judge inside a prompt that marks them as the text to grade.
- **The judge model sees the answers and the rules.** If answers may hold private data, host the judge where that data is allowed to go.
- **The rules file is tenant input.** Redline loads it with a safe YAML loader and checks each rule's fields.
- **The judge key stays on the worker.** It is read from the environment and is never written to a report.
- **Scan data that reaches the app's own model is untrusted.** That is the case Job 2 tests.

## What can go wrong, and what the app should show

| Case | What Redline does | What the app should show |
| --- | --- | --- |
| The judge cannot be reached | Records the answer as not evaluated. Stops calling after three failures in a row. | The count of answers not evaluated, and that the scan is incomplete. |
| The judge answers but gives no usable verdict | Review. | A review queue for a person. |
| An answer is blank | Pass, with no judge call. | Nothing special. A rule that requires the assistant to say something is not checked on a blank answer. |
| A rule is vague | The judge guesses. | Nothing can fix this at run time. Check new rules on a few known answers first. |

## Limits

- Redline is a detector. Its score is specific to the judge model, the rules and the attacks it was tested on.
- Rules only mode sends one judge call per rule per answer unless `top_k` is set. Seven rules and 500 answers is 3,500 calls.
- The quality of rules only mode has not been measured.
- The garak detector is installed by a small file placed in garak's folder, because garak 0.17.1 does not load outside plugins. It is not an official garak plugin.
- English only.

## Diagram

[redline-in-aegisshield.excalidraw](redline-in-aegisshield.excalidraw) holds the architecture and both data flows. Open it at excalidraw.com or in your own Excalidraw with Open. Boxes marked planned are not built yet.
