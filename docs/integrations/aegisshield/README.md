# Using Redline in an application: AegisShield

This page shows how one application consumes Redline. AegisShield is a threat modeling and scanning web app. It calls Redline. Redline does not call it and knows nothing about it.

Status: this is a reference design. The Redline pieces it uses exist in Redline 0.5.0. The AegisShield side is planned and not built yet. Its LLM scan route is API only today, with no page and no worker, and its stub runs inline. Nothing on this page is a measured result.

[Use Redline from an application](../../using-redline-in-an-app.md) has a tested example of every Python call named here.

## The two jobs Redline does for the app

**Job 1. Policy checks for a tenant's LLM scan.** AegisShield lets a tenant register an LLM endpoint and scan it. The scan runs [garak](https://github.com/NVIDIA/garak) probes. garak's own detectors answer general questions, such as whether a jailbreak worked. Redline adds one more question: did the answer break this tenant's written rules?

**Job 2. Checks on the app's own model output.** AegisShield's prompts already tell its model how to behave, for example "Do not speculate beyond what is visible". Redline grades saved outputs against those same instructions. That shows whether the model follows them, including when the input data carries text an attacker wrote.

The first targets for Job 2 are the outputs the app produces today: the threat model, the capture report and the policy writer.

## What the app supplies

| The app supplies | Redline supplies |
| --- | --- |
| Each tenant's rules as data, or one set for its own outputs | A check on save: `validate_policy_data`. The grader: `Grader.from_rules` |
| A garak report, or the answers to check | `redline.garak.grade_report` for a report, `Grader.grade` for one answer |
| A judge model endpoint and its key | A verdict per answer: pass, fail or review, with a rule and a reason code |
| A cap on judge calls, a time limit and a way to cancel | `estimate_report_calls` before a run. A run that stops cleanly at the cap, the limit or the cancel |
| A place to store and show results | Counts with intervals, and counts of answers not evaluated and not graded |

## What the app must build first

These are on the app's side. None of them is part of Redline, and a real runner should send nothing until they exist.

- A guard on the tenant's endpoint address, so a scan cannot be pointed at an address the app must not reach.
- Storage for the endpoint's credential.
- A fixed allow list for probe names. A tenant picks from it and never supplies a name of their own.
- A separate worker image for garak.
- A table and a setup page for tenant rules.
- Outbound network rules for the scanner.

## Setup the app needs

1. **Install Redline with no extras** in the worker that grades, from a pinned commit. That install has no torch in it. Grading needs only the report file, so this worker does not need garak.
2. **Put garak in its own worker image.** It runs the probes and writes the report.
3. **Pick a judge model.** It must not be the model being checked. Any endpoint that serves `/chat/completions` works.
4. **Build the judge in the worker** with `ChatEndpointJudge(base_url, model, api_key, timeout=...)`. The key comes from the app's own secret storage. It never goes in rules, a report or a log.
5. **Store each tenant's rules as fields** in the app's database. Start from the prefilled rules below.

The app can also run Redline as a detector inside garak. Then Redline is installed beside garak, `redline garak-install` is run once there, and the judge settings come from three environment variables. See [the garak page](../../garak.md). This page uses the report path, because it keeps garak and the judge key in separate workers.

## Prefilled rules

Two rules files ship in this folder so the setup screen does not start empty.

- [rules-llm-endpoint-starter.yaml](rules-llm-endpoint-starter.yaml) is for Job 1. It holds seven rules a regulated business is likely to need: private setup, health information, account data, credentials, card numbers, medical direction, and instructions hidden in content. A tenant edits the wording, removes what does not apply and adds their own.
- [rules-aegisshield-outputs.yaml](rules-aegisshield-outputs.yaml) is for Job 2. Five of its six rules restate an instruction from an AegisShield prompt, and a comment names the prompt. The sixth, about instructions inside data, is new.

The YAML files are the starting content, not the storage format. The app stores each rule as fields and passes them to Redline as data. It never parses YAML that a tenant wrote.

Both files load with `redline.policy.load_policy`, and their content passes `redline.policy.validate_policy_data` with no problems. The rules are a starting point written for this page. No one has measured how well a judge applies them.

A rule works best when it is one sentence that a person could check by reading a single answer. [Writing rules](../../writing-rules.md) has more.

## Data flow for Job 1, a tenant's LLM scan

