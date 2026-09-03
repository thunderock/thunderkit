---
name: tk-map
description: "Use before planning work in a large or unfamiliar repo: builds or refreshes a code map (structure, entry points, ownership, hotspots) so plan and execute work from facts, not guesses."
metadata:
  thunderkit:
    role: recon
    tier: prep
---

# tk-map — big-repo reconnaissance

A repo too large to hold in one context window cannot be planned from memory. `tk-map` builds a
compact, durable **code map** so `tk-plan` and `tk-execute` reason about real structure. Route
here first whenever the repo is large, unfamiliar, or hasn't been mapped this session.

Preferred model: **Fable 5.1** (wide, cheap — recon fans across many files). See
`../references/model-roster.md`.

## What a code map contains

Write it to `.thunderkit/MAP.md` (committed, refreshable):

1. **Shape** — top-level modules/packages, what each is for, rough LOC per area.
2. **Entry points** — binaries, services, jobs, test roots, build/CI entry.
3. **Boundaries** — where subsystems meet (the seams lanes will be cut along).
4. **Ownership signals** — CODEOWNERS, directory conventions, per-area lint/test config.
5. **Hotspots** — highest-churn and highest-fan-in files (where a change ripples).
6. **How to verify each area** — the smallest build/test command that exercises it.

## Procedure

1. **Reuse existing intelligence first.** If the fleet has a code-graph tool available
   (codegraph, scout, or similar), use it — it's cheaper and more accurate than re-reading.
   Name which tool produced the map. If none is available, fall back to structured file/dir
   inspection and say so.
2. **Fan wide, cheaply.** Summarize each major area in parallel on Fable 5.1 rather than one
   serial deep read. The map is breadth, not depth — depth is `tk-plan`'s job per lane.
3. **Record verification per area** — every area's smallest test/build command, because
   `tk-plan` will attach one to each lane and `tk-review` will run it.
4. **Write `.thunderkit/MAP.md`** and note the timestamp + the tool used. Stale maps mislead;
   `tk-plan` should refresh if the map is older than the working branch's base.

## Output contract

`.thunderkit/MAP.md` with the six sections above, each area carrying its verification command.
This is what `tk-plan` consumes to cut disjoint, file-scoped lanes along real seams.

## Degrade honestly

No code-graph tool? Say the map is inspection-based (lower fidelity) and recommend which tool to
install. Repo too large to fully map in budget? Map the areas the requested change touches plus
their immediate boundaries, and mark the rest `unmapped` rather than guessing.
