---
name: tk-review
description: "Use to review and verify completed big-repo work: fans a diff to two-plus model families for cross-family review, consolidates findings by severity, and runs each lane's verification command so done means evidence, not intent."
metadata:
  thunderkit:
    role: reviewer
    tier: review
---

# tk-review — cross-family review + evidence gate

The quality gate. `tk-review` does two inseparable jobs (merged by design):

1. **Cross-family review** — fan the change to **≥2 model families** and consolidate. A model
   family reviewing its own output is not review; the author's family cannot be the only reviewer.
2. **Evidence gate** — run each lane's `verify` command. A lane without a passing verification is
   **not done** — it's blocked. Done means evidence, never intent.

Preferred reviewers: **Sol + Opus 5** (at least one different from whoever authored the lane).
Verification runs on **Fable 5.1** (running commands is cheap). See
`../references/model-roster.md`.

## Cross-family review procedure

1. **Identify the author family** per lane (from `.thunderkit/runs/<lane-id>`). Choose reviewers
   from *other* families — if Opus authored, review with Sol (+ Opus 5 as the strong same-lineage
   second, but never Opus alone).
2. **Fan the diff** to each reviewer via portable dispatch (roster dispatch table). Send the lane
   diff, its acceptance criteria, and the goal. Ask each for findings with severity
   (blocker / major / minor / nit) and a file:line anchor.
3. **Consolidate** — merge reviewer outputs, dedupe overlapping findings, keep the highest
   severity when they disagree, and record *which reviewer* raised each (families disagree — that
   disagreement is signal, preserve it).
4. **Show each reviewer's model** inline: `(Sol)`, `(Opus 5)`. Best-effort reviewers (Sol is
   credit-capped) that fail are dropped with a note, not silently omitted.

## Evidence gate procedure

For every lane in the plan:

1. Run its `verify` command from `plan.json`.
2. Record pass / fail / blocked with the actual command output (truncated), not a summary.
3. A lane is **done** only if: verify passes **and** it has no unresolved blocker-severity review
   finding. Otherwise it's `blocked` — name what's needed.

## Output contract — `.thunderkit/REVIEW.md`

```
## Lane L0-auth-token-refresh
- Author: Opus 4.8 | Reviewers: Sol, Opus 5
- Verify: `cargo test -p auth token::` → PASS (12 passed)
- Findings:
  - [major] (Sol) src/auth/token.rs:88 — backoff not jittered; thundering herd on mass expiry
  - [nit] (Opus 5) src/auth/token.rs:40 — name `t` → `token`
- Status: BLOCKED (1 major unresolved)
```

Plus a roll-up: N lanes, X done, Y blocked, and the consolidated blocker list that must clear
before the change is shippable.

## Opinions this skill enforces

- **≥2 families or it's not a review.** If only one family is available/authed, say the review is
  single-family (reduced confidence) and name what to install for a real cross-family pass —
  don't quietly downgrade.
- **No verify, not done.** A lane whose verify can't run is blocked, full stop.
- **Preserve disagreement.** When families split on a finding, record both positions; don't
  average them into mush.

## Degrade honestly

Sol credit-capped (429) and no other second family authed? Report the review as best-effort
single-family, list the specific blocker findings you *could* get, and recommend the login that
restores a cross-family gate. Never present a single-family pass as a full review.
