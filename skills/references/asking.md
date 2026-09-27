# Asking the user

Every Thunderkit skill that needs a decision from the user asks through the host's structured
choice tool, so the user picks an answer instead of typing one. This file is the shared rule;
skills link to it rather than restating it.

## Host tools

| Host | Structured choice tool |
|---|---|
| Hermes | `clarify` |
| Claude Code | `AskUserQuestion` |
| Copilot CLI | `ask_user` |
| OpenCode, Codex, others | none verified; use the numbered-list fallback |

A tool name in this table is a routing hint, not proof the tool is loaded. If the call fails or
the tool is absent, use the fallback; never invent a tool name.

## Option rules

- One decision per question; independent questions may share one call when the tool supports it.
- Two to four real options, the recommended one first.
- Options go in the tool's choice list, never written into the question text.
- Always leave a free-text answer available; accept free text even when it matches no option.
- Model choices list catalog keys from `references/models.json`, never invented keys.

## Numbered-list fallback

Only when the host has no structured choice tool: ask the question in one sentence, then a
numbered list of the same options with the recommended one first, ending with
`N) Something else - type your answer`. A reply by number, by option text or in free text is valid.

Non-interactive runs (no user present) never block on a question: record the decision as
`unknown` and stop at the first gate that needs it.
