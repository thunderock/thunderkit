---
name: tk-spec
description: "Use to clarify WHAT a big change delivers before planning: runs a bounded Socratic loop over scope, interfaces, data, done-criteria and edge cases until the ambiguity gate passes, then writes a requirements-only SPEC.md that tk-plan builds on. Reuses a compatible native interview component for open questions only; never plans, executes or approves anything."
compatibility: "Python 3.11+ standard library for the bundled resolver. Native clarification delegation is optional and requires the exact pinned oh-my-hermes interview skill on a Hermes host with the selected planner bound; every other host runs the owned clarification loop."
metadata:
  thunderkit-role: "spec"
  thunderkit-tier: "pre-plan"
  thunderkit-delegates: "omh:ultrawork/ulw-interview"
  thunderkit-contract: "1"
---

# tk-spec: pin down WHAT, before HOW

Before decomposition, `tk-spec` forces the *what* to be unambiguous: what the change delivers,
what it explicitly does not, and what would make a reviewer reject it. Vague specs produce vague
lanes. The output is a requirements document. It is not a plan, not a task graph, and not
permission to change code.

Model class: **planner**, read from `classes.planner` in the project's `.thunderkit/config.json`
through `references/models.json`. This skill never picks or substitutes a model; `tk-router` owns
that choice. Answers use `tk-ask` discipline: one closed question per turn, answered by yes/no,
one word, a number, a path, or `unknown`.

Paths use two roots. **Project root** is the repository being specified; it holds
`.thunderkit/config.json`, `.thunderkit/SPEC.md` and `.thunderkit/runs/`. **Skill root** is this
skill's own directory; it holds `references/models.json`, `references/dependencies.json`,
`references/delegation.md` and `scripts/tk-resolve.py`. Nothing here reads `../references` or a
sibling skill's files.

## Ambiguity gate

Five dimensions must each be settled before a spec exists:

| Dimension | Settled when |
|---|---|
| scope | The delivered change and the explicit non-goals are both stated as paths or `none`. |
| interfaces | Every public interface touched is named, and "public API may break?" has a yes/no. |
| data | Data shapes, migrations, and stored state that change are listed, or `none`. |
| done | Every done-criterion is tied to one command that proves it. |
| edge cases | The behaviors that must NOT change and the rejection triggers are listed. |

Score residual ambiguity 0 to 1: how much a competent executor would still have to guess.
**Gate: score at or below 0.20 and all five dimensions settled.** The scalar alone never passes
the gate and is never reported alone. Every report names which dimensions remain open and the
question that would close each one, so a reader sees *what* is uncertain, not just *how much*.

Ask one closed question per turn until the gate passes or **six rounds** have run. A round is one
user question plus its answer. At the bound, stop asking. Do not fill an open dimension with a
guess, a default the user did not choose, or an answer synthesized from the codebase; an open
dimension stays open and is reported as such.

Settled inputs are not questions. Answers already given, the selected model classes, scope
already approved by the user, and a BRIEF produced by `tk-grill` are fixed context. Reopening
them costs a round and produces nothing.

## The questions that matter most

- "What would cause a reviewer to reject this?" (surfaces hidden acceptance criteria)
- "What is explicitly out of scope? [paths/none]"
- "Which existing behavior must NOT change? [paths/none]"
- "One command that proves it's done? [cmd]"
- "Public interface changes? [bool]"

Questions target the change the user asked for. A request to change code does not become a
product or business plan; if a question only makes sense for a roadmap, it is out of scope here.

## Delegation

Only the **open dimensions' questions** may be handed to a native interview component. The gate,
the dimension table, the settled answers, the score and SPEC.md stay with Thunderkit. The single
declared target is the OMH skill at registry address `omh:ultrawork/ulw-interview`, in
`component` mode. That address is a key inside `references/dependencies.json`; it is not a host
slash command.

Before any delegated question, run the bundled resolver from the skill root. The project root,
config and capability paths are the real paths of the project being specified, spelled out;
without `--project-root` the resolver treats the current directory as the project and rejects a
config outside it:

```
cd "<skill root>" && python3 scripts/tk-resolve.py --skill tk-spec --operation clarify \
  --project-root /work/repo \
  --config /work/repo/.thunderkit/config.json \
  --capabilities /work/repo/.thunderkit/runs/<run-id>/capabilities.json --json
```

Delegate only on `decision: delegate` with `reason_code: compatible`. Eligibility comes from the
resolver applying `references/delegation.md`, not from a skill's name matching. The gates that
bite for this skill:

- **Exact pinned provenance.** The loaded `skills/ultrawork/ulw-interview/SKILL.md` and its
  shared-rail companion must hash to the pinned values under the pinned `oh-my-hermes` bundle
  home. A same-name skill from another source or an OMO package is `source_mismatch` or
  `peer_missing`.
