---
name: tk-review
description: "Use for independent cross-family review of a plan before execution or a completed diff: preserve selected reviewers, consolidate evidence-backed findings and disagreements, and block approval on insufficient actual families, stale targets, unresolved blocker or major findings, or missing verification."
compatibility: "Python 3.11+ for the bundled read-only resolver; explicit project model choices and supported, model-bound read-only reviewer channels. Optional native diff component requires the pinned Hermes peer and every operation-specific provenance, tool and binding gate."
metadata:
  thunderkit-role: "reviewer"
  thunderkit-tier: "review"
  thunderkit-delegates: "omh:reviewer/omh-code-review"
  thunderkit-contract: "1"
---

# tk-review — cross-family review + evidence gate

Thunderkit owns reviewer selection, independence, family coverage, consolidation and completion.
A native review is one read-only reviewer component, never the panel or its final authority.

## Two modes

- **`tk-review`** selects operation `diff` (the default): independently review the completed
  source/diff and run every required lane verification on the actual reviewed tree.
- **`tk-review --plan`** — review the *plan* before execution (the plan-check gate, lifecycle
  stage 8). Fan `PLAN.md`/`plan.json` to the reviewer families and check: are lanes truly
  disjoint, does every lane have a runnable verify, are the dependency layers acyclic, do lanes
  cite real symbols (not hallucinated names)? Output `.thunderkit/PLAN-REVIEW.md`. `tk-execute`
  requires the current identity-bound independent plan-review gate below, not mere report
  existence, plus native acceptance when applicable. Select operation `plan`; keep it owned,
  not aliased to a code-review target. Check acceptance coverage and frozen paths as well.

Before dispatch, freeze one common target for every reviewer of the lane or plan:

- Actual project/worktree, operation, scope and excluded paths, constraints and acceptance criteria.
- For `diff`: base and head commit/tree identities, the exact diff's SHA-256, and the content
  identities of any included staged, unstaged or untracked changes. Name excluded local changes.
  Bind the approved plan and relevant model/config snapshot to the review as well.
- For `plan`: exact paths and SHA-256 values for **both plan artifacts defined above**, the
  referenced source revision/tree, model/config snapshot, and any native plan plus its real acceptance evidence.
  Preserve native plan paths and bytes; a normalized summary cannot replace their identity.

Missing identity blocks approval. A plan pass applies only to that plan and its constraints,
not implementation correctness; a diff pass cannot retroactively approve a plan. Native plan
acceptance and the independent plan review are separate prerequisites to execution. Check both
for the current target, not merely whether a report file exists.

## Reviewer selection

Read this skill's [model-roster.md](references/model-roster.md), [models.json](references/models.json)
and [config.schema.json](references/config.schema.json). Validate the actual project config using
the bundled [model_config.py](scripts/model_config.py); preserve all selected classes, list order,
literal reviewers `"all"`, `review_families_min` (integer at least 2) and `frozen_paths`.
Recognized legacy input produces only an in-memory preview/warning, never an automatic rewrite.

- Every explicitly selected reviewer must return independent, identity-verified evidence for
  the same target. Preferred models or cheap verification never override the selected class.
- `"all"` considers every catalog model, not just planner/executor choices or the current host's
  native subset. Keep each unavailable optional candidate and its actual failure visible.
  A candidate explicitly required elsewhere remains required; do not make it optional here.
- Count distinct **catalog families of actual verified responding reviewers**, not configured
  labels, providers, harnesses, native roles or successful preflight requests. Opus 4.8, Opus 5
  and Fable 5.1 are one `anthropic` family, even on different providers; Sol is `openai`.
- Establish the author's actual family per lane, or the planner-author's family for plan review,
  from genuine run evidence. At least one responding reviewer family must differ from the author.
  Missing author/reviewer identity is unverified, not an inferred match from configuration.
- Quota, timeout or lost second-family access never lowers the minimum, removes an explicit
  reviewer, or turns single-family findings into a pass. Retain useful partial findings and block.

The current preflight adapters cannot establish two verified families from their native formats.
Do not convert requested IDs, initialization fields, a pong, or synthetic fixture results into
observed serving identity. Review completion needs its own genuine identity-bound evidence.

## Delegation

Follow [delegation.md](references/delegation.md) and the exact operation map in
[dependencies.json](references/dependencies.json). Resolve `TK_REVIEW_ROOT` to the directory
containing this loaded skill, and `PROJECT_ROOT` to the actual reviewed project/worktree, not
the skill installation or an arbitrary directory that makes a path check pass. Use only bundled
resources; missing assets are a blocked prerequisite, not a reason to borrow a checkout copy.

