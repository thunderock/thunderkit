---
name: tk-audit
description: "Use to check a milestone actually achieved its intent before archiving: aggregates every lane's verification, checks cross-lane integration and requirements coverage across all model families, and fails closed on orphaned or unverified requirements."
metadata:
  thunderkit:
    role: audit
    tier: deliver
---

# tk-audit — did the milestone actually land

The parallel-thunderkit analogue of GSD's audit-milestone. Individual lanes passing doesn't mean
the milestone achieved its intent — integration can be broken, requirements can be orphaned.
`tk-audit` aggregates the whole run and checks done-ness against the *original* intent, with the
full reviewer set.

Model class: **reviewers** (all authed families — the audit is the last blind-spot check).

## Procedure

1. **Aggregate verifications** — collect every lane's `REVIEW.md`/`UAT.md` result. A lane missing
   its verification is a blocker, not a pass.
2. **Cross-lane integration** — check the seams: the disjoint lanes were merged; do the E2E user
   flows that cross lane boundaries actually work? A parallel decomposition's risk is exactly at
   the joints.
3. **Requirements coverage** (3-source cross-reference) — every requirement in `SPEC.md` should
   appear satisfied in a lane's verification AND exercised in `UAT.md`. Mismatches:
   - required but no lane verified it → **orphaned** (treat as unsatisfied)
   - verified but not in the spec → scope creep (flag it)
4. **Fail gate** — any orphaned or unverified requirement fails the audit. Fail closed.

## Output — `.thunderkit/AUDIT.md`

Per-requirement final status (satisfied / partial / orphaned), the integration findings, and the
overall milestone verdict. Only a clean audit clears the milestone for archive via `tk-memory`.

## Why the full reviewer set

The audit is where a single family's blind spot would do the most damage — a missed integration
gap ships. Every authed family looks, and disagreement between them is surfaced, not averaged.
