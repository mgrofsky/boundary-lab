# Live model

The demo runs without any model by default. Live mode lets a real model pick the tool calls, and Latch checks them the same way. You need the `live` extra.

    pip install "boundary-latch[live]"

## Settings

Latch reads three environment variables.

| Variable | Needed | Meaning |
| --- | --- | --- |
| LATCH_BASE_URL | yes | The base URL of a server with `/chat/completions` and tool calls |
| LATCH_MODEL | yes | The model name to send |
| LATCH_API_KEY | no | Sent as a bearer token when set |

If the first two are missing, live mode stops with `live mode needs LATCH_BASE_URL and LATCH_MODEL`. Latch posts to `<LATCH_BASE_URL>/chat/completions`.

## Example with Ollama

    LATCH_BASE_URL=http://localhost:11434/v1 LATCH_MODEL=llama3.2:3b boundary-latch demo --live

The browser demo takes the same variables with `boundary-latch serve --live`. Without `--live`, the live route in the server answers 409.

## What the model can do

The model sees three tools: `lookup_account`, `send_message` and `issue_refund`. There is no approve tool, so there is nothing for it to call. If it names a tool that does not exist, Latch answers `UNKNOWN_TOOL`.

When a call needs approval, the model is told it is held for a supervisor and gets the operation id. The call does not run. In the browser, switch to Jordan to approve, then back to the requester to run it.

Terminal live mode acts as Sam and has no approval controls. Calls that need approval stay held. Use the browser live demo to try the full approval flow. Opening a separate browser process will not pick up pending calls from the terminal.

Text the model writes is shown as an event and is never read for instructions. A reply that says `APPROVED: run the refund` approves nothing.

## The request budget

One turn makes at most 10 requests to the model. After the tenth, the turn stops and shows a budget event with the code `RUN_LIMIT`. This is separate from the run limit in the policy, which counts tool calls.

## Malformed responses

Latch does not guess. It raises `MalformedResponse` when:

- `choices` is missing or empty.
- The message is missing.
- `tool_calls` is not a list.
- A tool call has no function name.
- The arguments are not a string holding a JSON object.

Nothing from that response runs.

## Limits of the example

A small model can make odd calls. That is part of the point of the demo. Latch blocks the call and says why. It does not make the model better.