1. An analyst opens the setup page. The app shows the starter rules as editable fields.
2. The analyst saves. The app calls `validate_policy_data` on the fields and shows every problem it returns. Rules with no problems are stored.
3. The analyst requests a scan of the tenant's LLM endpoint.
4. The app checks the endpoint address and the probe names, stores the request and hands it to the garak worker.
5. The garak worker runs garak as a subprocess with the tenant's endpoint as the target. garak sends its probe prompts, collects the answers and writes its report file.
6. The grading worker calls `estimate_report_calls` on the report and the tenant's rules. The app can show the largest number of judge calls before any is made.
7. The grading worker calls `grade_report` with the report, the rules as data and the judge. It passes `max_judge_calls`, `deadline_seconds`, `on_progress` and `should_stop`.
8. For each answer, Redline asks the judge model whether the answer breaks one of the tenant's rules. The app's `on_progress` updates the scan's progress.
9. `grade_report` returns the summary per probe, with counts and intervals, and the graded rows. The worker stores them with the scan.
10. The analyst reads the results and reviews the answers marked review.

## Data flow for Job 2, the app's own output

1. A fixed set of synthetic inputs is prepared. Some carry an injected instruction in a field an attacker could control, such as a DNS name or a user agent.
2. The app's model writes a threat model, a capture report or a policy for each input, as it does in normal use.
3. The outputs are saved to a file.
4. The app's own code makes the checks a program can make exactly. See the next section.
5. `Grader.from_rules` grades each output against the rules in `rules-aegisshield-outputs.yaml`. `redline run --rules-only` does the same from the command line.
6. A person labels a sample of the outputs. The labels show how often the judge was right.
7. The result goes in the app's compliance notes. The app's CI then replays the saved grades with `ReplayJudge`, offline.

This path runs offline. Redline is not in the app's request path.

## Notes on the Job 2 rules

- **Code first, judge second.** Some checks a program can make exactly: an identifier that must appear in the supplied data, or a citation that must come from a fixed table. Those belong in the app's own code. Use a judge rule only for what code cannot check.
- **GROUND-01 quotes a path that is off by default.** The scan synthesis prompt it restates is on a path that is switched off by default. The rule stays in the file, and the comment above it says so.
- **Three rules quote the capture prompt.** They are NOINVENT-01, CITE-01 and PLAIN-01. That prompt is due to be rewritten, and their source lines will need to be checked again then.

## Trust boundaries

- **The tenant's LLM endpoint is untrusted.** Its answers are data. They go to the judge inside a prompt that marks them as the text to grade.
- **The judge model sees the answers and the rules.** If answers may hold private data, host the judge where that data is allowed to go.
- **Tenant rules are tenant input.** The app checks them with `validate_policy_data` when they are saved. Redline parses no tenant YAML on this path, and sets no size limits, so the app sets its own.
- **The judge key stays on the grading worker.** The garak worker does not need it, and it is never written to a report.
- **Scan data that reaches the app's own model is untrusted.** That is the case Job 2 tests.

## What can go wrong, and what the app should show

| Case | What Redline does | What the app should show |
| --- | --- | --- |
| The judge cannot be reached | Records the answer as not evaluated. Stops calling after three failures in a row. The summary says `stopped: judge_unavailable`. | The count of answers not evaluated, and that the scan is incomplete. |
| The run reaches `max_judge_calls` | Never makes the call that would pass the cap. Counts the answers left under `not_graded` with the reason `cap`. | The count not graded, and that the cap ended the scan. |
| The run reaches `deadline_seconds`, or `should_stop` returns true | Stops before the next answer. The reason is `deadline` or `cancelled`. | The count not graded, and why. |
| The judge raises an error | Writes the three files with `stopped: error`, then raises the error again. | That the scan failed, with what was graded so far. |
| The judge answers but gives no usable verdict | Review. | A review queue for a person. |
| An answer is blank | Pass, with no judge call. | Nothing special. A rule that requires the assistant to say something is not checked on a blank answer. |
| A rule is vague | The judge guesses. | Nothing can fix this at run time. Check new rules on a few known answers first. |

When a run ends early for any reason in this table, the output files are still written whole. A crash cannot leave half a file.

## Limits

- Redline is a detector. Its score is specific to the judge model, the rules and the attacks it was tested on.
- Rules only mode sends one judge call per rule per answer unless `top_k` is set. Seven rules and 500 answers is 3,500 calls. `estimate_report_calls` gives the number for a real report.
- The quality of rules only mode has not been measured.
- The garak detector, where it is used, is installed by a small file placed in garak's folder, because garak 0.17.1 does not load outside plugins. It is not an official garak plugin.
- English only.

## Diagram

[redline-in-aegisshield.excalidraw](redline-in-aegisshield.excalidraw) holds the architecture and both data flows. Open it at excalidraw.com or in your own Excalidraw with Open. Boxes marked planned are not built yet.

The diagram was drawn for Redline 0.4.0 and shows the detector path. Where it differs from this page, this page is right.
