---
name: tk-test
description: "Use after tk-router selects model classes, on a fresh machine, or when a fleet stalls: run the bounded CLI preflight to distinguish verified model reachability, completed but unverified replies, missing harnesses and reviewer-family failure before starting work."
compatibility: "Python 3.11+ (stdlib) and POSIX process groups. Run from the target project with explicit model selections and the intact skill-local payload. Probes need preinstalled, already configured/authenticated catalog-supported Claude, Codex, Hermes or OpenCode CLIs with the modes below; no native peer is required."
metadata:
  thunderkit-role: "preflight"
  thunderkit-tier: "intake"
  thunderkit-delegates: "none"
  thunderkit-contract: "1"
---

# tk-test — does the configured fleet actually answer?

A model-fleet smoke test, not the project's unit-test runner. Before starting work, check the
classes `tk-router` chose through their real harness CLIs with one prompt:
`Reply with exactly one word: pong`. A completed reply without serving-model identity is
**unverified**, not a pass. Configuration validity alone proves neither binding nor reachability.

## Run it

Resolve `TK_TEST_ROOT` to the absolute directory containing this loaded `SKILL.md`. Stay in the
project being checked; do not change cwd to the installed skill. Choose the needed invocation:

```sh
TK_TEST_ROOT="/absolute/path/to/installed/tk-test"
python3 "$TK_TEST_ROOT/scripts/tk-test.py"
python3 "$TK_TEST_ROOT/scripts/tk-test.py" --json
python3 "$TK_TEST_ROOT/scripts/tk-test.py" --config path/to/config.json --timeout 150 --json
python3 "$TK_TEST_ROOT/scripts/tk-test.py" --json --help
```

| Option | Contract |
|---|---|
| `--config PATH` | Defaults to `.thunderkit/config.json`, relative to the project cwd. Missing selections never choose a fleet. |
| `--timeout SECONDS` | Positive, finite number; default **120 per model**, not per fleet. Fractions are accepted. Hermes receives a rounded-up integer run budget; the process deadline remains the requested value. |
| `--json` | One JSON object on stdout; diagnostics stay on stderr. |
| `-h`, `--help` | Usage only, exit 0 with valid arguments and loadable support modules. No config read or model launch; **not readiness**. |

These are the preflight options. Do not pass the separate resolver's `--project-root` or
`--operation` flags to `tk-test.py`. Non-help invocations launch real model calls and may incur
costs; use help, not a probe, to inspect usage.

Use the installed payload's [scripts/tk-test.py](scripts/tk-test.py), its sibling
[model_config.py](scripts/model_config.py) and [preflight_protocols.py](scripts/preflight_protocols.py),
and [references/models.json](references/models.json). The script checks those local imports and
catalog rather than borrowing a parent/global copy. [config.schema.json](references/config.schema.json)
documents configuration shape; executable validation uses `model_config.py`.

## Selections and the family gate

- Validate every catalog entry/mapping and the config before launching anything. Preserve one
  planner, ordered nonempty unique executor/reviewer lists, or literal reviewers `"all"`.
  Complete recognized legacy input becomes an in-memory canonical preview with a warning;
  the source config is never rewritten. Invalid, mixed or incomplete selections fail closed.
- Resolve keys, provider/model identities, harness mappings and families from the local catalog,
  not prose labels or embedded wire IDs. Probe each distinct selected model once, in first-use
  order: planner, executors, then reviewers. `all` expands to **every catalog candidate** in
  sorted-key order, not just candidates with an installed CLI.
- For each model, use the first installed mapping in catalog order. If none is installed, the
  first mapping reports `not-installed`. A failed invocation does not retry another mapping,
  switch models, or repair host configuration.
- Planner, executors and explicitly listed reviewers are required and must verify. Under `all`,
  other reviewer candidates are optional: keep their failures visible in `models` and
  `unavailable_candidates`. They cannot count as verified reviewers. A candidate also explicitly
  selected as planner/executor remains required.
- Count distinct catalog families among **verified reviewer candidates only**, against
  `review_families_min` (default 2, validated integer at least 2). Planner/executor success does
  not supply a reviewer family unless that model is also a reviewer. The catalog's three
  Anthropic variants still constitute **one family**, regardless of provider or harness.

## Completion and identity

The script constructs these argv modes; `<probe>` is the exact prompt above and all provider/model
values come from the catalog. Installed CLI versions must support these flags and output formats;
finding a binary on PATH does not establish that support.

