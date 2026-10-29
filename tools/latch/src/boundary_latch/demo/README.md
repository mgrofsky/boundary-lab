# Northwind demo

The Northwind demo runs Latch against a fake support backend with three sample identities.

- `boundary-latch demo` plays the nine scenarios in the terminal.
- `scenarios.py` holds the scenarios. They run through `session.py`, the same code path the browser demo uses.

Durable reservations are Latch's, the fake backend forgets. After a restart the store still knows every operation id and its outcome, but the accounts, outbox and refunds start over.