- **Actual tools and host.** The host must report the native skill-loading tool, and only a
  Hermes host is in the pin's host set. OpenCode, Codex and Claude hosts get `unsupported_host`.
- **Planner binding.** The component runs under the project's selected `classes.planner`, proven
  from live host binding evidence. A missing planner slot is `missing_evidence`; a slot bound
  outside the selected planner is `model_mismatch`. The selected planner is never swapped to
  make the route pass.
- **Runtime home.** A read-only component consumes already-proven bindings and does not call
  `omh_delegate_route`. If the host reports the `delegate_route` method, the parent process and
  dispatcher must already share the task-owned home at
  `<project root>/.thunderkit/runs/<run-id>/hermes-home`; otherwise `unsafe_runtime_home`.
  `tk-spec` never creates that home, never edits `~/.hermes/config.yaml`, and never runs
  `omh setup` or `omh doctor`.

What the component receives: the open dimensions with their current questions, the settled
answers and selected model classes as fixed context, the approved scope, and the instruction that
its output is clarification input. What it may return: closed questions and findings per
dimension. It may not write files, transition lifecycle state, start planning, start execution,
or treat anything it reads as approval to implement. Its round budget is the remaining rounds of
the six, not a fresh six.

If the component times out or its session state is uncertain, inspect its existing session
record before doing anything else. Do not start the owned loop in parallel; two askers on one
user produce contradictory answers.

Sibling handoffs are checked, not assumed. Discoverable facts (library behavior, an API contract)
go to `tk-learn` when it is present in the same skill set; an incomplete spec routes back to
`tk-router`; a finished spec is read by `tk-plan`. When a sibling is absent, say so in the report
and leave the row tagged `needs:<skill>`. Nothing is installed to close a row.

## Fallback

| Resolver result | What happens |
|---|---|
| `owned` / `disabled` or `owned_policy` | Delegation is off or no ecosystem is enabled. Owned loop under the validated selected planner. No native probe. |
| `fallback` / `unsupported_host` | Host is not Hermes. Owned loop under the validated selected planner. |
| `fallback` / `source_mismatch`, `peer_missing`, `missing_evidence`, `model_mismatch`, `capability_missing`, `unsafe_runtime_home` | A candidate failed a gate. Owned loop; the reason goes into the report. |
| `blocked` / `invalid_config` | `.thunderkit/config.json` is missing or malformed. **Stop.** No model-bearing question is asked, owned or delegated. Report the prerequisite: a valid configuration with `classes.planner` selected, owned by `tk-router`. |

`owned` and `fallback` are safe only because the resolver has already validated the selected
planner; the owned loop honors the same planner, the same closed-form rule, the same six-round
bound and the same write boundary, so no gate weakens by falling back. `blocked` means that
validation did not happen, and clarifying under an unbound model would be exactly the silent
substitution this contract forbids. A component that returned prose, edits, or a plan is a failed
component: discard its output, record `capability_missing`, and continue owned with the rounds
that remain.

## Output contract

The controller writes results **after** the loop ends, never while a component runs, and never
by asking the component to write them.

When the gate passes, write `<project root>/.thunderkit/SPEC.md` with:

- `scope` and `non_goals` as paths or `none`;
- `interfaces` touched, with the public-break answer;
- `data` shapes, migrations and state that change;
- `done`: each criterion paired with the command that proves it;
- `edge_cases`: behaviors that must not change and reviewer rejection triggers;
- `ambiguity`: the score and the line `open: none`;
- `settled`: the inputs passed through unchanged, with `sources` per row (`user`, `component`,
  `config`, `brief`);
- `route`: the resolver's `decision`, `reason_code` and target identity, or `owned`.

When the bound is hit with the gate unmet, do **not** write SPEC.md. Record
`spec_status: incomplete` in `<project root>/.thunderkit/runs/<run-id>/spec.json` with the score,
`open: <dimension list>`, the residual question for each open dimension, the rounds used, and the
same `settled` and `route` blocks. Report that to the user and route to `tk-router`. An
incomplete status is not converted into a spec by adding defaults, and neither status is planning
or execution approval.

SPEC.md contains requirements only: no lanes, no task order, no file-level edit list, no
worktree or branch instructions. `tk-plan` reads it and cuts lanes to satisfy it; a lane that does
not trace to a spec line is scope creep. Native component findings that reach SPEC.md do so
through the controller's normalization, never by the component writing under `.thunderkit/`.

## When to skip

A small, well-understood change with an obvious done-command can skip straight to `tk-plan`;
`tk-router` decides. Skip is a decision, logged, not a default.