For native diff consideration, `CAPABILITIES` must name a real, project-contained snapshot of
current host descriptors, loaded provenance, tools and effective reviewer bindings. Config and
capability paths must resolve inside the explicit project root without escaping via symlinks.

```sh
python3 "$TK_REVIEW_ROOT/scripts/tk-resolve.py" \
  --skill tk-review --operation diff --project-root "$PROJECT_ROOT" \
  --config "$PROJECT_ROOT/.thunderkit/config.json" --capabilities "$CAPABILITIES" --json
```

Plan review has no native target and needs no native capability snapshot:

```sh
python3 "$TK_REVIEW_ROOT/scripts/tk-resolve.py" \
  --skill tk-review --operation plan --project-root "$PROJECT_ROOT" \
  --config "$PROJECT_ROOT/.thunderkit/config.json" --json
```

Only `diff` may use `omh:reviewer/omh-code-review`, in registry mode **`component`** on Hermes.
Its package is `oh-my-hermes@2.0.3`, selector `reviewer/omh-code-review`, bare skill name
`omh-code-review`, and manifest canonical name `code-review`. These identify different fields,
not interchangeable invocation aliases. Require `tool:skill` and `model-binding:reviewers`.
Verify the pinned package/source/root identity, loaded entrypoint and SHA-256 of **every** file
in the registry provenance map, including `references/review-dispatch.md`, `review-response.md`,
`smell-baseline.md` under that native skill and `guide/omh-routing/references/skill-common-rail.md`
under the peer's skills root. The bundle root containing `manifest.json` is not `HERMES_HOME`.
Present, loaded, source-compatible, model-bound and result-verified are separate checks; a
quarantined skill, self-reported checksum or missing companion is not eligible.

On `delegate`, invoke only the verified categorized selector through the host's actual supported
skill/reviewer channel, bound to the particular selected reviewer, with the common immutable
target and read-only constraints. Preserve all requested selections and per-member associations;
do not narrow the config to qualify a component. Hermes has no catalog Sol mapping. A compatible
same-family native subset, including under `"all"`, cannot supply the missing independent family.
Do not alias OMO `review-work`'s single gate-reviewer workflow to this panel or to plan review.

Binding is required on **every** route, including owned/off/fallback. Verify a real read-only
channel's effective provider/wire-model and supported effort against the selected catalog member
before dispatch; a valid config or arbitrary root session is not binding. OMO `task()` has no
model argument and `load_skills` only injects text; use proven effective agent/category mappings,
not a prompt asking for a different model. Do not assume a live root changes after a config edit.
Never change global settings, auth, providers, effort or fallback chains to make a route succeed.

If an OMH channel uses `omh_delegate_route`, apply the common existing task-owned local-disk
home, identical actual parent/dispatcher home, plugin, consent and set → dispatch → clear rules.
Passing a different path does not rebind a running dispatcher. The controller owns routing;
the read-only reviewer cannot reconfigure it. Otherwise use an already-proven nonmutating
binding. If the host cannot enforce the component's read-only boundary, do not invoke it.

Keep the resolver's fixed decision record unchanged, including requested/effective bindings,
null pre-invocation observation, target, reason and evidence paths. Exit 0 is only a computed
route; `blocked` or malformed input stops dispatch. Record later invocation failures/results
separately rather than rewriting a `delegate` decision into a claimed completion.

## Fallback

- `plan`, no enabled target, or `delegation: off`: use the owned independent-review procedure.
  Off still validates choices but omits native capability discovery and peer invocation,
  installation, doctor and native routing tools; the local resolver itself dispatches nothing.
- A named native denial permits owned diff review only through proven selected read-only
  channels with the same scope, evidence and family gates. Missing configuration, binding,
  required reviewer or family remains blocked even if the resolver can compute an owned route.
- Missing peer/runtime/tools/provenance: preserve the exact reason; provide operator guidance
  without installations, logins, config repairs, guessed aliases or automatic substitutions.
- On uncertain timeout/in-flight work, preserve captured session IDs, artifacts and partial
  output as unknown/unverified. Inspect the original session and reconcile ownership before
  any retry, replacement reviewer or fallback dispatch; do not create duplicate owners.