| Harness | Probe argv mode |
|---|---|
| Claude | `claude -p <probe> --model <model> --output-format json --tools "" --max-turns 1` |
| Codex | `codex exec --json --skip-git-repo-check --sandbox read-only -m <model> <probe>` |
| Hermes | `hermes chat -q <probe> --oneshot --format stream-json --provider <provider> -m <model> --max-turns 1 --run-budget <ceil(timeout)> --source tool` |
| OpenCode | `opencode run --format json -m <provider>/<model> <probe>` |

Only authoritative completed text whose `strip().casefold()` equals `pong` satisfies the answer
check. Surrounding whitespace and case normalize; quotes, backticks, punctuation and extra words
do not. `not pong`, `"pong"` and `pong.` fail. A partial text event, an echoed prompt or process
exit 0 alone is not a completed answer.

| Format | Required completion evidence |
|---|---|
| Claude JSON object | `type: result`, `subtype: success`, boolean `is_error: false`, no reported errors, and `result` text. Only `modelUsage` entries with positive integer `outputTokens` prove serving identity: exactly one output-bearing model must equal the requested model ID. |
| Codex JSON Lines | `thread.started`, an active `turn.started`, completed `agent_message` text from `item.completed`, then `turn.completed` with usage. Failed turns, terminal errors, rerouting or tool activity fail. Recovered errors/warnings followed by genuine completion can yield only `unverified`. |
| Hermes stream-json | `system/init` followed by a final same-session `result` with `exit_code: 0`, no error and final `text`. Init `model` is not observed serving identity. `tool_use` or `tool_result` invalidates the probe. |
| OpenCode JSON Lines | Matching session/message IDs, `step_start`, completed text with `part.time.end`, and `step_finish` with reason `stop`. Stale/incomplete text, error or tool events do not qualify. These records provide no positive serving-model identity. |

**Identity limit:** only Claude's output-bearing `modelUsage` can verify identity in these
adapters. Examined Codex, safe Hermes and OpenCode formats remain `unverified` after a genuine
completed pong. Requested/configured IDs, init fields and successful routing are not observations.
With the current catalog and adapters, native preflight cannot establish two verified families.
Do not fabricate a second family, lower the gate, substitute a model, or treat synthetic internal
aggregation as evidence of native readiness.

Claude disables tools; Codex uses its read-only sandbox. Hermes tools are **not disabled** by
this mode; neither an empty toolset flag nor an approval bypass is part of the command. Retain
upstream approval/configuration policy. Detected tool activity, nonzero process exit, terminal
error, malformed completion or model substitution cannot establish readiness.

Probes use closed stdin, an owned POSIX process group, a per-model deadline, group kill and
bounded reap. Stdout is captured temporarily and only up to 1 MiB is parsed; this is not a cap on
all bytes a child might write before its deadline. Raw replies and child stderr are not echoed.
Report safe categories, not guessed auth/quota causes or raw errors. Native CLIs can persist
their sessions; do not describe model probes as side-effect-free.

## Delegation

`tk-test` owns its sole/default operation `preflight`; [dependencies.json](references/dependencies.json)
declares no native targets. The local [scripts/tk-resolve.py](scripts/tk-resolve.py) requires an
explicit config argument for this model-bearing operation. With valid choices it reports
`owned` / `owned_policy`, or `owned` / `disabled` for `delegation: off`, with null target and
preserved requested bindings. Missing config or a wrong operation gives `blocked` /
`invalid_config`, exit 2. No capability snapshot is required for the owned route. Resolver exit 0
means a route was computed, **not** that preflight ran or the fleet passed.

Keep peer states separate: **present** means found, **loaded** means the host loaded the
source-qualified skill, **compatible** means required host/version/source/capability gates pass,
**model-bound** means effective selections match, and **verified** means returned evidence was
checked. None alone proves fleet reachability or reviewer-family readiness; these are not extra
fields in the preflight JSON. [delegation.md](references/delegation.md) defines those peer gates.

`thunderkit deps` prints dependency guidance, not installed/loaded/runtime proof. Setup and doctor
are operator actions, not model probes; doctor may write local state. With delegation off, do
not invoke peers, discovery, doctor or routing tools. Use the owned preflight procedure without
waiving its model probes or evidence gates. Never replace it with a peer's self-reported readiness.

