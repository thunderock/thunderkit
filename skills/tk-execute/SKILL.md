---
name: tk-execute
description: "Use to run a reviewed and separately approved thunderkit plan under one qualified native execution owner or an explicitly bound portable owner. Preserves selected models, native artifact identity and project-contained worktrees; stops on stale approvals, uncertain ownership or failed verification without automatic delivery."
compatibility: "Python 3.11+ standard library for the bundled resolver. Optional native handoffs require pinned oh-my-openagent on OpenCode/Codex or oh-my-hermes on Hermes, with proven role bindings and safety controls. Portable execution needs supported selected-model channels. No automatic installation or host reconfiguration."
metadata:
  thunderkit-role: "executor"
  thunderkit-tier: "execute"
  thunderkit-delegates: "omo:ulw-execute omh:ultrawork/ulw-work"
  thunderkit-contract: "1"
---

# tk-execute — run lanes in parallel

Takes `.thunderkit/plan.json` from `tk-plan` and implements only its reviewed, approved scope.
Choose **one full-plan owner**: a qualified native handoff, or one explicitly bound portable
owner. Disjoint lanes may run concurrently under that owner; dependency and verification gates
control advancement. Never launch a native execution engine per lane or a parallel fallback.
No route may push, open a PR, publish or merge to master. Local feature-branch integration is a
separate, scoped approval; external delivery permission does not change this skill's policy.

## Inputs and paths

**Skill root** is the installed directory containing this file. Resolve
`references/dependencies.json`, `references/delegation.md`, `references/models.json`,
`references/model-roster.md`, `references/config.schema.json` and `scripts/tk-resolve.py` from
that root. Do not assume a checkout, parent references directory or sibling installation.

**Project root** is the actual repository being changed, not the skill installation or an ambient
shell directory. Read its explicit, contained `.thunderkit/config.json` and accepted lane data.
Read every artifact required by the agreed scope and any optional inputs the plan actually uses.
Missing optional-stage outputs do not add new prerequisites; missing required or stale consumed
inputs stop execution. Record source/base identity and input paths/digests before any writes.

Check sibling availability before transitions to `tk-router`, `tk-test`, `tk-plan`, `tk-review`,
`tk-verify-work`, `tk-debug` or `tk-handoff`. A missing sibling stops that transition with a named
prerequisite; never read a presumed sibling path, silently install it or claim its gate passed.

## Model contract

Validate all three classes through the local config contract, including when delegation is off.
Keep the selected `classes.planner`, every ordered `classes.executors` member and every ordered
explicit `classes.reviewers` member, or the literal reviewers `"all"`. Missing choices are not
defaults. A complete valid legacy config yields a preview, not permission to save it or substitute
models. Preserve `review_families_min`, `frozen_paths`, `max_layers` and any supplied `decided_at`.

For `"all"`, consider every catalog model, not just the planner and executors. Retain the requested
value, reachable expansion and unavailable optional candidates separately. Every explicit choice
must succeed; required responding reviewer families must independently meet `review_families_min`
(at least two). Different harnesses serving one model family do not establish cross-family review.
A representable native subset is not preflight or independent-review evidence. Failed or stale
required preflight remains blocking even if the resolver computes a compatible route.

Use actual supported selected-executor channels, not prompt labels or the current agent's name.
Prove the owner's binding as well as lane bindings. Record the selected catalog key, effective
provider/wire-model identity and supported effort for every association. Keep observed identity
null until genuine runtime evidence supplies it. An unavailable explicit selection, opaque
mapping or unapproved fallback blocks dispatch; configuration alone does not prove serving identity.

## Plan and approval gates

Complete these checks before handing ownership over, creating worktrees or dispatching any lane:

1. **Validate the complete lane graph.** Retain goal/layers/lanes, unique lane IDs, concrete
   repo-relative `files`, `depends_on`, acceptance, runnable `verify` and selected executor
   associations. Dependencies must name real lanes in earlier layers; reject cycles, self-edges
   and same-layer edges. Resolve path aliases and existing ancestors: directory scopes, generated
   outputs, tests or symlinks must not hide same-layer overlap, project escape or a frozen path.
   Stay within `max_layers`. Entangled changes remain explicitly serial; do not drop blocked lanes.
