---
name: tk-router
description: "Use when starting big-repo multi-model work: sizes the change, has you pick three model classes (one planner, a set of executors, reviewers) from the local catalog, checks which workflow backend and sibling tk-* stages are actually available, and routes through the thunderkit lifecycle with plan review gated before execution. Entry point for the thunderkit pack."
compatibility: "Python 3.11+ for the local read-only resolver and model helper; file access to the project's .thunderkit/ directory; sibling tk-* skills are optional and reported when absent."
metadata:
  thunderkit-role: "router"
  thunderkit-tier: "entry"
  thunderkit-delegates: "none"
  thunderkit-contract: "1"
---

# tk-router — the router

The entry point. You reach for `tk-router` when a change is **big enough that one model in one
pass is the wrong tool** — a large repo, a cross-cutting refactor, a feature touching many
files, a migration. `tk-router` sizes the request, gets the **model classes** chosen, works out
which workflow backend can honor them, and hands off through the lifecycle one stage at a time.
It does not implement, plan, or review — it routes, and it owns the policy for doing so.

## Paths: skill root versus project root

Two roots matter. They are distinct responsibilities, and every command names both explicitly,
whether or not they happen to be the same directory on a given host:

- **Skill root** is the directory containing this `SKILL.md`. Everything the router needs to
  reason about models and routing lives under it: `references/models.json` (the model catalog),
  `references/config.schema.json`, `references/dependencies.json`, `references/delegation.md`,
  `references/model-roster.md`, and the helpers `scripts/model_config.py`,
  `scripts/capability_gates.py` and `scripts/tk-resolve.py`. Resolve these relative to the skill
  root only. Do not reach for `../references`, a repository checkout path, or another skill's
  copy; in a single-skill installation those do not exist.
- **Project root** is the repository being worked on. Project state lives in its `.thunderkit/`
  directory: `config.json`, the per-stage artifacts named in the lifecycle table below, and
  `runs/`. The resolver treats this root as the boundary for evidence paths: a `--config` that
  resolves outside it is rejected as `invalid_config`, so always pass `--project-root`
  explicitly rather than relying on the current working directory.

Sibling `tk-*` skills are separate installations. Before handing off to one, check whether the
host has it loaded (its skill listing or skill tool). A sibling that is not loaded is an
**unavailable stage**: name it, say what it would have produced, and stop that stage. Never
invent a slash command for it, read its files by guessing a path, or install it.

## Model classes — chosen by the user, remembered by the project

Every thunderkit run uses three classes of model, and **the user picks them**:

| Class | Cardinality | Why it is its own class |
|---|---|---|
| **Planner** | exactly one | Planning is a single point of failure; one best brain writes the plan. |
| **Executors** | a nonempty ordered set | Execution is throughput; lanes are spread across the set by weight, strongest first. |
| **Reviewers** | an explicit set, or the literal `all` | Review is a blind-spot problem; `all` means every reachable catalog model, not just the planner and executors. |

The catalog is `references/models.json` under the skill root. It is the only source of model
keys, labels, families, provider IDs, and per-harness mappings. Do not carry a second roster in
this skill or in your head; if a key is not in the catalog, it is not a choice.

### Bootstrap (no `.thunderkit/config.json`)

Bootstrap is model-free and needs no project configuration. Do this before anything that would
require a config:

1. Read the catalog and list the keys with their labels and families. Annotate which ones the
   current host can map (a harness entry exists for this host) and which need auth or host
   configuration. Annotation is information, not a choice made on the user's behalf.
2. Ask the three closed questions, `tk-ask` style, with enums built from the catalog:
   planner `[enum: <catalog keys>]`, executors `[multi: <catalog keys>]`, reviewers
   `[multi: <catalog keys> | all]`. Do not proceed until the user picks; offering to pick for them
   is not picking.