## Fallback

- No config: stop and return the missing-choice problem to `tk-router`, which owns config-free
  bootstrap and user selection. If it is unavailable, report that prerequisite as missing;
  do not invent a default fleet.
- Missing Python/POSIX support, script or local assets: report **not run** when the CLI cannot
  start. If it starts and reports `invalid_assets`, retain that failure. Never reconstruct a
  passing report or borrow another installation's helpers/catalog.
- Missing CLI, incompatible output, timeout, failed identity or insufficient families: retain the
  actual row/status and failed gate. An optional candidate failure is not hidden; a required
  choice or family failure blocks readiness. Resume data does not override it.

There is no native substitute. Leave installation, login and configuration changes to the
operator; do not install dependencies, inspect/copy credentials, rewrite global settings,
enable bypasses or silently switch provider/model. A repaired environment needs a newly
authorized preflight, not reclassification of old evidence.

## Output contract

Consume stdout as **one object** in JSON mode, keeping stderr separate; append no human trailer.
The script's normal report has exactly these fields:

- `schema_version: 1`, `status: passed|failed`, and `reason_code`: `ready`,
  `required_models_unavailable`, or `insufficient_review_families` (required failures take priority).
- `models`: catalog-keyed records containing `harness`, `requested: {provider, model_id}`,
  `observed`, `status`, `reason_code`, `session_id`, `resumable`, and `resume`.
  `observed` is a list of catalog-known observed `{model_id, provider: null}` entries, or null.
  Unknown observed model names are withheld, but their mismatch still fails; no observed
  provider is inferred from the requested one.
- `classes`: normalized requested classes, preserving order and literal `"all"`.
  `resolved_classes` copies planner/executors unchanged and includes **only verified reviewers**;
  its planner/executor entries do not themselves certify success.
- `reviewer_candidates`, `reviewers_mode: explicit|all`, `required_failures`,
  `unavailable_candidates`, sorted `reviewer_families`, `reviewer_family_count`,
  `review_families_min`, boolean `family_gate`, and `warnings`.

| Model status | Reason categories |
|---|---|
| `reachable` | `verified` |
| `unverified` | `identity_unavailable` |
| `substituted` | `model_mismatch` |
| `unreachable` | `process_exit`, `process_error`, `unexpected_response`, `terminal_error`, `missing_completion`, `tool_activity` |
| `malformed` | `malformed`, `invalid_encoding`, `output_limit` |
| `not-installed` | `executable_missing` |
| `timeout` | `deadline_exceeded`, `cleanup_timeout` |

Invalid CLI/config/local assets return a smaller object: `schema_version: 1`, `status: invalid`,
`reason_code: invalid_cli|invalid_config|invalid_assets`, empty `models` and `classes`, empty
`reviewer_families`, and `reviewer_family_count: 0`. Do not expect normal-report-only fields there.
`--json --help` instead returns only `{"usage": "<usage text>"}`.

Session IDs come only from decoded persisted completion records and pass harness-specific syntax
checks: UUIDs for Claude/Codex, a bounded alphanumeric/underscore/hyphen ID for Hermes, and a
`ses_` prefix with bounded alphanumerics for OpenCode. Missing/unsafe IDs yield `session_id: null`,
`resumable: false`, `resume: null`. A valid ID can accompany an unverified or failed answer;
`resumable` records ID availability, not readiness or a tested continuation.

| Harness | Emitted `resume` argv, when the ID is valid |
|---|---|
| Claude | `["claude", "-p", "--resume", "<session_id>"]` |
| Codex | `["codex", "exec", "resume", "<session_id>", "--skip-git-repo-check"]` |
| Hermes | `["hermes", "chat", "--resume", "<session_id>"]` |
| OpenCode | `["opencode", "run", "-s", "<session_id>"]` |

Use the returned argv as arguments, never evaluated shell text or a guessed/latest session ID.
Continue from the same project directory, especially for OpenCode. The human report prints
classes, status/reason, requested/observed identity, available resume argv, required failures,
optional candidate failures and the family count; it does not expose raw pong text or timing.

Normal exit **0** requires a nonempty run, every explicit choice verified, and the verified
reviewer-family minimum met. Exit **1** means readiness failed; exit **2** means invalid CLI,
config or local assets. Human and JSON modes enforce identical gates. Help's exit 0 and an owned
routing/scenario success establish neither live model reachability nor workflow completion.
