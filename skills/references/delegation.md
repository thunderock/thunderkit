# Native-peer delegation

[dependencies.json](dependencies.json) is the authoritative operation and target map.
Only `omo` and `omh` are peer ecosystems; the distribution CLI is not a peer.
Resolve a target by `(ecosystem, selector)`, never by an unqualified skill name.
Targets are alternatives for a compatible host, not an instruction to run every peer.

## Resolve before invoking

1. Validate configuration and the requested skill and operation against the manifest.
   Use `default_operation` only when the operation is omitted; reject unknown values.
2. `delegation: off` invokes nothing native: no peer, installer, doctor, discovery
   probe, or routing helper. Record `owned` / `disabled` and use Thunderkit's procedure.
3. Filter targets by operation. An empty result means `owned` / `owned_policy`;
   another operation's target must not be borrowed to fill the gap.
4. Check the active host against the ecosystem's `hosts`, exact package pin, runtime,
   provenance, and every target `requires` entry. Capabilities are all-of requirements.
5. Verify requested model bindings and any runtime-home boundary before invocation.
   Missing or contradictory evidence is not permission to try an unverified target.
6. Record the decision, scope, and evidence; then invoke only an eligible target.
   Check returned evidence before accepting completion or handing ownership back.

`tool:skill` means the host's verified native skill-loading capability.
`model-binding:<class>` requires the project's selected class to be enforceable.
`delivery:disabled` requires the execution opt-out below; `user-request:explicit`
requires the user's actual missing-session lookup request, not inferred interest.
`runtime_home:isolated` requires the task-owned home described below.
Installation and doctor hints are operator instructions, never automatic actions.
In particular, `omh doctor` may record local state and is not a read-only probe.

## Decisions and reasons

| Decision | Meaning |
|---|---|
| `delegate` | A qualified target passed the gates and may run in its declared mode. |
| `owned` | Thunderkit owns the operation by policy or delegation is disabled. |
| `fallback` | A native candidate is unusable, but Thunderkit can safely perform its own procedure. |
| `blocked` | Configuration, safety, or evidence prevents any approved execution. |

| Reason code | Meaning |
|---|---|
| `compatible` | All required compatibility and safety gates passed. |
| `owned_policy` | No native target is declared for this operation. |
| `disabled` | Configuration explicitly turns delegation off. |
| `peer_missing` | The pinned peer or its required installed skill is absent. |
| `unsupported_host` | The active host is outside the peer's declared host set. |
| `version_mismatch` | The installed version differs from the exact pin. |
| `source_mismatch` | Loaded path, fingerprint, package source, or identity differs from the pin. |
| `capability_missing` | A required tool, runtime, binding mechanism, or safety control is absent. |
| `model_mismatch` | Requested, effective, or observed model choices disagree without approval. |
| `missing_evidence` | Required provenance, binding, or result evidence is unavailable. |
| `unsafe_runtime_home` | A mutating native route would use a shared or unproven home. |
| `invalid_config` | Configuration, skill, operation, or target qualification is invalid. |

Use the specific failed gate as the reason for `fallback` or `blocked`.
Fallback means a Thunderkit-owned implementation, never an undeclared peer.
If fallback cannot honor the same model, evidence, and safety policy, remain blocked.
An approved model substitution must be named and recorded, never made silently.

## Provenance is mandatory

The **loaded skill path + fingerprint + package version must match the pin**.
Capture package identity, resolved loaded path, and a content fingerprint tied to the
pinned package integrity and source, including `source_commit` when declared.
An installed package name or a successful doctor report alone does not prove readiness.
A same-name skill from another source is **not ready**, even if its text looks similar.
Missing proof is `missing_evidence`; contradictory proof is `source_mismatch`.
OMO skills must come from the pinned package's `dist/skills/<skill_name>/SKILL.md`.
They load in-process through the host skill tool, not through `npx skills`.
Thunderkit must not redistribute or relicense the OMO skill bodies.
OMH resolves categorized selectors beneath `~/.omh/skills`, using the matching
`~/.omh/manifest.json`: for example, `ultrawork/ulw-plan/SKILL.md`.
Its shared rail is `guide/omh-routing/references/skill-common-rail.md` under that root.
Keep the category in `selector`; `skill_name` remains the bare directory name.
The two `ulw-plan` names belong to different packages and are not interchangeable.

