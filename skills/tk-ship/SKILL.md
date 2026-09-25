---
name: tk-ship
description: "Use when a completed change needs a readiness check and PR-body draft: require fresh cross-family review, per-lane verification, applicable UAT and unchanged frozen paths; prepare an engineering summary and branch handoff only, without push, PR creation, merge, deploy or publication."
compatibility: "Python 3.11+ for bundled read-only routing; explicit project model choices and genuinely bound executor/reviewer channels. Optional evidence assessment requires the pinned OMH peer on Hermes, Node 18+, Python 3.11+ and verified loaded provenance and reviewer bindings."
metadata:
  thunderkit-role: "ship"
  thunderkit-tier: "deliver"
  thunderkit-delegates: "omh:reviewer/omh-verification-gate"
  thunderkit-contract: "1"
---

# tk-ship — prepare the change for merge

Thunderkit owns this local preparation gate. Inspect existing evidence, report whether the
exact change is ready, and print a branch handoff and PR-body draft. Preparation is not delivery
authorization, and a native assessor is not a replacement for Thunderkit's completion gates.

Model class: **cheapest selected executor**, as assigned to preparation by `tk-router`.
Choose only within `classes.executors` using known cost/availability, not a hardcoded model or
an invented price ranking. The optional evidence assessor separately uses `classes.reviewers`.
Read this skill's [model roster](references/model-roster.md), [catalog](references/models.json)
and [config schema](references/config.schema.json); validate with the bundled
[model helper](scripts/model_config.py). Preserve all choices, array order, literal reviewers
`"all"`, the family minimum and frozen paths. Legacy normalization is a preview, not a write.

Every operation here is model-bearing, including owned/off/fallback assembly. Before work,
prove the actual executor channel's effective host/provider/wire-model and supported effort
match its selected catalog member; prove reviewer bindings separately for any assessment.
Valid configuration, a model name in a prompt or a skill load does not bind the current root.
Represent selected plural members without silently narrowing the set. Never change global
config, credentials, effort or fallback chains, or assume a running session changes after a
config edit. Missing selected channels block work rather than using an arbitrary current model.

## Delegation

Follow [delegation.md](references/delegation.md) and [dependencies.json](references/dependencies.json).
Set `SKILL_ROOT` to the directory containing this loaded skill and `PROJECT_ROOT` to the actual
project/worktree being prepared. Resolve resources only from this skill's own `scripts/` and
`references/`; missing assets block, with no borrowed checkout or presumed sibling copy.
Config and capability inputs must resolve within the explicit project root, without symlink
escapes. When considering a native component, `CAPABILITIES` is a current project-contained
snapshot of actual loaded descriptors, provenance, tools and effective bindings, not secrets.

```sh
python3 "$SKILL_ROOT/scripts/tk-resolve.py" \
  --skill tk-ship --operation prepare --project-root "$PROJECT_ROOT" \
  --config "$PROJECT_ROOT/.thunderkit/config.json" --capabilities "$CAPABILITIES" --json
```

`prepare` is the only operation and the default. With delegation off or no enabled target,
omit `--capabilities` and perform no native discovery, loading, routing, doctor or installation.
Configuration and the owned executor binding are still required.

Only `omh:reviewer/omh-verification-gate` is eligible, on Hermes in **component** mode with
`tool:skill` and `model-binding:reviewers`. Verify `oh-my-hermes@2.0.3`, its pinned source,
bundle root containing `manifest.json`, canonical name `verification-gate`, loaded entrypoint
and every provenance-map SHA-256, including the shared
`skills/guide/omh-routing/references/skill-common-rail.md` companion. The bundle root is not
the skills directory or `HERMES_HOME`. Missing/quarantined files, self-reported hashes or a
same-named foreign skill do not qualify. The local registry supplies the trusted identities.

On `delegate`, use only the verified categorized selector through an actual supported,
selected-reviewer-bound host channel. The internal address is not a slash command. Give the
assessor the immutable target, supplied evidence and a bounded read-only question; it returns
findings only. It cannot edit, rerun a workflow, change gates or deliver. Prefer an already
proven nonmutating binding; do not call `omh_delegate_route` from this read-only preparation.
If that boundary cannot be enforced, do not invoke the component. OpenCode and Codex have no
eligible OMO preparation target: never substitute a native delivery workflow.

Keep the resolver's fixed decision record immutable: schema, operation, reason, target,
requested/effective bindings, null pre-invocation observation, runtime home and evidence paths.
Exit 0 means only that routing was computed, not work executed or readiness proved. Blocked
or malformed input stops dispatch. Later invocation failures and findings get separate records;
do not rewrite `delegate` to claim native completion or erase a failure with an owned route.

## Ship gates (all must pass, fail-closed)

Bind all checks to one target: actual project/worktree and branch, base/head commit and tree,
exact diff digest including in-scope staged/unstaged/untracked bytes, approved scope and relevant
config/plan/evidence artifact identities. Name excluded local changes. Missing identity is
unverified, not a guessed hash. Recheck these identities immediately before reporting readiness.

1. **Review passed** — a current `REVIEW.md` and underlying independent evidence cover every
   lane; each lane is complete, with no unresolved **blocker or major** finding or assessment.
   A `done` label or report's existence alone is insufficient; retain minor findings and risks.