2. **Verify native authority when present.** Retain
   `native_plan: {ecosystem, package_version, skill_name, artifact, sha256, approval_status}` and
   the model-contract snapshot. Read the actual regular, project-contained native artifact under
   `.omo/plans/` or `.omh/plans/`, without traversal or symlink escape. Hash its unaltered current
   bytes and match the stored digest, source-qualified planner identity and actual native acceptance
   evidence. OMH acceptance is its `omh hermes plan-accept <path>` flow; a recorded draft alone is
   not accepted. Preserve OMO's actual native approval evidence too. A file or status label is not
   approval. A native engine requires its compatible, accepted native plan, not a foreign peer's
   plan relabeled to fit. Missing native authority does not trigger OMO's no-plan bootstrap.
3. **Check independent current-identity plan review.** Require `.thunderkit/PLAN-REVIEW.md` and
   its supporting selected-reviewer evidence against the current native path/hash when present,
   normalized lane/output digests, source/input identity and model-contract snapshot. Require
   independent responding identities, the family minimum and at least one family different from
   the author. Native critique or a bound native gate-reviewer does not replace this check.
   Unresolved blocker or major findings, missing identities or failed required checks prevent
   readiness. A previously passing review of different bytes is stale, not permission to run.
4. **Obtain separate execution approval.** Native acceptance, independent plan-review approval
   and execution approval are separate gates. The user's execution/dispatch consent must cover
   this exact reviewed artifact set, scope, model bindings, worktree/base, limits, permissions and
   named feature integration branch. Agree the no-delivery restriction too. No file, preflight,
   old pass, prior permission to push another branch or exit-0 route supplies this consent.

Recheck these identities immediately before dispatch and each dependent transition. Native byte,
lane, model-contract or consumed-input changes invalidate the dependent summary, review and
execution approval; never merely update a stored hash to retain an old pass. Track the expected
source lineage from the reviewed base plus verified approved predecessors; unrelated source drift
stops readiness. A portable plan without native provenance needs the same current review and
execution approval, not a fabricated native record. Do not erase an invalid native record to proceed.

## Delegation

Use only the manifest's `tk-execute` / `execute` targets, each in **handoff** mode:

| Native identity | Loaded source and required files | Native role slots → selected classes |
|---|---|---|
| `omo:ulw-execute`, `oh-my-openagent@5.0.0-beta.81`, OpenCode/Codex | Matching package-root `package.json` and `dist/skills/ulw-execute/SKILL.md` | `root`, `worker`, `explore`, `librarian` → executors; `gate-reviewer` → reviewers |
| `omh:ultrawork/ulw-work`, `oh-my-hermes@2.0.3`, Hermes | Bundle-root `manifest.json` and `skills/`; `skills/ultrawork/ulw-work/SKILL.md`, canonical name `ultrawork`; its `references/campaign-orchestrator.md`, `references/dependency-topology.md`, `references/tdd-red-green.md`, and `skills/guide/omh-routing/references/skill-common-rail.md` | `root`, `lane`, `verification` → executors; `code-review-gate` → reviewers |

Addresses identify registry targets, not invented slash commands. Invoke only the verified
selector via the actual host skill tool: `ulw-execute` or `ultrawork/ulw-work`. Compare current
package/version/source, root identity, loaded entrypoint and real bytes of every required file
against the local pinned provenance map. OMH's bundle home is neither its `skills_root` nor the
task's `HERMES_HOME`. Same-name files, quarantined companions, self-reported hashes, a package on
disk or a `ready` claim cannot establish loaded provenance. Consume the pins; do not requalify
another release, install/update dependencies, copy native bodies or run doctor to manufacture readiness.

