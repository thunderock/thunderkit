# Model Roster

**The single source of truth for which model runs which kind of work.** Every thunderkit skill
reads this file instead of hardcoding a model id inline, so when a model id changes (they do —
ids move faster than skills), you update one table here and the whole pack follows.

Model ids below are **public** provider ids only. thunderkit ships no private endpoints,
tokens, or org-internal routing.

## The fleet (today)

| Short name | Config key | Provider id | Harness(es) | Auth | Character |
|---|---|---|---|---|---|
| **Fable 5.1** | `fable51` | `us.anthropic.claude-fable-5-1` (Bedrock) | hermes, opencode | Bedrock bearer token (login-free) | Fast, cheap, wide. Breadth, retrieval, cleanup, exploration. |
| **Opus 4.8** | `opus48` | `claude-opus-4-8` (Anthropic) | claude, hermes | Anthropic login | Strongest coder on the critical path. |
| **Opus 5** | `opus5` | `us.anthropic.claude-opus-5` (Bedrock) | hermes, opencode | Bedrock bearer token (login-free) | Strong, login-free. Critical-path fallback + a strong second reviewer. |
| **Sol** | `sol` | `gpt-5.6-sol` (OpenAI/Codex) | codex | Codex/ChatGPT login | Different family. The cross-family reviewer. Best-effort (credit-capped). |

`Config key` is what `.thunderkit/config.json` stores; it is stable across provider renames.

> If a model isn't authenticated on this machine, the skill using it must degrade to an
> available one and **say so** — never fail silently, never invent a result.

## The three model classes (what tk-router asks for)

Every run picks three classes. `tk-router` asks once per project and stores them in
`.thunderkit/config.json`:

| Class | Cardinality | Role | Default |
|---|---|---|---|
| **Planner** | exactly one — the most capable model | spec, discuss, plan, debug-reasoning | `opus48` (→ `opus5` without Anthropic login) |
| **Executors** | a set — lanes spread by weight | map, research, implement, docs-write | `opus48 opus5 fable51` |
| **Reviewers + verifiers** | all authed families | plan-check, review, verify, UAT, audit, docs-verify | `all` |

The planner is one best brain (planning is a single point of failure); executors are many hands
matched to lane weight (throughput); reviewers are every family (blind-spot coverage). A model
appears in more than one class — the strongest model plans *and* takes the heaviest execution
lane *and* reviews.

## Work type → routing

| Work type | Class | Preferred within class | Why |
|---|---|---|---|
| **Route / classify** (tk-router) | planner | Opus 4.8 | Routing is reasoning; get it right once. |
| **Spec / discuss / plan** (tk-spec, tk-discuss, tk-plan) | planner | Opus 4.8 → Opus 5 | Load-bearing; one best brain. |
| **Repo recon / research** (tk-map, tk-research) | executors | Fable 5.1 | Wide, mechanical, cost-sensitive — fan out. |
| **Critical-path implementation** (tk-execute) | executors | Opus 4.8 → Opus 5 | The hardest lane wants the strongest coder. |
| **Breadth / cleanup / docs write** (tk-execute, tk-docs) | executors | Fable 5.1 | Parallel-wide, cost-sensitive. |
| **Plan-check / review / verify / UAT / audit** (tk-review, tk-verify-work, tk-audit) | reviewers | Sol + Opus 5 | ≥2 families; at least one ≠ author. |
| **Verification commands** (tk-review evidence half) | reviewers | Fable 5.1 | Running commands is cheap. |

**tk-router asks the user for the three classes before dispatching**, then auto-assigns each work
type to its class and reports the pick. A model that isn't authed degrades to an available one,
named — never silently swapped.

## Portable dispatch reference

thunderkit runs lanes via portable CLI dispatch (no private orchestrator). Each dispatch must
capture a **resumable id** so a stalled lane can be steered or resumed:

| Harness | One-shot dispatch (JSON) | Resumable id | Resume |
|---|---|---|---|
| **Claude Code** | `claude -p "<prompt>" --output-format json --permission-mode acceptEdits` | `.session_id` from the JSON result | `claude -p --resume <session_id>` |
| **Codex** | `codex exec --json "<prompt>" --skip-git-repo-check` | `.thread_id` from the JSON stream | `codex exec resume <thread_id> --skip-git-repo-check` |
| **hermes** | `hermes chat -q "<prompt>" --oneshot -m <model> --provider <provider>` | session id from `--pass-session-id` | `hermes chat --resume <id>` |
| **opencode** | `opencode run "<prompt>" -m <provider>/<model>` | (per opencode session) | (per opencode) |

Grant the executor every permission the lane needs **on the dispatch command** (Claude:
`--permission-mode acceptEdits` or explicit `--allowedTools`; Codex: sandbox/approval flags) —
a permission denial in a non-interactive run repeats identically on retry. Prove the grant with
a scratch-edit probe before the real dispatch on a fresh machine.

## Updating this file

When a provider renames a model, change the id in **The fleet** table only. Skills reference
models by short name ("Fable 5.1", "Opus 4.8") and resolve ids here, so no skill body needs to
change. Add a row to the project decision log (`.thunderkit/DECISIONS.md`) noting the swap.
