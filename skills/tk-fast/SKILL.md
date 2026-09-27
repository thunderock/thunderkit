---
name: tk-fast
description: "Use when a change is trivial and local (a typo, a rename, a one-line fix, a config value): edit inline in the current session with no model selection, plan, subagents or review, run the targeted test and make one atomic commit; escalate to tk-quick when it stops being trivial."
compatibility: "Any host with a skill loader, a shell and git; Python 3.11+ for the bundled resolver. On Claude Code, Codex, Copilot and other GSD hosts the locked GSD gsd-fast skill is used when installed; OpenCode and Hermes use the owned inline path."
metadata:
  thunderkit-role: "fast"
  thunderkit-tier: "execute"
  thunderkit-delegates: "gsd:gsd-fast"
  thunderkit-contract: "1"
---

# tk-fast: trivial edits, inline

`tk-fast` is the smallest path through thunderkit. It is for a change you can describe in one
sentence and verify with one command: a typo, a rename inside one module, a one-line fix, a
config value. It does not choose a model, write a plan, spawn subagents or ask for a review.
The current session does the edit.

## When to use

All of these must hold before starting:

- the change touches at most 3 files;
- no path listed in `frozen_paths` in `.thunderkit/config.json` is touched;
- one targeted test or check command can show the change works.

## Escalation

Stop and hand the task to `tk-quick` (or `tk-router` for larger work) when any of these becomes
true during the edit:

- a fourth file needs changing;
- a frozen path would change;
- the targeted test still fails after one fix attempt.

Report the escalation with the files touched so far; do not commit partial work.

## Delegation

On Claude Code, Codex, Copilot and other GSD hosts the only native target is GSD `gsd-fast`
(`thunderkit-delegates: gsd:gsd-fast`). It needs no GSD project under `.planning/` and binds no
model class. Check the route first:

```
python3 scripts/tk-resolve.py --skill tk-fast --operation edit --config .thunderkit/config.json --capabilities <snapshot> --lock .thunderkit/peers.lock.json --json
```

`delegate` means hand the task to `gsd-fast` and keep this skill's scope and escalation rules.
`fallback` means use the inline procedure below. `blocked` means stop and report the reason.
OpenCode and Hermes have no fast target, so the resolver returns `fallback` / `unsupported_host`
there. A missing lock returns `peer_unlocked`: print `npx thunderkit peers --host <host>` and use
the fallback. The resolver result is a routing decision, not evidence that an edit happened.

## Fallback

1. Read the files you will change.
2. Make the edit.
3. Run the targeted test or check.
4. Make one atomic Conventional Commit containing only this change.

No push, PR, tag or publish. Never skip the test to make the path faster.

## Output contract

```
change: <one sentence>
files: <paths>
check: <command> -> <exit code>
commit: <sha> <subject> | none (escalated)
route: delegate gsd-fast | inline (<reason_code>) | escalated -> tk-quick
```
