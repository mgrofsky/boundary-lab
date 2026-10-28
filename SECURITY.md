# Reporting a security problem

If you find a security problem in this repository, please report it privately. Use the Security tab on GitHub and choose "Report a vulnerability". That opens a private report that only the maintainer can see.

Please do not open a public issue for security problems.

I will reply within a week, and I will say what I plan to do and when. When a fix lands, the report is credited to you unless you ask otherwise.

## What counts

- A way to make the judge pass an answer that breaks a rule, using text inside the answer.
- A way to leak a key or local file through the command line tool or the promptfoo scripts.
- A bug in the metrics that makes the published numbers wrong.

## What does not count

- The chatbot under test breaking its own rules. That is what the project measures. If you have a new attack prompt, open a pull request that adds it to data/attacks.jsonl.
- Problems in the models themselves. Report those to the people who publish the model.
