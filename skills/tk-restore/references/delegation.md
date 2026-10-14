# Native-peer delegation

[dependencies.json](dependencies.json) is the authoritative operation and target map.
`omo`, `omh` and `gsd` are the peer ecosystems, one required per host (Hermes: `omh`, OpenCode: `omo`, every other host: `gsd`); the distribution CLI is not a peer.
Resolve a target by `(ecosystem, selector)`, never by an unqualified skill name.
Targets are alternatives for a compatible host, not an instruction to run every peer.

## Resolve before invoking

1. Validate the requested skill and operation against the manifest.
   Use `default_operation` only when the operation is omitted; reject unknown values.
2. Resolve model-free owned operations **before requiring configuration or capabilities**:
   `tk-ask validate`, `tk-memory view`, `tk-router bootstrap`, and `tk-handoff save`.
   Missing project configuration must not disable these operations.
3. For model-bearing operations, validate the explicit model selections even when
   delegation is disabled. Missing or invalid required selections block the operation.
   `delegation: off` invokes nothing native: no peer, installer, doctor, discovery
   probe, or routing helper. Record `owned` / `disabled` and use Thunderkit's procedure.
4. Filter targets by operation. An empty result means `owned` / `owned_policy`;
   another operation's target must not be borrowed to fill the gap.
5. Check the active host against the ecosystem's `hosts`, exact package pin, runtime,
   provenance, and every target `requires` entry. Capabilities are all-of requirements.
6. Verify requested model bindings and any runtime-home boundary before invocation.
   Missing or contradictory evidence is not permission to try an unverified target.
7. Record the decision, scope, and evidence; then invoke only an eligible target.
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

Every native target carries a `provenance` object with exactly these three keys:

| Key | Meaning |
|---|---|
| `root_kind` | `package` (OMO: the extracted npm `package/` directory) or `omh` (the OMH bundle home containing both `manifest.json` and `skills/`). |
| `entrypoint` | Root-relative POSIX path of the target's `SKILL.md`; it must appear in `files`. |
| `files` | Root-relative POSIX path → lowercase SHA-256 of the exact deployed bytes for the entrypoint and every required companion. |

Every OMH target also carries **target-level** `canonical_name`: the source-derived
catalog identity recorded as `name` in `manifest.json`, not the categorized directory
label. Reject missing, misplaced, or incorrect identities; neither `canonical_name`
nor `native_roles` belongs inside `provenance`. OMO targets have no `canonical_name`.

Fingerprints in `files` come from the integrity-verified published artifacts: the OMO
tarball checked against `integrity`, and deployed Markdown from the pinned OMH wheel.
They are never a capability snapshot,
an installed copy, or a manifest's self-reported hash. Compare the real bytes of every
listed file, in place, against these values; a path that merely contains the package
name, a `ready` flag, a null or missing hash, or a checksum that only matches itself is
not evidence. Missing required files or an entrypoint found at another location block
`delegate`; the shared rail is a required companion for every OMH target.
Companions may be elsewhere inside the same trusted peer root, including another skill
directory. Only the declared trusted companion map is eligible: reject unknown companions,
traversal, absolute or escaping paths, and malformed hashes. A snapshot cannot declare its
own additional trusted file. Unrelated files elsewhere in the peer root are not companions.

OMO skills must come from the pinned package's `dist/skills/<skill_name>/SKILL.md`.
Validate the root by reading its `package.json`: `name` and `version` must equal the pin
before any file fingerprint is compared. The `files` set is the complete
`dist/skills/<skill_name>/**` tree of the pinned release, so a missing script, reference,
or attribution file is a mismatch even when `SKILL.md` matches.
They load in-process through the host skill tool, not through `npx skills`.
Thunderkit must not redistribute or relicense the OMO skill bodies.

OMH's default bundle home is `~/.omh`, with `skills_root` at `~/.omh/skills` and identity
file `~/.omh/manifest.json`. The provenance root is the **bundle home**, not `skills_root`
and not the task's `HERMES_HOME`. Thus `skills/ultrawork/ulw-plan/SKILL.md` and
`manifest.json` are both relative to the same root, without doubling `skills/`.
Validate that manifest as the root identity: `schema_version` is `1`, `package` is
`oh-my-hermes`, and `version` equals the pin. Each skill record carries `name`, `path`,
`sha256`, and `source`. `path` is relative to `skills_dir` and includes the category but
not the leading `skills/`; `source: builtin` names the installer mode and is never compared
to the repository URL. `name` is the canonical catalog name (`ralplan`, `deep-interview`,
`ultrawork`), not the directory label in `selector`; match records on target `canonical_name`
and the categorized path together. A record's own `sha256` is untrusted until the file's
real bytes hash to the pinned value.
Its shared rail is `skills/guide/omh-routing/references/skill-common-rail.md` relative to
the bundle home, or `guide/omh-routing/references/skill-common-rail.md` under `skills_root`.
Keep the category in `selector`; `skill_name` remains the bare directory name.
The two `ulw-plan` names belong to different packages and are not interchangeable:
their entrypoint fingerprints, roots, and canonical identities all differ.
Artifact provenance proves which bytes a host loads; it does not prove that the host can
run the skill. Native runtime readiness is a separate gate with its own evidence.

