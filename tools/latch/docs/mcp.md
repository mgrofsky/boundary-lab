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
| `--tool-timeout SECONDS` | `60` | How long one tool call may take. After that the outcome is `UNCERTAIN`. |
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

The proxy passes every message through unchanged, in both directions, except `tools/list` and `tools/call`. That covers initialize, ping, resources, prompts, sampling, elicitation, roots, progress, cancel, and logging.

`tools/list` shows only the server's tools that the policy names. A tool the policy does not name is hidden. A tool whose name does not fit Latch's pattern is hidden too. Names start with a letter, then up to 63 letters, digits, underscores, dots, or hyphens. The proxy logs hidden names to stderr once. A call to a hidden tool returns `UNKNOWN_TOOL`.

A blocked call is a normal result with `isError` set to true and one text block:

    Blocked by Latch: MISSING_PERMISSION. The user does not have the permission this tool requires.

It is not a JSON-RPC error, so the model can read why and tell the person.

Outcomes from the server reach the client like this:

| The server | Latch records | The client gets |
| --- | --- | --- |
| returns a result | `EXECUTED` | the server's result, unchanged |
| returns a result with `isError` true | `FAILED` | the server's result, unchanged |
| returns a JSON-RPC error | `FAILED` | that error, under the client's own request id |
| times out or exits in the middle of a call | `UNCERTAIN` | `isError` true with the code and explanation |

## Arguments

The proxy builds an arguments model for each tool from the top level `properties` of its `inputSchema`. A name in `required` must be present. An argument the schema does not list is refused with `INVALID_ARGUMENTS`. The proxy does not check types inside an argument; the server does its own validation.

The server sees exactly the arguments Latch checked. The proxy parses each client message and writes it again from the parsed value. It never forwards the client's raw bytes. Only the tool name and the checked arguments go to the server. Any other field in the `tools/call` params, such as `_meta`, is dropped.

A line that is not valid JSON gets a `-32700` error. A JSON array (a batch) gets `-32600`. A `tools/call` with a repeated key anywhere in it also gets `-32600`. None of these reach the server. NaN, Infinity, and numbers too large for a float are not valid JSON here.

For `over_amount`, the amount must be a finite number. A string, a bool, a missing value, or NaN is refused with `INVALID_ARGUMENTS`. For `recipient_field`, the recipient must be a string on the allowed list.

## Approval

A call that needs approval waits on the approval page. The page lists each waiting call with the tool, a one line summary, the flow reason when the flow rule applies, the full arguments, the operation id, and how long it has waited. A person clicks Approve or Deny. The decision goes through `Latch.approve` or `Latch.deny` with the reference `approval-page`.

The approver is the user from `--approver`. That user holds every `approve_permission` in the policy, in the same organization as the agent.

- Approved: the call runs and the client gets the result.
- Denied, or cancelled: the client gets a blocked result.
- No decision before `--approval-wait` ends: the client gets `isError` true. The text says the call waits for a person, gives the operation id, and asks to repeat the same call later.

A repeated call with the same tool and the same arguments reuses the waiting operation. Once a person approves it, the next repeat runs it. After an operation has run or closed, a repeat is a new operation.

If the client sends `notifications/cancelled` for a call that waits for approval, the proxy cancels the operation as the requester. The client gets no answer for that request.

With `--approval-port -1` there is no page, so a call that needs approval is refused at once with `NEEDS_APPROVAL`.

The flow rule can apply after `prepare`, when another call adds a label in between. Then `execute` refuses with `NEEDS_APPROVAL` and the reason. The proxy does not pass that refusal to the client. It prepares the call again, waits for a person like any other approval, and runs it after approval. With the page off, the client gets `NEEDS_APPROVAL` and the reason.

### The page

The page runs on `127.0.0.1` only. Each proxy process makes a random key and prints the full address with the key on stderr. Every request must carry that key. The page refuses a request whose `Host` header is not `127.0.0.1` or `localhost` with the right port, which stops DNS rebinding. Decisions are POST only.

The page sends `Cache-Control: no-store`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a Content-Security-Policy that allows no outside loads. While nothing waits and no definition changed, it refreshes itself every two seconds. While it shows something, it does not reload by itself, so an item never moves under the pointer just before a click. It shows a Refresh link and the time it was drawn instead. Everything it shows came from the model or the server, so it escapes all of it. The key never appears in anything sent to the client.

## Pinned tool definitions

The proxy hashes the whole tool object it serves: name, description, `inputSchema`, title, annotations, `outputSchema`, and any other field. The first definition it sees for a name is pinned. If the server later gives the same name a different definition, the tool is changed. Calls to it return `TOOL_CHANGED` until a person accepts the new definition on the approval page.

The page shows the pinned and new definitions side by side. Until a person accepts, `tools/list` keeps showing the pinned definition. After acceptance it shows the new one. The proxy writes an audit row with event `tool_changed` when it finds a change and `tool_accepted` when a person accepts.

Without `--pin`, pins last for the process only. With `--pin FILE`, the proxy reads the pins at start and writes them each time they change. Each entry holds the hash, the first seen time, and the definition. The file is replaced in one step, so a crash never leaves half a file. A change between runs is caught too. If the file cannot be read, the proxy refuses to start.

Before 0.4.0 the hash covered only the name, description, and `inputSchema`. When the proxy loads a pin written that way, it checks the kept definition against the old hash. If it matches, the proxy pins it again under the new hash without asking, since nothing changed. A pin that does not match is left alone, so a real change still shows.

The proxy learns tools from the server's `tools/list`, following `nextCursor` to the end. When the server sends `notifications/tools/list_changed`, the proxy fetches the list again on next use and passes the notification on.

## Starter policy

    boundary-latch mcp --print-policy -- <server command> [args...]

This starts the server, lists its tools, prints a TOML policy to stdout, and exits. Each tool gets `require_permission = "mcp:use"`, `approve_permission = "mcp:approve"`, and approval mode `always`. The tool's description goes in a comment above it, cut to one line of 100 characters. Names that need quoting in TOML are quoted. The output passes `boundary-latch check`.

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
- Only stdio servers are supported.
