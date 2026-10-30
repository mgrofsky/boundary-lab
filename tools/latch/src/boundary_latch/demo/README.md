# Northwind demo

The Northwind demo uses a sample support system and three example users to show how Latch checks tool calls.

- `boundary-latch demo` plays the nine scenarios in the terminal.
- `scenarios.py` contains the scenarios. They call `session.py`, which the browser demo also uses.

When you use a database file and keep it between restarts, Latch remembers operation IDs and recorded outcomes. The sample support system starts over each time, so its accounts, outbox, and refunds reset. A call Latch already recorded will not run again just because the sample data reset.
