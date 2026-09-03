# thunderkit — Project North Star

The project-scoped north star for building **thunderkit itself**. (thunderkit's product thesis
lives in the repo-root `../NORTH_STAR.md`; this file is the *project memory* that `tk-memory`
maintains — dogfooding the skill on its own repo.)

## Goal

Ship and refine an opinionated, public, secrets-free skill pack that makes heterogeneous agents
(Bedrock / Claude / Codex) split big-repo work into parallel lanes and route each to its best
model — plus a static docs site generated from the skills.

## Must never break

- **Public + secrets-free.** No Adobe IP, internal endpoints, tokens, or work-repo names. The CI
  leakage gate enforces this.
- **Plain SKILL.md distribution.** Installable by `npx skills add thunderock/thunderkit`. No npm
  launcher, plugin, or hook machinery to own.
- **Model-id indirection.** Skills reference models by short name; ids live only in the roster.
- **Tests + site stay green offline.** stdlib-only; `make run_tests` needs no network.

## Out of scope (v1)

- UX / SEO / design / payments / telegram breadth.
- A one-command installer or Claude-plugin conversion.
- Coupling lane execution to any private orchestrator.

## Done looks like

- 6 skills + shared roster, all passing the frontmatter validator.
- `npx skills add` resolves the repo (verified, not assumed).
- Site builds from frontmatter and the drift gate fires on mismatch.
- CI runs tests + leakage gate + site build.
- Refined together with Ashutosh, then pushed on his go-ahead.
