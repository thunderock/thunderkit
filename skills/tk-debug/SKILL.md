---
name: tk-debug
description: "Use when a lane fails, behavior is wrong or a crash has no obvious cause: preserve symptoms, falsifiable hypotheses, executed probes, a demonstrated root cause and a minimal fix with failing-before/passing-after regression evidence. Separates native investigation advice from executed debugging."
compatibility: "Python 3.11+ standard library for the bundled resolver; supported channels bound to selected planner and executors, plus the tools needed by each probe. Optional pinned OMO on OpenCode/Codex or OMH on Hermes; OMH requires Node 18+ and Python 3.11+. No automatic debugger installation or host reconfiguration."
metadata:
  thunderkit-role: "debug"
  thunderkit-tier: "verify"
  thunderkit-delegates: "omo:debugging omh:reviewer/omh-native-debugging gsd:gsd-debug"
  thunderkit-contract: "1"
---

# tk-debug — scientific-method debugging

When execution or verification fails without an obvious cause, investigate instead of guessing:
symptoms → hypotheses → executed probe → confirmed root cause → minimal fix → regression proof.
Keep a resumable debug record; an investigation plan is not an executed investigation or repair.

## Scope and model contract

Default to **`general`**. Select **`native-fault`** explicitly only for genuine native crashes:
segfaults, native extensions or FFI failures requiring native symbols, stack inspection or DAP.
A business-logic error, wrong response or ordinary failed test is not a native fault. If an
explicit native-fault request does not fit, report the scope mismatch before invoking anything;
correct the classification to general rather than borrowing the OMH component to fill a gap.

Use **planner** for hypotheses and root-cause reasoning; use **executors** for running probes,
instrumentation, reproductions, applying the fix and regression commands. Validate all three
class selections through this skill's [config contract](references/config.schema.json) and
[model roster](references/model-roster.md), including on owned routes or with delegation off.
Preserve the single planner, ordered plural selections, literal reviewers `"all"`, frozen paths
and review-family policy. Missing choices are not defaults; a valid legacy preview is not
permission to rewrite config. The `reviewer/` category of an OMH skill does not change its class.

Prove a supported channel's actual binding for every class it uses, including the owner/root.
Record the selected catalog member, effective host descriptor, provider/model and supported
effort; preserve each selected executor's association without collapsing the set. A bounded run
need not exercise every executor. A role doing both reasoning and execution must satisfy both
class selections; do not pretend that loading a skill switches its model. Prompt labels or
valid config alone are not binding proof, and no arbitrary current agent substitutes for a
selected model. Missing or mismatched bindings leave an investigation/fix gap and block that work.
Debug reasoning, even from an Oracle, does not count as independent cross-family review.

Record the actual project, source/base identity, approved lane/worktree, permitted files,
frozen paths and finite probe/time budget. Inspect existing work and owner/session state before
starting. All probes and fixes use the approved worktree as their working directory; no edits
outside its authorized scope, automatic worktree replacement or delivery. Missing permission
for a required probe or fix is a gap, not permission to expand the task.

## Delegation

Read the installed skill's [registry](references/dependencies.json),
[delegation contract](references/delegation.md) and [catalog](references/models.json).
Set `SKILL_ROOT` to the directory containing this loaded file, and `PROJECT_ROOT` to the actual
repository under investigation, not the skill installation or an incidental shell directory.
Resolve only its own `references/` and `scripts/`; missing local assets block routing rather
than triggering a parent-directory or sibling-installation search.

For enabled native candidates, collect current loaded-source and effective-binding evidence
without credentials or configuration changes. Set `CAPABILITIES_PATH` to that explicit file
inside the project and `OPERATION` to the validated general or native-fault selection:

```sh
python3 "$SKILL_ROOT/scripts/tk-resolve.py" --skill tk-debug --operation "$OPERATION" \
  --project-root "$PROJECT_ROOT" --config "$PROJECT_ROOT/.thunderkit/config.json" \
  --capabilities "$CAPABILITIES_PATH" --json
```

Config and capability files must resolve inside the actual project, without traversal or
symlink escape. With delegation off or no enabled ecosystems, omit capabilities and perform
no native discovery, loading, routing, doctor or installer calls. Config remains required.

| Qualified target | Host | Operation | Mode | Registry requirements |
|---|---|---|---|---|
| `omo:debugging` | OpenCode or Codex | general, native-fault | handoff | `tool:skill`, `model-binding:planner` |
| `omh:reviewer/omh-native-debugging` | Hermes | native-fault only | component | `tool:skill`, `model-binding:planner` |

