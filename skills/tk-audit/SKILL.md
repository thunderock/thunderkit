---
name: tk-audit
description: "Use to check a milestone actually achieved its intent before archiving: aggregates every lane's verification, checks cross-lane integration and requirements coverage across all model families, and fails closed on orphaned or unverified requirements."
compatibility: "Python 3.11+ for the bundled read-only resolver; explicit project model selections and supported, model-bound read-only reviewer channels. Optional evidence assessment requires the pinned OMH peer on Hermes with verified provenance, tools and reviewer bindings."
metadata:
  thunderkit-role: "audit"
  thunderkit-tier: "deliver"
  thunderkit-delegates: "omh:reviewer/omh-verification-gate"
  thunderkit-contract: "1"
---

# tk-audit — did the milestone actually land

Individual lanes passing does not mean the milestone achieved its intent: integration can be
broken and requirements can be orphaned. Thunderkit owns the complete requirements-to-evidence
audit and final acceptance decision. Native findings are inputs, not a replacement verdict.

Model class: **reviewers** (all selected families — the last blind-spot check). The only
operation is `audit`, including when omitted; every route is model-bearing.

## Reviewer selection

Read the installed skill's [model roster](references/model-roster.md),
[catalog](references/models.json) and [config schema](references/config.schema.json).
Validate the actual project's selections with [model_config.py](scripts/model_config.py).
Preserve all three classes, explicit reviewer order, literal `"all"`, `review_families_min`
(at least 2) and frozen paths. Legacy normalization is a preview, not a config write.

- Every explicit reviewer must supply an independent assessment of the same complete audit
  target. `"all"` considers every catalog model, including those outside planner/executors;
  retain reachable candidates and unavailable optional candidates with their actual outcomes.
  A model explicitly required elsewhere does not become optional through `"all"`.
- Count catalog families of actual identity-verified responding reviewers, not configured
  labels, providers, harnesses or native slots. Opus 4.8, Opus 5 and Fable 5.1 are one
  `anthropic` family; Sol is `openai`. The unchanged minimum must independently be met.
- Establish each lane author's actual family from genuine run evidence and require a
  responding reviewer family different from that author. Missing author or reviewer identity
  is unverified. Use separate read-only reviewer sessions, not the author's session.
- Give reviewers the same requirements, evidence and identities before sharing conclusions.
  Consolidate afterward, retaining attribution and disagreements. Do not average away an
  unresolved blocker or major finding or let a majority vote erase a coverage gap.

A native subset, preflight pong, initialization label or fixture route cannot prove serving
identity or cross-family completion. Missing family access, quota loss or a required reviewer
timeout blocks acceptance; it never lowers the threshold or silently changes the selection.

## Delegation

Follow [delegation.md](references/delegation.md) and the exact audit entry in
[dependencies.json](references/dependencies.json). Resolve `SKILL_ROOT` to the directory of
this loaded skill and `PROJECT_ROOT` to the actual audited project/worktree. Use only this
skill's own `scripts/` and `references/`; missing support files block routing rather than
trigger a search of sibling installations or a repository checkout.

For native consideration, `CAPABILITIES` names current project-contained host descriptors,
loaded provenance, tools and effective reviewer bindings. Config and capability paths must
resolve inside the explicit project root, with no symlink escape and no credential contents.

```sh
python3 "$SKILL_ROOT/scripts/tk-resolve.py" \
  --skill tk-audit --operation audit --project-root "$PROJECT_ROOT" \
  --config "$PROJECT_ROOT/.thunderkit/config.json" --capabilities "$CAPABILITIES" --json
```

With `delegation: off` or no enabled audit target, omit `--capabilities`: the local resolver
still validates choices but dispatches nothing. Do not run native discovery, loading,
routing helpers, doctor or installers on the off path.

