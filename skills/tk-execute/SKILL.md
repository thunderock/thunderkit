---
name: tk-execute
description: "Use to run an accepted thunderkit plan: implements disjoint lanes in parallel across the fleet via portable CLI dispatch (claude/codex), each lane in its own git worktree with a captured resumable session id."
metadata:
  thunderkit:
    role: executor
    tier: execute
---

# tk-execute — run lanes in parallel

Takes `.thunderkit/plan.json` from `tk-plan` and **implements its lanes in parallel** across the
fleet. Layer by layer: all lanes in a layer dispatch concurrently (they're disjoint by
construction), the layer's verifications gate advancement, then the next layer starts.

Dispatch is **portable CLI only** — `claude -p` and `codex exec` — so this runs on anyone's
machine with no private orchestrator. See the dispatch table in `../references/model-roster.md`.

## Prerequisites (check, don't assume)

- `.thunderkit/plan.json` exists and passed `tk-plan`'s parallelism check.
- The user has chosen the load-bearing models (via `tk-router`) — critical-path lane model is
  resolved, not a placeholder.
- The harnesses the plan's models need are installed and authed. If not, **degrade and name**:
  run the lanes you can, report which lanes are blocked on which missing auth.

## Per-lane execution

Each lane runs **in its own git worktree** so parallel lanes never touch each other's working
tree:

```sh
git worktree add ../wt-<lane-id> -b tk/<lane-id>
```

Dispatch the lane to its chosen model via the roster's dispatch commands. **Always capture the
resumable id** — a lane that stalls with no session id is stranded work:

```sh
# Claude Code lane (critical path), permissions granted on the command:
claude -p "<lane prompt>" --output-format json --permission-mode acceptEdits \
  --add-dir ../wt-<lane-id> > .thunderkit/runs/<lane-id>.json
#   → read .session_id ; resume with: claude -p --resume <session_id>

# Codex lane (cross-family / breadth):
codex exec --json "<lane prompt>" --skip-git-repo-check -C ../wt-<lane-id> \
  > .thunderkit/runs/<lane-id>.jsonl
#   → read .thread_id ; resume with: codex exec resume <thread_id> --skip-git-repo-check
```

## The lane prompt (what you actually send)

Build it from the lane record. It must be self-contained — the dispatched agent has none of this
conversation's context:

- The goal (from `plan.json`), and **this lane's** file scope and acceptance criteria.
- The hard boundary: **touch only the files in this lane's `files` list.** Editing outside scope
  breaks the disjointness guarantee and collides with a sibling lane.
- The verification command the lane must make pass.
- Instruction to commit atomically in the worktree when the verify passes.

Show the composed prompt (a bounded preview) in your status output — the user must see *what*
each lane was asked to do, not just that something ran.

## Dispatch discipline (from the fleet's delegation contract)

- **Name each lane's model + effort** inline in status: `(Opus 4.8 high)`, `(Sol)`, `(Fable 5.1)`.
- **Prove permissions before the real dispatch** on a fresh machine: a one-file scratch-edit
  probe run. A permission denial in a non-interactive run recurs identically on retry — never
  redispatch until a changed grant is proven.
- **Bound every run** — pass the harness's max-runtime/turn cap so a runaway lane self-terminates.
- **Reap on exit** — don't leave orphaned worktrees; `git worktree remove` after merge.

## Layer gating

1. Dispatch all lanes in layer N concurrently.
2. When each returns, run its `verify` (or hand the whole layer to `tk-review`).
3. Merge passing lanes' worktree branches into the working branch. A failing lane blocks only
   itself and its dependents — sibling lanes still land.
4. Advance to layer N+1 only when layer N's dependency-providing lanes are merged.

## Merge + collision safety

Because lanes in a layer are file-disjoint, their worktree branches merge without conflict *by
construction*. If a merge *does* conflict, the plan's disjointness was violated — stop, report
it as a `tk-plan` defect (overlapping `files`), and don't paper over it with a manual resolve.

## Output

- `.thunderkit/runs/<lane-id>.json[l]` per lane (with the resumable id).
- Merged commits on the working branch, one atomic commit per lane.
- A run summary: per lane — model used, pass/blocked, resume id, files touched.

Never push or open a PR — stop at merged local commits and hand to `tk-review`.
