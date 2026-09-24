---
name: tk-plan
description: "Use to turn an agreed big-repo change into dependency-layered lanes with file ownership, acceptance criteria and verification commands. Hands planning to one qualified native planner or a bound owned planner, preserves native artifacts and approvals, and prepares a lane summary for separate cross-family plan review before execution approval."
compatibility: "Python 3.11+ standard library for the bundled resolver. Optional native handoffs require pinned oh-my-openagent on OpenCode/Codex or oh-my-hermes on Hermes, with proven loaded provenance and effective role bindings. No automatic installation or host reconfiguration."
metadata:
  thunderkit-role: "planner"
  thunderkit-tier: "plan"
  thunderkit-delegates: "omo:ulw-plan omh:ultrawork/ulw-plan"
  thunderkit-contract: "1"
---

# tk-plan — decompose into parallel lanes

The heart of the thunderkit thesis. `tk-plan` takes a change and produces a plan whose unit is
the **lane**: a disjoint, file-scoped slice of work that can run *in parallel* with its siblings
without collision, ordered into dependency layers. Entangled work stays explicitly sequential;
never manufacture parallelism or treat planning as permission to implement.

The planner is the user's one `classes.planner`, not a preferred model or the arbitrary current
root. Preserve `classes.executors` and explicit `classes.reviewers` in their requested order,
or retain the literal reviewers `"all"`. Backend choice never changes those selections.

## Inputs and paths

**Skill root** is the installed directory containing this file. Resolve
`references/dependencies.json`, `references/delegation.md`, `references/model-roster.md`,
`references/models.json`, `references/config.schema.json` and `scripts/tk-resolve.py` from that
root, not the current directory, a checkout, or another installed skill.

**Project root** is the actual repository being planned. Read its required, explicit,
project-contained `.thunderkit/config.json`, `.thunderkit/BRIEF.md` and `.thunderkit/MAP.md` inputs.
Also read, validate and consume `.thunderkit/SPEC.md`, `.thunderkit/CONTEXT.md` and other upstream
outputs whenever already produced or required by the approved scope/lifecycle. A full milestone
requires the outputs of its preceding stages.

For input completeness on the minimum path, config/BRIEF/MAP suffice when spec/discuss were
intentionally omitted and no additional upstream output is required or already produced. Record
each intentional stage omission and its reason with the input record; a missing file alone does
not establish omission.

Record input paths, content digests and source/base identity. Reject escaping paths and resolve
aliases before checking containment. Supply the settled goal, scope, non-goals, constraints,
accepted decisions, `frozen_paths`, `max_layers`, acceptance checks and verification requirements.
A missing required or previously produced input, stale evidence (including optional inputs),
contradictory artifacts or open brief unknown stops planning; do not silently ignore it, replace
it with assumptions or reopen a settled decision.

Validate all three classes through the bundled config contract before model-bearing work,
including owned work with delegation off. Missing choices are not defaults. Complete valid legacy
configuration is a preview only; never save it or change a model without the user's approval.
For reviewers `"all"`, consider every catalog model, not just the planner and executors. Report
unavailable optional candidates; every explicit selection must succeed and the responding review
families must independently meet `review_families_min`. A native host's representable subset does
not establish reachability or that later family gate. Preserve the controller's current preflight
requirements; resolver admission cannot rescue missing or failed preflight evidence.

Before handing work to `tk-router`, `tk-map`, `tk-spec`, `tk-discuss`, `tk-grill`, `tk-test`,
`tk-review` or `tk-execute`, check that the sibling is actually available. If absent, report the
missing prerequisite and stop that transition; do not read a presumed sibling path or install it.

## Delegation

The local manifest's `tk-plan` / `plan` entry is authoritative. Its targets are alternatives,
both in **handoff** mode, not components or planners to launch for each lane:

| Native identity | Loaded provenance and required companions | Native role slots → selected classes |
|---|---|---|
| `omo:ulw-plan`, `oh-my-openagent@5.0.0-beta.81`, OpenCode/Codex | Package root with matching `package.json`; `dist/skills/ulw-plan/SKILL.md` plus `agents/openai.yaml`, `references/full-workflow.md`, `references/intent-clear.md`, `references/intent-unclear.md` and `scripts/scaffold-plan.mjs` under that skill directory | `root` → planner; `explore`, `librarian`, `metis` → executors; `momus`, `oracle` → reviewers |
| `omh:ultrawork/ulw-plan`, `oh-my-hermes@2.0.3`, Hermes | Bundle root containing `manifest.json` and `skills/`; entry `skills/ultrawork/ulw-plan/SKILL.md`, canonical installer name `ralplan`, and `skills/guide/omh-routing/references/skill-common-rail.md` | `root` → planner |