## Bind models, not prompt labels

Resolve project model selections through [model-roster.md](model-roster.md).
A skill's `role` is not a model class: prove the operation's actual class binding.
Preserve one selected planner, the approved executor set, and reviewer-family policy.
Record requested choices, effective host mapping, and models observed in runtime evidence.
Prompt text, suggested model names, and selected skill text are not binding evidence.

OMO `task()` has **no model parameter**; `load_skills` injects text only.
Bind through a verified agent/category mapping in the active host and prove the
effective mapping honors the requested class before delegating.
Do not fabricate a model argument or treat a loaded skill as an executor selection.

OMH `omh_delegate_route` writes `delegation.*` in the **active Hermes home**.
Use it only with an isolated, task-owned `HERMES_HOME` at:
`<repo>/.thunderkit/runs/<run-id>/hermes-home`.
Resolve the path and prove task ownership; reject shared, default, or symlink-escaped
homes before any routing write. Bind the task process to that home, never global state.
This rule applies whenever the routing tool is used, including component calls;
the OMH execution target additionally requires `runtime_home:isolated` unconditionally.
Read-only components may consume already-proven bindings without calling that tool.

## Exactly one workflow owner

In `handoff` mode, the native target owns the full scoped workflow until it returns.
Thunderkit supplies constraints and checks outputs, but does not run a competing loop.
In `component` mode, Thunderkit remains the owner: give a bounded read-only question
and receive findings, not edits, lifecycle transitions, or an independent workflow.
If the native component cannot honor that boundary, use fallback or block.

Before OMO `ulw-execute`, require an enforceable no-delivery opt-out:
**no `--make-pr`/`--ship`, no push/PR/merge**. Omitting flags alone is insufficient if
the native workflow still publishes; refusal to honor the opt-out blocks that target.
No delegated target gains delivery approval from readiness findings.
OMH visual QA prepares/assesses; the wrapper collects captures and owns acceptance.
OMH native debugging returns an investigation plan only, not a verified repair.
Session lookup runs only for explicit user-requested missing-session recovery;
Thunderkit continues to own ordinary handoff save and restore.

## Decision record

Every decision uses these required keys; paths are repository-relative evidence files.
`target` is null for owned work or no selected candidate; otherwise it contains all
six identity fields below, with package and version resolved from the ecosystem.
`bindings` contains class-to-model-list maps: `requested`, `effective`, and `observed`.
Resolve roster keys to comparable model identities; empty maps mean unproven, not equal.
`runtime_home` is null when unused; otherwise record the resolved task-owned path.
Populate `observed` from runtime evidence after invocation; a mismatch or missing
required evidence blocks acceptance, never becomes a fabricated successful result.

```json
{
  "skill": "tk-plan",
  "operation": "plan",
  "decision": "delegate",
  "reason_code": "compatible",
  "detail": "Pinned source and effective planner binding verified before invocation.",
  "target": {
    "ecosystem": "omo",
    "package": "oh-my-openagent",
    "version": "5.0.0-beta.81",
    "skill_name": "ulw-plan",
    "selector": "ulw-plan",
    "mode": "handoff"
  },
  "bindings": {
    "requested": {"planner": ["<model-id>"]},
    "effective": {"planner": ["<model-id>"]},
    "observed": {}
  },
  "runtime_home": null,
  "evidence_paths": [".thunderkit/runs/<run-id>/peer.json", ".thunderkit/runs/<run-id>/bindings.json"]
}
```
