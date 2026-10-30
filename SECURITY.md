# Reporting a security problem

If you find a security problem, report it privately. Open the Security tab on GitHub and choose "Report a vulnerability" to send a private report to the project maintainer.

Don't open public issues for security problems.

I'll reply within a week and explain what I plan to do and when. When the fix is ready, I'll credit you for the report unless you'd rather stay unnamed.

## What counts

- Text in an answer that tricks Redline's checking model into passing an answer that breaks a rule.
- A way to expose a key or local file through the command line tool or the promptfoo scripts.
- A calculation bug that makes the published results wrong.

## What does not count

- A chatbot breaking the rules you're testing. That's what Redline is meant to measure. To add another attack example, open a pull request with the prompt in `data/attacks.jsonl`.
- Problems in a model itself. Report those to the people who publish that model.
