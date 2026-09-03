---
name: tk-plan
description: "Use to turn a big-repo change into a parallel execution plan: decomposes work into disjoint, dependency-layered lanes, each file-scoped with acceptance criteria and a verification command, ready for tk-execute."
metadata:
  thunderkit:
    role: planner
    tier: plan
---

# tk-plan — decompose into parallel lanes

The heart of the thunderkit thesis. `tk-plan` takes a change and produces a plan whose unit is
the **lane**: a disjoint, file-scoped slice of work that can run *in parallel* with its siblings
without collision, ordered into dependency layers. A plan that can't be split into lanes is not
finished here — that's the opinion this skill enforces.

Preferred model: **Opus 4.8** (planning is load-bearing — bad lanes cost the whole run). This is
one of the choices `thunderkit` should offer the user (Opus 4.8 / Opus 5). See
`../references/model-roster.md`. Read `.thunderkit/MAP.md` from `tk-map` first.

## What a lane is

- **Disjoint file scope** — two lanes in the same layer must not write the same files. This is
  what makes parallel execution safe. If two slices need the same file, they belong in different
  *layers*, not the same layer.
- **A dependency layer** — lanes in layer N may depend only on layers < N. Layer 0 lanes have no
  intra-plan dependencies and start immediately.
- **Acceptance criteria** — what "this lane is done" means, testably.
- **A verification command** — the exact command `tk-review` runs to gate the lane. No command
  → the lane is `blocked`, not plannable.
- **A model hint** — critical-path lane vs. breadth/cleanup lane, resolved against the roster.

## Output contract — `.thunderkit/PLAN.md` + `.thunderkit/plan.json`

Human-readable `PLAN.md` and a machine-readable `plan.json` that `tk-execute` consumes:

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

## Procedure

1. **Refresh the map if stale** (older than the branch base) — route back to `tk-map`.
2. **Cut along seams**, not arbitrarily. Use the boundaries in `MAP.md` so lanes fall on real
   module edges and file scopes genuinely don't overlap.
3. **Layer by dependency.** Put independent slices in the same layer (they parallelize); put a
   slice that needs another's output in a later layer.
4. **Attach acceptance + verify to every lane** from the map's per-area verification commands.
   A lane with no runnable verify is `blocked` — record why and what's needed to unblock it.
5. **Mark model hints.** Flag the critical-path lane(s) so `thunderkit` knows to ask the user
   which model implements them.
6. **Check testability** before finishing: can each lane's verify actually run in this repo? If
   a command is aspirational (test doesn't exist yet), the lane's first task is to create it.

## The parallelism check (do this before declaring the plan done)

- Every pair of lanes in the same layer has **non-overlapping `files`**. If not, re-layer.
- Every lane has a **`verify`** or is explicitly `blocked`.
- At least the critical-path lane has a **`model_hint`** for the user-choice step.
- Layer 0 is non-empty (something can start immediately) — if not, the decomposition is too
  serial; reconsider the seams.

## Record unresolved tradeoffs

If a clean disjoint decomposition isn't possible (genuinely entangled code), say so explicitly:
record the entanglement, propose the least-bad layering, and flag the lanes that must run serial.
Don't flatten a real dependency into fake parallelism.
