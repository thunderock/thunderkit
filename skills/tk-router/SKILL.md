---
name: tk-router
description: "Use when starting big-repo multi-model work: sizes the change, asks you to pick three model classes (one planner, a set of executors, everyone as reviewers), and routes through the thunderkit lifecycle — grill, map, plan, execute, review, ship. Entry point for the thunderkit pack."
metadata:
  thunderkit:
    role: router
    tier: entry
---

# tk-router — the router

The entry point. You reach for `tk-router` when a change is **big enough that one model in one
pass is the wrong tool** — a large repo, a cross-cutting refactor, a feature touching many
files, a migration. `tk-router` classifies the request, gets the **model classes** chosen,
and hands off through the lifecycle. It does not implement — it routes.

Read `../references/model-roster.md` first. It is the source of truth for every model id and
which work type prefers which model. Never hardcode a model id here.

## The thunderkit thesis (enforce it, don't just cite it)

Big work in big repos is won by **decomposition + heterogeneity**, not by one smart model. See
`../../NORTH_STAR.md`. As router you enforce the opinions: no single-model plans, cross-family
review, evidence-gated done, degrade-and-name for missing agents, **the user picks the model
classes**, commit project context.

## Step 0 — Model classes (ask once per project, then remember)

Every thunderkit run uses **three classes of model**. On a project with no
`.thunderkit/config.json`, ask these three questions — closed form, `tk-ask` style — before
anything else. On a project that has one, read it and *report* the classes instead of asking.

| Class | Cardinality | Question to the user | Default offer (from roster) |
|---|---|---|---|
| **Planner** | exactly **one**, the most capable model available | "Planner? [enum: opus48 \| opus5]" | `opus48` (→ `opus5` if no Anthropic login) |
| **Executors** | **a set**; lanes are spread across it by lane weight | "Executors? [multi: opus48 \| opus5 \| sol \| fable51]" | `opus48 opus5 fable51` — heavy lanes to the strongest, wide/cheap lanes to Fable 5.1 |
| **Reviewers + verifiers** | **all** of the above, plus any other authed family | "Reviewers = everyone authed? [bool]" | `yes` — every model reviews; the author's family never reviews alone |

Why three classes: planning is a single point of failure (one best brain), execution is a
throughput problem (many hands, matched to lane weight), and review is a blind-spot problem
(every family looks, so no one family's blind spot survives). One-model plans are rejected by
`tk-plan`; single-family review is rejected by `tk-review`.

Write the answers via `tk-memory` to `.thunderkit/config.json`:

```json
{
  "classes": {
    "planner": "opus48",
    "executors": ["opus48", "opus5", "fable51"],
    "reviewers": "all"
  },
  "review_families_min": 2,
  "max_layers": 3,
  "frozen_paths": [],
  "decided_at": "YYYY-MM-DD"
}
```

Rules: a key present → use it and say so ("planner: Opus 4.8, per project config"); absent → ask,
then write. The user can override any run in one line, which also updates the file and logs a
`DECISIONS.md` entry. Short names resolve to ids via the roster, so a model rename never
invalidates a project's config. If a chosen model isn't authed on this machine, **degrade and
name it** — never silently substitute.

## The lifecycle (routing procedure)

thunderkit mirrors the GSD phase loop — *discuss → plan → execute → verify → ship* — with every
stage made parallel and cross-model. Route in this order; skip a stage only when its artifact
already exists and is fresh.

| # | Stage | Skill | Artifact in `.thunderkit/` | Model class |
|---|---|---|---|---|
| 1 | **Size** | (you) | — | — |
| 2 | **Intake** — closed-question grill of user + harness | `tk-grill` (+ `tk-ask`) | `BRIEF.md` | Fable 5.1 (cheap turns) |
| 3 | **Spec** — WHAT is delivered, ambiguity-scored | `tk-spec` | `SPEC.md` | planner |
| 4 | **Map** — parallel code recon along seams | `tk-map` | `MAP.md` | executors (wide) |
| 5 | **Discuss** — implementation decisions, gray areas | `tk-discuss` | `CONTEXT.md` | planner asks, user decides |
| 6 | **Research** — parallel investigation of unknowns | `tk-research` | `RESEARCH.md` | executors (wide) |
| 7 | **Plan** — disjoint dependency-layered lanes | `tk-plan` | `PLAN.md` + `plan.json` | **planner** (one) |
| 8 | **Plan check** — cross-family critique of the plan | `tk-review --plan` | `PLAN-REVIEW.md` | reviewers (all) |
| 9 | **Execute** — lanes in parallel, worktrees, resume ids | `tk-execute` | `runs/` | **executors** (set) |
| 10 | **Review + verify** — cross-family diff review + evidence gate | `tk-review` | `REVIEW.md` | **reviewers** (all) |
| 11 | **UAT** — conversational walk-through of what was built | `tk-verify-work` | `UAT.md` | reviewers |
| 12 | **Debug** — scientific-method loop when 10/11 fail | `tk-debug` | `debug/<slug>.md` | planner + executors |
| 13 | **Ship** — PR body from artifacts, gates, no auto-merge | `tk-ship` | — | Fable 5.1 (assembly) |
| 14 | **Docs** — parallel doc write + verify against code | `tk-docs` | — | executors + reviewers |
| 15 | **Audit** — milestone done-ness vs original intent | `tk-audit` | `AUDIT.md` | reviewers (all) |
| 16 | **Remember** — north star, decisions, config | `tk-memory` | `NORTH_STAR.md`, `DECISIONS.md`, `config.json` | any |

**Minimum path** for a mid-size change: 1 → 2 → 4 → 7 → 9 → 10 → 16.
**Full path** for a milestone: all of it. `tk-plan` refuses a BRIEF with open unknowns;
`tk-execute` refuses a plan with no `PLAN-REVIEW.md` when `review_families_min ≥ 2`;
`tk-ship` refuses without a passing `REVIEW.md`.

## Asking the user (closed form, from the roster)

Present it concretely:

> Planner — one model, most capable. `[enum: opus48 | opus5]` (default `opus48`)
> Executors — a set; heavy lanes go to the strongest listed. `[multi: opus48 opus5 sol fable51]`
> Reviewers — everyone authed reviews every lane. `[bool]` (default `yes`)

Do not proceed until the user picks or explicitly says "defaults."

## Degrade honestly

If an agent/model a class wants isn't installed or authed on this machine, say which class and
which lanes are affected, what you're falling back to, and what the user would install/login to
get the intended model. Never fake a lane's result. Fewer than two reviewer families → the run is
marked `single-family-review` in `REVIEW.md` and `tk-ship` refuses.
