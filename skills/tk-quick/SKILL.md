---
name: tk-quick
description: "Use when a task is small but not trivial: run it on one model (the planner class from .thunderkit/config.json, or model=<key> from models.json) with no plan document or parallel lanes, atomic commits and tests, and exactly one reviewer from a different model family before each commit."
compatibility: "Python 3.11+ standard library for the bundled resolver and model helpers; a shell, git and a supported channel for the selected model plus one reviewer of another family. No native peer is required."
metadata:
  thunderkit-role: "quick"
  thunderkit-tier: "execute"
  thunderkit-delegates: "none"
  thunderkit-contract: "1"
---

# tk-quick: a small task on one model, with one review

`tk-quick` sits between `tk-fast` and the full lifecycle. It runs a small task on one chosen
model, with atomic commits and tests, and gets exactly one review from a model of a different
family before each commit. There is no plan document and there are no parallel lanes.

## Model choice

- Default author: the `classes.planner` model from `.thunderkit/config.json`.
- Override: `model=<key>`, where `<key>` must be a key in `references/models.json`. An unknown
  key is rejected; never guess a nearby model.
- Reviewer: one model from `classes.reviewers` whose family in `references/models.json` differs
  from the author's. If no configured reviewer has a different family, stop as blocked; never
  review with the same family.

When the user must choose, use the host's structured choice tool (options as buttons, the
recommended one first, plus free text); fall back to a numbered list only when the host has none.

## Scope

Use it when the task needs a handful of files and one owner but is more than a trivial edit.
If the task needs parallel lanes, a design decision or a plan review, route it to `tk-plan`
through `tk-router`.

## Delegation

`tk-quick` delegates nothing (`thunderkit-delegates: none`). GSD quick mode requires a GSD
project (`.planning/ROADMAP.md`), so it is not a target. After validating the configuration the
resolver returns `owned`:

```
python3 scripts/tk-resolve.py --skill tk-quick --operation quick --config .thunderkit/config.json --json
```

That result proves only that the route is owned; it is not evidence the task ran.

## Fallback

The owned procedure is the skill:

1. Confirm the author model (default or `model=`) and one different-family reviewer.
2. Make the change on the author model; keep each commit to one logical step.
3. Run the relevant tests before each commit.
4. Give the reviewer the diff and the test output; address findings or record why not.
5. Commit only after the review returns. No push, PR, tag or publish.

Without a valid configuration, stop as blocked and ask for one; never pick a model silently.

## Asking the user

When this skill needs a decision from the user, ask through the host's structured choice tool as described in `references/asking.md`: one decision per question, two to four options with the recommended one first, free text always accepted. Use the numbered-list fallback only when the host has no such tool; in a non-interactive run record `unknown` and stop at the gate.

## Output contract

```
task: <one sentence>
author: <model key> (<family>)
reviewer: <model key> (<family>)
commits: <sha subject>...
tests: <command> -> <exit code>
review: approved | changes addressed | blocked: <reason>
```