3. Hand the answers to `tk-memory` to write the canonical `schema_version: 2` file described in
   `references/config.schema.json`. The three classes are required user selections with no
   defaults. Operational keys (`review_families_min`, `max_layers`, `frozen_paths`,
   `ecosystems`, `delegation`) get their documented defaults in memory when absent; nothing
   rewrites a file just to add them. `decided_at` is different: it is an optional timestamp the
   writer may record, and when it is absent it stays absent. No default, no placeholder, no
   generated date.

### Route (config exists)

Read the config and **report** the classes; do not re-ask. Run the local resolver to validate
and normalize what was chosen. The script and its references live under the skill root; the
config lives under the project root; both are passed by name, quoted, and the project root is
never left to the current working directory:

```sh
SKILL_ROOT="/path/to/the/directory/containing/this/SKILL.md"
PROJECT_ROOT="/path/to/the/repository/being/worked/on"
python3 "$SKILL_ROOT/scripts/tk-resolve.py" --skill tk-router --operation route \
  --project-root "$PROJECT_ROOT" \
  --config "$PROJECT_ROOT/.thunderkit/config.json" --json
```

Exit 0 with `decision: owned` means the routing was computed and the selections are valid. It
does **not** mean any model answered, any native workflow ran, or any stage succeeded. Exit 2
with `invalid_config` means the file cannot be used as is: an unknown model key, an empty
required class, a mixed legacy shape, a config outside `--project-root`, or a missing config on
a model-bearing operation. Report the error text and route to `tk-memory` to fix it; do not
guess a substitute.

A recognized complete legacy `models.plan/critical_path/review` file normalizes as a
**preview**: `plan` becomes the planner, `critical_path` becomes a one-element executor array,
review choices are kept. Say that it is a preview and that saving it goes through `tk-memory`
with the user's normal approval.

The user can override any class for one run in one line. Report the override alongside the
saved values, and route the change through `tk-memory` (which appends the `DECISIONS.md` entry)
if they want it kept.

## Selected models versus the workflow backend

Keep these two facts on separate lines in every status report:

- **Selected models**: the planner, executor list (in the user's order), and reviewers (explicit
  list or `all`) from the config. These are the user's decision.
- **Workflow backend**: whichever native workflow host and peer the stage skills can use for
  this project (per `references/dependencies.json` and the resolver), or Thunderkit's own
  portable procedure when none qualifies.

A backend is chosen to serve the models, never the other way around. Changing or losing a
backend cannot change the planner key, the executor order, the reviewer set or `all`, the
`review_families_min` floor, or any family requirement. If a backend cannot honor a selected
class (no harness mapping on this host, wrong effective identity), that stage reports
`blocked` / `model_mismatch` or a named `fallback`; the config stays as the user wrote it.

Whether the selected models are actually reachable is a separate question from whether they
are selected. `tk-test` answers it, and only when it is loaded on this host and the run is at
a point where a paid probe is appropriate (normally right before the first model-bearing
stage). When `tk-test` is not loaded, report "preflight unavailable: install tk-test" as the
prerequisite for dispatch and stop there. Never treat the resolver's exit 0, a config read, or
a skill listing as readiness. A preflight that reaches only one reviewer family is a failed
gate for execution, not a warning to note and move past.

## The lifecycle (routing procedure)

Restoring a handoff is the precondition for everything else: if `.thunderkit/HANDOFF.md` exists,
stage 0 runs before any question is asked or any stage dispatched (see "Context discipline"
below). Then route in this order; skip a stage only when its artifact already exists **and** is
fresh for the current inputs. Each stage is a handoff to a sibling skill that owns its own
procedure, approvals, and artifacts; the router does not run the stage inline.

