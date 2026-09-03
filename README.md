# thunderkit

**Opinionated skills for working in very large codebases — by decomposing big changes into
parallel lanes and routing each to the best model across a heterogeneous agent fleet.**

> Big work in big repos is won by **decomposition + heterogeneity**, not by one smart model.

thunderkit takes a large change in a large repo and: **decomposes** it into disjoint,
dependency-layered lanes → **routes** each lane to the best model *and* harness → **runs** them
in parallel across a heterogeneous fleet (Bedrock Fable 5.1, Claude Opus, Codex Sol) → **reviews**
the result across ≥2 model families → **remembers** the project's intent as a committed artifact.

It's deliberately opinionated — see [`NORTH_STAR.md`](NORTH_STAR.md). No single-model plans.
Cross-family review always. Done means evidence, not intent. The user picks the load-bearing
models. Context is committed, not recalled.

## The skills

| Skill | What it owns |
|---|---|
| **thunderkit** | Router / entry point. Sizes the work, routes through the pipeline, reads `.thunderkit/config.json` for past selections, and **asks you to choose among the top models** only for undecided load-bearing lanes. |
| **tk-ask** | Answer discipline — a harness must answer in yes/no, one word, a number, a path, or `unknown`. No prose. |
| **tk-grill** | Closed-question intake — interrogates you *and* the harness until the brief has no unknowns → `.thunderkit/BRIEF.md`. |
| **tk-map** | Big-repo reconnaissance — a durable code map so planning works from structure, not guesses. |
| **tk-plan** | Decompose a change into **disjoint, dependency-layered lanes**, each file-scoped with acceptance criteria + a verification command. |
| **tk-execute** | Run the lanes **in parallel** via portable CLI dispatch (`claude`/`codex`), each in its own git worktree with a captured resumable session id. |
| **tk-review** | **Cross-family review + evidence gate** — fan the diff to ≥2 model families, consolidate by severity, and run every lane's verification. |
| **tk-memory** | Project north-star memory — maintain `.thunderkit/` so the opinion + decisions persist across sessions and agents. |

Shared: [`skills/references/model-roster.md`](skills/references/model-roster.md) — the single
source of truth for which model runs which work. Skills reference models by short name and
resolve ids here, so a model rename is a one-line change.

## Install — every harness, one command

thunderkit is plain [Agent Skills](https://agentskills.io) (`skills/<name>/SKILL.md`), the open
standard read natively by Claude Code, Codex, opencode, hermes, Cursor, Gemini CLI, Windsurf,
Zed, Goose, Kilo and 70+ others. Distribution is the [vercel `skills`](https://github.com/vercel-labs/skills)
CLI — the same mechanism the popular packs (sshlg-skills, vercel agent-skills, …) use:

```sh
# whole pack → every agent detected on this machine
npx skills add thunderock/thunderkit --all

# whole pack, but only for named harnesses
npx skills add thunderock/thunderkit -s '*' -g --agent claude-code codex opencode hermes-agent

# one skill
npx skills add thunderock/thunderkit -s thunderkit -g

# what's in the repo, without installing
npx skills add thunderock/thunderkit -l
```

**How that reaches every harness.** `skills add -g` writes one canonical copy to
`~/.agents/skills/<name>/` and **symlinks** it into each agent's own skills dir
(`~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`, hermes' external dirs, …).
One `npx skills update -g` refreshes all of them at once. Packs that ship an npm launcher (sshlg)
just wrap this same call with a fixed agent list; thunderkit skips the launcher and uses the CLI
directly. A fresh-machine setup script can pin it with one line:

```sh
npx -y skills add thunderock/thunderkit -s '*' -g -y --agent '*'
```

Then invoke the router by name (e.g. `thunderkit: refactor the auth layer across the monorepo`)
and it routes the rest.

## How the fleet is used

| Work | Preferred model | Why |
|---|---|---|
| Route / plan (load-bearing) | Opus 4.8 | **You're asked to choose** — bad plans cost the whole run. |
| Recon / breadth / cleanup | Fable 5.1 | Fast, cheap, login-free — fan wide. |
| Critical-path implementation | Opus 4.8 → Opus 5 | **You're asked to choose** the strongest coder. |
| Cross-family review | Sol + Opus 5 | A different family than the author catches blind spots. |

If a model isn't authed on your machine, the skill degrades to what's available and **tells you**
what to install — it never fakes a result. Details in the roster.

## Project memory — `.thunderkit/`

Every project keeps its own north star and decision log, committed to the repo:

```
.thunderkit/
  NORTH_STAR.md   # this project's goals, constraints, non-negotiables
  DECISIONS.md    # append-only: what was decided, why, what was rejected
  config.json     # selections the router reuses: models per role, review families, layers
  BRIEF.md        # tk-grill intake (closed answers, no unknowns)
  MAP.md          # code map (tk-map)
  PLAN.md/.json   # current decomposition (tk-plan)
  REVIEW.md       # latest cross-family review (tk-review)
```

Any agent that opens the repo inherits the project's opinion — so heterogeneous agents stay
aligned without re-litigating settled choices.

## Development

```sh
make run_tests   # frontmatter validator + roster/leakage checks + site drift gate
make lint        # py_compile + shellcheck (best-effort)
make site        # regenerate the static docs site → site/_site
```

Everything is stdlib-only Python — `make run_tests` works offline on a fresh checkout. CI runs
the tests, a secrets/leakage denylist grep, and the site build on every push.

## License

MIT © Ashutosh Tiwari. Public, secrets-free, no proprietary content.