These addresses are registry identities, not invented slash commands. Invoke the admitted
selector through the host's real skill tool: OMO `ulw-plan` or OMH `ultrawork/ulw-plan`.
OMH's catalog name `ralplan` is not a replacement selector. Its bundle root is neither
`skills_root` nor `HERMES_HOME`. Compare package/version/source, root identity, loaded entrypoint
and the real bytes of **every** manifest companion with the pinned fingerprints. A same-name
skill, quarantined file, missing companion, null hash, self-reported checksum or `ready` flag
does not qualify. Consume the local pins; do not qualify a different release on the fly.

Before handoff, verify every `native_roles` slot, including roles that may not run on this request.
Use actual live host descriptors and effective agent/category or session mappings. Record each
slot's class, selected catalog member, exact supported provider/model identity and supported
effort. OMO requires all three binding classes; OMH planning requires only planner. Preserve the
other selected classes for later stages without claiming they were exercised by OMH planning.
For each required explicit plural selection retain **every member's association and order**;
a slot may use only its own class. For `"all"`, retain the request and the reported native subset
separately from the later catalog-wide reviewer expansion. A run need not exercise every member,
but an opaque, collapsed, reordered or unrepresentable selection is not admitted.

OMO `task()` has no per-call model parameter; `load_skills` supplies instructions, not a model
binding. Inspect the actual root-session model as well as effective delegated role slots.
Editing config does not prove the running root switched. If a selected model requires native
configuration or restart, report operator guidance and wait for fresh binding evidence; never
rewrite global/provider/auth configuration to make a route appear ready. OMH planning is one
planner-bound session; its in-session critic is not an independent reviewer. OMO Momus/Oracle
bindings also do not replace Thunderkit's separate cross-family plan review.

Gather the current capability snapshot without credentials, installation or doctor calls. With
`SKILL_ROOT`, `PROJECT_ROOT` and `RUN_ID` set to the actual installed skill, repository and
controller run, resolve the explicit operation:

```sh
python3 "$SKILL_ROOT/scripts/tk-resolve.py" --skill tk-plan --operation plan \
  --project-root "$PROJECT_ROOT" --config "$PROJECT_ROOT/.thunderkit/config.json" \
  --capabilities "$PROJECT_ROOT/.thunderkit/runs/$RUN_ID/capabilities.json" --json
```

The input paths must be explicit and inside the actual project root. For delegation off or no
enabled ecosystem, omit `--capabilities` and do not discover native peers. Keep the complete
resolver record unchanged, including `decision`, `reason_code`, `target`, requested/effective
bindings and evidence paths. Exit 0 means routing was computed, not native completion.
Only `delegate` / `compatible` admits the chosen native owner; `blocked` means stop.

## Native ownership and return

Give the single admitted owner the settled inputs above and the required lane/output contract
below. It owns its planning workflow, native approvals and native state until a **known return**.
Thunderkit does not cut a competing plan, launch another planner per lane or start implementation
while that owner is active. Preserve the native workflow rather than copying it into this skill.

- **OMO** writes only within its planning domains `.omo/drafts/` and `.omo/plans/`. Keep drafts
  distinct from the actual approved plan and preserve its native approval flow and evidence.
- **OMH** records under `.omh/plans/` through its native `omh hermes plan --record` flow and
  obtains native acceptance through `omh hermes plan-accept <path>`. Retain the actual acceptance
  evidence for the returned artifact; an in-session critique or a recorded draft is not acceptance.
- Neither planner may write into `.thunderkit/`, edit implementation files, dispatch execution,
  push, open a PR, publish or merge. The controller alone performs later normalization, after
  the native owner returns. If the native workflow cannot preserve this boundary, do not invoke it.
- Do not activate conditional external-owner/`ulw-maestro`, durable-checkpoint/`ulw-loop`, or
  no-plan execution paths. They remain unavailable at the pin; report `capability_missing`
  as the unmet capability separately from the unchanged resolver record. Do not launch them
  or add their sources to trust.

