---
name: tk-ask
description: "Use when you need a harness or model to answer in a very limited set of simple words: enforces yes/no, one-word, number, or path answers with a hard word cap, so answers are checkable and cannot hide uncertainty in prose."
metadata:
  thunderkit:
    role: answer-discipline
    tier: intake
---

# tk-ask — answer in simple words, or say `unknown`

A model asked an open question returns a paragraph, and a paragraph can hide "I'm not sure" in
confident prose. `tk-ask` is the answer discipline the rest of thunderkit relies on: a question is
posed with an **allowed answer set**, and the reply must be **one item from that set** — or the
literal word `unknown`.

Use it standalone to get a checkable fact out of any harness, or as the protocol `tk-grill` and
`tk-review` apply to every question they ask.

## The five allowed answer shapes

| Shape | Allowed replies | Example |
|---|---|---|
| **bool** | `yes` `no` | "Tests exist for this file?" → `no` |
| **word** | exactly one token, ≤ 20 chars | "Language?" → `rust` |
| **number** | an integer or decimal, unit stated in the question | "LOC touched?" → `340` |
| **path** | one repo-relative path per line, nothing else | "Entry point?" → `src/main.rs` |
| **enum** | one of the options listed in the question | "Model? (opus48/opus5/sol)" → `opus5` |

Plus, always allowed: **`unknown`** — the honest answer. It is never a failure; a confident wrong
`yes` is.

## How to pose a question (the asker's side)

Every question states its shape and, for enum, its options:

```
Q: Does src/auth/ have integration tests?   [bool]
Q: Which dir owns the token refresh logic?  [path]
Q: Preferred critical-path model?           [enum: opus48 | opus5 | sol]
Q: How many dependency layers?              [number]
```

Ask several at once to a harness; ask **one at a time** to a human.

## How to answer (the harness's side — enforce this on yourself and on dispatched lanes)

1. Reply with the answer only. No preamble, no "I think", no explanation.
2. If you're below ~80% sure, reply `unknown`. Don't round up.
3. If the shape doesn't fit reality (two entry points, not one), reply `unknown` and let the asker
   re-shape — don't smuggle a list into a `word` slot.
4. Hard cap: **the whole reply is ≤ 3 words** except `path`, which is one path per line.

## Validation (the asker checks, mechanically)

- `bool` → must be exactly `yes`/`no`/`unknown`.
- `word` → one token, no spaces, ≤ 20 chars.
- `number` → parses as a number.
- `path` → each line exists in the repo (check it!) or reply was `unknown`.
- `enum` → exact match to a listed option.

An invalid reply gets **one** re-ask with the shape restated. A second invalid reply is recorded as
`unknown`. Never accept prose as an answer.

## Why so strict

Because every downstream thunderkit skill *acts* on these answers — `tk-plan` cuts lanes along the
paths, `tk-execute` picks the enum'd model, `tk-review` trusts the bool "tests exist". A paragraph
can't be acted on; `no` can. And `unknown` is the single most useful word in the pack: it's the
exact place where `tk-map`, `tk-learn`, or the user has to fill a gap before work starts.

## `unknown` routing (where a gap goes)

`unknown` is not a dead end — it's a dispatch. The asker routes each `unknown` by *what kind* of
gap it is, so no gap silently becomes an assumption:

| The `unknown` is about… | Route it to |
|---|---|
| repo structure / where something lives | `tk-map` (recon fills it) |
| external behavior / a library / a domain rule | `tk-learn` (research fills it) |
| a product decision / intent / scope | the **user** (one closed question) |

This is the contract that lets `tk-grill` interrogate a harness safely: the harness answering
`unknown` is a *feature*, because the answer is actionable — it names exactly who fills the gap.