These are source-qualified addresses, not slash commands. Invoke only the verified host
selector after package/version/source, loaded entrypoint and every required file's real bytes
match the pinned registry. OMO uses `oh-my-openagent@5.0.0-beta.81`; its entire declared debugging
reference/script tree is required. OMH uses `oh-my-hermes@2.0.3`, categorized selector
`reviewer/omh-native-debugging` and canonical identity `native-debugging`; its native-debug-loop
reference and categorized shared rail must both match. Its provenance root contains
`manifest.json` and `skills/`, not just the skills directory or a task's Hermes home.
An installed package, same-name file, quarantined companion, self-reported hash or `ready` flag
is not proof. Never install, render peer code or change host/provider/auth configuration to qualify it.

Keep the resolver record unchanged: `schema_version`, `skill`, `operation`, `decision`,
`reason_code`, `detail`, `target`, `bindings`, `runtime_home`, `evidence_paths`. Exit 0 means
routing was computed, not that a probe or native workflow ran. Blocked is exit 1; invalid input
is exit 2. Only `delegate/compatible` admits a native candidate, subject to the additional
scope, role, permission and ownership gates below. Record later invocation failures separately;
never rewrite a compatible routing reason to explain a failed run. A corrected input produces
a new record without overwriting the earlier decision.

## Native handoff

OMO `debugging` is **one owner for the scoped investigation and fix**, not a component inside
another debug loop. Before handoff, verify the actual native root, hypothesis/synthesis and
Oracle reasoning channels against the selected planner, and every probe/reproduction/fix
channel against the selected executors, including conditional roles the native workflow may use.
The registry checks planner only; a compatible result does not prove these additional roles.
Inspect actual host descriptors and effective agent/category mappings. OMO `task()` has no model
parameter and `load_skills` only supplies instructions. An opaque, unrepresentable or mismatched
role blocks the handoff; do not silently fall back around a model-binding failure or assume a
running root changes after a config edit. Report the operator action needed for fresh proof.

Pass the scoped symptoms, source/worktree identity, selected classes, bounded permissions and
required evidence to that single owner. It follows its own applicable runtime/tool references
and native journal-before-modification discipline. Respect its **no-commit rule**: no `git commit`
inside native debugging. No push, PR, publish or merge either. Thunderkit must not launch its
portable loop in parallel, create a second native owner or mirror the native state machine.

The owner retains its real `.debug-journal.md` and other native artifact locations and handles
only its own authorized temporary instrumentation/process cleanup. Request the actual native
session ID, journal/artifact paths and verified SHA-256 digests with the probe and regression
outputs. Capture the journal identity before native cleanup; if the owner removes it as part
of that cleanup, record its removal and last verified digest with the returned native evidence.
Do not recreate, rename or copy the journal into Thunderkit state, invent a digest, or remove
the user's existing work. After a known return, the controller references native evidence in
the debug record and checks the output contract; it does not turn native success text into proof.

## Investigation component

On Hermes, `reviewer/omh-native-debugging` is a bounded, read-only **investigation-planning
component for native-fault only**, using an already-proven planner channel. Thunderkit remains
the owner. Supply the native crash evidence and request hypotheses, discriminating probes,
required debugger/symbol/DAP prerequisites and suggested fix boundaries. Do not ask it to attach
a debugger, run probes, edit source or start a second workflow. Use the verified host selector;
do not mutate shared Hermes routing to obtain a binding.

Its returned plan is **planned work only**. It proves neither debugger execution nor a confirmed
cause nor a working fix, even if it says `done` or exits successfully. After the component's
known return, hand each approved real probe and any fix to a genuinely bound selected executor.
The executor's actual outputs, source identity and regression results supply the evidence.
Until those runs happen, record probes as not executed and the cause/fix as unverified. If the
component cannot stay within this boundary, do not invoke it; use the fallback guard instead.

## Fallback

For `owned` or `fallback`, retain the specific resolver reason and use the bounded loop below
only with valid model selections and genuinely bound planner/executor channels for their work.
No qualified general target on Hermes means owned investigation, not an OMH native-fault call.
Missing peers or source companions may allow portable work; missing selected channels do not.
A `blocked` result stops dispatch and is never reinterpreted as fallback permission. Record a
separate blocked outcome when a post-resolution gate fails without changing the routing record.

Uncertain, timed-out or in-flight native work still owns its scope. Preserve the real session
identity, artifacts and worktree; inspect that same session before considering fallback.
History metadata alone is not evidence that it can be resumed. Unknown terminal state remains
blocked/unknown, with no duplicate owner or blind retry. Never clear a captured ID just because
the run timed out. A known failed owner must be explicitly retired, with its work preserved,
before a replacement starts under fresh gates; no reset, stash or cleanup to conceal failure.

