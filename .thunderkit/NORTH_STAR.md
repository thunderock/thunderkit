# thunderkit — Project North Star

The project-scoped north star for building **thunderkit itself**. (thunderkit's product thesis
lives in the repo-root `../NORTH_STAR.md`; this file is the *project memory* that `tk-memory`
maintains — dogfooding the skill on its own repo.)

## Goal

Ship and refine an opinionated, public, secrets-free skill pack that makes heterogeneous agents
(Bedrock / Claude / Codex) split big-repo work into parallel lanes and route each to its best
model — plus a static docs site generated from the skills.

## Must never break

- **Public + secrets-free.** No proprietary IP, internal endpoints, tokens, or employer/work-repo
  names. The CI leakage gate enforces this.
- **Portable skill distribution.** Installable by `npx -y skills@1.7.0 add thunderock/thunderkit`.
  The npm pointer in `bin/thunderkit.js` exposes install/list and read-only dependency display;
  it never installs or activates native peers. Each skill carries its owned support files.
- **User-selected model classes.** The planner, ordered executors and reviewers remain
  authoritative across backend changes; unavailable explicit selections block dispatch.
- **Model-id indirection.** Skills use stable catalog keys. `skills/references/models.json`
  defines IDs, families and supported harness mappings; the roster is its human reference.
- **Tests + site stay green offline.** Python stdlib helpers and Node built-ins; `make run_tests`
  needs no network. Help/version/deps need Node ≥18; install/list need Node ≥22.20.0;
  the local Python resolver/model helpers need Python ≥3.11.

## Architecture

Thunderkit owns lifecycle policy, portable project context, user choices, independent
cross-family review and completion gates. `skills/references/dependencies.json` declares
optional pinned native peers: OMO on OpenCode/Codex, OMH on Hermes. Other skill-compatible
hosts use owned procedures, not an implied native adapter.

Native installation, activation and doctor checks are separate operator actions documented in
`../DEPENDENCIES.md`. Loaded provenance, effective model bindings and safety controls must
qualify each operation before delegation. A missing peer gets a named portable fallback only
when the same model and evidence requirements can be honored; otherwise work stays blocked.
One native handoff owns its scoped workflow, with no competing Thunderkit execution loop.

## Out of scope (v1)

- UX / SEO / design / payments / telegram breadth.
- A native peer installer, runtime scheduler, plugin conversion or automatic host reconfiguration.
- Coupling lane execution to any private orchestrator.

## Done looks like

- 19 skills with relocatable owned references/helpers, all passing the frontmatter validator.
- The pinned distribution CLI resolves the repo, and the npm pointer reports dependency
  information without running peer setup or doctor commands.
- Canonical `schema_version: 2` model classes preserve selections, reviewer-family requirements
  and frozen paths; backend availability never changes them silently.
- Site builds from frontmatter and the drift gate fires on mismatch.
- CI runs tests + leakage gate + site build.
- Refined together with Ashutosh, then pushed on his go-ahead.
