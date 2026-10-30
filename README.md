# Boundary Lab

<p align="center">
  <img src="assets/boundary-lab-logo-transparent.png" alt="Boundary Lab logo" width="360">
</p>

<p align="center">
  <a href="https://github.com/mgrofsky/boundary-lab/actions/workflows/ci.yml"><img src="https://github.com/mgrofsky/boundary-lab/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg" alt="AGPL-3.0-only license"></a>
</p>

Boundary Lab is a collection of practical safety and security tools for apps that use language models. It gives you working code to try, learn from, and use in your own projects.

The goal is to make these checks easier to understand and apply. You can test whether a chatbot follows your rules, or check whether a user is allowed to run a tool call. The examples show what happens and why.

It's for developers building applications, security people testing them, and anyone learning how these checks fit into an app.

## Projects

| Project | What you can do with it | Get started |
| --- | --- | --- |
| **Redline** | Test chatbot answers against your rules. See which violations the checks catch, what they miss, and which good answers get flagged. | [Redline README](redline/README.md) |
| **Latch** | Check tool calls before they run. Add permissions, approval steps, and limits to an application. | [Latch README](tools/latch/README.md) |

Each project stands on its own. You can use either one without setting up the other. Their READMEs hold the installation steps, examples, settings, results, and limits.

## Start with an example

Run the included example first. Change a rule or permission and see how the result changes. Then replace the sample data or functions with your own.

The examples use made-up businesses and data, so you can try things without connecting a real customer account. The code and explanations show where the checks work and where your application needs to do more.

## Help improve it

Found a confusing step, a check that misses something, or an example that would help? Open an issue. See [Contributing](CONTRIBUTING.md) for how to make a change.

Copyright (c) 2026 Matt Grofsky. Licensed under the GNU Affero General Public License, version 3 only (AGPL-3.0-only). See [LICENSE](LICENSE).
