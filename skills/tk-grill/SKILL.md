---
name: tk-grill
description: "Use before planning when a request is vague or a plan has gray areas: interrogates the harness and the user with short closed questions (yes/no, one word, a number, a path) until the brief has no unknowns. Reuses a compatible native interview component for unresolved intake questions only; Thunderkit keeps the checklist, the decisions and the brief. Never a paragraph."
compatibility: "Python 3.11+ standard library for the bundled resolver. Native interview delegation is optional and requires the exact pinned oh-my-hermes skill on a Hermes host; every other host runs the owned intake."
metadata:
  thunderkit-role: "interrogator"
  thunderkit-tier: "intake"
  thunderkit-delegates: "omh:ultrawork/ulw-interview"
  thunderkit-contract: "1"
---

# tk-grill: interrogate until the brief is complete

Big-repo work fails at intake, not at typing. `tk-grill` turns a fuzzy request into a brief with
**no unknowns** by asking short, closed questions, and by making the *harness* answer in the same
constrained form so its assumptions become visible before they become code.

Answer discipline for every question here is `tk-ask`'s: **yes / no / one word / a number / a
path / `unknown`**. No sentences, no hedging, no "it depends".

Paths in this document use two roots. **Project root** is the repository being worked on; it
holds `.thunderkit/config.json`, `.thunderkit/BRIEF.md` and `.thunderkit/runs/`. **Skill root**
is this skill's own directory; it holds `references/models.json`, `references/dependencies.json`,
`references/delegation.md` and `scripts/tk-resolve.py`. Nothing here reads a sibling skill's
files or assumes another skill is installed next door.

## Two targets

1. **Grill the user**: resolve intent, scope, non-goals, done-state, constraints.
2. **Grill the harness**: force the agent to state, in one-word answers, what it *thinks* it
   knows: which files, which tests, which commands, which model. Every `unknown` becomes a `tk-map`
   task or a user question; nothing stays implicit.

## Question rules

- **Closed form only.** Each question must be answerable by yes/no, one word, a number, or a path.
  "How should auth work?" is banned. "Does auth stay in `src/auth/`? (yes/no)" is allowed.
