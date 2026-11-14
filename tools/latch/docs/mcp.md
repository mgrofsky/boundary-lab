# Latch for MCP servers

`boundary-latch mcp` sits between an MCP client and one MCP server. The client can be Claude Code, Cursor, or any other MCP host. The client starts the proxy as its stdio MCP server. The proxy starts the real server as a child process and relays messages both ways.

Every `tools/call` goes through Latch `prepare` and `execute` before it reaches the server. A call the policy blocks never reaches the server. A call that needs approval waits until a person approves it on a small page in the browser.

    boundary-latch mcp --policy policy.toml [options] -- <server command> [args...]

The proxy needs nothing beyond Latch itself. It uses the Python standard library and pydantic.

## Quick start

1. Write a starter policy from the server's own tool list.

        boundary-latch mcp --print-policy -- npx -y @modelcontextprotocol/server-filesystem ~/notes > policy.toml
        boundary-latch check policy.toml

2. Point your MCP client at the proxy instead of the server. The examples below show how.

3. Start the client. The proxy prints the approval page address on stderr. The page opens in your browser the first time a call waits for approval.

## Claude Code

    claude mcp add notes -- boundary-latch mcp --policy /path/to/policy.toml -- npx -y @modelcontextprotocol/server-filesystem /path/to/notes

## Any MCP host