2. **Cross-family** — actual verified responding reviewer identities prove at least the
   unchanged `review_families_min` catalog families, including one different from each lane's
   author. Require every explicit reviewer; `"all"` considers all catalog candidates and
   preserves unavailable optional candidates. Multiple harnesses or same-family variants do
   not add families. `single-family-review`, missing author identity or quota-lost required
   review means not ready, never a reduced-confidence pass or a lower minimum.
3. **Per-lane verification** — every required lane verification has genuine successful results
   on the exact target: command/argv, cwd, exit status and sanitized output/counts. Missing,
   failed, skipped required or stale checks block. Inspect supplied evidence here; missing
   execution returns to its owning stage, not an invented pass or an automatic test/fix loop.
4. **UAT applicability and result** — account for each acceptance criterion and relevant
   CLI/API/visual surface. Applicable UAT requires current actual observations in `UAT.md`,
   with no gaps or unresolved failures. Preserve an explicit not-applicable decision and its
   reason/scope/target identity; do not turn it into a claimed executed pass. An optional stage
   that never ran is not evidence that required UAT is unnecessary. Missing applicability or
   required UAT blocks; a prior not-applicable decision is stale if the surface/scope changes.
5. **Frozen paths untouched** — compare the complete intended change and local in-scope bytes
   against `config.json.frozen_paths`, including additions, deletions and renames. A changed
   frozen path blocks; do not unfreeze it, exclude it from the diff or edit config to pass.
6. **Freshness** — source/tree/diff, relevant artifact bytes, scope/constraints or model-contract
   changes invalidate dependent review, verification and UAT. A newer timestamp or a native
   PASS on a narrower claim cannot refresh them. Missing identities block readiness.
7. **Optional assessor outcome** — if invoked, preserve its read-only findings. Native
   **HOLD/BLOCK prevents readiness** until the named issue is resolved with current evidence.
   Unknown/incomplete native outcomes remain blocked/unverified. Native PASS adds evidence
   only; it cannot replace any gate above. An optional assessor never invoked is recorded as
   not used, not as a passed assessment or a missing required UAT waiver.

Any failed, missing or ambiguous gate means **not ready**. Name the exact gap, affected target
and owning correction/check; retain useful evidence without certifying readiness. Check that
`tk-review`, `tk-verify-work`, `tk-router` or any other requested sibling is actually available
before handoff; absent siblings are prerequisites, not assumed paths or implicit installs.

## PR body from artifacts

Assemble, don't re-derive: goal + non-goals from `SPEC.md`; decisions from `CONTEXT.md`/
`DECISIONS.md`; lanes + verification from `PLAN.md`/`REVIEW.md`; risks from `PLAN.md`; UAT
evidence from `UAT.md`. These are inputs, not public citations. Trace each output claim to
underlying engineering facts: actual commits/diffs, code behavior, tests, verification commands
and observed results. If a claim lacks that support, omit or qualify it; do not invent coverage.

Write normal engineering prose: purpose and scope, implementation choices and trade-offs,
tests and their real results, compatibility/migration impact, remaining risks and limitations.
The public draft contains no `.thunderkit` or planning-artifact paths/names, internal receipts,
stage/lane bookkeeping, model-routing history or process narration. Do not disguise internal
filenames as aliases or encoded citations. Keep internal traceability in project context,
separate from the PR body; this does not change the product's committed-context convention.

## Fallback

- `owned`/`disabled`, no enabled target or an unsupported host uses the same preparation
  procedure, but only through a genuinely selected-executor-bound channel. A computed owned
  route cannot waive model readiness or the completion gates.
- Missing peer, provenance/companion failure or reviewer binding mismatch permits a named
  owned fallback, not an undeclared peer or model substitution. Preserve the resolver reason.
  If owned binding, explicit selections or required evidence cannot be honored, stop and
  record a separate blocked outcome. Give operator guidance, never install, log in or repair
  global configuration automatically.
- On uncertain timeout/in-flight native work, retain the genuine session ID, artifacts and
  partial output, mark blocked/unknown, and inspect that same session before any retry or
  fallback. If its termination/outcome is unprovable, remain blocked; never duplicate work.
  A captured resume ID does not prove that the session is currently runnable.

## Output contract

Return two clearly separated outputs:

1. **Preparation status and branch handoff** — ready or not ready for the exact target;
   current branch/base/head/tree/diff identities, in-scope and excluded local changes; each
   owned gate's result and named gaps; actual review-family coverage, every lane's verification
   and UAT applicability (including explicit not-applicable reasons). Preserve the unchanged
   resolver record and separate invocation outcome, requested/effective/observed model and
   family, package/version/selector, actual native artifact path/SHA-256 and genuine session ID
   using the common delegated-run fields. Unknown facts remain null/unverified. Keep native
   artifacts at their real paths, without moving, rewriting or mirroring native state.
2. **PR title and body draft** — the engineering summary above, ready to copy only when all
   gates pass. On failure, label any partial draft not ready and list blockers separately,
   never as a hidden warning beneath a readiness claim. No public artifact/process references.

Do not include credentials in either output. Readiness applies only to the recorded identity,
not future edits. Print the proposed handoff; do not create or alter branches/commits, apply
fixes, start missing stages or issue remote delivery commands as part of this skill.

## Boundary — preparation only

No push, PR creation, merge (including automatic/local merge), deploy or publish commands.
Never call OMO `--ship`/`--make-pr`, a deployment workflow or a release publisher. A ready
summary, native PASS or request to run `tk-ship` grants none of that authority. Delivery needs
a **separate explicit user instruction outside this skill**; stop at the local preparation
result even when every gate passes.
