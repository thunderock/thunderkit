---
name: tk-grill
description: "Use before planning when a request is vague or a plan has gray areas: interrogates the harness and the user with short closed questions (yes/no, one word, a number, a path) until the brief has no unknowns. Never a paragraph."
metadata:
  thunderkit:
    role: interrogator
    tier: intake
---

# tk-grill — interrogate until the brief is complete

Big-repo work fails at intake, not at typing. `tk-grill` turns a fuzzy request into a brief with
**no unknowns** by asking short, closed questions — and by making the *harness* answer in the same
constrained form so its assumptions become visible before they become code.

Answer discipline for every question here is `tk-ask`'s: **yes / no / one word / a number / a
path / `unknown`**. No sentences, no hedging, no "it depends".

Preferred model: **Fable 5.1** (cheap; grilling is many small turns). See
`../references/model-roster.md`.

## Two targets

1. **Grill the user** — resolve intent: scope, non-goals, done-state, constraints.
2. **Grill the harness** — force the agent to state, in one-word answers, what it *thinks* it
   knows: which files, which tests, which commands, which model. Every `unknown` becomes a `tk-map`
   task or a user question; nothing stays implicit.

## Question rules

- **Closed form only.** Each question must be answerable by yes/no, one word, a number, or a path.
  "How should auth work?" is banned. "Does auth stay in `src/auth/`? (yes/no)" is allowed.
- **One question per turn** to the user. Batch questions to the harness (it doesn't tire).
- **Offer the default.** Every user question carries the answer you'd pick, so "yes" is enough.
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
| critical_model | "Critical-path model? (name/ask)" | `ask` |
| review_families | "Review families? (number ≥2)" | `2` |
| unknowns | "Anything you can't answer? (list/none)" | `none` |

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

## Output contract — `.thunderkit/BRIEF.md`

The filled checklist plus the harness grill transcript. `tk-plan` refuses to plan without a
BRIEF whose `unknowns` row is `none`. Persistent selections (`critical_model`, `review_families`)
also go to `.thunderkit/config.json` via `tk-memory` so the router stops asking on this project.

## Degrade honestly

If the user says "you decide" for a row, record `default:<value>` — the choice is visible and
reversible, not buried. If the harness can't answer in the closed form after one retry, record
`unknown` and move on; don't accept a paragraph as an answer.