The sole optional target is `omh:reviewer/omh-verification-gate`, mode **`component`**, on
Hermes: package `oh-my-hermes@2.0.3`, selector `reviewer/omh-verification-gate`, bare skill
name `omh-verification-gate`, canonical manifest identity `verification-gate`. Require
`tool:skill` and `model-binding:reviewers`. Verify the pinned package/source/version, bundle
root containing `manifest.json`, loaded entrypoint and actual SHA-256 of every required file,
including `guide/omh-routing/references/skill-common-rail.md` under the peer's skills root.
The bundle root is not `HERMES_HOME`. A listing, self-reported digest, missing companion or
quarantined skill cannot qualify it.

Only after `delegate` and dispatch consent, invoke the verified categorized selector through
the host's actual supported reviewer channel, bound to its selected member. Supply a bounded
read-only evidence-gap question, the common audit target and the whole requirements inventory.
The component may assess supplied evidence and return findings only: no code edits, fixes,
test execution, archive transition, new workflow, config mutation or delivery authority.
If that boundary cannot be enforced, do not invoke it. `omh-production-audit` is explicitly
not equivalent: production readiness is narrower than complete milestone requirements coverage.
No OMO audit target exists; OpenCode/Codex must use the guarded owned procedure, not an alias.

Every model-bearing action, including owned/fallback assessment and controller synthesis,
requires a real channel whose effective provider/wire-model and supported effort match a
selected reviewer. Preserve each selected member's ordered association; config validation,
prompt labels and skill loading alone do not bind channels. Use proven effective mappings,
not an invented `task(model=...)` argument or the arbitrary current root model. Do not assume
a running root changes after a config edit. Hermes has no catalog Sol mapping; a compatible
native subset under `"all"` cannot stand in for the remaining reviewer family.

Use an already-proven nonmutating Hermes binding; this read-only assessment does not call
`omh_delegate_route` or reconfigure shared homes. Never change global settings, auth,
provider/effort choices or fallback chains to force readiness.

Preserve the resolver's fixed decision record unchanged: requested/effective bindings,
null pre-invocation observation, target, reason and evidence paths. Exit 0 means routing was
computed, not executed work or milestone success. A blocked result or malformed input stops
dispatch. Record invocation failures/results separately, never rewrite the routing decision.

## Procedure

1. **Freeze the complete target.** Read `.thunderkit/SPEC.md` and enumerate every specified
   requirement by stable ID or exact section, including required acceptance criteria and
   approved scope changes. Do not derive the inventory from implemented lanes or silently
   drop an uncovered requirement. Record the spec's path/hash, approved scope and config
   snapshot, source base/head commits and trees, exact diff hash, and content hashes for
   included staged/unstaged/untracked changes. Missing identities stay null/unverified.
2. **Aggregate lane evidence.** Collect each lane's `REVIEW.md` and applicable `UAT.md`, their
   paths/hashes, author/reviewer identities, commands, cwd, exit/results and actual surface
   observations. Match their source/diff/artifact identities to the audited integrated tree.
   A lane branch pass is not proof after integration changed its target; any reuse needs
   evidence covering the current target. File existence, timestamps and a success label
   alone are insufficient. A missing required check is a blocker, not a skipped pass.
3. **Cross-reference every requirement.** Map `SPEC.md` → lane verification → applicable UAT
   with precise evidence locations and identity matches. Assign exactly one coverage status:
   - **satisfied**: every required acceptance item has fresh independent verification and
     applicable real-surface evidence on the current target.
   - **partial**: a lane addresses it but verification, applicable UAT, freshness, identity or
     a required seam is missing, stale, failed or unverified; state exactly what remains.
   - **orphaned**: no lane verification addresses the specified requirement; treat as unsatisfied.
   Mark UAT not applicable only with a requirement-specific, reviewed rationale showing no
   relevant surface exists. An inaccessible environment is unverified, not not-applicable.
   Flag verified work outside the spec as scope creep; it cannot compensate for an omission.
4. **Check cross-lane integration.** Inventory every interface and E2E flow crossing lane
   boundaries and link it to affected requirements. Confirm integration membership and require
   current integrated-tree evidence that the combined flow actually works, not just isolated
   unit passes or conflict-free merges. Record broken seams and explicitly **unverified**
   seams, including absent/unsafe/unavailable integration checks. Preserve useful lane passes
   without promoting them to integration success.
