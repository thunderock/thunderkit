# thunderkit — Decision Log

Append-only. Newest first. Each entry: what was decided, why, what was rejected.

## 2026-09-03 — Build thunderkit as an opinionated big-repo delegation pack
- Decision: 6 skills (thunderkit router, tk-map, tk-plan, tk-execute, tk-review, tk-memory) +
  a shared model-roster reference. Plain SKILL.md, installed via `npx skills add`.
- Why: the thesis is decomposition + heterogeneity for very large repos; a router + plan +
  parallel execute + cross-family review + committed memory covers that loop.
- Rejected: (a) vendoring sshlg-skills' UX/SEO/delivery breadth — orthogonal scope; we took its
  *shape* (plain skills, docs site, validator+CI) only. (b) one mega "big-repo" skill — kills the
  per-lane model choice and parallelism the pack exists to enforce.

## 2026-09-03 — Merge tk-verify into tk-review
- Decision: one skill owns cross-family review AND the per-lane evidence/verification gate.
- Why: review and "did the verify pass" are the same quality gate; splitting them added a seam
  without adding signal.
- Rejected: a standalone tk-verify (7th skill).

## 2026-09-03 — Portable CLI dispatch for tk-execute (not the private orchestrator)
- Decision: lanes run via `claude -p --output-format json` / `codex exec --json`, each capturing
  a resumable id, each in its own git worktree.
- Why: public + portable — runs on anyone's machine, no private wiring.
- Rejected: routing lanes through the hermes kanban orchestrator (richer for one fleet, but
  couples a public pack to private infra).

## 2026-09-03 — Offer today's fleet exactly
- Decision: the roster names Fable 5.1, Opus 4.8, Opus 5, Codex Sol as the models offered per
  work type; load-bearing choices are put to the user.
- Why: concrete, working fleet the author runs; model-agnostic tiers were too abstract to be
  opinionated.
- Rejected: adding a Gemini slot now (no authed access); a fully model-agnostic roster.