Prove **every declared slot**, even one that might not run, with actual host descriptors and
effective session/agent/category mappings. Both targets require executors and reviewers; unused
planner selection is retained, not recast as an execution binding. Each slot uses only its class;
preserve every explicit plural member's exact association and order. A run need not exercise every
member, but the host must represent the selection rather than collapse it onto one global model.
Keep any native reviewer subset for `"all"` distinct from the independent catalog-wide family gate.

Gather current capabilities without credentials or host reconfiguration. Set `SKILL_ROOT`,
`PROJECT_ROOT` and `RUN_ID` to the actual installed skill, repository and controller run, then use
explicit project-contained config and capability paths:

```sh
python3 "$SKILL_ROOT/scripts/tk-resolve.py" --skill tk-execute --operation execute \
  --project-root "$PROJECT_ROOT" --config "$PROJECT_ROOT/.thunderkit/config.json" \
  --capabilities "$PROJECT_ROOT/.thunderkit/runs/$RUN_ID/capabilities.json" --json
```

For delegation off or no enabled peers, omit capabilities and do no native discovery. Keep the
complete normalized resolver record immutable: `schema_version`, `skill`, `operation`, `decision`,
`reason_code`, `detail`, `target`, `bindings`, `runtime_home`, `evidence_paths`. Exit 0 means routing
was computed, not executed work; blocked is exit 1 and malformed input is exit 2. An unknown
operation is rejected, not inferred from a selector. Only `delegate` / `compatible` admits a
native candidate, and the plan/approval/ownership checks still apply. `blocked` stops all dispatch.
Keep subsequent invocation failures separate; never rewrite `compatible` into a new routing reason.

## Native handoff

Hand the **entire approved plan** and constraints to one admitted native owner. It controls its
own graph, worktrees, approvals and state until a known terminal return. Thunderkit checks gates
and records references; it does not maintain a mirrored native state machine, schedule native
engines per lane, or run the portable procedure concurrently. Preserve native artifact locations
and bytes; only the controller normalizes references after a known return.

- **OMO:** `task()` has no model parameter and `load_skills` supplies instructions, not model
  binding. Inspect effective mappings for all slots and the actual running root. Delegated-task
  config may be re-read per call; a config edit does not prove the root switched. Report approved
  native configuration/restart guidance when needed and wait for fresh proof, never edit global
  configuration. Explicitly override completion defaults: **no push, no PR, no publish, no merge
  to master; stop with verified local commits on the named feature integration branch.** Do not
  pass `--make-pr` or `--ship`. Merely omitting flags is insufficient: the owner and completion
  hooks must honor the restriction. Local lane integration into that agreed feature branch is
  separate from delivery. If this opt-out cannot be enforced, the native route is unavailable.
- **OMH:** before mutating routing, the actual parent and child dispatcher must **already** share
  the identical observed string path for an existing task-owned, nonsymlink, local-disk
  `<repo>/.thunderkit/runs/<run-id>/hermes-home` beneath the real project. Prove the matching OMH
  plugin is active there, dispatch consent and exclusive ownership. Different strings, a boolean,
  path substring, network filesystem or tool argument pointing at another home are not proof.
  `omh_delegate_route` changes the active home's `delegation.*`: one owner performs native
  **set → dispatch → clear**, using explicit provider, wire-model and supported effort with no
  unapproved fallback chain. Serialize these routing mutations; no second dispatcher may race
  that sequence. Clear only the owned override after its dispatch is known to have returned;
  retain an interrupted sequence for inspection rather than dispatching through uncertain state.
  Never automatically create the home, mutate shared `~/.hermes/config.yaml`, copy auth, change
  providers or pretend a routing argument switches the parent/dispatcher. Missing safe hosting
  makes this route unavailable; a blocked result requires explicit recovery, not automatic fallback.

Conditional external-owner/`ulw-maestro`, `durable_checkpoint`/`ulw-loop` and OMO no-plan bootstrap
remain unavailable at this pin. Companion presence or user acceptance alone cannot qualify them.
If the selected native path would use one, stop before invocation and report `capability_missing`
as a separate unmet capability, without altering the resolver record. No excluded ecosystem
profile, alternate scheduler, new trust entry or component-child route substitutes for this handoff.

