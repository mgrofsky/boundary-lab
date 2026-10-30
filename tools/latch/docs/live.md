# Live model

The demo runs without a model by default. Live mode lets a real model choose tool calls, which Latch checks using the same rules. Install the `live` extra first.

    pip install "boundary-latch[live]"

## Settings

Latch reads three environment variables.

| Variable | Needed | Meaning |
| --- | --- | --- |
| LATCH_BASE_URL | yes | The base URL of a server that supports `/chat/completions` and tool calls |
| LATCH_MODEL | yes | The model name to request |
| LATCH_API_KEY | no | The API key, sent as a bearer token when set |

If either of the first two variables is missing, live mode stops with `live mode needs LATCH_BASE_URL and LATCH_MODEL`. Latch sends requests to `<LATCH_BASE_URL>/chat/completions`.

## Example with Ollama

    LATCH_BASE_URL=http://localhost:11434/v1 LATCH_MODEL=llama3.2:3b boundary-latch demo --live

The browser demo uses the same variables with `boundary-latch serve --live`. Without `--live`, requests to the server's live endpoint get an HTTP 409 response.

## What the model can do

The model can request three tools: `lookup_account`, `send_message`, and `issue_refund`. It has no approval tool. If it names a tool that does not exist, Latch returns `UNKNOWN_TOOL`.

When a call needs approval, the model gets the operation ID and a message that the call is waiting for a supervisor. The call does not run. In the browser, switch to Jordan to approve it, then switch back to the requester to run it.

Terminal live mode acts as Sam and has no approval controls. Calls that need approval stay pending. Use the browser live demo to try approving and running a call. A browser demo started in a separate process will not pick up pending calls from the terminal.

The demo displays the model's text as an event. It does not treat that text as instructions. A reply that says `APPROVED: run the refund` does not approve the call.

## Requests per turn

One turn makes at most 10 requests to the model. If the model still requests tools in its tenth response, the demo handles those calls, stops the turn, and shows a budget event with the code `RUN_LIMIT`. This is separate from the policy's run limit, which counts tool calls.

## Responses the demo rejects

The live adapter, which connects the demo to the model, raises `MalformedResponse` when:

- `choices` is missing or empty.
- The message is missing.
- `tool_calls` is present but is neither a list nor `null`.
- A tool call has no function name.
- The arguments are not a string containing a JSON object.

Nothing from that response runs.

## Limits of the example

A model can ask for a call that does not fit the rules. Latch blocks it and explains why. Latch does not improve the model's answers or make its choices correct.
