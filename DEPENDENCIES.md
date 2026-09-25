# Native workflow dependencies

Thunderkit is a policy and interoperability layer, not a native runtime installer. It owns
user-selected model classes, lifecycle routing, portable project context, independent
cross-family review and completion gates. It can reuse compatible native implementations
without copying their workflow bodies or running a second workflow owner.

The authoritative registry is [`skills/references/dependencies.json`](skills/references/dependencies.json).
It records exact package pins, source identity, published integrity, loaded-file fingerprints,
host constraints, and each skill's operation-specific targets and owned fallback.

## Optional peers and host support

| Peer | Exact pin | License | Eligible active hosts | Upstream source |
|---|---|---|---|---|
| OMO | `oh-my-openagent@5.0.0-beta.81` | SUL-1.0 | OpenCode, Codex | [code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) |
| OMH | `oh-my-hermes@2.0.3` | MIT | Hermes | [rlaope/oh-my-hermes](https://github.com/rlaope/oh-my-hermes) |

OMO's registry source commit is `a5eb7c130cae64125f31de13adee083eccc5d004`; read its
[pinned license](https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/a5eb7c130cae64125f31de13adee083eccc5d004/LICENSE.md).
The published metadata is linked at
[OMO 5.0.0-beta.81](https://registry.npmjs.org/oh-my-openagent/5.0.0-beta.81) and
[OMH 2.0.3](https://registry.npmjs.org/oh-my-hermes/2.0.3).
Thunderkit's MIT license does not relicense either peer or confer commercial-use rights to
OMO. Thunderkit does not redistribute the peer implementations; each upstream license applies.

Only these two ecosystems are eligible. Claude Code and other skill-compatible hosts have no
declared native peer route and use Thunderkit-owned portable procedures. Even on an eligible
host, a target is conditional: a package present on disk is not necessarily loaded, compatible,
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
| OMO | Host-managed; runtime version not specified in the registry | Follow the pinned upstream host requirements; no universal Node-only claim |

`skills@1.7.0` is a distribution tool, **not a third peer ecosystem**. The pointer's Node ≥18
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

JSON contains `schema_version`, `ecosystems`, `distribution_cli` and `note`. The peer records
include manual hints, not results of probing the machine. `deps` never executes installation,
activation, doctor, update or login commands. Running it is not evidence that a model answered
or a native workflow ran. The `npx` form may retrieve Thunderkit itself if it is not cached.

## Separately approved native setup

Installing Thunderkit through `npx thunderkit install` or `skills@1.7.0` installs Thunderkit
skills only. Native setup is an optional operator action, outside that installation. Review
the pinned peer's requirements and license before making host configuration changes.

### OMO

The registry's exact installation hint is:

> Host-native opencode.json plugin pin: {"plugin":["oh-my-openagent@5.0.0-beta.81"]}; Thunderkit never runs this installation.

This is an OpenCode plugin configuration hint, not a Codex installer command or an instruction
to overwrite an existing configuration. The registry permits Codex as a host but supplies no
separate Codex installation command. Consult the pinned upstream's host-specific instructions;
until the loaded source and effective bindings are proven, the native route is unavailable.

After operator-approved installation, activate/load the peer through the host's native
mechanism. If a restart is needed, do it separately; loading a Thunderkit skill does not
reconfigure an already running host. OMO skills load in-process from `dist/skills`, not through
`npx skills`. Use the verified host skill tool (`skill(name=...)` / `$name`), not an invented
ecosystem-prefixed slash command.

The separately run doctor hint, copied from the registry:

```sh
bunx oh-my-openagent@5.0.0-beta.81 doctor
```

### OMH

The exact registry hint combines package installation with native setup:

```sh
npm install -g oh-my-hermes@2.0.3 && omh setup --full --yes --no-interactive --no-menubar --scope user
```

The first command installs the pinned launcher; the second performs user-scoped activation
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
For example, OMO's Codex host entry does not make `opus48` a supported Codex model; OMH's Hermes
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
operation, exact source/version and required file bytes, capabilities, effective model bindings
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

## Context cost and standalone skills

The pinned OMH full profile installs **123 skills**; core installs **10**, according to the
registry. The provided setup hint selects full. These counts are not token-cost measurements:
host discovery, loaded instructions, tools and companion references affect context usage.
OMO also loads native instructions in-process; a small Thunderkit wrapper does not guarantee
a small total prompt or low runtime cost. No numeric OMO context budget is specified here.

Standalone Thunderkit skills carry local copies of their owned references and helpers, not
either upstream catalog. Installing one does not install its sibling `tk-*` stages or native
peers. Missing siblings are named as unavailable rather than read through guessed paths or
installed implicitly. Keep project context committed as described in the
[README](README.md#project-memory--thunderkit); changing hosts still requires fresh qualification.
