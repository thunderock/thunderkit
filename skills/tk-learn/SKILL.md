---
name: tk-learn
description: "Use to learn something the fleet doesn't know yet: researches a topic online, writes a source-backed knowledge note under .thunderkit/knowledge/, and can draft a new validated tk-* skill from what was learned — so knowledge becomes reusable, not one-shot."
metadata:
  thunderkit:
    role: learner
    tier: knowledge
---

# tk-learn — gather knowledge, make it reusable

The fleet can't route work it doesn't understand. `tk-learn` closes that gap: pick a topic the
project needs (a library, an API, a pattern, a domain), research it online, and write a
**source-backed knowledge note** the rest of the pack can consume — and, when the topic is a
recurring capability, draft a new `tk-*` skill from it.

Model class: **executors** (wide, cheap — learning is breadth-first reading), with the **planner**
distilling. Every claim is source-backed; unverified claims are labelled, never asserted.

## When to reach for it

- Before planning work in an unfamiliar domain (feeds `tk-plan` better than guessing).
- When `tk-grill`/`tk-ask` return `unknown` on something the *project* should know — a `learn`-mode
  grill routes the unknown here instead of to the user.
- When a workflow keeps recurring by hand — learn it once, draft a skill, stop re-deriving it.

## Procedure

1. **Frame the question** (use `tk-grill --learn`): what exactly to learn, from which kinds of
   sources, and how a claim will be verified. One learning goal per note.
2. **Research in parallel** — fan wide across sources (docs, specs, reference implementations,
   primary sources over blog posts). Each finding carries its source URL and a confidence.
3. **Distill** — the planner consolidates findings into a knowledge note: what's true, the
   evidence, the contradictions (kept, not averaged), and the residual unknowns.
4. **Optionally draft a skill** — if the topic is a reusable capability, write
   `skills/tk-<name>/SKILL.md` from the note, then **validate it**
   (`python3 tests/validate_frontmatter.py`) and rebuild the site drift gate. Never auto-commit a
   drafted skill — surface it for review first.

## Output — `.thunderkit/knowledge/<slug>.md`

```
# <topic>
learned_at: YYYY-MM-DD · confidence: high|medium|low
## What's true (each line cites a source)
## Contradictions / open questions
## Sources
```

Committed, so the knowledge travels with the repo (same rule as the north star). A drafted skill,
if any, lands as a separate reviewable change.

## Discipline

- **Source or it didn't happen.** A claim without a citation is `unverified`, not a fact.
- **Primary over secondary.** Prefer official docs / specs / source to blog summaries.
- **Learning is read-only** — `tk-learn` gathers and drafts; it never edits production code. A
  drafted skill is a proposal that must pass the validator and your review before it ships.
