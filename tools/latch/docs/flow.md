# The flow rule

An agent is easy to mislead when one run does three things. It reads text an outsider wrote. It reads private data. It can act on the outside world. A known leak worked exactly that way. An agent read a public issue, then a private repository, then opened a public pull request. Each tool was trusted. The chain was the problem.

Latch keeps state for each run, so it can see the chain. The flow rule stops the call that would complete it, or makes that call wait for a person.

## Turn it on

    [flow]
    all_three = "approve"

    [tools.get_issue]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["untrusted"]

    [tools.get_file]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["sensitive"]

    [tools.create_pull_request]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["external"]

`all_three` takes one of three values.

- `allow`: the rule is off. This is the default. Labels may be set; Latch records them but never gates a call.
- `approve`: the call that would complete the chain waits for a person, whatever its approval mode says.
- `deny`: that call is refused with `FLOW_BLOCKED`.

When the rule is on, every tool must set `labels`. Use `labels = []` for a tool that has none. A tool without the key makes Latch reject the policy, and the error names the tool. In `approve` mode, every tool with a label needs `approve_permission`.

## The three labels

Labels describe what a tool does. You choose them; Latch takes your word.

| Label | Meaning | Examples |
| --- | --- | --- |
| `untrusted` | The tool's result can carry text an outsider wrote. | Read an issue, an email, a web page, a support ticket, a customer note. |
| `sensitive` | The tool reads private data. | Read a private repository, an account balance, a medical record, a secret store. |
| `external` | The tool changes state or sends data out. | Open a pull request, send a message, post a comment, issue a refund, fetch a URL the model chose. |

A tool can carry more than one label. A tool that reads a web page the model named is both `untrusted` and `external`, because the address itself sends data out. A tool that only reads data your own team wrote, and sends nothing, gets `labels = []`.

When you are not sure, add the label. A label you did not need costs an approval. A label you left out can let the chain through.

## How it decides

A run holds a label from the moment a call carrying it is reserved to run. Labels are never removed within a run. A call that fails, or whose outcome is unknown, keeps its labels, since the result may still have reached the model.

The rule gates a call when it is on and either of these is true.

1. The run's labels plus this tool's labels make all three, and the run does not hold all three yet.
2. The run already holds all three, and this tool has the label `external`.

A tool with no labels is never gated.

The order does not matter. Reading a private file, then opening a pull request, then reading an issue is gated at the issue.

## The reason

When the rule gates a call, Latch builds one plain sentence that says why. It names what set each label the run holds and what this call adds.

    This run read untrusted input (get_issue) and private data (get_file). This call acts outside (create_pull_request).

The sentence is built from stored facts, never from model text. It names tools, or says "marked by the app" for `mark_run`, or "copied value in send_message" for a copied value. It is on `PreparedAction.flow_reason`, `OperationStatus.flow_reason`, the operation row, the audit row, and the explanation of a `FLOW_BLOCKED` or `NEEDS_APPROVAL` refusal.

## Prepare and execute

`prepare` checks the rule with the run's labels as they are then. In `deny` mode it returns a `Decision` with `FLOW_BLOCKED`. In `approve` mode it sets `needs_approval` and `flow_reason`.

`execute` checks again, inside the same database transaction that reserves the call and writes its labels. Another call may have added a label since `prepare`. Two calls at once cannot both pass as the call that does not complete the chain.

- In `deny` mode the operation is closed with `FLOW_BLOCKED`, like `RUN_LIMIT` at `execute`.
- In `approve` mode an approval counts only if the approver saw the reason that holds now. If there is no approval, or it was given for an empty or different reason, the tool does not run. The operation goes back to waiting, with the new reason, and the old approval is dropped. `execute` returns outcome `denied` with `NEEDS_APPROVAL` and the reason. The call shows in `pending()`.

Prepare the same operation ID again to get a `PreparedAction` with the new reason. Approve that one, then run it.

    result = latch.execute(prepared, ctx)
    if result.code == Code.NEEDS_APPROVAL:
        prepared = latch.prepare(ctx, tool, args, operation_id=prepared.operation_id)
        # show prepared.flow_reason to the approver, then approve and execute

## Mark the run yourself

Some untrusted text reaches the model without a tool call. A user uploads a file, or your app pastes an email into the prompt. Tell Latch with `mark_run`.

    latch.mark_run(ctx, "untrusted", note="user uploaded notes.txt")

The label must be one of the three. The note is one line of at most 200 characters. Latch keeps it as the label's source and writes an audit row with event `mark_run`. The reason names it only as "marked by the app".

A run can reach all three labels this way with no call to refuse. Rule 2 then gates every later `external` call, in `deny` mode too.

## With Dye

A call whose arguments hold a copied value gives the run the `untrusted` label when it is reserved. Pass the paths as you already do:

    prepared = latch.prepare(ctx, "send_message", args, operation_id=op, copied=report)

Dye, another Boundary Lab project, finds copied values. The rule checks the call itself by its tool's labels only. The copied value counts from the moment the call is reserved, so later calls see it. To hold or stop the call that carries the copied value, set `copied_arguments = "approve"` or `"deny"` on that tool. See [policy.md](policy.md).

## With the MCP proxy

Set labels in the policy the proxy loads. `boundary-latch mcp --print-policy --flow` writes a starter policy with the rule on in `approve` mode and all three labels on every tool. Remove the labels that do not apply. See [mcp.md](mcp.md).

The approval page shows the reason above the arguments. A call that the rule gates only at `execute` goes back to the approval wait and runs after a person approves it. With the page off it is refused with `NEEDS_APPROVAL` and the reason.

## Limits

- Labels are the policy author's claim. Latch does not look inside a tool to check them.
- A new run ID clears the labels. Your app must not start a new run just to get past the rule. Keep one run ID for one conversation.
- The rule sees tool calls, plus what you pass to `mark_run` and `copied`. Text that reaches the model another way is invisible to it.
