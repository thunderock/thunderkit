---
name: tk-debug
description: "Use when a lane or verification fails and the cause isn't obvious: runs a scientific-method debug loop (symptoms, hypotheses, isolating probes, root cause, fix, regression proof) with state persisted so it survives context resets."
metadata:
  thunderkit:
    role: debug
    tier: verify
---

# tk-debug — scientific-method debugging

The parallel-thunderkit analogue of GSD's debug. When `tk-execute` or `tk-review` fails for a
reason that isn't a one-line fix, `tk-debug` runs a disciplined loop instead of guess-patching:
symptoms → hypotheses → isolating probe → root cause → fix → regression proof. State is persisted
so the investigation survives a context reset and can be resumed.

Model class: **planner** for hypotheses/root-cause reasoning; **executors** for running probes.

## The loop

1. **Symptoms** — the exact failure: command, output, expected vs actual. No paraphrase.
2. **Hypotheses** — 2–4 candidate causes, each falsifiable.
3. **Probe** — the smallest experiment that eliminates hypotheses. Run it; record the result.
4. **Root cause** — the surviving hypothesis, confirmed by a probe, not asserted.
5. **Fix** — the smallest change that addresses the root cause (not the symptom).
6. **Regression proof** — a test that fails before the fix and passes after. Paste both.

## Output — `.thunderkit/debug/<slug>.md`

Symptoms, the hypothesis ledger with each probe's result, the confirmed root cause, the fix, and
the before/after regression evidence. Resumable: re-read the file, don't restart the investigation.

## Discipline

- A root cause is *confirmed by a probe*, never assumed. "Probably the cache" is a hypothesis.
- The fix targets the cause; if you're editing the symptom's line to make it green, you haven't
  found the cause yet.
- Never weaken or delete the failing test to make it pass — a red test means fix the code.
