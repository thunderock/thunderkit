---
name: tk-router
description: "Use when starting big-repo multi-model work: routes a large change into parallel lanes across the best model/harness per lane, asking you to pick load-bearing models. Entry point for the thunderkit pack."
metadata:
  thunderkit:
    role: router
    tier: entry
---

# tk-router — the router

The entry point. You reach for `tk-router` when a change is **big enough that one model in one
pass is the wrong tool** — a large repo, a cross-cutting refactor, a feature touching many
files, a migration. `tk-router` classifies the request, decides which skills and which fleet
models the work needs, and hands off. It does not implement — it routes.

Read `../references/model-roster.md` first. It is the source of truth for every model id and
which work type prefers which model. Never hardcode a model id here.

## The thunderkit thesis (enforce it, don't just cite it)

Big work in big repos is won by **decomposition + heterogeneity**, not by one smart model. See
`../../NORTH_STAR.md`. As router you enforce the opinions: no single-model plans, cross-family
review, evidence-gated done, degrade-and-name for missing agents, **ask the user for
load-bearing model choices**, commit project context.

## Routing procedure

1. **Size the work.** If it fits in one context window and is one coherent edit, say so and
   suggest a plain single-agent edit — thunderkit is overhead for small work. Otherwise continue.
2. **Grill if anything is gray.** Route to `tk-grill` to fill `.thunderkit/BRIEF.md` with closed
   answers. Skip only when the request already names scope, frozen paths, and a done-command.
3. **Recon if the repo is large/unfamiliar.** Route to `tk-map` to build/refresh a code map so
   planning works from structure, not guesses.
4. **Plan.** Route to `tk-plan` to decompose into dependency-layered disjoint lanes, each with
   acceptance criteria and a verification command.
5. **Pick load-bearing models — from config first.** Read `.thunderkit/config.json`. For any
   model key already present, use it and say so. For any absent key (planning model, critical
   path), present the preferred model + "also offer" set from the roster, **ask the user**, then
   have `tk-memory` write the answer. Auto-pick cheap/wide lanes (Fable 5.1) and just report them.
6. **Execute.** Route to `tk-execute` to run lanes in parallel via portable CLI dispatch, each
   in its own git worktree with a captured resumable id.
7. **Review + verify.** Route to `tk-review` for cross-family review and per-lane verification.
8. **Remember.** Route to `tk-memory` to record decisions and keep `.thunderkit/NORTH_STAR.md`
   and `config.json` current.

## Where selections live (per project) — `.thunderkit/config.json`

The router never asks the same load-bearing question twice on one project. Choices go through
`tk-memory` into `.thunderkit/config.json` (committed), and the router **reads it first**:

```json
{
  "models": { "plan": "opus48", "critical_path": "opus5", "review": ["sol", "opus5"] },
  "review_families_min": 2,
  "max_layers": 3,
  "frozen_paths": ["src/billing"],
  "decided_at": "2026-09-03"
}
```

Rules: a key present → use it and *report* it ("critical path: Opus 5, per project config"); a
key absent → ask, then write it. The user can override any run with a one-line instruction,
which also updates the file and logs a `DECISIONS.md` entry. Short names resolve to ids via the
roster, so a model rename never invalidates a project's config.

## Intake first: `tk-grill` + `tk-ask`

Before `tk-plan`, a request with any gray area goes to `tk-grill`, which fills
`.thunderkit/BRIEF.md` using `tk-ask`'s closed-answer discipline (yes/no/word/number/path/
`unknown`). `tk-plan` refuses a BRIEF with open unknowns.

## The map (which skill owns what)

| Skill | Owns |
|---|---|
| `tk-ask` | Answer discipline: yes/no/word/number/path/`unknown`, hard word cap |
| `tk-grill` | Closed-question intake of user + harness → `.thunderkit/BRIEF.md` |
| `tk-map` | Big-repo reconnaissance / code map |
| `tk-plan` | Decompose into parallel dependency-layered lanes |
| `tk-execute` | Run lanes in parallel (portable CLI dispatch, worktrees) |
| `tk-review` | Cross-family review **and** evidence/verification gate |
| `tk-memory` | Project north-star memory, decision log, **`config.json` selections** |

## Asking the user to choose models (required for load-bearing lanes)

Present it concretely, from the roster:

> Critical-path lane (the auth refactor) — preferred **Opus 4.8**. Alternatives: **Opus 5**
> (login-free), **Sol** (different family). Which should implement it?

Do not proceed on a load-bearing lane until the user picks or explicitly says "you decide."

## Degrade honestly

If an agent/model the plan wants isn't installed or authed on this machine, say which lane is
affected, what you're falling back to, and what the user would install/login to get the
intended model. Never fake a lane's result.