## Bind models, not prompt labels

Resolve project model selections through [model-roster.md](model-roster.md).
A skill's `role` is not a model class: prove the operation's actual class binding.
Preserve one selected planner, ordered executor/reviewer selections, and reviewer-family policy.
Record requested choices, effective host mapping, and models observed in runtime evidence.
Prompt text, suggested model names, and selected skill text are not binding evidence.

The four planning/execution handoffs declare `native_roles` at target level:

| Target | Native role slot → selected class |
|---|---|
| OMO `ulw-plan` | root → planner; explore, librarian, metis → executors; momus, oracle → reviewers |
| OMH `ultrawork/ulw-plan` | root → planner |
| OMO `ulw-execute` | root, worker, explore, librarian → executors; gate-reviewer → reviewers |
| OMH `ultrawork/ulw-work` | root, lane, verification → executors; code-review-gate → reviewers |

For each of these targets, its `model-binding:*` class set must equal the values of
`native_roles`. The roles themselves are required, not inferred from whatever requirements
remain. Other targets have no role map. Components retain their declared class checks,
including planner for `tk-grill interview` and executors for `tk-learn discover`.
OMH planning's critic is a view within the same planner-bound session, not an independent
reviewer. Bound native reviews **do not replace the later Thunderkit family gate**.

Before handoff, prove every declared slot from live host descriptors and effective
configuration, including slots that might not run on this request. Record the descriptor,
selected catalog member, exact catalog-supported provider/model identity for the active
harness, and supported effort for each association. A nonempty model label or equal array
length is not proof. Preserve requested array order and the association of each selected
plural member; do not silently collapse a selection onto one opaque global model.
A slot may use only a member of its required class. A run need not exercise every selected
member, but the host must be able to represent the selection and its per-member associations.
Missing/opaque mappings deny delegation with `missing_evidence`; an out-of-class mapping
uses `model_mismatch`; an unrepresentable selection uses `capability_missing`.
Fallback is allowed only when the owned procedure can honor the same constraints.

OMO `task()` has **no model parameter**; `load_skills` injects text only. Read the effective
agent/category mapping for each slot and the actual root-session model. Role slot names
are contract vocabulary, not invented commands: the snapshot identifies the real host
descriptor filling each slot. Do not assume a running root changes after a configuration
edit; operator-approved native configuration or restart guidance is not live binding proof.

OMH `omh_delegate_route` writes `delegation.*` in the **active Hermes home**.
Use it only with an isolated, task-owned `HERMES_HOME` at:
`<repo>/.thunderkit/runs/<run-id>/hermes-home`.
The **actual parent process and child dispatcher must already use the same string path**
for that home; both observed paths must equal the verified `runtime_home`. Resolve the
actual project boundary and prove that the home is an existing, non-symlink task directory
on local disk inside it, with no symlink escape. A boolean claim, path substring, or two
different strings resolving to one location is insufficient. Passing a different
`hermes_home` to a routing tool does not change the dispatcher's active home.
Require the matching OMH plugin, one controller owning the home, and live support for
explicit per-lane provider, wire-model and supported-effort overrides. Use the native
set → dispatch → clear sequence and permit no unapproved fallback chain. Never mutate
shared `~/.hermes/config.yaml`, copy auth files into the project, or set up a home silently.
If the already-configured isolated host is unavailable, use a safe fallback or block.
This rule applies whenever the routing tool is used, including component calls;
the OMH execution target additionally requires `runtime_home:isolated` unconditionally.
Read-only components may consume already-proven bindings without calling that tool.

## Exactly one workflow owner

