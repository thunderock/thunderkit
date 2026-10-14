# Native workflow dependencies

Thunderkit is a policy and interoperability layer, not a native runtime installer. It owns
user-selected model classes, lifecycle routing, portable project context, independent
cross-family review and completion gates. It can reuse compatible native implementations
without copying their workflow bodies or running a second workflow owner.

The authoritative registry is [`skills/references/dependencies.json`](skills/references/dependencies.json).
It records the required peer for each host, the release channel resolved at install time,
source identity, the files each target loads, host constraints, and each skill's
operation-specific targets and owned fallback. Installed versions and file digests live in the
machine-local lock `.thunderkit/peers.lock.json`, which is never committed or packed.

## Required peer per host

| Host | Required peer | Package | Channel | License | Upstream source |
|---|---|---|---|---|---|
| Hermes | OMH | `oh-my-hermes` | `latest` dist-tag | MIT | [rlaope/oh-my-hermes](https://github.com/rlaope/oh-my-hermes) |
| OpenCode | OMO | `oh-my-openagent` | highest `5.x` `-beta.N` prerelease | SUL-1.0 | [code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) |
| Claude Code, Codex, Copilot, Gemini, Cursor, Windsurf | GSD | `get-shit-done-cc` | `latest` dist-tag | MIT | [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) |

No peer version is fixed in the repository. `thunderkit install` resolves each channel when you
install, and the lock records the resolved version and the SHA-256 of every installed file the
targets use (trust on first lock). OMC stays excluded. Read OMO's
[license](https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/a5eb7c130cae64125f31de13adee083eccc5d004/LICENSE.md)
before installing it.

Thunderkit's MIT license does not relicense either peer or confer commercial-use rights to
OMO. Thunderkit does not redistribute the peer implementations; each upstream license applies.

Each host has exactly one required peer. When it is missing, a skill stops and prints the
peer's non-interactive install command instead of running an owned copy. On GSD hosts only
project-free commands are targets (`gsd-debug`, `gsd-explore`); every other operation uses a thin
Thunderkit-owned path, because GSD phase commands need a `.planning/` project. Even on a
supported host, a target is conditional: a package present on disk is not necessarily loaded, compatible,
model-bound, or verified. Each operation uses only its own declared target, never a same-named
skill from a different source. A native installation on one host does not activate another.

## Runtime requirements

| Surface | Requirement | Boundary |
|---|---|---|
| Thunderkit npm pointer: help, version, deps | Node ≥18 | Displays information; `deps` does not run peer commands |
| Thunderkit install/list | Node ≥22.20.0 | Invokes the pinned `skills@1.7.0` distribution CLI |
| Thunderkit local resolver/model helpers | Python ≥3.11 | Local validation; no native installer or model call |
| OMH npm launcher | Node ≥18 | Separate from the Thunderkit distribution CLI requirement |
| OMH packaged wheel | Python ≥3.11 | Required by the Python implementation behind the launcher |
| GSD installer | Node ≥22.0.0 | Required by `get-shit-done-cc` |
| OMO | Host-managed; runtime version not specified in the registry | Follow the upstream host requirements; no universal Node-only claim |

`skills@1.7.0` is a distribution tool, **not a peer ecosystem**. The pointer's Node ≥18
package requirement does not mean installation works on Node 18. Both install and list enforce
the higher distribution boundary; list still delegates to that CLI even though it installs no
skills. A distribution lock for individual skills does not install or restore native peers.

## Display information without native setup

```sh
npx thunderkit deps
npx thunderkit deps --json

# Existing checkout: no npx package retrieval
node bin/thunderkit.js deps --json
```

JSON contains `schema_version` (2), `hosts`, `ecosystems`, `distribution_cli` and `note`. The peer records
include manual hints, not results of probing the machine. `deps` never executes installation,
activation, doctor, update or login commands. Running it is not evidence that a model answered
or a native workflow ran. The `npx` form may retrieve Thunderkit itself if it is not cached.

## Separately approved native setup

Installing Thunderkit through `npx thunderkit install` or `skills@1.7.0` installs Thunderkit
skills only. Native setup is an optional operator action, outside that installation. Review
the pinned peer's requirements and license before making host configuration changes.

### OMO

The registry's exact installation hint is:

> Host-native opencode.json plugin pin: {"plugin":["oh-my-openagent@<resolved 5.x beta>"]}; Thunderkit never runs this installation.

This is an OpenCode plugin configuration hint, not an instruction to overwrite an existing
configuration. OMO is required only on OpenCode; Codex uses GSD. Consult the upstream's
host-specific instructions;
until the loaded source and effective bindings are proven, the native route is unavailable.

After operator-approved installation, activate/load the peer through the host's native
mechanism. If a restart is needed, do it separately; loading a Thunderkit skill does not
reconfigure an already running host. OMO skills load in-process from `dist/skills`, not through
`npx skills`. Use the verified host skill tool (`skill(name=...)` / `$name`), not an invented
ecosystem-prefixed slash command.

The separately run doctor hint, copied from the registry:

```sh
bunx oh-my-openagent@<locked version> doctor
```

### OMH

The exact registry hint combines package installation with native setup:

```sh
npm install -g oh-my-hermes@latest && omh setup --full --yes --no-interactive --no-menubar --scope user
```

The first command installs the launcher; the second performs user-scoped activation
and configuration. These are explicit operator-approved side effects, never actions taken by
Thunderkit's install or dependency display. Confirm the matching plugin and categorized skills
are loaded in the actual Hermes process before attempting delegation.

The separately run doctor hint is:

```sh
omh doctor
```

**`omh doctor` may record local state.** It is not a guaranteed read-only probe, and a successful
doctor report alone does not prove a workflow's provenance, model bindings or completion.

The default skill root is `~/.omh/skills`, with identity in `~/.omh/manifest.json`. The provenance
root is the bundle home containing both, not the skills subdirectory or a task's `HERMES_HOME`.
Use categorized selectors such as `ultrawork/ulw-plan`; the shared required reference is
`guide/omh-routing/references/skill-common-rail.md`. Missing or quarantined companions make a
target unavailable; a known pathname does not authorize bypassing a scanner.

### GSD

The registry hint for Claude Code, Codex, Copilot and the other GSD hosts:

```sh
npx get-shit-done-cc@latest --<runtime> --global
```

Pass the host runtime flag (for example `--claude`, `--codex` or `--copilot`) and a location
flag; with both, the installer does not prompt. It writes `gsd-<name>/SKILL.md` skills and
`gsd-file-manifest.json` into the host config directory. Thunderkit never runs it. GSD phase
workflows expect a `.planning/` project, which Thunderkit ignores in git and npm; only commands
that work without one are targets.

## Models remain the user's choice

[`models.json`](skills/references/models.json) defines the supported mappings independently of
the peer host matrix. The catalog currently declares:

| Model key | Family | Supported harness mappings |
|---|---|---|
| `opus48` | Anthropic | Claude, Hermes |
| `opus5` | Anthropic | OpenCode, Hermes |
| `fable51` | Anthropic | OpenCode, Hermes |
| `sol` | OpenAI | Codex |

All other model/harness combinations are unsupported by this catalog, not guessed aliases.
For example, GSD's Codex host entry does not make `opus48` a supported Codex model; OMH's Hermes
entry does not make `sol` a supported Hermes model. A peer may therefore be usable for one
operation but unable to represent an entire chosen model set for another.

The three required choices are `classes.planner` (one key), `classes.executors` (a nonempty
unique list), and `classes.reviewers` (`"all"` or a nonempty unique list). No backend chooses
these for the user. `"all"` considers every catalog model, including those outside the other
classes; preflight reports unavailable optional candidates. Explicit selections must succeed,
and responding reviewers must independently meet `review_families_min` (at least two).
Three Anthropic variants are still one family, regardless of how many harnesses run them.

Native roles must honor the selected classes and supported effort settings through effective
host configuration, not prompt labels. OMO `task()` has no model parameter; `load_skills`
injects instructions, not model bindings. A host reconfiguration or restart remains an
operator action, not proof that an existing root session switched models. Native internal
critique is not automatically an independent cross-family review.

## Fallback and ownership

The resolver returns `delegate`, `owned`, `fallback`, or `blocked`. It validates configuration,
operation, source identity, the locked version and required file bytes, capabilities, effective model bindings
and safety boundaries before a native invocation. A successful resolver exit means a routing
decision was computed, not that work completed.

- **Owned:** no target is declared, or `delegation: "off"` disables native invocation.
- **Fallback:** a peer is missing, unsupported, mismatched or insufficiently evidenced, and
  the skill's documented Thunderkit-owned procedure can meet the same constraints.
- **Blocked:** no compliant procedure can honor explicit models, review families or safety
  requirements. Report what is missing; never silently substitute a model or another ecosystem.

A bounded native component returns findings while Thunderkit owns the stage. A full planning
or execution handoff has one native owner, retaining its native artifacts and approvals;
execution remains a separate approved stage. Completion checks use actual model identities,
artifact/diff identities and genuine session evidence. Missing resume IDs stay explicitly
unavailable. Changed artifacts invalidate dependent gates, and uncertain in-flight timeouts
do not authorize duplicate execution or a competing fallback loop.

OMO execution must honor an explicit no-push/no-PR/no-publish/no-merge-to-master boundary and
stop at verified commits on the named feature branch; omitting delivery flags alone is not
enough. OMH execution requires the actual parent process and child dispatcher to share the
same existing task-owned local-disk `HERMES_HOME` inside the project, with the matching plugin.
Thunderkit does not mutate a shared Hermes home or copy credentials into the project to make
that route ready. See the [delegation contract](skills/references/delegation.md) for full gates.

## Recovery and checkpoint connections

Recovery and preservation helpers are Thunderkit-owned, model-free software, not native-peer
workflows. `tk-restore` load/preview/export uses Python ≥3.11 and supported Linux/POSIX no-follow
filesystem operations; bundled-history verification/export additionally needs local Git and
bounded writable scratch. Recovery can read an explicit snapshot without the original checkout
or model config. Export and checkpoint writes each require their own explicit approval.
See the [recovery and preservation guide](README.md#recovery-and-preservation) for actual commands.

`checkpoint_cli.py preview`, `setup` and `resume-setup` are project-only. Setup writes inert
private settings and a first verified snapshot, not host connections or publication authority.
`resume-setup` needs fresh approval for the exact failed local claim with unchanged scope and
ownership evidence; it is not an adoption or cleanup command. Retain the actual successful
setup stdout using `umask 077` in a new user-owned mode-0600 file outside capture sources.
`--setup-result` requires that exact successful `SetupResult` JSON, not a fabricated example,
serialized executable grant or saved approval. See the
[setup and receipt commands](README.md#preview-project-setup-and-capture-a-checkpoint).

`capture --scope global --global-config` supports independently approved global files with an
exact identity, matching backup root and a selected subset of `global.approved_sources`.
Global capture has no project root, branch history or push. Its versioned nested policy must
validate too; see the [global settings example](README.md#capture-an-independently-approved-global-scope).
Global capture/runtime support is not global scheduler support: the implemented host and
schedule commands require a local project repository. There is no global host installation
or periodic-service command on this surface; that coverage remains a gap.

`host-preview` and separately approved `host-install` bind the exact unchanged setup scope
and recorded storage identity, verified snapshot and explicit trusted `--python`. A replaced
store is refused without requalifying the replacement. Installation activates only the
repository-local `reference-transaction` hook; foreign hooks and external hook managers are
refused, not replaced. `schedule-preview`, `schedule-install` and `schedule-remove` use an
explicit trusted `--crontab` and require both `--service-qualified` and `--exclusive-writer`,
backed by actual operator evidence of a qualified running service and exclusive-writer
authority. Binary presence and those flags alone do not establish these conditions.
The driver preserves unrelated crontab bytes with read/compare-and-swap replacement.

Remove the schedule before `host-remove`; both removals require fresh approval and reload
private ownership records. Retain blocked/uncertain ownership state for inspected recovery;
do not delete it to force success. Changed files and snapshots are not cleanup targets.
Portable `backup.enabled: false` is checked at the installed project host boundary and stops
capture without removing the connection. Other installed settings remain frozen and need
reviewed reinstall. See the [host commands](README.md#host-connections-require-separate-qualification)
and [schedule/removal commands](README.md#preview-install-and-remove-the-project-schedule).

A generated periodic command is not scheduler activation or observed idle coverage. Loading a
skill creates no authority or daemon activation; full host activation requires separate
approval and real qualification. No new daemon, replacement global hook manager, automatic
model selection or credential copying is part of this setup.

The [approved checkpoint controller exception](skills/references/delegation.md#approved-preservation-checkpoints)
does not relax native `delivery:disabled` or preparation-only `tk-ship`. A default-on push
preference cannot replace a fresh exact-repository/remote/ref `PushGrant` from current in-process
authority after the captured commit's review/test and ownership gates. Never save or restore
that grant. Ref-event capture and reachable-history reconciliation do not prove preservation of
transient unreachable commits; real storage durability and host cadence need separate evidence.

## Context cost and standalone skills

The OMH full profile installs **123 skills**; core installs **10**, according to the
registry. The provided setup hint selects full. These counts are not token-cost measurements:
host discovery, loaded instructions, tools and companion references affect context usage.
OMO also loads native instructions in-process; a small Thunderkit wrapper does not guarantee
a small total prompt or low runtime cost. No numeric OMO context budget is specified here.

Standalone Thunderkit skills carry local copies of their owned references and helpers, not
any upstream catalog. Installing one does not install its sibling `tk-*` stages or native
peers. Missing siblings are named as unavailable rather than read through guessed paths or
installed implicitly. Keep project context committed as described in the
[README](README.md#project-memory--thunderkit); changing hosts still requires fresh qualification.