After a known terminal return, the controller reads the actual native artifact and acceptance
evidence. Verify a regular, project-contained file under the selected `.omo/plans/` or
`.omh/plans/` directory, with no traversal or symlink escape. Hash its **unaltered bytes**, record
the actual repo-relative path, and bind native approval to that content identity. Missing,
unapproved, conflicting or unusable output stops readiness; never infer success from returned
Markdown, exit 0, `done`, a filename or an old approval. Request correction through the same
native planning flow only after ownership is settled, then require approval for the corrected bytes.

Record genuine native session/resume identity and requested versus effective versus observed
model identities alongside the returned artifacts. Missing facts remain `null` / `unverified`,
including `session_id`, `observed_model` and `observed_family`; a config choice is not a runtime
observation. Retain native output evidence even when incomplete, but missing required identity
proof or an unapproved model change prevents acceptance.

An unknown, timed-out or still-in-flight native owner retains ownership. Inspect its **real captured
session** and artifact state before any retry or fallback; do not invent a session ID or treat
history metadata as proof that the session is resumable. Without an ID or known terminal state,
stop as blocked/unknown and report the missing evidence. A known invocation/output failure is
recorded separately; it never rewrites the earlier resolver reason into a different routing result.

## Fallback

`owned` / `disabled` or `owned_policy` and a computed `fallback` may use the bounded lane procedure
below only when no native owner remains active or uncertain. Keep the specific resolver reason
and failure evidence. Delegation off performs no native invocation, discovery, doctor or routing
helper call. No undeclared ecosystem substitutes for a failed peer.

Owned planning still requires a **genuinely bound selected planner**. Validating configuration or
mentioning `classes.planner` in a prompt does not bind the current root. Use only an already
supported channel proven to run that selected planner, with the same scope, limits and approval
policy; otherwise stop as blocked and report the binding gap. A `blocked` resolver result never
starts fallback. Once admitted, the owned planner produces the same lane contract and the
controller writes the documented Thunderkit outputs; omit `native_plan` for owned work rather
than fabricating native provenance or approval. A known failed handoff must be explicitly retired
before an owned replacement is authorized; never hide an unusable native artifact behind a ready
summary. The separate plan-review and execution-approval gates apply unchanged.

## What a lane is

- **Disjoint file scope** — two lanes in the same layer must not write the same files. This is
  what makes parallel execution safe. If two slices need the same file, they belong in different
  *layers*, not the same layer.
- **A dependency layer** — lanes in layer N may depend only on layers < N. Layer 0 lanes have no
  intra-plan dependencies; they become eligible only after review and execution approval.
- **Acceptance criteria** — what "this lane is done" means, testably.
- **A verification command** — the exact command `tk-review` runs to gate the lane. No command
  → the lane is `blocked`, not plannable.
- **A model hint** — critical-path lane vs. breadth/cleanup lane, resolved within the selected
  executor class. A hint cannot substitute a model or approve dispatch.

## Output contract — `.thunderkit/PLAN.md` + `.thunderkit/plan.json`

Human-readable `PLAN.md` and a machine-readable `plan.json` that `tk-execute` consumes:

For a native handoff these are **controller-derived lane summaries**, not another executable
plan. Normalize only after known return, approved artifact verification and lane validation;
retain the native plan as the execution authority. Do not change its bytes to fit the summary.
Preserve the existing goal/layers/lanes structure and every lane's fields:

```json
{
  "goal": "one-line change description",
  "layers": [
    {
      "layer": 0,
      "lanes": [
        {
          "id": "L0-auth-token-refresh",
          "files": ["src/auth/token.rs", "src/auth/token_test.rs"],
          "depends_on": [],
          "acceptance": "token refresh retries 3x with backoff; expired token triggers refresh",
          "verify": "cargo test -p auth token::",
          "model_hint": "critical-path"
        }
      ]
    }
  ]
}
```

For native planning add
`native_plan: {ecosystem, package_version, skill_name, artifact, sha256, approval_status}`.
Use the actual verified native path and digest; `approval_status` comes from native acceptance
evidence, not the controller's optimism. Attach a model-contract snapshot retaining requested
classes/order/`all`, effective per-slot/member associations, observed identities or nulls,
`review_families_min`, `max_layers`, `frozen_paths` and the supporting evidence paths.

Alongside existing harness output, retain the delegated record
`{lane_id, ecosystem, package_version, skill_name, requested_model, effective_model, observed_model,
observed_family, artifact, artifact_sha256, session_id, status, evidence_paths}`.
Planning has no implementation lane yet: leave `lane_id` null unless an actual association exists.
Keep the source-qualified selector and package/source snapshot in the unchanged routing record;
the common bare `skill_name` alone cannot distinguish these two planners.

