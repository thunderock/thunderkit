---
name: tk-research
description: "Use to investigate unknowns before planning a big change: fans parallel research lanes (library options, prior art, pitfalls, API behavior) across cheap wide models, each writing a focused finding, consolidated into RESEARCH.md."
metadata:
  thunderkit:
    role: research
    tier: pre-plan
---

# tk-research — parallel investigation of the unknowns

The parallel-thunderkit analogue of GSD's research step. When a plan would otherwise rest on
guesses — how a library actually behaves, what prior art exists, where the pitfalls are —
`tk-research` fans **parallel research lanes** across the wide/cheap executor models, each with a
fresh context and a narrow question, then consolidates.

Model class: **executors** (the wide, cheap ones — research is breadth). Each lane writes its own
finding; the orchestrator only collects and dedupes.

## Procedure

1. Turn the `BRIEF.md`/`SPEC.md` unknowns (the `unknown` rows from `tk-grill`) into discrete
   research questions — one per lane, disjoint.
2. Dispatch each as its own lane (portable dispatch, resume id captured — same contract as
   `tk-execute`), on a wide model, with a fresh context.
3. Each lane returns a finding: the answer, the evidence (a link, a file, a probe result), and a
   confidence. `unknown` is a valid finding — it goes back to the user.
4. Consolidate into `RESEARCH.md`: findings grouped by question, contradictions preserved (two
   sources disagreeing is signal), each with its evidence and confidence.

## Output — `.thunderkit/RESEARCH.md`

Decision-driving findings with evidence, consumed by `tk-plan` — options and rejected
alternatives in the plan should cite these, not restate assumptions.

## Boundary

Research is source-backed and read-only — it investigates, it does not implement. A finding
without evidence is a guess; label it `unverified` rather than presenting it as fact.
