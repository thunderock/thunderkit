---
name: tk-discuss
description: "Use before planning to capture implementation decisions and resolve gray areas: adaptive questioning that records choices and their rejected alternatives in CONTEXT.md so tk-plan and tk-execute inherit settled decisions."
metadata:
  thunderkit:
    role: discuss
    tier: pre-plan
---

# tk-discuss — settle decisions before they become code

The parallel-thunderkit analogue of GSD's discuss-phase. Between spec and plan, `tk-discuss`
surfaces the implementation decisions a plan would otherwise make silently — library choices,
patterns, migration order, compatibility — and records each with its rejected alternatives, so
every executor lane inherits the same settled ground instead of re-deciding mid-lane.

Model class: **planner** asks and frames; the **user decides**. `tk-ask` discipline for answers.

## Procedure

1. Read `SPEC.md` and `MAP.md`. Identify the decisions a plan must assume.
2. For each gray area, ask one closed question with the option you'd pick as default.
3. Record every decision as `Decision / Why / Rejected` — the rejected branch is what stops a
   later session or a different agent from re-opening it.
4. Note anything deferred ("not now") separately so it isn't lost or silently pulled in.

## Output — `.thunderkit/CONTEXT.md`

A `## Decisions Captured` section (grouped by category) and a `## Noted for Later` section.
`tk-plan` treats captured decisions as fixed constraints; `tk-memory` mirrors the load-bearing
ones into `DECISIONS.md` so they persist project-wide.

## Why it matters for parallel work

Parallel lanes are dangerous when they each make an independent architectural guess — three lanes
can each pick a different error-handling pattern. `tk-discuss` makes those choices once, up front,
so the lanes stay coherent when they merge.
