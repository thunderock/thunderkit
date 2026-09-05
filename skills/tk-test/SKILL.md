---
name: tk-test
description: "Use to prove the fleet configured by tk-router is actually reachable: pings every model in .thunderkit/config.json through its real harness CLI with a one-word probe and reports reachable/unreachable per model and per class before any real work starts."
metadata:
  thunderkit:
    role: preflight
    tier: intake
---

# tk-test — does the configured fleet actually answer?

A smoke test for the model classes `tk-router` chose. Before committing a big run to a fleet,
`tk-test` proves each configured model is **actually reachable through its harness on this
machine** — not assumed from config. It sends the smallest possible `tk-ask` probe ("Reply with
exactly one word: pong") to every model and reports what came back, with the resumable id.

Run it right after `tk-router` writes `.thunderkit/config.json`, on a fresh machine, or whenever a
run mysteriously stalls — a stalled lane is usually an unreachable or unauthed model, and this
finds that in seconds instead of minutes of silence.

## What it does

`scripts/tk-test.py` reads `.thunderkit/config.json`, resolves each short name to a harness +
provider id via the roster, and dispatches the probe through the **real CLI** for each:

| Harness | Probe command | Reads |
|---|---|---|
| claude | `claude -p "<probe>" --model <id> --output-format json` | `.result` == pong, `.session_id` |
| codex | `codex exec --json --skip-git-repo-check -m <id> "<probe>"` | `item.completed` text, `thread.started.thread_id` |
| hermes | `hermes chat -q "<probe>" --oneshot -Q --provider <p> -m <id> -t ""` | last line == pong, `session_id` |

Each model is reported `reachable` (answered "pong"), `unreachable` (CLI ran but wrong/failed
answer — auth, quota, bad id), or `not-installed` (harness not on PATH). It also checks the
**cross-family invariant**: how many distinct model *families* are reachable among the reviewers,
against `review_families_min` — because if only one family answers, `tk-review` can't do a real
cross-family review and `tk-ship` will block.

## Run it

```sh
python3 skills/tk-test/scripts/tk-test.py                 # human table, exit 0 iff all reachable
python3 skills/tk-test/scripts/tk-test.py --json          # machine-readable
python3 skills/tk-test/scripts/tk-test.py --timeout 150   # per-probe timeout (default 120s)
python3 skills/tk-test/scripts/tk-test.py --config path/to/config.json
```

(When the pack is installed via `npx skills`, the script lives at
`~/.agents/skills/tk-test/scripts/tk-test.py`.)

## Output — reachability report

Per-model status + timing + resumable id, then a per-class roll-up and the family check:

```
✓ opus48  claude   reachable   8.9  pong
✓ opus5   hermes   reachable  28.1  pong
✓ fable51 hermes   reachable  14.8  pong
✗ sol     codex    unreachable      rc=1 quota exceeded

  reviewer families reachable: 1 (anthropic); required ≥ 2  ← cross-family review NOT possible
```

Exit 0 only when every configured model answered; non-zero otherwise, so it drops straight into a
Makefile target or CI preflight.

## Discipline

- **Never fakes a result.** A model that doesn't answer is `unreachable`/`not-installed`, never a
  silent pass. The probe asserts the literal word `pong` came back, not just that the CLI exited 0.
- **Degrade honestly.** If a class loses a model, `tk-test` says which class and whether the
  cross-family invariant still holds — the same honesty rule the rest of the pack follows.
- **Cheap and bounded.** One tiny turn per model, each under a timeout, so a hung harness can't
  stall the preflight.