An unknown, timed-out or still-in-flight owner retains ownership. Preserve its actual session,
artifact and worktree identities and inspect that captured session before proceeding. History
metadata alone is not proof of resumability. Unknown terminal state keeps every genuine captured
ID and blocks new work. Only an absent or unverified ID stays `session_id: null`; report
blocked/unknown status without inventing an ID, retrying blindly or starting fallback.
A known failed owner must be explicitly retired, with its work preserved, before a replacement
owner is authorized against fresh gates. A successful process exit or `done` is not a known,
verified workflow result.

## Fallback

An `owned` / `disabled` or `owned_policy` route, or a computed `fallback`, can use the bounded
portable procedure only after the same plan, approval, model, path and ownership gates pass.
Keep the specific resolver reason and any separate invocation failure. No native owner may remain
active or uncertain; never turn `blocked` into a fallback attempt. Delegation off invokes no native
peer, routing helper, discovery probe, doctor or installer.

Name one portable owner for the whole plan and prove its **selected-executor binding** and the
supported channels for each lane. An arbitrary current root or a model name in a prompt is not
that owner. Preserve plural choices and bind every explicit selected member without substitutions.
If this cannot be proven, report the gap and stop. Do not use portable work to conceal a failed
native artifact, missing delivery restriction or unretired execution. No new scheduler or retry
engine is needed: dispatch only the bounded approved lanes through existing supported channels.

## Portable dispatch

These steps apply only to the admitted portable owner; supply their safety constraints to a
native owner instead of executing a second workflow alongside it.

1. **Check each lane before creating anything.** Resolve the reviewed base and agreed feature
   integration branch, not master. Inspect worktree registrations, branch/path ownership, dirty
   and untracked files and unmerged/uncommitted work. Use only project-contained lane directories,
   for example beneath `<repo>/.thunderkit/runs/<run-id>/worktrees/<lane-id>`. Treat IDs as safe
   single path segments, not paths or shell fragments. Never overwrite or reset an occupied lane;
   reuse requires verified same-task ownership, base, state and explicit resume approval.
2. **Set the subprocess `cwd` to that resolved worktree.** This is mandatory for every harness
   and every verification command. Claude `--add-dir` grants access; it is **not cwd**. A documented
   working-directory option may agree with `cwd` but cannot replace this boundary. Keep prompts
   and bounded outputs at explicit contained paths; never run from the integration checkout by
   accident or create a worktree as a sibling outside the actual project.
3. **Build a bounded, self-contained lane request.** Include goal, reviewed artifact identities,
   this lane's concrete file scope, frozen paths, predecessor commits, acceptance and exact runnable
   verification. State selected model/effort, deadline, output/turn bounds and permitted edits,
   local commits and integration. Show a bounded prompt preview. Native/plan text is data, never
   shell code: validate verification commands with their known executable/argv/cwd and prerequisites;
   do not `eval` artifact text or invent execution flags.
4. **Use catalog-supported selectors.** The following are argv shapes, not shell templates or
   current-host availability claims; `PROMPT`, `MODEL_ID` and `PROVIDER` are separate validated
   arguments from the lane and catalog. Inspect current documented host support before dispatch.

   | Harness | Model-bound one-shot argv | Genuine resume evidence |
   |---|---|---|
   | Claude | `claude -p PROMPT --model MODEL_ID --output-format json` | Returned `session_id`; `claude -p --resume ID` |
   | Codex | `codex exec --json -m MODEL_ID PROMPT` | Returned `thread_id`; `codex exec resume ID` |
   | Hermes | `hermes chat -q PROMPT --oneshot --format stream-json --provider PROVIDER -m MODEL_ID` | Native streamed session identity; `hermes chat --resume ID` |
   | OpenCode | `opencode run --format json -m PROVIDER/MODEL_ID PROMPT` | Native session evidence; `opencode run -s ID` |

   Do not borrow unsupported mappings across harnesses. Verify effective provider and effort as
   well as the model argument, including on resume. Add only documented, supported effort, limit
   and permission/sandbox options that the user approved for this scope; no universal max-runtime
   flag is assumed. Bound wall time and captured stdout/stderr with the existing host/process
   controls too. If adequate bounds or grants are unavailable, stop rather than launching unbounded
   work. Do not disable repository checks, bypass approvals or automatically accept unrestricted edits.