Most hosts read an `mcpServers` map. Put the proxy where the server command was, and move the server command after `--`.

    {
      "mcpServers": {
        "notes": {
          "command": "boundary-latch",
          "args": [
            "mcp", "--policy", "/path/to/policy.toml", "--pin", "/path/to/pins.json",
            "--db", "/path/to/latch.sqlite",
            "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "/path/to/notes"
          ]
        }
      }
    }

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--policy FILE` | none | The TOML policy. Required, except with `--print-policy`. |
| `--user NAME` | `agent` | The user id Latch records for the agent's calls. |
| `--org NAME` | `local` | The organization id. |
| `--permissions A,B` | see below | The permissions the agent holds. |
| `--approver NAME` | your login name | The user id of the person on the approval page. It must differ from `--user`. |
| `--db PATH` | `:memory:` | The SQLite file for operations and audit rows. Use a file to read the rows later with `boundary-latch audit`. |
| `--tool-timeout SECONDS` | `60` | How long one tool call may take. After that the outcome is `UNCERTAIN`. It also limits how long the proxy waits for its own requests, such as its `tools/list`. |
| `--approval-wait SECONDS` | `120` | How long a call waits for a decision. `0` means it does not wait. |
| `--approval-port N` | `0` | The approval page port. `0` picks a free port. `-1` turns the page off. |
| `--no-open` | off | Do not open the approval page in a browser. |
| `--pin FILE` | none | A JSON file that keeps tool definitions pinned between runs. |
| `--print-policy` | off | Print a starter policy for the server's tools and exit. |
| `--flow` | off | With `--print-policy`, turn on the flow rule and give every tool all three labels. |

By default the agent holds every `require_permission` named in the policy. It never holds an `approve_permission`, even when another tool requires the same string. The proxy refuses to start when one tool uses the same string for both.

One proxy process is one Latch run. Each process gets a new run id, and each `tools/call` gets a new operation id. A run has a time limit of 900 seconds and a limit of 20 calls unless the policy says otherwise. A coding session lasts longer, so set these in the policy:

    [limits]
    max_actions_per_run = 1000
    run_lifetime_seconds = 0
    run_idle_seconds = 3600

`run_lifetime_seconds = 0` removes the fixed lifetime. `run_idle_seconds` ends the run after an hour without tool calls. Restart the client to start a new run.

## What the client sees

The proxy passes every message through unchanged, in both directions, except `tools/list`, `tools/call`, and the `tasks/` methods other than `tasks/get` and `tasks/cancel`. That covers initialize, `server/discover`, ping, resources, prompts, sampling, elicitation, roots, progress, cancel, logging, `subscriptions/listen`, `tasks/get`, and `tasks/cancel`.

`tools/list` shows only the server's tools that the policy names. A tool the policy does not name is hidden. A tool whose name does not fit Latch's pattern is hidden too. So is a tool whose `inputSchema` names `$inputResponses` or `$requestState`, the two argument names the proxy keeps for input rounds. Names start with a letter, then up to 63 letters, digits, underscores, dots, or hyphens. The proxy logs hidden names to stderr once. A call to a hidden tool returns `UNKNOWN_TOOL`.

A blocked call is a normal result with `isError` set to true and one text block:

    Blocked by Latch: MISSING_PERMISSION. The user does not have the permission this tool requires.

It is not a JSON-RPC error, so the model can read why and tell the person.

Every `tools/list` from the client makes the proxy fetch the server's whole list again, every page. The pins then check what the server has now, so a cached list never hides a change. The answer is built from an allow list, so a server cannot put text beside the tools that a client might trust:

- `tools`, the filtered and pinned list.
- `resultType`, always `"complete"`.
- `ttlMs`, when the server's last page has it as an int of 0 or more.
- `cacheScope`, when the server's last page has it as `"public"` or `"private"`.
- `_meta`, holding only `io.modelcontextprotocol/serverInfo`, when the server sent that as an object.

Every other field of the server's page is dropped, such as `nextCursor`, `instructions`, or a second list of tools. A `tools/call` uses the list the proxy already has. It fetches the list only when there is none, or after the server sent `notifications/tools/list_changed`. A change that arrives while the proxy fetches the list keeps the list stale, so the next call fetches it again.

The proxy waits at most `--tool-timeout` seconds for the server's answer to its own `tools/list`. After that the client's `tools/list` gets a `-32603` error, "could not list tools". A `tools/call` that needed the list gets `PROXY_ERROR`.

Outcomes from the server reach the client like this:

| The server | Latch records | The client gets |
| --- | --- | --- |
| returns a result | `EXECUTED` | the server's result, unchanged |
| returns a result with `isError` true | `FAILED` | the server's result, unchanged |
| returns a result with `resultType` `"input_required"` | `EXECUTED` | the server's result, unchanged |
| returns a JSON-RPC error | `FAILED` | that error, under the client's own request id |
| times out or exits in the middle of a call | `UNCERTAIN` | `isError` true with the code and explanation |

## Arguments

The proxy builds an arguments model for each tool from the top level `properties` of its `inputSchema`. A name in `required` must be present. An argument the schema does not list is refused with `INVALID_ARGUMENTS`. The proxy does not check types inside an argument; the server does its own validation.

The server sees exactly the arguments Latch checked. The proxy parses each client message and writes it again from the parsed value. It never forwards the client's raw bytes. Only the tool name, the checked arguments, the input round, and the allowed `_meta` keys go to the server.

Only these keys of the client's `params._meta` go to the server: `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo`, `io.modelcontextprotocol/clientCapabilities`, `io.modelcontextprotocol/logLevel`, `progressToken`, `traceparent`, `tracestate`, and `baggage`. They are protocol and tracing metadata, not part of what the tool is asked to do. So they are not in the request hash and not on the approval page, and Latch does not check them.

The call that runs after an approval is the client's repeat, and it brings its own `_meta`. Any other key, such as `vendor/actAs`, could change between what a person approved and what runs. So the proxy drops every other key and logs each dropped key name once. The only `params` keys the proxy understands are `name`, `arguments`, `_meta`, `inputResponses`, and `requestState`. A `tools/call` with any other key gets a `-32602` invalid params error that names the key, and it never reaches the server.

A line that is not valid JSON gets a `-32700` error. A JSON array (a batch) gets `-32600`. A `tools/call` with a repeated key anywhere in it also gets `-32600`. None of these reach the server. NaN, Infinity, and numbers too large for a float are not valid JSON here.

For `over_amount`, the amount must be a finite number. A string, a bool, a missing value, or NaN is refused with `INVALID_ARGUMENTS`. For `recipient_field`, the recipient must be a string on the allowed list.

## Input rounds

In MCP 2026-07-28 a server can answer a `tools/call` with `resultType` `"input_required"`, a set of `inputRequests`, and an opaque `requestState`. The client asks the person, then sends the same `tools/call` again with `inputResponses` and the `requestState`. That reply changes what the call does, so Latch checks it.

The proxy puts `inputResponses` and `requestState` into the arguments Latch checks, under the names `$inputResponses` and `$requestState`. They start with `$`, so a tool's own argument is unlikely to use them by accident. Every proxied tool's arguments model has these two optional fields. The request hash, the summary, and the approval page cover them. When the call runs, the proxy moves them back to `params.inputResponses` and `params.requestState`. They are never sent inside `arguments`.

- `inputResponses` must be an object and `requestState` a string, or the call gets `-32602`.
- A client `tools/call` whose `arguments` already holds either name gets `-32602`. Otherwise the model could write the person's answer itself.
- Each round is its own Latch operation, checked like any call: policy, limits, the flow rule, and approval when the policy asks for it.
- The approval page shows the two fields under their own headings, apart from the arguments.
- An `"input_required"` result from the server is a normal result. Latch records the round as `EXECUTED`, and the client gets the result unchanged.

A round must answer a request the server made, in this proxy process:

- When a `tools/call` result has `resultType` `"input_required"`, the proxy keeps a record for that tool and those arguments. It holds the `requestState`, or that there was none, and the keys of `inputRequests`.
- Arguments are compared as canonical JSON, without the input round.
- A `tools/call` that carries `inputResponses` or `requestState` passes only when a record exists for the same tool and arguments. Its `requestState` must equal the recorded one, and both absent counts as equal. Every key of `inputResponses` must be one the server asked for.
- Otherwise the proxy refuses the call before `prepare`, with the code `INPUT_NOT_REQUESTED`: "This call carries an answer the server did not ask for." It writes an audit row with that code.
- A record is used up when a round that matches it is sent to the server. A new `"input_required"` result for the same tool and arguments replaces it.
- A result whose `requestState` is not a string, or whose `inputRequests` is not an object, leaves no record. No answer to it passes.
- The proxy keeps at most 1000 records and drops the oldest first.
- A round that passes still goes through Latch like any call.

## Protocol versions

The proxy works with clients and servers of both eras: the older form that starts with `initialize`, and MCP 2026-07-28, where every request carries the protocol version, the client's name, and its capabilities in `_meta`. The proxy does not choose an era. It follows what the messages show.

- The proxy keeps the protocol fields (`io.modelcontextprotocol/protocolVersion`, `clientInfo`, and `clientCapabilities`, nothing else) from the latest client request that had them. It puts them on every request of its own, such as its `tools/list`. Before any client request has carried them, it sends none.
- It takes them only from a request it accepted, never from one it refused. The version must be a string of at most 32 letters, digits, dots, and hyphens. `clientInfo` and `clientCapabilities`, when present, must be objects. Otherwise the request's fields are not kept. A new set replaces the old one as a whole.
- Results the proxy writes itself carry `resultType` `"complete"`: blocked, waiting, and uncertain results, and the `tools/list` answer. Older clients ignore the field.
- `subscriptions/listen` passes through. Its answer may never come. When the client cancels it, the proxy forgets its id. The server tags each notification on the stream with `io.modelcontextprotocol/subscriptionId`, the id of the listen request. The server only saw the proxy's id for it, so the proxy puts the client's id back. A notification for a subscription that is no longer open is dropped, since the client could not match it.
- `notifications/tools/list_changed` makes the proxy forget its list, whatever `_meta` it carries.
- A message from the server that is not a valid JSON-RPC shape is dropped and logged. That includes an object with both `method` and `result` or `error`, and a response with both `result` and `error`.
- A message from the server never stops the proxy. A subscription id that is not an int was never the proxy's, so its notification is dropped. An error while the proxy handles one line from the server drops that line, and the log names only the error's type.
- If the proxy stops reading the server while the server still runs, every call in flight gets `PROXY_ERROR`. The proxy then stops the server and exits with code 1.
- Text from the server that the proxy logs or prints keeps printable characters only. That covers tool names, error messages, and versions. Any other character becomes `?`, and the text is cut at 200 characters. Terminal escape codes never reach your terminal through the proxy.

## Approval

A call that needs approval waits on the approval page. The page lists each waiting call with the tool, a one line summary, the flow reason when the flow rule applies, the full arguments, the operation id, and how long it has waited. A person clicks Approve or Deny. The decision goes through `Latch.approve` or `Latch.deny` with the reference `approval-page`. Each form carries a digest of the request hash, the summary, and the flow reason as the page drew them. If any of them changed since, for example because other calls gave the run new labels, the page answers "This page is out of date, refresh it." and records nothing. The person refreshes and decides again.

The approver is the user from `--approver`. That user holds every `approve_permission` in the policy, in the same organization as the agent.

- Approved: the call runs and the client gets the result.
- Denied, or cancelled: the client gets a blocked result.
- No decision before `--approval-wait` ends: the client gets `isError` true. The text says the call waits for a person, gives the operation id, and asks to repeat the same call later.

A repeated call with the same tool and the same arguments reuses the waiting operation. Once a person approves it, the next repeat runs it. After an operation has run or closed, a repeat is a new operation.

If the client sends `notifications/cancelled` for a call that waits for approval, the proxy cancels the operation as the requester. The client gets no answer for that request. A cancel that arrives while the proxy fetches the tool list for the call counts too: the call is not prepared and does not run, and one that was already prepared is cancelled.

With `--approval-port -1` there is no page, so a call that needs approval is refused at once with `NEEDS_APPROVAL`.

The flow rule can apply after `prepare`, when another call adds a label in between. Then `execute` refuses with `NEEDS_APPROVAL` and the reason. The proxy does not pass that refusal to the client. It prepares the call again, waits for a person like any other approval, and runs it after approval. With the page off, the client gets `NEEDS_APPROVAL` and the reason.

### The page

The page runs on `127.0.0.1` only. Each proxy process makes a random key and prints the full address with the key on stderr. Every request must carry that key. The page refuses a request whose `Host` header is not `127.0.0.1` or `localhost` with the right port, which stops DNS rebinding. Decisions are POST only. The page logs each request without its query string, and never logs the key after the startup line.

The page sends `Cache-Control: no-store`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a Content-Security-Policy that allows no outside loads. While nothing waits and no definition changed, it refreshes itself every two seconds. While it shows something, it does not reload by itself, so an item never moves under the pointer just before a click. It shows a Refresh link and the time it was drawn instead. Everything it shows came from the model or the server, so it escapes all of it. The key never appears in anything sent to the client.

## Pinned tool definitions

The proxy hashes the whole tool object it serves: name, description, `inputSchema`, title, annotations, `outputSchema`, and any other field. The hash is the full sha256 of the object as canonical JSON, and every compare uses all 64 hex characters. `ToolSpec.version` shows only the first 32; it never decides whether a tool changed. The first definition it sees for a name is pinned. If the server later gives the same name a different definition, the tool is changed. Calls to it return `TOOL_CHANGED` until a person accepts the new definition on the approval page.

The page shows the pinned and new definitions side by side. Until a person accepts, `tools/list` keeps showing the pinned definition. After acceptance it shows the new one. The proxy writes an audit row with event `tool_changed` when it finds a change and `tool_accepted` when a person accepts.

Without `--pin`, pins last for the process only. With `--pin FILE`, the proxy reads the pins at start and writes them each time they change. Each entry holds the hash, the first seen time, and the definition. The file is replaced in one step, so a crash never leaves half a file. A change between runs is caught too. If the file cannot be read, the proxy refuses to start.

Earlier builds of the proxy stored 12 hex characters: first of a hash over only the name, description, and `inputSchema`, later of the whole object hash. When the proxy loads a pin written either way, it hashes the kept definition both ways. If one matches the stored value, the proxy pins it again under the full hash without asking, since nothing changed. A pin that does not match is left alone, so the tool shows as changed.

The proxy learns tools from the server's `tools/list`, following `nextCursor` to the end. When the server sends `notifications/tools/list_changed`, the proxy fetches the list again on next use and passes the notification on. A call that waited for approval is such a use: after the approval and before the call runs, the proxy fetches the list again if the server said it changed. If the tool changed during the wait, the call returns `TOOL_CHANGED` and does not reach the server, and the approval does not carry over to the new definition. If the tool left the list, the call returns `UNKNOWN_TOOL`.

## Starter policy

    boundary-latch mcp --print-policy -- <server command> [args...]

This starts the server, lists its tools, prints a TOML policy to stdout, and exits. It first sends `server/discover` with protocol version 2026-07-28. A server that answers with `supportedVersions`, or with an unsupported version error that lists versions, gets the newest version both sides know. A server that answers with any other error, or not within five seconds, gets the `initialize` handshake. A tool whose name was listed before is written once, and stderr says which one was left out. A tool that uses a reserved argument name is left out too, and stderr says so. Each tool gets `require_permission = "mcp:use"`, `approve_permission = "mcp:approve"`, and approval mode `always`. The tool's description goes in a comment above it, cut to one line of 100 characters. Names that need quoting in TOML are quoted. The output passes `boundary-latch check`.

Every call needs approval at first. Loosen it from there. A read only tool can drop the approval lines, for example:

    [tools.read_file]
    require_permission = "mcp:use"

## Flow labels

The flow rule stops one run from reading untrusted input, reading private data, and acting outside. See [flow.md](flow.md). For a GitHub server, a labelled policy looks like this:

    [flow]
    all_three = "approve"

    [tools.get_issue]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["untrusted"]

    [tools.get_file_contents]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["sensitive", "untrusted"]

    [tools.create_pull_request]
    require_permission = "mcp:use"
    approve_permission = "mcp:approve"
    labels = ["external"]

    [tools.list_branches]
    require_permission = "mcp:use"
    labels = []

`get_file_contents` carries `untrusted` too, since a file in a public repository is text an outsider wrote. Reading an issue and a file, then opening a pull request, waits on the approval page with the reason.

To start from the safe side, add `--flow`:

    boundary-latch mcp --print-policy --flow -- <server command> [args...]

This turns on `[flow]` with `all_three = "approve"` and gives every tool all three labels. A head comment tells you to remove the labels that do not apply. If the server sent annotations for a tool, such as `readOnlyHint`, `destructiveHint`, or `openWorldHint`, a comment shows them as the server's own claim. The proxy never uses them to choose labels, because a server can claim anything.

## Limits

- Only tool calls are checked. Resources and prompts pass through unchecked.
- An agent that can run shell commands on the same machine can reach the approval page, or edit its own MCP config to skip the proxy. The proxy protects against a misled model, not against code running on your machine.
- The proxy does not detect copied values yet, so `copied_arguments` has no effect here.
- Use one proxy per database file.
- A server can answer a `tools/call` with a task handle. Latch records that as one finished call. What the task does later is not checked.
- `tasks/get` and `tasks/cancel` pass through. Every other `tasks/` method, such as `tasks/update`, could feed input into a call that already ran through Latch, with nothing checked. The proxy refuses such a request with a `-32601` error that says "Blocked by Latch: input to a running task is not checked yet". It refuses a `tasks/get` or `tasks/cancel` that carries `inputResponses` or `requestState` the same way. It drops such a notification and logs it.
- A server of the older form can send requests to the client, such as sampling, elicitation, and roots. The client's answers to them pass through unchecked.
- `_meta` keys of a `tools/call` outside the list under "Arguments" are dropped. A server that needs another key does not get it.
- The server's own stderr is not the proxy's output. It reaches your terminal as the server wrote it.
- In MCP 2026-07-28 a server reports list changes only on a `subscriptions/listen` stream the client opened. A client that opens none still gets a fresh list on each `tools/list`, but a `tools/call` uses the list the proxy fetched last.
- Only stdio servers are supported.
- Each proxy process starts a new run id. Flow labels do not carry across a restart of the proxy, or between two proxied servers, while the model's context does. A model that read untrusted text through one server can act through another with no label set.
- Only tool definitions are pinned. Server instructions, resource and prompt text, and any other text outside a tool definition pass through unpinned and unchecked.
