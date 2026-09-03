---
name: tk-ship
description: "Use to close a completed big change: gates on passing cross-family review and UAT, assembles a rich PR body from the .thunderkit artifacts, and prepares a branch for merge — never pushing or merging without your go-ahead."
metadata:
  thunderkit:
    role: ship
    tier: deliver
---

# tk-ship — prepare the change for merge

The parallel-thunderkit analogue of GSD's ship. `tk-ship` closes the loop: it verifies the change
is actually shippable, assembles a PR body from the artifacts the pipeline already produced, and
prepares the branch. It **never pushes or merges on its own** — it stops at a prepared PR and
hands the go/no-go to the user.

Model class: **Fable 5.1** (assembly is mechanical). See `../references/model-roster.md`.

## Ship gates (all must pass, fail-closed)

1. **Review passed** — `REVIEW.md` exists, every lane `done`, no unresolved blocker finding.
2. **Cross-family** — `REVIEW.md` is not marked `single-family-review` (≥ `review_families_min`
   families reviewed). If it is, ship is blocked until a second family reviews.
3. **UAT clear** — no acceptance criterion in `UAT.md` is a `gap` (when UAT ran).
4. **Frozen paths untouched** — nothing in `config.json.frozen_paths` changed.

Any gate fails → block, name the gate, name the artifact that resolves it. Never ship on an
ambiguous or missing gate.

## PR body from artifacts

Assemble, don't re-derive: goal + non-goals from `SPEC.md`; decisions from `CONTEXT.md`/
`DECISIONS.md`; lanes + verification from `PLAN.md`/`REVIEW.md`; risks from `PLAN.md`; UAT
evidence from `UAT.md`. One coherent PR body that traces every claim to an artifact.

## Boundary — no auto-push, no auto-merge

Prepare the branch and the PR body; print them. Stopping here is the rule, not a limitation —
the human owns the push and the merge. (This mirrors the project convention: commit locally, wait
for go-ahead.)