The controller records normalized output identities and the native/input identities they derive
from. Any native byte change invalidates the dependent summary, plan-review readiness and
execution approval. Changes to normalized lanes, source inputs or the model contract also require
fresh validation and review. Do not update a stored digest simply to keep an old approval green.

## Procedure

Use these bounded steps for an admitted owned planner. For a native handoff, supply their required
outputs to the native owner and inspect the returned plan instead of running this procedure in
parallel with it.

1. **Refresh the map if stale** against the current source/base identity — stop and route to
   available `tk-map`; a timestamp alone is not freshness evidence.
2. **Cut along seams**, not arbitrarily. Use the boundaries in `MAP.md` so lanes fall on real
   module edges and file scopes genuinely don't overlap.
3. **Layer by dependency.** Put independent slices in the same layer (they parallelize); put a
   slice that needs another's output in a later layer.
4. **Attach acceptance + verify to every lane** from the map's per-area verification commands.
   A lane with no runnable verify is `blocked` — record why and what's needed to unblock it.
5. **Mark model hints.** Flag the critical-path lane(s) so `tk-router` knows to ask the user
   which selected executor implements them, without reopening settled class choices.
6. **Check testability** before finishing: can each lane's verify actually run in this repo? If
   a command is aspirational (test doesn't exist yet), the lane's first task is to create it.

## The parallelism check (do this before declaring the plan done)

- Every pair of lanes in the same layer has **non-overlapping `files`**. If not, re-layer.
- Enumerate concrete repo-relative files, including tests and generated outputs. Resolve aliases
  and existing ancestors so directory scopes or symlinks cannot hide overlap or escape. No lane
  may write a frozen file or a file under a frozen directory.
- IDs are unique, every dependency names a real lane, and all edges point to earlier layers.
  Self-dependencies, cycles and same-layer dependencies stop readiness, not just execution order.
- Stay within `max_layers`; do not silently increase it to repair an overlap or entanglement.
- Every lane has a **`verify`** or is explicitly `blocked`.
- Verify commands have an actual working directory, executable and known prerequisites. A test
  to be created is an explicit owned file/task; its future result is not present verification.
  Unavailable prerequisites remain blockers with an owner and the evidence needed to unblock them.
- At least the critical-path lane has a **`model_hint`** for the user-choice step.
- Layer 0 is non-empty with no intra-plan dependencies. This is structural readiness, never
  permission to start immediately; preserve genuinely serial work rather than inventing seams.

For a native plan, a failed check returns an unresolved finding to its owner after known return.
Do not repair only the derived lanes while leaving the native execution authority contradictory.

## Record unresolved tradeoffs

If a clean disjoint decomposition isn't possible (genuinely entangled code), say so explicitly:
record the entanglement, propose the least-bad layering, and flag the lanes that must run serial.
Don't flatten a real dependency into fake parallelism.

Record unresolved scope, approval, model, artifact and verification blockers alongside the lane
summary and report the plan as not ready. A native approval does not erase an overlap, cycle,
frozen-path conflict or exceeded layer budget. Get a corrected, newly approved native artifact
before regenerating its summary; do not drop blocked lanes to manufacture a passing subset.

## Plan review and execution approval

Planning ends with a readiness report and the next gate, not implementation. Check availability
before routing to `tk-review --plan`; its plan operation is distinct from diff review. Require
`.thunderkit/PLAN-REVIEW.md` from independent selected reviewers against the **current** native
path/hash, normalized output hashes, source/input identity and model-contract snapshot. Count
actual responding model families, not harness names: meet `review_families_min` (at least two),
with at least one family different from the author. Explicit reviewers cannot disappear because
of quota or host limitations; `"all"` retains its reported reachable expansion. Unresolved blocker
or major findings and missing identity/verification evidence prevent execution readiness.

Native acceptance, in-session/native critique, Thunderkit plan-review approval and **execution
approval are separate gates**. Even a currently passing cross-family plan review does not
authorize execution. The controller must obtain separate execution approval for that exact
reviewed artifact set and scope before an available `tk-execute` takes ownership. Immediately
before that transition, compare identities again; stale hashes, missing or unapproved plans,
changed selections or unresolved lane blockers stop dispatch. This skill never starts execution.