Before transitions to `tk-router`, `tk-plan`, `tk-execute` or another sibling, check that the skill
is actually available. If absent, name the missing prerequisite; never read a presumed sibling
checkout path or install it implicitly. A component invocation adds no write, fix or ship authority.

## Independent review

1. Give each selected reviewer a separate read-only session with the same source/diff or plan
   snapshot, goal, acceptance criteria, model/scope constraints and frozen paths. Do not share
   another reviewer's conclusions as authority or reuse the author's session as a reviewer.
2. Collect findings with severity `blocker / major / minor / nit`, source file:line (or exact
   plan section), concrete evidence, impact and an actionable fix. Keep genuine no-finding
   responses as well as failures, partial outputs, identities and native artifact references.
3. Consolidate only after independent responses. Dedupe the same issue while retaining every
   originating reviewer, evidence and disagreement. Use the highest **supported** severity,
   not a majority vote or the loudest unsupported claim. Request concrete evidence before
   retaining a severe claim; keep pending/disputed claims visible and do not pass an unresolved
   assessment. Record evidence-based resolution rather than erasing contrary findings.
4. Return code fixes to the selected executor and plan revisions to the selected planner;
   reviewers do not patch, weaken tests, alter scope/frozen paths, change thresholds or ship.
   Stay within the caller's approved correction/review budget; absent one, return after this
   review round rather than start an automatic fix loop. Re-review affected targets after a
   correction with fresh independent evidence; exhausted budgets leave an explicit block.

## Evidence gate

For `diff`, run **every** required lane `verify` command from `plan.json` on the actual reviewed
tree, using a proven selected reviewer/verifier channel rather than a hardcoded cheap model.
Record command/argv, cwd, source/tree/diff identity, exit status, pass/fail/blocked and actual
sanitized output/results. Keep full local evidence and clearly label truncated excerpts.
Do not run destructive or out-of-scope commands; missing safe authorization/tooling is blocked,
not a skipped check or a weakened replacement test. Keep generated caches/output in allowed
local runtime paths without altering reviewed source or frozen paths.

For `plan`, check every lane has a real runnable verification command and run required plan
validation checks against the referenced tree with the same cwd/status/result evidence.
Do not claim unexecuted implementation checks passed, or run implementation/fix work to produce
a plan approval. Preserve any applicable native acceptance and explicit user approval separately.

A lane or plan passes only when **all** required reviewers supplied independent verified
evidence, actual family coverage meets the unchanged minimum with a family different from the
author, all required checks succeeded for this operation, and no unresolved **blocker or major**
finding or assessment remains. Minor/nit findings remain visible. Report partial/single-family
coverage as blocked, never as reduced-confidence completion.

Recheck identities before accepting: any target bytes, source/diff, native plan/acceptance,
relevant model binding/selection or scope/constraint change invalidates the affected gate.
A stale report, file existence, process exit 0, one native PASS or missing identity cannot
certify completion. Diff verification, plan approval and delivery authorization stay distinct.

## Output contract

Write the consolidated diff result to `.thunderkit/REVIEW.md` or the plan result to
`.thunderkit/PLAN-REVIEW.md`, with supporting run evidence in the project's local runtime area.
Keep native artifacts at their real paths and
reference their SHA-256 values; do not rename or mirror native state into a competing workflow.

Include, per lane or plan:

- Operation and common immutable target, approved scope/constraints, relevant config snapshot
  and current native acceptance where applicable; plan approval is not diff verification.
- Author and reviewer requested catalog model/family, effective host/provider/model/effort and
  catalog family, and separately observed serving model and its catalog family. Never infer
  observed provider or family from requested settings. Unknown facts remain null/unverified.
- Preserved resolver decision plus separate invocation records using the common delegated-run
  fields: `lane_id`, `ecosystem`, `package_version`, `skill_name`, `requested_model`,
  `effective_model`, `observed_model`, `observed_family`, `artifact`, `artifact_sha256`,
  `session_id`, `status`, `evidence_paths`. Capture genuine IDs/hashes, not placeholders or
  guessed resume commands; a captured ID does not prove a session remains runnable.
- Explicit reviewer order or the unchanged `"all"` request and candidate outcomes, actual
  verified family count versus the minimum, author-family comparison and all missing evidence.
- Findings with attribution, locations, supporting evidence, actionable fixes, resolution and
  disagreement; required commands with actual cwd/status/results; pass/fail/blocked reasons.

Roll up reviewed, passed and blocked lanes plus unresolved blocker **and major** findings and
the selected owner of each required correction.
