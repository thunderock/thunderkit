---
name: thunderkit
description: "Use when starting big-repo multi-model work: routes a large change into parallel lanes across the best model/harness per lane, asking you to pick load-bearing models. Entry point for thunderkit."
metadata:
  thunderkit:
    role: router
    tier: entry
---

# thunderkit — router

The entry point. You reach for `thunderkit` when a change is **big enough that one model in one
pass is the wrong tool** — a large repo, a cross-cutting refactor, a feature touching many
files, a migration. thunderkit classifies the request, decides which skills and which fleet
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
2. **Recon if the repo is large/unfamiliar.** Route to `tk-map` to build/refresh a code map so
   planning works from structure, not guesses.
3. **Plan.** Route to `tk-plan` to decompose into dependency-layered disjoint lanes, each with
   acceptance criteria and a verification command.
4. **Pick load-bearing models.** Before execution, for the planning model and the critical-path
   lane, present the preferred model + the "also offer" set from the roster and **ask the user
   to choose**. Auto-pick the cheap/wide lanes (Fable 5.1) and just report them.
5. **Execute.** Route to `tk-execute` to run lanes in parallel via portable CLI dispatch, each
   in its own git worktree with a captured resumable id.
6. **Review + verify.** Route to `tk-review` for cross-family review and per-lane verification.
7. **Remember.** Route to `tk-memory` to record decisions and keep `.thunderkit/NORTH_STAR.md`
   current.

## The map (which skill owns what)

| Skill | Owns |
|---|---|
| `tk-map` | Big-repo reconnaissance / code map |
| `tk-plan` | Decompose into parallel dependency-layered lanes |
| `tk-execute` | Run lanes in parallel (portable CLI dispatch, worktrees) |
| `tk-review` | Cross-family review **and** evidence/verification gate |
| `tk-memory` | Project north-star memory + decision log |

## Asking the user to choose models (required for load-bearing lanes)

Present it concretely, from the roster:

> Critical-path lane (the auth refactor) — preferred **Opus 4.8**. Alternatives: **Opus 5**
> (login-free), **Sol** (different family). Which should implement it?

Do not proceed on a load-bearing lane until the user picks or explicitly says "you decide."

## Degrade honestly

If an agent/model the plan wants isn't installed or authed on this machine, say which lane is
affected, what you're falling back to, and what the user would install/login to get the
intended model. Never fake a lane's result.