Missing a required debugger, DAP adapter, symbols/source maps, reproducible input or access leaves
an explicit investigation/fix gap. Keep any partial evidence but do not claim an executed probe
or verified fix. No auto-install, permission bypass, global reconfiguration or unapproved model
substitution. Use an available alternative probe only if it genuinely tests the same hypothesis
within the approved scope; do not replace missing runtime evidence with a plausible story.

## The loop

Use this only for owned work or the real executor work following a returned OMH plan, never
alongside the OMO owner. Stop at the agreed budget with the remaining gap, not a guessed fix.

1. **Symptoms** — capture the exact failing command/input, cwd, source/build identity, exit or
   signal, output and expected versus actual behavior. Preserve diagnostic values; redact secrets.
   Verify the runtime and required probe tools before using them, without installing anything.
2. **Hypotheses** — the selected planner records 2–4 distinct, falsifiable causes. For each, name
   the smallest discriminating probe, predicted confirming/refuting observations and prerequisites.
   Keep proposed probes distinct from executed ones; an OMH plan can seed this ledger, not fill results.
3. **Probe** — a selected executor runs the approved experiment in the approved worktree. Record
   exact invocation, timestamp, source/session identity, exit/signal and observed values/output.
   The planner updates each hypothesis from those results; an unavailable probe stays not executed.
   Recheck live target/session state before any side-effecting inspection or continuation.
4. **Root cause** — require an executed discriminating probe that demonstrates the mechanism,
   not merely a surviving guess or agreement between models. Reproduce the observation and,
   within approved reversible scope, toggle the suspected cause to show the failure changes with
   it. If that causal evidence is missing or contradictory, retain an unconfirmed hypothesis.
5. **Fix** — establish and record the failing regression first, then let a selected executor
   make the smallest cause-targeted change in the approved lane. Respect frozen paths and
   preserve unrelated work. No adjacent refactor, masking the symptom or delivery. Remove only
   owned temporary instrumentation with a scoped undo that preserves the real fix and test.
6. **Regression proof** — run the same regression test against the unfixed and fixed source,
   recording both identities, exact command/cwd, failure-before and pass-after outputs. Reuse the
   captured pre-fix failure rather than destructive source switching. Run the relevant existing
   suite and the original reproduction too. Never weaken, skip, quarantine or delete a failing
   test to force green. Any failed or unavailable required check leaves the fix unverified.

## Output contract

Write `.thunderkit/debug/<slug>.md`, using a safe single-segment slug and a project-contained path:

- Symptoms and source/build/base/worktree identity, scope, permissions and probe/time budget.
- Hypothesis ledger with predictions, each probe's planned/executed/not-executed status, actual
  results and evidence paths. Separate raw observations from the planner's interpretation.
- Confirmed root cause and causal probe evidence, or an explicit unconfirmed investigation gap.
- Minimal fix with file/diff identity, or not applied/unverified with the reason.
- Before/after regression command, cwd, source identities and both outputs; relevant suite and
  original-reproduction results, with failures and unavailable checks retained.
- Immutable routing record plus separate invocation/verification outcomes; requested, effective
  and actually observed models/effort; qualified native source/version, real journal/artifact
  path and SHA-256, and genuine session/resume identity. Preserve a captured ID; use null/unverified
  only for unavailable facts. Do not derive observed identity from config or a prompt.

For delegated work retain the common run fields from `references/delegation.md`, including
`artifact`, `artifact_sha256`, `session_id`, `status` and `evidence_paths`. Record native cleanup
without fabricating a still-existing artifact. A component plan, process exit, model assertion,
compile check or stale test result cannot satisfy executed-probe or fix evidence. Changed
source/diff, inputs, artifacts or model bindings invalidate the dependent evidence and readiness.

Resume by re-reading this record and its real native references, inspecting ownership and
freshness before continuing; do not restart an uncertain investigation. Keep the debug record
with the project's committed `.thunderkit` context; writing it grants no commit or delivery
authority. Native debugging does not commit, and this workflow never pushes, opens a PR or merges.
Check sibling availability before any transition to `tk-execute`, `tk-review` or `tk-plan`.
A missing sibling is a named prerequisite, not an implicit installation or presumed file path.
Hand a demonstrated fix to an available `tk-review`; native completion does not replace its
independent review gate. Broader work needs separate scope approval, not an expanded debug loop.
