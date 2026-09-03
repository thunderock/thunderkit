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

## Work type → routing

| Work type | Preferred | Also offer the user | Why |
|---|---|---|---|
| **Route / classify** (thunderkit entry) | Opus 4.8 | Opus 5 | Routing is a reasoning task; cheap to get right once. |
| **Repo recon / mapping** (tk-map) | Fable 5.1 | Opus 5 | Wide, mechanical, cost-sensitive — fan across many files. |
| **Planning / decomposition** (tk-plan) | Opus 4.8 | Opus 5 | Load-bearing: bad lanes cost the whole run. **Ask the user.** |
| **Critical-path implementation** (tk-execute lane) | Opus 4.8 → Opus 5 | Opus 5, Sol | The hardest lane wants the strongest coder. **Ask the user.** |
| **Breadth / cleanup / docs lanes** (tk-execute lane) | Fable 5.1 | Opus 5 | Parallel-wide, cost-sensitive. |
| **Review — author's family** | (the author's model) | — | For reference only; never the sole reviewer. |
| **Review — cross-family** (tk-review) | Sol + Opus 5 | Fable 5.1 | ≥2 families; at least one different from the author. |
| **Verification** (tk-review evidence half) | Fable 5.1 | lane-native | Running commands is cheap; use the cheap model. |

**Load-bearing choices (marked "Ask the user" above): thunderkit presents the preferred model
plus the "also offer" set and asks you to pick before dispatching.** Everything else auto-picks
the preferred model and just reports it.

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
