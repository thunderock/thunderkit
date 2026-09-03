---
name: tk-spec
description: "Use to clarify WHAT a big change delivers before planning: runs an ambiguity-scored Socratic loop until scope, non-goals, and rejection criteria are unambiguous, producing SPEC.md that tk-plan builds on."
metadata:
  thunderkit:
    role: spec
    tier: pre-plan
---

# tk-spec — pin down WHAT, before HOW

The parallel-thunderkit analogue of GSD's spec-phase. Before decomposition, `tk-spec` forces the
*what* to be unambiguous: what the change delivers, what it explicitly does not, and what would
make a reviewer reject it. Vague specs produce vague lanes.

Model class: **planner** (this is the one-best-brain stage). Answers use `tk-ask` discipline.

## Ambiguity gate

Score the spec 0–1 on how much a competent executor would still have to guess. **Gate: ≤ 0.20**
and every dimension (scope, interfaces, data, done-criteria, edge cases) at its minimum before
`SPEC.md` is written. Loop the Socratic questions — one closed question at a time to the user —
until the gate passes or you hit 6 rounds (then record the residual ambiguity explicitly).

## The questions that matter most

- "What would cause a reviewer to reject this?" (surfaces hidden acceptance criteria)
- "What is explicitly out of scope? [paths/none]"
- "Which existing behavior must NOT change? [paths/none]"
- "One command that proves it's done? [cmd]"
- "Public interface changes? [bool]"

## Output — `.thunderkit/SPEC.md`

Scope, non-goals, interfaces touched, data/edge cases, done-criteria (each tied to a command),
and the residual ambiguity score. `tk-plan` reads this and cuts lanes to satisfy it; a lane that
doesn't trace to a spec line is scope creep.

## When to skip

A small, well-understood change with an obvious done-command can skip straight to `tk-plan` —
`tk-router` decides. Skip is a decision, logged, not a default.
