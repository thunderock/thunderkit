<div align="center">

# ⏩ thunderkit

**Ship big changes in big repos — by splitting the work into parallel lanes and routing each to the best model across a heterogeneous agent fleet.**

[![skills](https://img.shields.io/badge/Agent_Skills-22-f0b429?style=for-the-badge&logo=markdown&logoColor=white)](https://skills.sshlg.me/)
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
`review_families_min: 2`, `max_layers: 3`, `frozen_paths: []`, `ecosystems: ["omo", "omh", "gsd"]`, and
`delegation: "auto"`. Existing versionless `classes` files remain readable without rewriting.
A supplied `decided_at` is preserved; readers never invent one. See the
[configuration contract](skills/references/config.schema.json) and
[model roster](skills/references/model-roster.md) for validation and approved legacy migration.

A backend never replaces a selected model or lowers the family minimum. Unsupported host/model
mappings are reported explicitly; changing a choice requires the user, not an automatic fallback.

## The skills (22)

| Stage | Skill | What it owns |
|---|---|---|
| **entry** | `tk-router` | Sizes the work, gets the three model classes chosen, routes the lifecycle. |
| **restore** | `tk-restore` | Model-free context load and verified backup export to a new/empty destination; never resumes work. |
| **handoff** | `tk-handoff` | Save portable context; validate model, artifact, repository and original-harness identity before an approved resume. |
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
| **execute** | `tk-fast` | Trivial inline edit: no model choice, plan, subagents or review; targeted test and one atomic commit. Uses GSD `gsd-fast` where installed. |
| **execute** | `tk-quick` | Small task on one chosen model (planner default or `model=<key>`), atomic commits and tests, one different-family review before each commit. |
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
  continuity.json # optional portable preservation policy, separate from model choices
  HANDOFF.md      # saved stage, artifacts, decisions and original-harness session identities
  BRIEF.md        # tk-grill intake (closed answers, no unknowns)
  SPEC.md         # what the change delivers (tk-spec)
  MAP.md          # code map (tk-map)
  PLAN.md/.json   # current decomposition into lanes (tk-plan)
  REVIEW.md       # latest cross-family review (tk-review)
  UAT.md · AUDIT.md · debug/*.md · runs/*.json
```

## Recovery and preservation

`tk-restore` reads context without model configuration, a native peer or the original checkout.
Load is inert: saved commands, approvals, model choices and next actions never authorize
execution. `tk-handoff restore` still requires current repository/branch/HEAD, artifact hashes,
model bindings, the original harness/session and permission before any continuation.

The bundled helpers require Python ≥3.11 and supported Linux/POSIX no-follow filesystem
operations; history verification/export also needs local Git and bounded writable scratch.
Set `SKILL_ROOT` to the directory of the actually loaded skill. All path variables below are
explicitly selected literal absolute paths without symlink components, not shell text from
recovered data. `SCOPE_ID` is the exact approved identity, not a name inferred from cwd.

### Load context, even without the checkout

`CONTEXT_PATH` may name a snapshot directory, its `manifest.json`, or a bounded legacy Markdown
file. `GLOBAL_CONFIG` names the selected continuity settings file; it may be absent if no saved
settings are needed, but an existing invalid file is refused.

```sh
python3 "$SKILL_ROOT/scripts/restore_context.py" load \
  --scope project --identity "$SCOPE_ID" --context "$CONTEXT_PATH" \
  --global-config "$GLOBAL_CONFIG"

# Use the remembered locator for this exact project identity instead.
python3 "$SKILL_ROOT/scripts/restore_context.py" load \
  --scope project --identity "$SCOPE_ID" --global-config "$GLOBAL_CONFIG"
```

No checkout is required: omit `--project-root` as above. When the actual project exists, add
`--project-root "$PROJECT_ROOT"` to use its bound private identity and local locator. Selection
order is explicit context, project-local locator, exact-project global locator, then an already
available matching pointer supplied with `--memory-pointer`. Invalid explicit paths and stale,
corrupt or mismatched selected locators fail rather than silently falling through. No broad
home, memory, transcript or session-history search occurs, and loading never remembers a path.

Without `--global-config`, continuity settings are selected from
`$THUNDERKIT_HOME/continuity.json`, then `$XDG_CONFIG_HOME/thunderkit/continuity.json`, then
`$HOME/.config/thunderkit/continuity.json`. These are settings locations, not default backup
roots. Approved global context uses `--scope global` and its own exact identity and source
authority; project approval never covers another project or profile.

Inspect JSON stdout's source, integrity status, metadata and warnings. Legacy Markdown is
unverified historical text. Snapshot hashes prove byte consistency, not trustworthy origin,
current approval, complete protection or resumability. See [tk-restore](skills/tk-restore/SKILL.md)
for limits, exclusions and refusal behavior.

### Preview and approve a recovery export

Export is separate from load. Select an explicit snapshot and a new or empty destination whose
parent already exists, outside the snapshot and any live checkout. Legacy Markdown is load-only.

```sh
python3 "$SKILL_ROOT/scripts/restore_context.py" preview \
  --context "$SNAPSHOT_PATH" --identity "$SCOPE_ID" --scope project \
  --destination "$DESTINATION"
```

Review the exported paths, deletion records, omissions, history status and `manifest_sha256`.
After fresh approval for that exact preview and destination, use its unchanged digest:

```sh
python3 "$SKILL_ROOT/scripts/restore_context.py" export \
  --context "$SNAPSHOT_PATH" --identity "$SCOPE_ID" --scope project \
  --destination "$DESTINATION" --approve \
  --expect-manifest-sha256 "$PREVIEW_MANIFEST_SHA256"
```

Changed inputs require a new preview and approval. Export reconstructs approved bytes and modes
without overwriting files; deletion records never authorize deletion from a live tree. Verified
bundled history may be imported into a new isolated repository, without inherited remotes,
hooks, credentials or grants. It never fetches remote-only history, executes recovered files,
installs dependencies, changes models or resumes a session.

### Preview project setup and capture a checkpoint

Save current artifacts and context at regular intervals, before risky transitions and before
pausing. Then use the [approved preservation procedure](skills/references/delegation.md#approved-preservation-checkpoints).
For project setup, select an existing `PROJECT_ROOT` and `BACKUP_ROOT`, the exact `SCOPE_ID`,
and explicit source allowlists. `APPROVED_SOURCE` below is one source file/directory; repeat
`--source` for others. The saved handoff is explicitly included even if ignored by Git.
`WORKING_REF` is the approved attached working branch, such as `refs/heads/feature-name`, not
a tag or default/protected branch. History capture is bounded and subject to secret exclusions.

Choose `INTEGRATION_ROOT` beneath the project's `.thunderkit/`. Setup's private settings and
integration files must be untracked and Git-ignored; arrange this with normal project-write
approval, not by silently changing ignore rules. Backup storage must be outside the project.

```sh
python3 "$SKILL_ROOT/scripts/checkpoint_cli.py" preview \
  --project-root "$PROJECT_ROOT" --scope project --identity "$SCOPE_ID" \
  --backup-root "$BACKUP_ROOT" --source "$APPROVED_SOURCE" \
  --source "$PROJECT_ROOT/.thunderkit/HANDOFF.md" \
  --context-entrypoint .thunderkit/HANDOFF.md \
  --include-history --history-ref "$WORKING_REF" --integration-root "$INTEGRATION_ROOT"
```

Preview is read-only. Inspect its mutations, blockers and capability report before approving
those exact inputs. Setup rechecks them, qualifies storage readback, creates a first verified
snapshot and writes private configuration; it does **not** activate a hook or scheduler:

```sh
python3 "$SKILL_ROOT/scripts/checkpoint_cli.py" setup \
  --project-root "$PROJECT_ROOT" --scope project --identity "$SCOPE_ID" \
  --backup-root "$BACKUP_ROOT" --source "$APPROVED_SOURCE" \
  --source "$PROJECT_ROOT/.thunderkit/HANDOFF.md" \
  --context-entrypoint .thunderkit/HANDOFF.md \
  --include-history --history-ref "$WORKING_REF" --integration-root "$INTEGRATION_ROOT" --approve
```

For an explicitly approved one-shot capture, choose an existing user-owned mode-0700
`STATE_DIR`, disjoint from the entire project and backup store. Capture uses explicit sources
and roots, not saved authority:

```sh
python3 "$SKILL_ROOT/scripts/checkpoint_cli.py" capture \
  --project-root "$PROJECT_ROOT" --scope project --identity "$SCOPE_ID" \
  --backup-root "$BACKUP_ROOT" --source "$APPROVED_SOURCE" \
  --source "$PROJECT_ROOT/.thunderkit/HANDOFF.md" \
  --context-entrypoint .thunderkit/HANDOFF.md \
  --include-history --history-ref "$WORKING_REF" --state-dir "$STATE_DIR" --approve
```

These commands are model-free and do not push. `--approve` is fresh consent for that invocation,
not permission restored from a file. Save portable policy in `.thunderkit/continuity.json`,
separate from model config; private `.thunderkit/continuity.local.json` holds machine-local
identity/locator information. Successful setup remembers its verified snapshot. Capture reports
the snapshot/store locator; do not assume it updates every private or global remembered pointer.

Policy defaults are a 15-minute interval, retention of 20 verified snapshots, pruning off, and
`git.push_after_commit: true`; none creates a backup root, schedule or push grant. Project
policy can disable backup or set `git.push_after_commit: false`. Mandatory secret, runtime-home,
transcript, cache and `.git` exclusions still apply, including to approved branch history.
Do not capture an entire home or copy live runtime databases.

Require exit zero and inspect the result, not just a success label. Report files, omissions,
history protection, saved-context age, last verified snapshot and push status independently.
Partial or failed preservation retains prior valid recovery state and does not become “fully
protected.” Local write/readback is not machine-loss durability or remote/FUSE crash qualification.

### Host connections require separate qualification

The [connection API](skills/references/checkpoint_host.py) exposes `preview_connections`,
`apply_connections` and `remove_connections` for an approved Linux local-repository scope with
an existing verified snapshot. It uses Git's `reference-transaction` hook and the same bounded
checkpoint runtime. The supported hook directory is the repository's empty `.git/hooks`;
foreign hooks or an external hook manager block setup, rather than being overwritten or bypassed.
It never rewrites global `core.hooksPath`.

`preview_schedule`, `apply_schedule` and `remove_schedule` use a trusted `ScheduleDriver` with
read/compare-and-swap replacement. Scheduler-service qualification and exclusive-writer
authority are separate prerequisites; unrelated crontab bytes must survive unchanged. The
generated once-per-minute command only checks whether the configured interval is due. A
launcher, an installed scheduler binary or a fixture test is not evidence of an active service
or idle-time backups on this host. Installation/removal require fresh approval and exact owned
file identities; changed files and snapshots are not deleted as cleanup.

Ref events and periodic reconciliation preserve the current authorized reachable branch graph;
they cannot prove capture of transient or deleted unreachable commits. Idle ticks capture the
last saved context and report its age, not invented semantic updates. Missing coverage remains
a blocker to full periodic/every-commit protection, not an implicit choice of reduced coverage.

Native workers remain delivery-disabled and `tk-ship` remains preparation-only. Only a separately
approved checkpoint controller can push an exact working-branch checkpoint through current
in-process authority after its review/test and ownership gates. A fresh `PushGrant` binds the
exact repository, remote and ref; it is never stored in configuration or restored from backup.
There is no force, tag, PR, publish, merge or default/protected-branch write on this path.
Machine-loss durability, connection activation and observed idle execution remain separate
operator gates; installing a skill or running project setup establishes none of them.

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

The **Release** workflow automatically publishes stable releases on pushes and merged PRs to
`master` in `thunderock/thunderkit`. Feature branches cannot publish. There is no release PR or
version commit: canonical `v<version>` Git tags are the version authority, not the source
`package.json`. Release history continues in [GitHub Releases](https://github.com/thunderock/thunderkit/releases).

**Automatic versions.** The highest stable canonical tag is the base; prerelease tags are ignored.
Conventional Commits in the non-merge range since that base determine the strongest bump:

| Base version | Breaking change | `feat` | `fix`, docs, tests, CI, chores and other commits |
|---|---|---|---|
| Before 1.0.0 | minor | patch | patch |
| 1.0.0 and later | major | minor | patch |

Every nonempty non-merge range produces at least a patch; an empty range does not release.
The base must be an ancestor of the source. Without a stable tag, the source package's canonical
stable version is the bootstrap candidate, not an increment. If that version is occupied, release
stops rather than guessing: choose an unused exact version after resolving initial package setup.
Legacy tags can establish history but do not prove that old npm artifacts match this workflow.

**Manual exact versions.** Dispatch the retained `release-please.yml` filename on `master` with
an optional `version` and `npm_tag`. For example, a maintainer can request:

```sh
gh workflow run release-please.yml --ref master -f version=1.0.0
```

Any unused canonical exact stable or prerelease version is allowed, including arbitrary jumps;
it need not be the next major. Do not include a `v` prefix, range, whitespace, leading numeric
zeros or build metadata (`+...`). Empty `version` selects automatic stable versioning and cannot
be combined with a nonempty `npm_tag`. A supplied version is never silently bumped on collision.

Channels must match `^[a-uwyz][a-z0-9-]{0,63}$`: 1–64 lowercase characters, starting with a
letter other than `v` or `x`, followed by lowercase letters, digits or hyphens. Examples include
`latest`, `next`, `beta` and `maintenance-0`; `1.x`, `v1`, `x` and `Latest` are invalid.
Stable versions default to `latest`, prereleases to `next`; prerelease + `latest` is forbidden.
A version below the highest stable Git tag, including an older prerelease, requires an explicit
non-`latest` channel. New explicit manual releases may intentionally move such a channel backwards.
`latest` must never regress: writes must exceed the observed stable npm `latest` and cannot trail
the highest stable Git tag. Git history alone is not proof of the registry's current channel.

**What gets published.** Both jobs use Node 24, npm 11.19.1 and Python 3.12 on hosted runners.
The gate checks the source SHA, validates inputs, stamps a clean copy of that exact tracked source,
and creates a real tarball. It inspects safe archive members against the source inventory and
exercises the packed CLI before running tests, lint and the site build on the stamped source.
The repository's package version stays unchanged; the npm package and its CLI report the released
version. No developer working directory is published.

After checking that the tarball bytes survived the gates unchanged, the workflow uploads only
`release-plan.json` and `package.tgz` as `release-<runId>-<attempt>`. The publisher downloads the
exact immutable artifact ID from the same run, verifies the record's SHA-256 against the gate
output, and checks request/source bindings, tarball size and SHA-512 integrity. It then creates
an immutable annotated tag binding source, version, channel and tarball integrity, publishes
that same tarball through [npm OIDC trusted publishing](https://docs.npmjs.com/trusted-publishers)
with provenance, and creates the GitHub Release only after registry identity checks succeed.
After an accepted upload, it checks availability every 30 seconds for up to 30 minutes:
[npm scans new versions before making them available](https://github.blog/changelog/2026-07-28-npm-publish-time-malware-scanning-and-dual-use-metadata/).
It never repeats the upload while waiting. Missing visibility at the deadline fails with
`E_REGISTRY_PENDING`, not a false integrity mismatch; conflicting bytes, tags, channels or
failed reads still stop immediately. The publish job has a 50-minute total timeout, including
setup, the bounded upload and this wait. A pending timeout is not proof the version was never
uploaded; inspect its availability before retrying or advancing to another version.
Tag creation uses command-scoped bot identity; no persistent Git credentials or npm token is used.

**Retries and recovery.** Rerun the original workflow run after an interruption, rather than
dispatching today's source. A failed-publisher-only rerun reuses the successful gate artifact;
its earlier attempt is accepted only within the same run. A full rerun must recreate bytes
identical to the annotated reservation, preserving its original version and channel. Omitted
`npm_tag` restores that channel; a different supplied channel fails. Each invocation stops at its
first error, even when a write may have succeeded but its acknowledgement was lost. The next
same-run retry reads actual state and performs only missing operations, never replacing a tag
or republishing a version. npm presence alone is insufficient: SHA-512 must match the reservation.

A matching historical npm version may finish its GitHub Release after a newer channel has
superseded it, without moving the channel back. Missing, invalid or rewound channels stop recovery;
there is no automatic channel repair or token fallback. Foreign packages, conflicting tags and
inconsistent GitHub releases also stop. A new automatic release may advance past a valid
ancestor reservation only when current npm metadata and an exact GitHub Release lookup both
confirm that version was never published. The old tag stays immutable and is not reused;
version selection still advances above it. A partial publication (npm present but GitHub
missing), inconsistent identity, or unavailable evidence still blocks with `E_BASE_INCOMPLETE`.
A fresh stale automatic source skips; a fresh stale manual source fails. Reserved retries
must still belong to master history and cannot publish a missing old version below a newer
stable tag or over newer `latest`. The shared release concurrency group serializes these writers.
Expired artifacts require a full same-run rerun with identical bytes. Unreproducible bytes or an
expired GitHub rerun window require separately authorized operator recovery, not today's source.

**Enablement and cutover.** Keep the npm trusted-publisher binding on `release-please.yml` and
authorize direct publish, not stage-only access. Initial npm package/trusted-publisher setup,
public provenance eligibility and GitHub tag/ruleset permissions are maintainer prerequisites;
a package that does not yet exist may require separately authorized initial publication before
trusted publishing can be configured. This workflow cannot bootstrap authentication itself.

Before enabling this route, drain or cancel old release runs, stop historical reruns and other
manual publishers, remove any obsolete `publish.yml` trusted-publisher binding, and close any
obsolete release-please PR after merge. Deleting the old workflow/config files does not cancel
queued runs or revoke their historical definitions. This must be the sole package writer;
uncoordinated npm maintainers or other workflows can race a registry read and channel write.
Do not rewrite master history or release tags. Live OIDC exchange, permissions and these cutover
conditions must be verified separately; offline tests do not certify them.

The repository-wide `npm-release` concurrency group never cancels a running release. GitHub's
default queue retains only one pending run and may replace it, including a manual dispatch;
ordering is not a guarantee of commit order or one release per push/dispatch. The eligible source
that actually runs covers the full non-merge range since the completed stable base.

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
