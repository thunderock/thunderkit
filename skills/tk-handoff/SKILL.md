---
name: tk-handoff
description: "Use to save or restore a work session in a portable format when a harness nears full context or you pause: save writes .thunderkit/HANDOFF.md (stage, lanes, resume ids, decisions, next action); restore reads north star plus handoff and resumes at the named stage."
metadata:
  thunderkit:
    role: continuity
    tier: context
---

# tk-handoff — save and restore a session, portably

A big-repo run outlives one context window. `tk-handoff` makes a session **survive a context
reset, a pause, or a switch to a different harness** by writing the state to a fixed file any
thunderkit-aware agent can read — not a harness-private session blob, but the same committed format
the rest of the pack uses.

Two verbs: **save** (checkpoint now) and **restore** (resume from the last checkpoint).

## When to save

- **Approaching the context limit** — save at roughly **80% of the window**, before quality
  degrades. The router watches for this; `tk-handoff save` is the action.
- **Pausing** work you'll resume later, possibly on a different machine or model.
- **Before a risky step**, so a bad turn is one `restore` away from recovery.

## Output — `.thunderkit/HANDOFF.md` (fixed schema)

```
# Handoff
saved_at: YYYY-MM-DD HH:MM · head: <git short sha> · context_at_save: ~NN%
north_star: .thunderkit/NORTH_STAR.md          # the why, read this first
current_stage: <lifecycle stage # + name>       # where the run is
active_artifact: .thunderkit/<PLAN.md|…>         # the file in play
lanes_in_flight:                                 # resumable dispatch, per lane
  - id: L1-…  model: opus48  resume: claude -p --resume <sid>  status: running|blocked
decisions_this_session:                          # what was settled (mirror to DECISIONS.md)
  - …
next_action: <the single next step>
open_unknowns: <what's unresolved / none>
```

The schema is fixed so `restore` (or a different agent) can parse it. `saved_at` + `head` let
restore detect staleness.

## Restore

1. Read `NORTH_STAR.md` first (the why), then `config.json` (the model classes), then `HANDOFF.md`.
2. **Staleness check** — if `HANDOFF.head` ≠ current HEAD, warn: the tree moved since the save;
   confirm before resuming, don't blindly continue.
3. Re-establish in-flight lanes from their `resume` commands (claude `--resume`, codex `resume`,
   hermes `--resume`).
4. Resume at `current_stage` / `next_action` — don't restart the lifecycle from the top.

`tk-router` runs restore as **stage 0**: if a `HANDOFF.md` exists, offer to resume from it before
starting fresh.

## Discipline

- **Portable, not harness-private.** The handoff is plain committed markdown so a session started
  on one harness can be resumed on another — the whole point of a heterogeneous fleet.
- **Save early, not at 100%.** A handoff written after context is already full is written by a
  degraded model — save at ~80%.
- **Never fabricate a resume id.** A lane with no captured session id is recorded `resume: none
  (not resumable)`, honestly, so restore knows it must re-dispatch that lane.
