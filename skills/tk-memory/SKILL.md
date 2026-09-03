---
name: tk-memory
description: "Use to give a project durable intent: scaffolds and maintains .thunderkit/ (north-star goals + a decision log) so the project's opinion and choices persist across sessions, agents, and model changes."
metadata:
  thunderkit:
    role: memory
    tier: context
---

# tk-memory — project north-star memory

Context is a committed artifact, not chat recall. `tk-memory` scaffolds and maintains the
project's `.thunderkit/` directory so the *why* — the project's north star and the decisions
made along the way — survives across sessions, across different agents, and across model
renames. Any agent that reads `.thunderkit/` inherits the project's opinion.

## What lives in `.thunderkit/`

| File | Purpose | Written by |
|---|---|---|
| `NORTH_STAR.md` | This project's specific goals, constraints, and non-negotiables. The "why" every lane serves. | tk-memory (you maintain) |
| `DECISIONS.md` | Append-only decision log — dated entries: what was decided, why, what was rejected. | tk-memory + tk-plan/tk-execute |
| `config.json` | **Per-project selections the router reuses**: chosen models per role (short names), min review families, max layers, frozen paths. Read by `tk-router` before it asks anything. | tk-memory (writes on user choice) |
| `BRIEF.md` | Intake checklist + harness grill transcript. | tk-grill |
| `MAP.md` | Code map. | tk-map |
| `PLAN.md` / `plan.json` | Current decomposition. | tk-plan |
| `runs/*.json[l]` | Per-lane dispatch records + resume ids. | tk-execute |
| `REVIEW.md` | Latest cross-family review + evidence. | tk-review |

`tk-memory` owns the first two; it *knows about* the rest so it can keep the north star
consistent with what actually happened.

## Scaffold procedure (new project)

1. Create `.thunderkit/` if absent.
2. Write `NORTH_STAR.md` from a short interview: What is this project's goal? What must never
   break? What's explicitly out of scope? What does "done" look like at the project level?
   Keep it tight — a north star is a page, not a spec.
3. Start `DECISIONS.md` with the seed decision (why thunderkit is being used here).
4. Add `.thunderkit/runs/` to the project's `.gitignore` **only if** the run records contain
   machine-local paths; the north star, decisions, map, plan, and review are meant to be committed.

## Selections — `config.json` (the router's memory)

Whenever the user picks a load-bearing option (a model for a role, min review families, layers,
frozen paths), write it here **and** log a `DECISIONS.md` entry. Keys are stable; values for models
are roster short names (`opus48`, `opus5`, `sol`, `fable51`) so a provider rename never breaks a
project. Schema:

```json
{
  "models": { "plan": "opus48", "critical_path": "opus5", "review": ["sol", "opus5"] },
  "review_families_min": 2,
  "max_layers": 3,
  "frozen_paths": [],
  "decided_at": "YYYY-MM-DD"
}
```

Absent key = "not decided yet" → the router asks once and you write it. To change a choice, the
user says so; you update the value, bump `decided_at`, and append the decision with the old value
as `Rejected:`.

## Decision-log entry format

Append-only. Newest first. Each entry:

```
## 2026-09-03 — Chose portable CLI dispatch over the orchestrator
- Decision: tk-execute dispatches claude/codex CLIs directly.
- Why: public/portable; no coupling to private wiring.
- Rejected: routing through a private kanban orchestrator (richer, but non-portable).
- Ref: lane L2-execute-dispatch.
```

## Maintaining the north star

- When `tk-plan` or `tk-execute` makes a load-bearing choice (model selection, a scope cut, a
  rejected approach), append it to `DECISIONS.md` — the decision log is how the next session
  learns what this one settled.
- When the north star and reality diverge (the project's goal shifted), update `NORTH_STAR.md`
  and log *that* as a decision. A stale north star is worse than none.
- On model renames, note the swap here (the roster changes the id; the log records that it
  happened and when), so history stays legible.

## Why committed, not conversational

A different agent — or you in a later session, or a teammate — opens the repo and reads
`.thunderkit/NORTH_STAR.md` + `DECISIONS.md` and immediately has the project's opinion and its
settled choices. That's the whole point: the opinion travels with the repo, so heterogeneous
agents stay aligned without re-litigating what was already decided.