In `handoff` mode, the native target owns the full scoped workflow until it returns.
Thunderkit supplies constraints and checks outputs, but does not run a competing loop.
Keep native plans and approvals in `.omo/plans` or `.omh/plans`; respect each planner's
write boundary. The controller may normalize references after the handoff, not instruct
the native planner to write elsewhere. Execution remains a separate approved stage.
In `component` mode, Thunderkit remains the owner: give a bounded read-only question
and receive findings, not edits, lifecycle transitions, or an independent workflow.
If the native component cannot honor that boundary, use fallback or block.

Before OMO `ulw-execute`, require an enforceable no-delivery opt-out:
**no `--make-pr`/`--ship`, no push, PR, publish, or merge to master**; stop at verified
commits on the named feature integration branch. Local feature-branch integration is
not remote delivery. Omitting flags alone is insufficient if
the native workflow still publishes; refusal to honor the opt-out blocks that target.
No delegated target gains delivery approval from readiness findings.
OMH visual QA prepares/assesses; the wrapper collects captures and owns acceptance.
OMH native debugging returns an investigation plan only, not a verified repair.
Session lookup runs only for explicit user-requested missing-session recovery;
Thunderkit continues to own ordinary handoff save and restore.

OMO's no-plan bootstrap is outside the qualified `execute` operation: require an approved
plan rather than silently entering native planning. OMH's external-owner/`ulw-maestro`
path and `durable_checkpoint`/`ulw-loop` path remain **unqualified** at this pin. Their
conditional companions are deliberately absent from the trusted maps; a known path or
user acceptance alone cannot qualify their bytes and capabilities. If any of these paths
would be exercised, return `capability_missing` with fallback or blocked; do not invoke,
install, or fabricate fingerprints for them.

An uncertain timeout is blocked/unknown, not permission to launch another owner or start
the portable execution fallback. Inspect the captured native session before proceeding.

## Decision record

Every decision uses the fixed keys below with `schema_version: 1`; evidence paths are
repository-relative. Exit 0 means a routing decision was computed, not that native work
ran; blocked operations return 1, malformed input returns 2, with one JSON object in JSON mode.
`target` is null for owned work or no selected candidate; otherwise it contains all
six identity fields below, with package and version resolved from the ecosystem.
`bindings.requested` preserves the validated class selections: `planner` is one catalog-key
scalar, not a model-ID array; explicit `executors` and `reviewers` are ordered, unique arrays
of catalog keys. Retain the `reviewers: "all"` request when used and associate its reachable
expansion with effective bindings rather than replacing the request silently.
`effective` records the proven per-slot/member associations for the target's required
classes; it does not turn unused class selections into verified native roles.
`bindings.observed` is **null before execution**, never an empty map standing for evidence.
`runtime_home` is null when unused; otherwise record the resolved task-owned path.
Populate `observed` only from runtime evidence after invocation; a mismatch or missing
required evidence blocks acceptance, never becomes a fabricated successful result.

This illustrative OMH planning record has one native role; the other selected classes
remain available for later stages. It is a record shape, not a report of local readiness.

```json
{
  "schema_version": 1,
  "skill": "tk-plan",
  "operation": "plan",
  "decision": "delegate",
  "reason_code": "compatible",
  "detail": "Locked source bytes and effective planner binding verified before invocation.",
  "target": {
    "ecosystem": "omh",
    "package": "oh-my-hermes",
    "version": "2.0.5",
    "skill_name": "ulw-plan",
    "selector": "ultrawork/ulw-plan",
    "mode": "handoff"
  },
  "bindings": {
    "requested": {"planner": "opus48", "executors": ["fable51", "opus5"], "reviewers": ["sol", "opus5"]},
    "effective": {
      "root": {"class": "planner", "catalog_key": "opus48", "provider": "anthropic", "model_id": "claude-opus-4-8"}
    },
    "observed": null
  },
  "runtime_home": null,
  "evidence_paths": [".thunderkit/runs/<run-id>/peer.json", ".thunderkit/runs/<run-id>/bindings.json"]
}
```

Preserve existing plan goal/layers/lanes fields. A native plan adds
`native_plan: {ecosystem, package_version, skill_name, artifact, sha256, approval_status}`
and a model-contract snapshot. `artifact` is a verified repo-relative native plan path;
approval comes from native acceptance evidence. Do not rewrite the native artifact.

A delegated run records `{lane_id, ecosystem, package_version, skill_name, requested_model,
effective_model, observed_model, observed_family, artifact, artifact_sha256, session_id,
status, evidence_paths}`. Use null/unverified for unavailable facts, including an absent
resume ID. Exit 0, a word `done`, or a skill listing is not completion evidence. Bind gates
to the actual source/diff/artifact identities; changed bytes invalidate dependent readiness.
