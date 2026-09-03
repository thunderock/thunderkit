<div align="center">

# ⚡ thunderkit

**Ship big changes in big repos — by splitting the work into parallel lanes and routing each to the best model across a heterogeneous agent fleet.**

[![skills](https://img.shields.io/badge/Agent_Skills-16-f0b429?style=for-the-badge&logo=markdown&logoColor=white)](https://skills.sshlg.me/)
[![CI](https://img.shields.io/github/actions/workflow/status/thunderock/thunderkit/ci.yml?branch=master&style=for-the-badge&logo=github&label=CI)](https://github.com/thunderock/thunderkit/actions/workflows/ci.yml)
[![Pages](https://img.shields.io/github/actions/workflow/status/thunderock/thunderkit/pages.yml?branch=master&style=for-the-badge&logo=githubpages&label=Docs)](https://thunderock.github.io/thunderkit/)
[![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)
[![harnesses](https://img.shields.io/badge/harnesses-77%2B-181717?style=for-the-badge&logo=anthropic&logoColor=white)](#install--every-harness-one-command)

**Claude · Codex · opencode · hermes · Cursor · Gemini · Windsurf · Zed · Kilo · Goose · +67 more**

[Install](#install--every-harness-one-command) · [The loop](#how-it-works--the-phase-loop-made-parallel) · [Model classes](#the-three-model-classes) · [Docs site](https://thunderock.github.io/thunderkit/) · [North Star](NORTH_STAR.md)

</div>

---

> **Big work in big repos is won by decomposition + heterogeneity, not by one smart model.**

thunderkit is an *opinionated* skill pack. It takes a large change in a large repo and:
**decomposes** it into disjoint, dependency-layered lanes → **routes** each lane to the best model
*and* harness → **runs** them in parallel across a heterogeneous fleet (Bedrock Fable 5.1, Claude
Opus, Codex Sol) → **reviews** the result across every model family → **remembers** the project's
intent as a committed artifact.

It's deliberately opinionated — see [`NORTH_STAR.md`](NORTH_STAR.md):

- **No single-model plans.** A plan that can't be split into parallel lanes isn't done.
- **Review is always cross-family.** A model family reviewing its own output is not review.
- **Done is evidence, not intent.** Every lane carries a verification command, or it's blocked.
- **You pick the model classes.** One capable planner, a set of executors, everyone as reviewers.
- **Context is committed**, not recalled — the opinion travels with the repo.

---

## How it works — the phase loop, made parallel

Like [GSD](https://github.com/open-gsd/gsd-core) drives a coding agent through a disciplined
*discuss → plan → execute → verify → ship* loop, thunderkit runs that same loop — but every stage
is **parallel and cross-model**, and a large repo is decomposed so it never has to fit in one
context window.

```
  intake        plan            execute (parallel)      review           ship
 ┌────────┐   ┌────────┐   ┌──────┬──────┬──────┐   ┌────────────┐   ┌────────┐
 │tk-grill│─▶ │tk-plan │─▶ │lane 0│lane 1│lane 2│─▶ │ tk-review  │─▶ │tk-ship │
 │tk-ask  │   │(1 best │   │Opus  │Opus5 │Fable │   │ every      │   │(no auto│
 │tk-spec │   │ brain) │   │  ▲ own worktree each│   │ family     │   │ merge) │
 │tk-map  │   └────────┘   └──────┴──────┴──────┘   │ +verify    │   └────────┘
 └────────┘    planner        executors (a set)      reviewers (all)
```

Each lane is **file-disjoint** (two lanes never touch the same file), runs in its **own git
worktree**, on its **own model**, via **portable CLI dispatch** (`claude -p --output-format json`,
`codex exec --json`) with a **captured resumable session id**. Lanes merge without conflict *by
construction* — if a merge conflicts, the plan's disjointness was violated, and that's a bug in
the plan, not something to paper over.

## The three model classes

thunderkit's core opinion: one model can't be planner, coder, and reviewer at once — a fleet can,
if the work is decomposed to feed it. So `tk-router` asks you to choose **three classes** (once
per project, then it remembers in `.thunderkit/config.json`):

| Class | Cardinality | Does | Default |
|---|---|---|---|
| 🧠 **Planner** | exactly **one** — the most capable model | spec, discuss, plan, root-cause | `Opus 4.8` |
| 🔨 **Executors** | a **set** — lanes spread by weight | map, research, implement, docs | `Opus 4.8 · Opus 5 · Fable 5.1` |
| 🔍 **Reviewers + verifiers** | **all** authed families | plan-check, review, verify, UAT, audit | `everyone` |

*Planning is a single point of failure → one best brain. Execution is a throughput problem → many
hands matched to lane weight. Review is a blind-spot problem → every family looks, so no one
family's blind spot survives.* A model can be in more than one class — the strongest model plans,
takes the heaviest lane, and reviews.

## The skills (16)

| Stage | Skill | What it owns |
|---|---|---|
| **entry** | `tk-router` | Sizes the work, gets the three model classes chosen, routes the lifecycle. |
| **intake** | `tk-ask` | Answer discipline — yes/no, one word, a number, a path, or `unknown`. No prose. |
| **intake** | `tk-grill` | Interrogates you *and* the harness with closed questions until the brief has no unknowns. |
| **pre-plan** | `tk-spec` | Ambiguity-scored Socratic loop pinning *what* the change delivers. |
| **pre-plan** | `tk-map` | Big-repo recon — a durable code map so planning works from structure. |
| **pre-plan** | `tk-discuss` | Captures implementation decisions + rejected alternatives before planning. |
| **pre-plan** | `tk-research` | Parallel investigation lanes for the unknowns, consolidated. |
| **plan** | `tk-plan` | Decompose into **disjoint, dependency-layered lanes**, each with acceptance + a verify command. |
| **execute** | `tk-execute` | Run lanes **in parallel** via portable CLI dispatch, own worktree + resumable id each. |
| **verify** | `tk-review` | **Cross-family review + evidence gate** (also `--plan` for pre-execution plan-check). |
| **verify** | `tk-verify-work` | Conversational UAT — walk each acceptance criterion through the real user surface. |
| **verify** | `tk-debug` | Scientific-method debug loop with persisted, resumable state. |
| **deliver** | `tk-ship` | Gate on review+UAT, assemble a PR body from artifacts — **never auto-pushes or merges**. |
| **deliver** | `tk-docs` | Parallel doc write, then verify every claim against the live code with a second family. |
| **deliver** | `tk-audit` | Milestone done-ness vs original intent — orphaned/unverified requirements fail closed. |
| **memory** | `tk-memory` | Project north star, decision log, and the router's per-project `config.json`. |

Shared: [`skills/references/model-roster.md`](skills/references/model-roster.md) — the single
source of truth for which model runs which work. Skills reference models by **short name** and
resolve ids here, so a model rename is a one-line change.

## Install — every harness, one command

thunderkit is plain [Agent Skills](https://agentskills.io) (`skills/<name>/SKILL.md`), the open
standard read natively by Claude Code, Codex, opencode, hermes, Cursor, Gemini CLI, Windsurf,
Zed, Goose, Kilo and 70+ others. Distribution is the [vercel `skills`](https://github.com/vercel-labs/skills)
CLI — the same mechanism the popular packs use:

```sh
# whole pack → every agent detected on this machine (verified: installs to 77 agents)
npx skills add thunderock/thunderkit --all

# whole pack, but only for named harnesses
npx skills add thunderock/thunderkit -s '*' -g --agent claude-code codex opencode hermes-agent

# one skill
npx skills add thunderock/thunderkit -s tk-router -g

# what's in the repo, without installing
npx skills add thunderock/thunderkit -l
```

**How that reaches every harness.** `skills add -g` writes one canonical copy to
`~/.agents/skills/<name>/` and **symlinks** it into each agent's own skills dir
(`~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`, hermes' external dirs, …).
One `npx skills update -g` refreshes all of them at once. Packs that ship an npm launcher just
wrap this same call with a fixed agent list; thunderkit skips the launcher and uses the CLI
directly. A fresh-machine setup script can pin it with one line:

```sh
npx -y skills add thunderock/thunderkit -s '*' -g -y --agent '*'
```

Then invoke the router by name (e.g. `tk-router: refactor the auth layer across the monorepo`)
and it routes the rest.

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

Everything is stdlib-only Python — `make run_tests` works offline on a fresh checkout. CI runs the
tests, a secrets/leakage denylist grep, and the site build on every push; a separate workflow
publishes the docs site to GitHub Pages.

## Why it works

Most multi-agent setups fail at scale for three reasons, and thunderkit answers each:

| The failure | thunderkit's answer |
|---|---|
| One model does everything and its weaknesses show everywhere | Three model classes — the right model for planning, execution, and review |
| "Parallel" agents collide on the same files | Lanes are **file-disjoint by construction**, each in its own worktree |
| Nothing checks the work; "done" is a claim | Every lane has a verify command; review is cross-family; ship fails closed |

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