- **One question per turn** to the user. Batch questions to the harness (it doesn't tire).
- **Offer the default.** Every user question carries the answer you'd pick, so "yes" is enough.
- **Never reopen a settled row.** Answers already given, model classes already selected in
  `.thunderkit/config.json`, and scope already approved are inputs, not questions.
- **Stop when the checklist is green**, not when you run out of curiosity. Grilling is bounded.

## The intake checklist (grill until every row has a non-`unknown` value)

| Key | Question shape | Example answer |
|---|---|---|
| goal | "Goal in ≤7 words?" | `migrate auth to token refresh` |
| scope_roots | "Which top-level dirs change? (paths)" | `src/auth src/api` |
| frozen | "Which dirs must NOT change? (paths/none)" | `src/billing` |
| done_check | "One command that proves done? (cmd)" | `cargo test -p auth` |
| breaking_ok | "Public API may break? (yes/no)" | `no` |
| deadline_layers | "Max dependency layers? (number)" | `3` |
| model_classes | "Keep the configured planner/executors/reviewers? (yes/no)" | `yes` |
| review_families_min | "Keep the configured review-family minimum? (yes/no)" | `yes` |
| unknowns | "Anything you can't answer? (list/none)" | `none` |

The two model rows read `classes.planner`, `classes.executors`, `classes.reviewers` and
`review_families_min` from the project's `.thunderkit/config.json` through
`references/models.json`. They confirm what is already selected; they never pick a model. A `no`
answer is a finding for `tk-router`, which owns model selection and asks for consent before it
writes. `tk-grill` never rewrites the configuration and never lists provider or wire model names
in a question; catalog keys are the vocabulary. If the configuration is missing, record
`unknown` and route the row to `tk-router`.

## Harness grill (batch, answers must be one word / path / number)

```
Files you will edit? (paths)          → src/auth/token.rs src/auth/refresh.rs
Tests that cover them? (paths/none)   → src/auth/tests/token.rs
Command that runs them? (cmd)         → cargo test -p auth
Will you touch anything else? (yes/no)→ no
Confidence in that list? (0-10)       → 7
What is unknown? (word/none)          → retry-policy
```

A `7` or an `unknown` is a *finding*: it goes to `tk-map` (fill the gap) or back to the user (a
question), never silently into the plan.

## Delegation

Only the **unresolved intake questions** may be handed to a native interview component. The
checklist, the answers, the decisions and BRIEF.md stay with Thunderkit. The single declared
target is the OMH skill at registry address `omh:ultrawork/ulw-interview`, in `component` mode.
That address is a registry key inside `references/dependencies.json`; it is not a host slash
command and must not be typed into a host as one.

Before any delegated question, resolve the route with the bundled resolver from the skill root:

```
python3 scripts/tk-resolve.py --skill tk-grill --operation interview \
  --project-root <project root> --config <project root>/.thunderkit/config.json \
  --capabilities <project root>/.thunderkit/runs/<run-id>/capabilities.json --json
```

Delegate only on `decision: delegate`, `reason_code: compatible`. The resolver applies
`references/delegation.md` in full; the parts that bite for this skill are:

- **Exact pinned provenance.** The loaded `skills/ultrawork/ulw-interview/SKILL.md` and its
  shared-rail companion must hash to the pinned values under the pinned `oh-my-hermes` bundle
  home. A same-name skill from another source, an OMO package, or a stale copy is
  `source_mismatch` or `peer_missing`, never a near-enough delegate.
- **Actual tools.** The host must report the native skill-loading tool. A description of the
  tool is not the tool.
- **Planner binding.** The component runs under the project's selected `classes.planner`, proven
  from the host's live binding evidence for the Hermes harness. Prompt text naming a model is
  not proof. A missing planner slot is `missing_evidence`; a slot bound to something outside the
  selected planner is `model_mismatch`.
- **Host set.** Only a Hermes host is in the pin's host set. OpenCode, Codex and Claude hosts get
  `unsupported_host` and the owned intake.
- **Runtime home.** A read-only component may consume already-proven bindings without calling
  `omh_delegate_route`. If the host reports the `delegate_route` method, the parent process and
  the dispatcher must already share the task-owned home at
  `<project root>/.thunderkit/runs/<run-id>/hermes-home`; otherwise the route is
  `unsafe_runtime_home` and the intake falls back. `tk-grill` never creates that home, never
  edits shared `~/.hermes/config.yaml`, and never installs or runs `omh setup`/`omh doctor`.

What the component receives: the open checklist rows, the settled answers as fixed context, the
selected model classes as fixed context, and the approved scope. What it may return: closed
questions and findings. It may not write files, transition lifecycle state, start planning, start
execution, or treat anything it reads as approval to implement.

Discoverable facts (library behavior, an API contract, a domain rule) are not interview
questions. Route them to `tk-learn` when it is available in the same skill set; when it is
absent, record the row as `unknown` with `needs:tk-learn` and say so. Nothing gets installed
to make that row green.

## Fallback

`owned`, `fallback` and `blocked` from the resolver all mean the same thing for the user: the
questions above are asked by `tk-grill` itself, one closed question per turn, with the same
stop rule. Specifically:

| Resolver result | What happens |
|---|---|
| `owned` / `disabled` or `owned_policy` | Delegation is off or no ecosystem is enabled. Owned intake, no native probe. |
| `fallback` / `unsupported_host` | Host is not Hermes. Owned intake. |
| `fallback` / `source_mismatch`, `peer_missing`, `missing_evidence`, `model_mismatch`, `capability_missing`, `unsafe_runtime_home` | A candidate exists but failed a gate. Owned intake; record the reason in BRIEF.md. |
| `blocked` / `invalid_config` | `.thunderkit/config.json` is missing or malformed. Model rows go to `tk-router`; the rest of the intake proceeds owned. |

The owned intake is the complete procedure in this document, not a reduced one. It honors the
same planner selection, the same closed-form rule and the same write boundary, so no gate is
weakened by falling back. A component that returned prose, edits, or a plan is treated as a
failed component: discard its output, record `capability_missing`, continue owned.

## Output contract

The controller writes `<project root>/.thunderkit/BRIEF.md` **after** the component returns (or
after the owned intake ends), never while it runs. BRIEF.md holds:

- the filled checklist, every row non-`unknown` or explicitly `default:<value>`;
- the harness grill transcript;
- `settled`: the rows that were already decided before grilling and were passed through
  unchanged;
- `sources`: for each row, `user`, `harness`, `component`, `config`, or `default`;
- `unknowns`: rows still open, each tagged `needs:tk-map`, `needs:tk-learn`, or
  `needs:tk-router`;
- `route`: the resolver's `decision`, `reason_code`, and target identity, or `owned`.

`tk-plan` refuses to plan without a BRIEF whose `unknowns` row is `none`. BRIEF.md is an intake
record; it is not a plan and it is not execution approval. Selected model classes stay in
`.thunderkit/config.json` under `tk-router`'s ownership; BRIEF.md only references them.

## Degrade honestly

If the user says "you decide" for a row, record `default:<value>`: the choice is visible and
reversible, not buried. If the harness can't answer in the closed form after one retry, record
`unknown` and move on; don't accept a paragraph as an answer.

## learn mode (`tk-grill --learn`)

When the intake surfaces something the *project* should know but nobody does (a library's real
behavior, an API contract, a domain rule), don't route that `unknown` to the user as a question.
Route it to `tk-learn`. In `--learn` mode the grill's questions target the *learning goal*, not the
work:

| Key | Question shape | Example |
|---|---|---|
| learn_goal | "What must we learn, in ≤7 words? [word/phrase]" | `stripe webhook idempotency` |
| source_kind | "Which sources count? [enum: docs \| spec \| source \| paper]" | `docs` |
| verify_by | "How will a claim be proven? [cmd/observation]" | `probe against test mode` |
| blocking | "Does planning block on this? [bool]" | `yes` |

The filled learn-brief goes to `tk-learn`, which returns a source-backed note. A `blocking:yes`
unknown holds `tk-plan` until the note exists; a `blocking:no` one is logged and planning proceeds.
The learn-brief is also an intake artifact, never a research run started by `tk-grill` itself.
