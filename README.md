<div align="center">

# ⏩ thunderkit

**Ship big changes in big repos — by splitting the work into parallel lanes and routing each to the best model across a heterogeneous agent fleet.**

[![skills](https://img.shields.io/badge/Agent_Skills-19-f0b429?style=for-the-badge&logo=markdown&logoColor=white)](https://skills.sshlg.me/)
[![npm](https://img.shields.io/npm/v/thunderkit?style=for-the-badge&logo=npm&logoColor=white&color=CB3837)](https://www.npmjs.com/package/thunderkit)
[![CI](https://img.shields.io/github/actions/workflow/status/thunderock/thunderkit/ci.yml?branch=master&style=for-the-badge&logo=github&label=CI)](https://github.com/thunderock/thunderkit/actions/workflows/ci.yml)
[![Pages](https://img.shields.io/github/actions/workflow/status/thunderock/thunderkit/pages.yml?branch=master&style=for-the-badge&logo=githubpages&label=Docs)](https://thunderock.github.io/thunderkit/)
[![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)
[![format](https://img.shields.io/badge/format-Agent_Skills-181717?style=for-the-badge&logo=markdown&logoColor=white)](#install)

**Portable skill files · Host-qualified native workflows · User-selected models**

[Install](#install) · [The loop](#how-it-works--the-phase-loop-made-parallel) · [Model classes](#the-three-model-classes) · [Dependencies](DEPENDENCIES.md) · [Docs site](https://thunderock.github.io/thunderkit/) · [North Star](NORTH_STAR.md)

</div>

---

> **Big work in big repos is won by decomposition + heterogeneity, not by one smart model.**

thunderkit is an *opinionated* skill pack. It takes a large change in a large repo and:
**decomposes** it into disjoint, dependency-layered lanes → **routes** each lane within your chosen
model classes and supported harness mappings → **runs** independent work in parallel across the
reachable fleet → **reviews** the result across the required model families → **remembers** the project's
intent as a committed artifact.

It's deliberately opinionated — see [`NORTH_STAR.md`](NORTH_STAR.md):

- **No single-model plans.** A plan that can't be split into parallel lanes isn't done.
- **Review is always cross-family.** A model family reviewing its own output is not review.
- **Done is evidence, not intent.** Every lane carries a verification command, or it's blocked.
- **You pick the model classes.** One capable planner, a set of executors, everyone as reviewers.
- **Context is committed**, not recalled — the opinion travels with the repo.

---

## How it works — the phase loop, made parallel

The loop is **discuss → plan → plan review → execute → verify → prepare delivery**.
Independent lanes run in parallel; dependency and approval gates stay ordered. A large repo is
decomposed so it never has to fit in one context window. Cross-family plan review must cover the
current plan before execution, and diff review must cover the actual changes afterward.

```
  intake        plan            execute (parallel)      review           ship
 ┌────────┐   ┌────────┐   ┌──────┬──────┬──────┐   ┌────────────┐   ┌────────┐
 │tk-grill│─▶ │tk-plan │─▶ │lane 0│lane 1│lane 2│─▶ │ tk-review  │─▶ │tk-ship │
 │tk-ask  │   │(1 best │   │Opus  │Opus5 │Fable │   │ every      │   │(no auto│
 │tk-spec │   │ brain) │   │  ▲ own worktree each│   │ family     │   │ merge) │
 │tk-map  │   └────────┘   └──────┴──────┴──────┘   │ +verify    │   └────────┘
 └────────┘    planner        executors (a set)      reviewers (all)
```

Each lane is **file-disjoint**, with its own worktree and a model from the selected executor
class. The workflow records genuine resume IDs when available, explicitly marking missing IDs
as unavailable. A merge conflict stops integration for a fresh ownership check; it is not an
excuse to overwrite another lane.

### Policy stays here; native implementation is optional

Thunderkit owns model choice, lifecycle routing, portable project context, cross-family review,
and completion gates. Stage skills may reuse a separately installed, pinned native peer:

| Active host | Eligible native peer | Without a qualified peer |
|---|---|---|
| OpenCode | OMO (`oh-my-openagent`) | Thunderkit-owned portable procedure |
| Codex | OMO (`oh-my-openagent`) | Thunderkit-owned portable procedure |
| Hermes | OMH (`oh-my-hermes`) | Thunderkit-owned portable procedure |
| Claude Code or another skill-compatible host | None declared | Thunderkit-owned portable procedure |

This is the host filter from [`dependencies.json`](skills/references/dependencies.json), not a
claim that every operation or selected model works on each host. A native route also requires
the exact version, loaded source fingerprints, required tools, enforceable model bindings, and
safety controls. A missing peer produces a named fallback, not a native success. If the owned
procedure cannot meet the same model, review, or safety requirements, the stage stays blocked.

A native planning or execution handoff has **one workflow owner** until it returns. Thunderkit
does not start a second execution loop alongside it. Native artifacts stay in their native
locations; Thunderkit references them and checks their identity. A timeout with uncertain
in-flight work blocks a duplicate launch. Delivery still needs separate user approval.

See [Dependencies](DEPENDENCIES.md) for exact pins, licenses, installation boundaries and
qualification details. Set `delegation: "off"` to use owned procedures without invoking peers.

## The three model classes

thunderkit's core opinion: one model can't be planner, coder, and reviewer at once — a fleet can,
if the work is decomposed to feed it. So `tk-router` asks you to choose **three classes** (once
per project, then it remembers in `.thunderkit/config.json`):

| Class | Cardinality | Does | Example choice — requires confirmation |
|---|---|---|---|
| 🧠 **Planner** | exactly **one** — the most capable model | spec, discuss, plan, root-cause | `Opus 4.8` |
| 🔨 **Executors** | a **set** — lanes spread by weight | map, research, implement, docs | `Opus 4.8 · Opus 5 · Fable 5.1` |
| 🔍 **Reviewers + verifiers** | `"all"` or a nonempty unique model list | plan-check, review, verify, UAT, audit | `"all"` |

*Planning is a single point of failure → one best brain. Execution is a throughput problem → many
hands matched to lane weight. Review is a blind-spot problem → every family looks, so no one
family's blind spot survives.* A model can be in more than one class — the strongest model plans,
takes the heaviest lane, and reviews.

The three classes have **no automatic defaults**. `reviewers: "all"` considers every catalog
model, not just the planner and executors; preflight forms the reviewer set from successful
responses and reports unavailable optional candidates. Every explicitly selected model must
respond, and at least `review_families_min` distinct families must answer independently.
`opus48`, `opus5` and `fable51` are one Anthropic family; `sol` is the OpenAI family.

Canonical configuration uses `schema_version: 2` and `classes.planner`, `classes.executors`,
and `classes.reviewers`. Missing operational fields default **in memory** to
`review_families_min: 2`, `max_layers: 3`, `frozen_paths: []`, `ecosystems: ["omo", "omh"]`, and
`delegation: "auto"`. Existing versionless `classes` files remain readable without rewriting.
A supplied `decided_at` is preserved; readers never invent one. See the
[configuration contract](skills/references/config.schema.json) and
[model roster](skills/references/model-roster.md) for validation and approved legacy migration.

A backend never replaces a selected model or lowers the family minimum. Unsupported host/model
mappings are reported explicitly; changing a choice requires the user, not an automatic fallback.

## The skills (19)

| Stage | Skill | What it owns |
|---|---|---|
| **entry** | `tk-router` | Sizes the work, gets the three model classes chosen, routes the lifecycle. |
| **restore** | `tk-handoff` | Save/restore a session in a portable format — resume across context resets or a different harness. |
| **preflight** | `tk-test` | Pings every configured model through its real harness CLI — proves the fleet is reachable and ≥2 review families answer before work starts. |
| **intake** | `tk-ask` | Answer discipline — yes/no, one word, a number, a path, or `unknown`. No prose. Routes each `unknown` to map/learn/user. |
| **intake** | `tk-grill` | Interrogates you *and* the harness with closed questions until the brief has no unknowns (`--learn` mode frames what to learn). |
| **pre-plan** | `tk-spec` | Ambiguity-scored Socratic loop pinning *what* the change delivers. |
| **pre-plan** | `tk-map` | Big-repo recon — a durable code map so planning works from structure. |
| **pre-plan** | `tk-discuss` | Captures implementation decisions + rejected alternatives before planning. |
| **pre-plan** | `tk-research` | Parallel investigation lanes for the unknowns, consolidated. |
| **pre-plan** | `tk-learn` | Research a topic online → source-backed knowledge note → optionally draft a new validated skill. |
| **plan** | `tk-plan` | Decompose into **disjoint, dependency-layered lanes**, each with acceptance + a verify command. |
| **execute** | `tk-execute` | One execution owner: a qualified native handoff or portable lane dispatch, with worktree and genuine session evidence. |
| **verify** | `tk-review` | **Cross-family review + evidence gate** (also `--plan` for pre-execution plan-check). |
| **verify** | `tk-verify-work` | Conversational UAT — walk each acceptance criterion through the real user surface. |
| **verify** | `tk-debug` | Scientific-method debug loop with persisted, resumable state. |
| **deliver** | `tk-ship` | Gate on review+UAT and prepare a PR body — no push, PR creation, publish, or merge. |
| **deliver** | `tk-docs` | Parallel doc write, then verify every claim against the live code with a second family. |
| **deliver** | `tk-audit` | Milestone done-ness vs original intent — orphaned/unverified requirements fail closed. |
| **memory** | `tk-memory` | Project north star, decision log, and the router's per-project `config.json`. |

Shared sources: [`models.json`](skills/references/models.json) defines model IDs, families and
supported harness mappings; [`model-roster.md`](skills/references/model-roster.md) is its human
reference. [`dependencies.json`](skills/references/dependencies.json) defines per-operation native
targets and fallbacks; [`delegation.md`](skills/references/delegation.md) defines their gates.
Standalone skills include local copies of these Thunderkit-owned references and helpers.

## Install

Thunderkit distributes [Agent Skills](https://agentskills.io) (`skills/<name>/SKILL.md`) with
small local Python helpers. The npm command is a pointer to the pinned
[Vercel `skills`](https://github.com/vercel-labs/skills) distribution CLI, **`skills@1.7.0`**.
Installing a skill file is not proof that the host can execute its workflow.

**Toolchains:** Thunderkit help, version and dependency display require Node **≥18**.
Install and list delegate to `skills@1.7.0` and require Node **≥22.20.0**, even though listing
does not install skills. The local resolver/model helpers require Python **≥3.11**.

```sh
# whole pack through the npm pointer (Thunderkit only)
npx thunderkit install

# equivalent direct distribution command
npx -y skills@1.7.0 add thunderock/thunderkit --all

# whole pack, but only for named harnesses
npx -y skills@1.7.0 add thunderock/thunderkit -s '*' -g --agent claude-code codex opencode hermes-agent

# one skill
npx -y skills@1.7.0 add thunderock/thunderkit -s tk-router -g

# what's in the repo, without installing
npx thunderkit list
```

Choose the intended agents and scope through the distribution CLI. A single-skill installation
contains its own support files but does not install sibling `tk-*` stages. The router names a
missing stage and stops there rather than guessing commands or installing it automatically.

**Native peers are separate and optional.** Installing Thunderkit does not install or activate
OMO or OMH, authenticate providers, or change model selections. To display the registry without
running peer installers or doctors:

```sh
npx thunderkit deps --json

# from an existing checkout, without npx package retrieval
node bin/thunderkit.js deps --json
```

The output is information, not a live readiness test. [Dependencies](DEPENDENCIES.md) documents
the separately approved native install, activation and doctor steps. Then invoke `tk-router`
through your host's skill interface (e.g. `tk-router: refactor the auth layer`), choose the model
classes, and run `tk-test` before model-bearing dispatch. A different host must recheck support;
portable project context does not make native sessions or model mappings interchangeable.

## Project memory — `.thunderkit/`

Every project keeps its own north star, decisions, and selections — committed to the repo, so any
agent (or you in a later session, or a teammate) inherits the project's opinion without
re-litigating settled choices:

```
.thunderkit/
  NORTH_STAR.md   # this project's goals, constraints, non-negotiables
  DECISIONS.md    # append-only: what was decided, why, what was rejected
  config.json     # the three model classes + review families + layers + frozen paths
  BRIEF.md        # tk-grill intake (closed answers, no unknowns)
  SPEC.md         # what the change delivers (tk-spec)
  MAP.md          # code map (tk-map)
  PLAN.md/.json   # current decomposition into lanes (tk-plan)
  REVIEW.md       # latest cross-family review (tk-review)
  UAT.md · AUDIT.md · debug/*.md · runs/*.json
```

## Development

```sh
make run_tests   # frontmatter validator + roster/leakage checks + site drift gate
make lint        # py_compile + shellcheck (best-effort)
make site        # regenerate the static docs site → site/_site
```

The test/build helpers use stdlib-only Python; the npm pointer uses Node built-ins.
`make run_tests` works offline on a fresh checkout. CI runs the
tests, a secrets/leakage denylist grep, and the site build on every push; a separate workflow
publishes the docs site to GitHub Pages.

## Why it works

Most multi-agent setups fail at scale for three reasons, and thunderkit answers each:

| The failure | thunderkit's answer |
|---|---|
| One model does everything and its weaknesses show everywhere | Three model classes — the right model for planning, execution, and review |
| "Parallel" agents collide on the same files | Lanes are **file-disjoint by construction**, each in its own worktree |
| Nothing checks the work; "done" is a claim | Every lane has a verify command; review is cross-family; ship fails closed |

## Releasing

Versioning is automated with [release-please](https://github.com/googleapis/release-please) and
Conventional Commits — no manual bump. Every push to `master` updates a bot PR
(`chore(master): release X.Y.Z`) whose version is computed from the commits since the last tag.
**Merging that PR** cuts the tag `vX.Y.Z` + a GitHub Release and, in the same run, publishes to
npm via [OIDC trusted publishing](https://docs.npmjs.com/trusted-publishers) (no long-lived token,
provenance attached). The publish job lives inside `release-please.yml` — a release created by
`GITHUB_TOKEN` never fires `on: release`, so a separate release-triggered workflow would stay silent.
`publish.yml` is a manual re-publish fallback (`workflow_dispatch` with a tag).

> **One-time bootstrap** (a package that doesn't exist yet can't be published by CI): the first
> publish is manual — `npm login` then `npm publish --access public` from a clean checkout — after
> which the trusted-publisher config on npmjs.com (workflow filename `release-please.yml`) hands
> all future releases to CI.

<div align="center">

## Star History

<a href="https://star-history.com/#thunderock/thunderkit&Date">
<picture>
<source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=thunderock/thunderkit&type=Date&theme=dark" />
<source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=thunderock/thunderkit&type=Date" />
<img alt="Star History Chart" src="https://api.star-history.com/svg?repos=thunderock/thunderkit&type=Date" />
</picture>
</a>

---

**A fleet of models, decomposed to win.** · MIT © [Ashutosh Tiwari](https://github.com/thunderock)

</div>
