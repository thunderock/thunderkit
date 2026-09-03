---
name: tk-docs
description: "Use to generate or refresh project documentation after a big change: fans parallel doc-writer lanes then verifies every factual claim against the live codebase with a second model family, so docs match reality instead of intent."
metadata:
  thunderkit:
    role: docs
    tier: deliver
---

# tk-docs — parallel docs, verified against the code

The parallel-thunderkit analogue of GSD's docs-update. Documentation is a deliverable, not an
afterthought. `tk-docs` writes docs in **parallel lanes** (one per doc, disjoint) and then
**verifies every factual claim against the live codebase** with a different model family — so a
doc can't drift from the code it describes.

Model class: **executors** write; **reviewers** (a different family) verify.

## Procedure

1. **Detect** the project's doc structure (README, ARCHITECTURE, CONFIGURATION, getting-started,
   API…). Build a work manifest listing every doc as an item with a status.
2. **Write in waves** — foundational docs (no cross-refs) in wave 1, dependent docs in wave 2 —
   each doc a parallel lane. Persist the manifest so no item is lost between waves.
3. **Verify** — a reviewer-family lane checks each factual claim (a command, a path, a flag, an
   API shape) against the actual repo. A claim not discoverable in the source is marked and fixed,
   not shipped.
4. **Fix loop** — bounded: correct flagged inaccuracies, re-verify, stop when clean or the budget
   is hit (then list residual unverified claims).

## Output

Updated docs on disk, each with its factual claims verified. Run this only when behavior, setup,
commands, examples, or public claims actually changed — not every phase.

## Discipline

An infrastructure claim not discoverable from the repository gets a `VERIFY:` marker, never a
confident sentence. Docs match reality or they say they're unverified.
