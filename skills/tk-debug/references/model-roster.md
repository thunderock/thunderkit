# Model Roster

[`models.json`](models.json) is the source of truth for model data; this roster is its human
reference. Skills resolve user-selected keys through the catalog rather than hardcoding IDs.

Model ids below are **public** provider ids only. thunderkit ships no private endpoints,
tokens, or org-internal routing.

## Machine-readable contracts

[`models.json`](models.json) is the machine-readable source of truth for model keys, provider
ids, portable harness mappings, families, and class cardinalities. [`config.schema.json`](config.schema.json)
defines the canonical project configuration and its recognized legacy mapping. The four provider
ids below must match `models.json` byte-for-byte; keep the catalog and this human roster synchronized.

Menus list catalog entries and annotate observed local availability, using `unknown` when not
probed. Listing choices requires no paid call and selects nothing. Use only documented harness
mappings; the catalog implies no undocumented effort choices.

## The fleet (today)

| Short name | Config key | Provider id | Harness(es) | Auth | Character |
|---|---|---|---|---|---|
| **Fable 5.1** | `fable51` | `us.anthropic.claude-fable-5-1` (Bedrock) | hermes, opencode | Bedrock bearer token (login-free) | Fast, cheap, wide. Breadth, retrieval, cleanup, exploration. |
| **Opus 4.8** | `opus48` | `claude-opus-4-8` (Anthropic) | claude, hermes | Anthropic login | Strongest coder on the critical path. |
| **Opus 5** | `opus5` | `us.anthropic.claude-opus-5` (Bedrock) | hermes, opencode | Bedrock bearer token (login-free) | Strong, login-free. Critical-path fallback + a strong second reviewer. |
| **Sol** | `sol` | `gpt-5.6-sol` (OpenAI/Codex) | codex | Codex/ChatGPT login | Different family. The cross-family reviewer. Best-effort (credit-capped). |

`Config key` is what `.thunderkit/config.json` stores; it is stable across provider renames.

> An unavailable explicitly selected model blocks dispatch. Report the failure and ask the user
> to choose another model; naming an automatic replacement does not make it an approved choice.

## The three model classes (what tk-router asks for)

Every run picks three classes. `tk-router` asks once per project and stores them in
`.thunderkit/config.json`:

| Class | Cardinality | Role | Example choice (requires confirmation) |
|---|---|---|---|
| **Planner** | exactly one catalog key | spec, discuss, plan, debug-reasoning | `opus48` |
| **Executors** | nonempty unique array of catalog keys | map, research, implement, docs-write | `["opus48", "opus5", "fable51"]` |
| **Reviewers + verifiers** | `"all"` or a nonempty unique array of catalog keys | plan-check, review, verify, UAT, audit, docs-verify | `"all"` |

The planner is one best brain (planning is a single point of failure); executors are many hands
matched to lane weight (throughput); reviewers are every family (blind-spot coverage). A model
appears in more than one class — the strongest model plans *and* takes the heaviest execution
lane *and* reviews.

All three classes are required choices, not reader defaults. A blank or partial configuration
cannot pass by inheriting the examples above. `reviewers: "all"` considers **every catalog
model**, including models not selected as planner or executor. Preflight reports unavailable
optional candidates and forms the reviewer set from successful responses. Explicit selections
must all succeed, and the reachable reviewer set must independently meet `review_families_min`.
`opus48`, `opus5`, and `fable51` are one `anthropic` family; `sol` is `openai`.

## Configuration readers and legacy previews

Canonical writes use `schema_version: 2` and `classes.planner/executors/reviewers`. Existing
`classes` configurations may omit the version. Only missing operational fields receive these
defaults **in memory**, without changing the file or replacing an explicit value:

| Field | Default when missing | Constraint |
|---|---|---|
| `schema_version` | `2` | Explicit canonical version must be integer `2` |
| `review_families_min` | `2` | Integer ≥2; booleans and floats are invalid |
| `max_layers` | `3` | Integer ≥1; booleans and floats are invalid |
| `frozen_paths` | `[]` | Literal repository-relative POSIX paths |
| `ecosystems` | `["omo", "omh", "gsd"]` | Unique list of `omo`, `omh` and/or `gsd`; `[]` disables all |
| `delegation` | `"auto"` | `"auto"` or `"off"` |

`decided_at` is optional for readers. Preserve a supplied string, including an empty string;
never fabricate a date or placeholder. The choice writer records the user's decision date.

Only a complete, valid legacy `models` object is recognized:

| Legacy field | Normalized field |
|---|---|
| `models.plan` | `classes.planner` (one catalog key) |
| `models.critical_path` | `classes.executors` (wrap the one catalog key in an array) |
| `models.review` | `classes.reviewers` (retain `"all"` or a nonempty unique catalog-key array) |

Legacy input may omit `schema_version` or specify integer `1` or `2`. Preserve known operational
fields and any supplied `decided_at`, and return a migration warning with the normalized preview.
Saving the preview requires the user's normal config-write approval; reading it never saves it.

Reject mixed `models`/`classes`, incomplete roles, unknown model keys, malformed choices, and
unknown object keys at the root or inside `classes`/legacy `models`. `critical_model` and
`review_families` are not supported aliases. Reject duplicate JSON keys before constructing
dictionaries, and reject non-finite numbers (`NaN`, `Infinity`, `-Infinity`, or numeric overflow).
Reject repeated model keys in class arrays and duplicate ecosystems rather than deduplicating.

Frozen paths must be nonempty and use forward slashes. Reject absolute paths, Windows drive
prefixes, any `..` segment, backslashes anywhere, and ASCII controls (U+0000–U+001F or U+007F).
Do not expand `~` or environment variables. `src/config.json`, `src/my file.py`, `.`, and `./src`
are relative paths; `src/../outside`, `C:relative`, and `src\config.json` are invalid.
Invalid input must produce an actionable error before any subprocess starts.

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

**tk-router asks the user for the three classes before dispatching**, then assigns each work
type within its selected class and reports the pick. The preferences above never override a
user's selections or authorize substitution when a selected model is unavailable.

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

When a provider renames a model, update its ID and documented harness mappings in `models.json`,
then synchronize **The fleet** table. Keep its config key stable so existing user choices are
preserved. Add a row to the project decision log (`.thunderkit/DECISIONS.md`) noting the swap.
