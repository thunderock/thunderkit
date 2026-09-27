---
name: tk-ask
description: "Use when you need a harness, a dispatched lane, or a person to answer one question in a checkable closed shape: enforces yes/no, one-word, number, path, or enum answers with a hard word cap, so an answer is either a listed value or the literal `unknown` and cannot hide uncertainty in prose."
compatibility: "Any host with a skill loader and a shell; validation is model-free and needs no project configuration, catalog access, or native peer."
metadata:
  thunderkit-role: "answer-discipline"
  thunderkit-tier: "intake"
  thunderkit-delegates: "none"
  thunderkit-contract: "1"
---

# tk-ask: answer in a closed shape, or say `unknown`

A model asked an open question returns a paragraph, and a paragraph can hide "I'm not sure" in
confident prose. `tk-ask` is the answer discipline the rest of thunderkit relies on. A question is
posed with one **requested answer shape**, and the reply must be one value that fits that shape,
or the literal word `unknown`. Nothing else counts as an answer.

Use it standalone to get a checkable fact out of any harness, or as the protocol `tk-grill` and
`tk-review` apply to every question they ask. It is a protocol, not an advisor: it never decides
what the answer should be, only whether a reply is one.

## The five answer shapes

| Shape | Allowed replies | Example |
|---|---|---|
| **bool** | `yes` `no` | "Tests exist for this file?" → `no` |
| **word** | exactly one token, at most 20 chars | "Language?" → `rust` |
| **number** | an integer or decimal; the question states the unit | "LOC touched?" → `340` |
| **path** | one repo-relative path per line, nothing else | "Entry point?" → `src/main.rs` |
| **enum** | one of the options listed in the question | "Model? (a / b / c)" → `b` |

Plus, always allowed: **`unknown`**, the honest answer. It is never a failure; a confident wrong
`yes` is. The shapes stay distinct on purpose: a `bool` is not a `word` that happens to be `yes`,
and a `path` is not an `enum` of files. Validate against the shape that was requested.

### Enum options come from the current catalog

When the enum is a model choice, list the config keys read from the catalog beside this file
(`references/models.json`, described in `references/model-roster.md`) at the moment you ask.
The examples in this document are illustrations of the shape, not a second roster. Never promote
them to options, never invent a key, and never pick a model on the answerer's behalf: an enum
question offers choices, the answer selects one, and configuring a host with that selection is a
separate, user-approved step owned by `tk-router`.

## How to pose a question (the asker's side)

Every question states its shape and, for enum, its options:

```
Q: Does src/auth/ have integration tests?   [bool]
Q: Which dir owns the token refresh logic?  [path]
Q: Planner model?                           [enum: <catalog keys>]
Q: How many dependency layers?              [number]
```

Ask several at once to a harness; ask **one at a time** to a person.

## How to answer (the answerer's side; enforce this on yourself and on dispatched lanes)

1. Reply with the answer only. No preamble, no "I think", no explanation.
2. If you're below roughly 80% sure, reply `unknown`. Don't round up.
3. If the shape doesn't fit reality (two entry points, not one), reply `unknown` and let the asker
   re-shape the question. Don't smuggle a list into a `word` slot.
4. Hard cap: the whole reply is **at most 3 words**, except `path`, which is one path per line.

## Validation (the asker checks, mechanically)

- `bool` → exactly `yes`, `no`, or `unknown`.
- `word` → one token, no spaces, at most 20 chars.
- `number` → parses as a number.
- `path` → each line exists in the repo (check it), or the reply was `unknown`.
- `enum` → exact match to a listed option, or `unknown`.

An invalid reply gets **one** re-ask with the shape restated and, for enum, the options repeated.
A second invalid reply is recorded as `unknown`, never as a best guess extracted from the prose.
Two invalid replies mean the question or the shape is wrong, and `unknown` is what sends it back
to whoever can fix that. Never accept prose as an answer.

## Why so strict

Every downstream thunderkit skill *acts* on these answers: `tk-plan` cuts lanes along the paths,
`tk-execute` binds the enum'd model, `tk-review` trusts the bool "tests exist". A paragraph can't
be acted on; `no` can. And `unknown` is the single most useful word in the pack: it marks the
exact place where evidence, or the user, has to fill a gap before work starts.

## `unknown` routing (where a gap goes)

`unknown` is not a dead end. The asker routes each one by *what kind* of gap it is, so no gap
silently becomes an assumption. Two kinds exist, and they go to different places:

| The `unknown` is about… | Kind | Route it to |
|---|---|---|
| repo structure, where something lives | discoverable fact | `tk-map` (recon fills it) |
| external behavior, a library, a domain rule | discoverable fact | `tk-learn` (research fills it) |
| a product decision, intent, scope, a preference | owner decision | the **user**, as one closed question |

A discoverable fact is settled by gathering evidence through a research skill that is actually
available and scoped to the gap. An owner decision is never researched into existence; only the
user answers it. If the sibling skill a gap should go to is not installed on this host, report
the gap as **unfilled: `tk-map` unavailable** (or `tk-learn`) and stop there. Do not invent a
dispatch, install anything, or answer the question yourself.

## Delegation

`tk-ask` delegates nothing (`thunderkit-delegates: none`). Its only operation is `validate`, and
the registry declares no native target for it, so the resolver beside this file always returns
`owned` / `owned_policy`, before reading any project configuration or capability snapshot:

```
python3 scripts/tk-resolve.py --skill tk-ask --operation validate --json
```

That call is a routing check. It proves the operation is owned; it is not evidence that any
answer was validated, and it involves no model. The shared policy for decisions and reason codes
is `references/delegation.md`; the paths above resolve from this skill's directory.

Do not substitute another skill for this protocol. An external advisor (a skill that hands the
question to a second model and returns its opinion) answers questions; `tk-ask` only checks
answers, and an advisor's confident paragraph is exactly what this protocol exists to refuse.
An interviewer that accepts free-form replies, a host's native ask tool, or a workflow framework
does not enforce shapes and is not a valid stand-in. Native evidence about such tools, whether
compatible, tampered, or missing, does not change this decision.

## Fallback

There is no native route to fall back from, so the fallback is the protocol itself, run by hand:

- No project configuration or catalog: `bool`, `word`, `number`, and `path` validate exactly as
  above. An enum that needs model keys cannot be posed; record `unknown` for it and route the gap
  to the user, who owns the catalog choice.
- No shell: validate by inspection against the rules in **Validation**; `path` existence checks
  still require a way to list the repo, otherwise record `unknown`.
- Unknown operation requested of this skill: refuse it. `tk-ask` has one operation; anything
  else belongs to a different skill and is reported as unavailable, not improvised.

The fallback never widens the accepted set, and it never converts an `unknown` into a guess.

## Asking the user

When this skill needs a decision from the user, ask through the host's structured choice tool as described in `references/asking.md`: one decision per question, two to four options with the recommended one first, free text always accepted. Use the numbered-list fallback only when the host has no such tool; in a non-interactive run record `unknown` and stop at the gate.

## Output contract

Every posed question yields exactly one record:

```
Q: <question>  [<shape>(: <options>)]
A: <valid value> | unknown
outcome: accepted | re-asked-then-accepted | unknown-after-re-ask | unknown
route: none | tk-map | tk-learn | user | unfilled: <sibling> unavailable
```

`A` is always a value that passed validation for the requested shape, or the literal `unknown`.
`outcome` records whether the re-ask was used, so a caller can see how much the answerer had to
be steered. `route` is set only when `A` is `unknown`, and names where the gap went. Nothing in
the record is prose from the answerer.