| # | Stage | Skill | Artifact in `.thunderkit/` | Model class |
|---|---|---|---|---|
| 0 | **Restore** — if a handoff exists, resume from it instead of starting fresh | `tk-handoff restore` | reads `HANDOFF.md` | any |
| 1 | **Size** — is this multi-model work at all? | (you) | — | — |
| 1.5 | **Preflight** — reachable models and reviewer families, when appropriate | `tk-test` | (report) | all configured |
| 2 | **Intake** — closed-question grill of user and harness | `tk-grill` (+ `tk-ask`) | `BRIEF.md` | **planner** (tk-grill's required role) |
| 3 | **Spec** — WHAT is delivered, ambiguity-scored | `tk-spec` | `SPEC.md` | planner |
| 4 | **Map** — parallel code recon along seams | `tk-map` | `MAP.md` | executors (wide) |
| 5 | **Discuss** — implementation decisions, gray areas | `tk-discuss` | `CONTEXT.md` | planner asks, user decides |
| 6 | **Research / Learn** — investigate unknowns; learn new domains source-backed | `tk-research`, `tk-learn` | `RESEARCH.md`, `knowledge/` | executors (wide) |
| 7 | **Plan** — disjoint dependency-layered lanes | `tk-plan` | `PLAN.md` + `plan.json` | **planner** (one) |
| 8 | **Plan review** — independent cross-family critique of the exact current plan | `tk-review --plan` | `PLAN-REVIEW.md` | **reviewers** |
| 9 | **Execute** — lanes in parallel, worktrees, resume ids | `tk-execute` | `runs/` | **executors** (set) |
| 10 | **Diff review + verification** — fresh cross-family review of the actual diff, evidence gate | `tk-review` | `REVIEW.md` | **reviewers** |
| 11 | **Surface checks** — CLI/API/visual checks of what was built, where applicable | `tk-verify-work` | `UAT.md` | reviewers |
| 12 | **Debug** — hypothesis loop when 10/11 fail | `tk-debug` | `debug/<slug>.md` | planner + executors |
| 13 | **Prepare** — PR body from artifacts and gates; no delivery | `tk-ship` | — | cheapest executor |
| 14 | **Docs** — doc write plus independent factual review | `tk-docs` | — | executors + reviewers |
| 15 | **Audit** — done-ness against original intent | `tk-audit` | `AUDIT.md` | reviewers |
| 16 | **Remember** — north star, decisions, config | `tk-memory` | `NORTH_STAR.md`, `DECISIONS.md`, `config.json` | any |
| any | **Handoff** — save session state at ~80% context or on pause | `tk-handoff save` | `HANDOFF.md` | any |

### Minimum path

For a mid-size change: 0 → 1 → 1.5 → 2 → 4 → **7 → 8 → 9 → 10** → 11 (where a surface exists)
→ 16. Stages 7, 8, 9 and 10 are the spine and there is no shorter path through them:

- **Plan review comes before execution, always.** `tk-execute` refuses a plan without a
  `PLAN-REVIEW.md` that reviews the exact bytes of the stage 7 plan artifacts about to run. A
  review of an earlier draft is stale the moment the plan changes; when `tk-plan` (or a native
  planner) rewrites the plan, route back through stage 8 before stage 9. A native planner's own
  internal critique does not satisfy this gate unless the recorded identities prove the
  required reviewer families.
- **Diff review is fresh, per diff.** Stage 10 reviews the actual changed bytes after
  execution. A passing `REVIEW.md` for a different diff is not a passing review.
- **Preparation waits for review.** `tk-ship` refuses without a current passing `REVIEW.md`,
  and it prepares only: no push, no PR creation, no merge, no publish.

A full milestone takes every stage. Whatever the path, the gates are: `tk-test` gates the first
model-bearing dispatch (unreachable required model or fewer than `review_families_min` reviewer
families → fix config or auth before dispatching); `tk-plan` refuses a `BRIEF.md` with open
unknowns; `tk-execute` refuses without current plan review; `tk-ship` refuses without current
diff review.

## Context discipline — restore before you re-ask

A run longer than one context window must not lose itself. At ~80% context, route to
`tk-handoff save`; it writes `.thunderkit/HANDOFF.md` with the current stage, lanes in flight
and their resume ids, decisions made this session, and the next action.

At the start of any run, **if `HANDOFF.md` exists, offer `tk-handoff restore` first** (stage 0).
Restore only the state the handoff explicitly scopes: its recorded stage, lane ids, and the
decisions it lists. Anything it settled — model classes, backend choice, an approved plan
identity — is settled; report it, do not ask again. Anything it does not mention is unknown
and is asked normally. The handoff is portable committed markdown, so a session started on one
harness resumes on another; a decision restored from it still gets re-validated against the
current config through the resolver, because the file may have changed since.

## Delegation

`tk-router` delegates nothing. Its two operations, `bootstrap` and `route`, are owned by policy
(`references/dependencies.json` declares no targets for it), because model selection and
lifecycle policy must stay local and portable across hosts. In particular:

- No host-side meta-router, model-routing advisor, or "pick the right skill" helper replaces
  this skill's decisions. Such tools may be consulted by a stage skill for their own purpose;
  they do not choose Thunderkit's classes or its stage order.
- No native full-lifecycle workflow is handed the whole run. Stage skills may hand a **stage**
  to a native peer when their own resolver decision says `delegate`; the router still owns the
  sequence, the gates between stages, and the normalization of results into `.thunderkit/`.
- The resolver is read-only. It computes a decision; it never dispatches, writes config, or
  touches host configuration. Any actual invocation happens inside the stage skill, after its
  own checks.

## Fallback

When something the router needs is missing, degrade and name it; never fake a stage or a result:

- **Sibling skill not loaded** → the stage is unavailable. Say which stage, which skill to
  install, and what it would have produced. Do not run the stage inline as a substitute unless
  this skill documents a bounded owned procedure for it (bootstrap questions and lifecycle
  sequencing are the only ones).
- **`tk-test` not loaded** → dispatch prerequisite unmet. Report the selected models, state
  that readiness is unverified, and stop before the first model-bearing stage.
- **Preflight fails or reaches one family** → do not dispatch. Report which class and which
  lanes are affected, what the user would authenticate or configure to fix it, and route to
  `tk-memory` if they change a choice. Fewer than `review_families_min` reachable reviewer
  families is a hard stop for execution, not a downgrade.
- **Backend unusable** (resolver `fallback` or `blocked`) → keep the selected classes, report
  the reason code, and let the stage skill use its documented portable procedure where one is
  allowed. A `blocked` decision starts nothing.
- **Invalid config** → route to `tk-memory` with the resolver's error. No silent substitution.

Every fallback is named in the status block before the next stage runs. The user is asked only
where a decision is theirs to make: changing a model choice, or saving a config or preview. A
failed readiness or family gate is not such a question, and no approval steps past it: the user
may change a model choice, authenticate, or fix host configuration, after which the gate is run
again, and the stage stays blocked until that rerun passes. Nothing lowers `review_families_min`
for a run. Carrying on with a documented portable procedure for an optional backend is not a
new question. The router never installs, logs in, edits a global host configuration, or
delivers (push/PR/merge) on its own.

## Output contract

Each router turn ends with a short status block. Its fields, in order:

1. **Stage** — the stage number and skill about to run, or `blocked` / `unavailable` with the
   reason.
2. **Selected models** — planner key, executor keys in order, reviewer keys or `all`; each
   annotated `per project config`, `override this run`, or `restored from handoff`.
3. **Backend** — the resolver decision and reason code for the next stage (`owned` /
   `delegate` / `fallback` / `blocked`), plus the target `ecosystem:selector` when one exists.
4. **Readiness** — `verified` (with the `tk-test` outcome), `unverified` (no probe yet), or
   `unavailable` (no `tk-test` loaded), stated separately from the selected models.
5. **Gates** — which of plan review, diff review, and surface checks are current for the exact
   artifact in play, and which are stale or missing.
6. **Next action** — one line, including any question that still needs the user.

Resolver JSON, when shown, is passed through unchanged (`schema_version`, `skill`, `operation`,
`decision`, `reason_code`, `detail`, `target`, `bindings`, `runtime_home`, `evidence_paths`);
`bindings.observed` stays null until a real run reports identity.