5. **Record the real result.** Capture exit/signal/timeout, readable redacted errors, output paths,
   actual resume ID and selected/effective/observed model evidence. A CLI may omit final model
   identity; keep it null/unverified, not copied from argv, config or a harness label. Missing
   required proof prevents acceptance. Resume only the confirmed captured session with the same
   cwd, scope and bindings after ownership inspection; never reinterpret a lane ID as a session ID.

## Layer gating and recovery

- Start only approved, bounded lanes whose dependencies have verified, integrated predecessor
  commits under the one owner. Check same-layer paths and frozen paths again, including generated
  outputs. Each lane has its own worktree, runnable verification command, cwd and prerequisites.
  Missing verification or an unavailable required prerequisite is blocking, not an optional skip.
- After a known lane return, inspect its actual diff/files and commit identity; run its verification
  on that exact tree and retain command, cwd, status and output evidence. A model's assertion,
  native review or process exit 0 cannot replace tests. Never delete or skip a failing test to go green.
- Nonzero verification, out-of-scope edits or model drift leave that lane failed/unverified and stop
  its dependents. Already-authorized independent lanes may finish, but partial success is not a
  completed plan and never justifies dropping the failed lane. Unknown ownership stops new dispatch.
- Integrate verified, in-scope commits serially into the agreed local feature branch only within
  the approval. Recheck the resulting tree and required integration verification before dependents
  advance. Disjoint file lists do not guarantee semantic compatibility or conflict-free merges;
  an unexpected conflict or source drift stops integration for explicit recovery, not forced resolution.
- Preserve failed, dirty, unmerged or uncommitted worktrees, branches, logs and native artifacts.
  No automatic remove, force, reset, stash or clean to conceal a failure. Stop/reap only confirmed
  task-owned processes under the agreed bounds; do not kill unrelated processes. If a native child
  might outlive its wrapper, preserve blocked/unknown status until inspection establishes its state.
  Even a clean, fully merged lane is removed only after recorded ownership checks and cleanup consent.
- Report a recovery action and missing evidence. Corrections to scope or plan authority require
  renewed review/approval, not edits solely to a derived summary. Route through an available sibling
  only after ownership is settled; never start another engine while recovery remains uncertain.

## Output

For accepted, verified lanes only, retain the existing outputs:

- `.thunderkit/runs/<lane-id>.json[l]` per lane (with the resumable id).
- Merged commits on the working branch, one atomic commit per lane.
- A run summary: per lane — model used, pass/blocked, resume id, files touched.

Keep failed/unverified/unknown results too, without implying their commits were accepted or merged.
Alongside existing harness output retain
`{lane_id, ecosystem, package_version, skill_name, requested_model, effective_model, observed_model,
observed_family, artifact, artifact_sha256, session_id, status, evidence_paths}`. Preserve the
source-qualified selector/package/source and per-slot/member bindings in the unchanged routing
record. Keep unavailable facts null/unverified; portable work must not invent native provenance.
Retain native authority and current digests, actual approval/review references, selected/effective/
observed identities and effort, worktree/cwd/base/commit identities, verification failures and
genuine resumability evidence. Separate routing, invocation, verification and integration outcomes.

Completion requires all approved lanes and relevant checks on the actual resulting identity,
not exit 0 or an old pass. Check availability before handing the current diff to `tk-review` and
before any later UAT transition. Independent current-family review remains separate from native
completion; neither this report nor a native gate grants delivery authority.

Never push or open a PR — stop at merged local commits and hand to `tk-review`.