5. **Collect independent full-set assessments.** Each selected reviewer checks the complete
   matrix and seams, not only its native component's subset. Retain no-finding responses,
   disagreements, failures and the actual responding family count. Native findings may expose
   gaps but Thunderkit decides acceptance under the unchanged requirements and family gates.
6. **Reconcile without repairing.** Name correction owners and missing evidence. Return needed
   verification or UAT to the appropriate available sibling stage with its normal permissions;
   do not manufacture evidence, modify code, weaken tests or start an automatic fix loop.
   Recheck all relevant identities before the final decision; changed bytes invalidate the
   affected coverage and dependent gates until fresh evidence is supplied.

## Archive eligibility

Archive eligibility requires **every required item covered**, all requirements satisfied,
fresh lane verification and applicable UAT, all required integrated seams verified, the full
required independent reviewer set with actual family coverage meeting the minimum and a
family different from each author, and no unresolved blocker/major finding or assessment.
Missing scope or identity prevents eligibility; an empty inventory is not a vacuous pass.

An orphaned requirement, stale evidence, broken/unverified seam or missing family blocks
archive eligibility even when every implemented lane reports success. A narrower native PASS
never satisfies the milestone. Retain partial findings and explicit fail/blocked reasons;
neither a process exit 0 nor production readiness grants acceptance, archive or ship authority.

## Output — `.thunderkit/AUDIT.md`

The controller writes the audit with:

- The complete scoped requirement inventory, spec/config/source/diff/artifact identities and
  per-requirement **satisfied / partial / orphaned** matrix, linked lane verification and UAT,
  freshness checks, not-applicable rationale and every missing acceptance item.
- Cross-lane seam/flow evidence on the integrated target, with broken and unverified seams
  explicit and linked to affected requirements; uncovered and out-of-scope work remain visible.
- Ordered requested reviewers or literal `"all"` plus candidate outcomes; requested catalog
  identities, effective host/provider/model/effort and separately observed serving identities
  and catalog families, author comparisons and actual family count versus the minimum.
- Attributed findings, disagreements, required correction owners, overall pass/fail/blocked
  verdict and explicit archive eligibility with reasons. Report a narrower native claim's scope
  separately so its PASS cannot be mistaken for the milestone verdict.
- The immutable resolver decision plus separate delegated-run records using the common fields
  `lane_id`, `ecosystem`, `package_version`, `skill_name`, `requested_model`, `effective_model`,
  `observed_model`, `observed_family`, `artifact`, `artifact_sha256`, `session_id`, `status`,
  `evidence_paths`. Preserve genuine session/resume IDs; unavailable facts stay null/unverified.

Keep native artifacts at their real paths with content digests; do not rename them or mirror
native state into a competing workflow. Redact credentials from evidence. User projects keep
their `.thunderkit` context, including this audit, committed with the repo; transient runtime
captures remain in the allowed runtime area. Only a clean audit clears archive eligibility
via `tk-memory`; it does not itself archive, push, publish, create a PR or merge.

## Fallback

- `owned`/off/no enabled target or a named native denial uses the same complete owned audit
  only through genuinely bound selected reviewer channels, including synthesis. Valid choices
  alone are not execution readiness. Missing configuration, required binding/reviewer/family
  leaves a separate blocked outcome even if the resolver computed an owned/fallback route.
- Preserve the exact failed native gate. Missing peers, unsupported hosts, tampered bytes or
  absent companions permit no install, doctor, guessed alias, silent model substitution or
  undeclared production workflow. Give operator guidance without changing configuration.
- On an uncertain timeout or in-flight component, preserve known session IDs, artifacts and
  partial output as unknown/unverified. Inspect that same session and establish its outcome
  and ownership before any retry, replacement or fallback. If uncertain, remain blocked;
  never create a duplicate owner merely because a response did not arrive.
- Before any handoff to `tk-review`, `tk-verify-work`, `tk-memory`, `tk-router` or another
  sibling, check actual availability. Report a missing sibling as an unavailable prerequisite;
  never read a presumed sibling path, invent a command or install it implicitly.
