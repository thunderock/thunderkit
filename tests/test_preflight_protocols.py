from __future__ import annotations

import json
from typing import Final
import unittest

from preflight_fixtures import (HERMES_ID, OPEN_ID, UUID, JsonObject, PreflightFixture,
                               claude, codex, hermes, jsonl, mapping, opencode)

IDENTITIES: Final = {
    "claude": ("anthropic", "claude-opus-4-8"), "codex": ("openai-codex", "gpt-5.6-sol"),
    "hermes": ("bedrock", "us.anthropic.claude-fable-5-1"),
    "opencode": ("amazon-bedrock", "us.anthropic.claude-fable-5-1"),
}


class ProtocolTests(PreflightFixture):
    def setUp(self) -> None:
        super().setUp()
        self.assertTrue((self.skill / "scripts/preflight_protocols.py").is_file(), "protocol adapter must exist")
        self.api = self.load_script("preflight_protocols.py")
        self.wires = {name: self.api.Wire(self.api.Harness(name), *identity)
                      for name, identity in IDENTITIES.items()}

    def test_real_format_when_complete_keeps_identity_evidence_separate(self) -> None:
        # Given documented complete records, not invented serving-model fields.
        cases = [("claude", json.dumps(claude()), "reachable", UUID, ("claude-opus-4-8",)),
                 ("codex", jsonl(codex()), "unverified", UUID, ()),
                 ("hermes", jsonl(hermes()), "unverified", HERMES_ID, ()),
                 ("opencode", jsonl(opencode()), "unverified", OPEN_ID, ())]
        for name, output, status, session, observed in cases:
            with self.subTest(harness=name):
                # When parsing the authoritative completion.
                result = self.api.decode(self.wires[name], output)
                # Then only Claude has positive observed identity.
                self.assertEqual((result.status, result.session_id, result.observed_models), (status, session, observed))

    def test_exact_response_when_trimmed_and_casefolded_is_required(self) -> None:
        for answer in (" \tPoNG\n", "not pong", '"pong"', "`pong`", "pong.", "pong!", "pong pong", "Reply: pong"):
            cases = {"claude": json.dumps(claude(answer)), "codex": jsonl(codex(answer)),
                     "hermes": jsonl(hermes(answer)), "opencode": jsonl(opencode(answer))}
            for name, output in cases.items():
                with self.subTest(harness=name, answer=answer):
                    # Given exact or misleading final text.
                    # When normalizing the final response.
                    result = self.api.decode(self.wires[name], output)
                    # Then only whitespace and case are ignored.
                    expected = "reachable" if name == "claude" else "unverified"
                    self.assertEqual(result.status, expected if answer == " \tPoNG\n" else "unreachable")

    def test_claude_when_usage_is_wrong_multiple_absent_or_invalid(self) -> None:
        cases: list[tuple[JsonObject, str]] = [
            ({}, "unverified"), ({"claude-opus-4-8": {"outputTokens": 0}}, "unverified"),
            ({"another-model": {"outputTokens": 1}}, "substituted"),
            ({"claude-opus-4-8": {"outputTokens": 1}, "another-model": {"outputTokens": 1}}, "substituted"),
            ({"claude-opus-4-8": {"outputTokens": 1}, "another-model": {"outputTokens": 0}}, "reachable"),
            ({"claude-opus-4-8": {"outputTokens": True}}, "malformed"),
            ({"claude-opus-4-8": {"outputTokens": "1"}}, "malformed"),
        ]
        for usage, expected in cases:
            with self.subTest(usage=usage):
                # Given output-bearing usage, not the requested model setting.
                output = dict(claude(), modelUsage=usage)
                # When checking model identity.
                result = self.api.decode(self.wires["claude"], json.dumps(output))
                # Then only a single matching serving model verifies.
                self.assertEqual(result.status, expected)
        record = claude()
        record.pop("modelUsage")
        self.assertEqual(self.api.decode(self.wires["claude"], json.dumps(record)).status, "unverified")

    def test_terminal_failure_when_pong_follows_never_recovers(self) -> None:
        failures: list[tuple[str, str]] = [
            ("claude", json.dumps(dict(claude(), is_error=True))),
            ("claude", json.dumps(dict(claude(), subtype="error_max_turns"))),
            ("claude", json.dumps(dict(claude(), errors=["failed"]))),
            ("codex", jsonl([*codex()[:2], {"type": "turn.failed", "error": {"message": "failed"}}, *codex()[2:]])),
            ("codex", jsonl([*codex(), {"type": "error", "message": "failed"}])),
            ("hermes", jsonl([*hermes()[:-1], dict(hermes()[-1], exit_code=1)])),
            ("hermes", jsonl([*hermes()[:-1], dict(hermes()[-1], error="failed")])),
            ("opencode", jsonl([{"type": "error", "sessionID": OPEN_ID, "error": {"name": "APIError"}}, *opencode()])),
        ]
        for name, output in failures:
            with self.subTest(harness=name, output=output):
                # Given a genuine terminal failure, regardless of process status.
                # When a completion also contains pong.
                result = self.api.decode(self.wires[name], output)
                # Then text cannot rescue that terminal failure.
                self.assertEqual((result.status, result.reason_code), ("unreachable", "terminal_error"))

    def test_codex_when_retry_and_warning_recover_is_unverified_not_failed(self) -> None:
        # Given retryable transport output and a warning in exec's error-item shape.
        events: list[JsonObject] = [*codex()[:2], {"type": "error", "message": "reconnecting"},
                  {"type": "item.completed", "item": {"id": "warning", "type": "error", "message": "deprecated"}},
                  *codex()[2:]]
        # When the real terminal event completes successfully.
        result = self.api.decode(self.wires["codex"], jsonl(events))
        # Then the attempt recovered, but model identity is still unavailable.
        self.assertEqual((result.status, result.observed_models), ("unverified", ()))

    def test_codex_when_rerouted_cannot_count_the_requested_model(self) -> None:
        # Given the documented reroute representation, including a successful ending.
        events: list[JsonObject] = [*codex()[:2], {"type": "item.completed", "item": {"id": "notice", "type": "error",
                  "message": "model rerouted: gpt-5.6-sol -> another-model (HighRiskCyberActivity)"}}, *codex()[2:]]
        # When interpreting the turn.
        result = self.api.decode(self.wires["codex"], jsonl(events))
        # Then substitution is not success for Sol.
        self.assertEqual(result.status, "substituted")

    def test_incomplete_or_stale_when_present_cannot_supply_final_text(self) -> None:
        cases = [("codex", jsonl(codex()[:-1])), ("codex", jsonl(codex()[:2] + codex()[3:])),
                 ("codex", jsonl(codex() + [{"type": "turn.started"}, {"type": "turn.completed", "usage": {}}])),
                 ("hermes", jsonl(hermes()[:-1])), ("hermes", jsonl(hermes() + hermes()[:1])),
                 ("opencode", jsonl(opencode()[:-1])),
                 ("opencode", jsonl([opencode()[0], opencode()[-1]]))]
        for name, output in cases:
            with self.subTest(harness=name, output=output):
                # Given no current completed answer.
                # When prior, partial or absent text is available.
                result = self.api.decode(self.wires[name], output)
                # Then no successful model evidence is returned.
                self.assertNotIn(result.status, ("reachable", "unverified"))

    def test_tool_output_when_pong_is_not_an_answer(self) -> None:
        cases = [("codex", jsonl([*codex()[:2], {"type": "item.completed", "item": {
                     "id": "tool", "type": "command_execution", "aggregated_output": "pong"}}, *codex()[2:]])),
                 ("hermes", jsonl([*hermes()[:1], {"type": "tool_result", "output": "pong"}, *hermes()[1:]])),
                 ("opencode", jsonl([opencode()[0], {"type": "tool_use", "sessionID": OPEN_ID}, *opencode()[1:]]))]
        for name, output in cases:
            with self.subTest(harness=name):
                # Given tool activity, even with later pong text.
                # When evaluating this no-tool probe.
                result = self.api.decode(self.wires[name], output)
                # Then tool output does not establish readiness.
                self.assertEqual((result.status, result.reason_code), ("unreachable", "tool_activity"))

    def test_framing_when_malformed_is_rejected(self) -> None:
        for name, wire in self.wires.items():
            for output in ("", "[]", "null", "{}", '{"type":"result","type":"result"}',
                           '{"type":NaN}', '{"type":1e999}', '"pong"', "not json\n", "{\"type\": []}"):
                with self.subTest(harness=name, output=output):
                    # Given invalid framing or typed envelopes.
                    # When parsing stdout.
                    result = self.api.decode(wire, output)
                    # Then the invalid record cannot pass.
                    self.assertEqual(result.status, "malformed")

    def test_bound_fields_when_wrongly_typed_or_mismatched_are_rejected(self) -> None:
        records = opencode()
        mapping(records[1]["part"])["messageID"] = "msg_stale"
        cases = [("claude", json.dumps(dict(claude(), result=["pong"]))),
                 ("claude", json.dumps(dict(claude(), is_error=0))),
                 ("codex", jsonl([dict(codex()[0], thread_id=[]), *codex()[1:]])),
                 ("hermes", jsonl([*hermes()[:-1], dict(hermes()[-1], exit_code=False)])),
                 ("hermes", jsonl([*hermes()[:-1], dict(hermes()[-1], session_id="other-session")])),
                 ("opencode", jsonl(records)),
                 ("opencode", jsonl([opencode()[0], dict(opencode()[1], sessionID="ses_other"), opencode()[2]]))]
        for name, output in cases:
            with self.subTest(harness=name, output=output):
                # Given invalid proof-bearing fields or a foreign turn.
                # When decoding that response.
                result = self.api.decode(self.wires[name], output)
                # Then typed/binding errors fail closed.
                self.assertEqual(result.status, "malformed")

    def test_session_when_unsafe_is_not_resumable(self) -> None:
        for value in (None, "", "$(touch stolen)", "--resume=other", "id\ncommand", "https://secret.invalid/token"):
            with self.subTest(session=value):
                # Given a successful result with no safe persisted identifier.
                output = dict(claude(), session_id=value)
                # When extracting resumability.
                result = self.api.decode(self.wires["claude"], json.dumps(output))
                # Then no unvalidated shell argument is offered.
                self.assertIsNone(result.session_id)

    def test_command_when_built_uses_safe_documented_selectors(self) -> None:
        for name, wire in self.wires.items():
            with self.subTest(harness=name):
                # Given one catalog wire identity, with no effort override.
                # When constructing the invocation.
                argv = self.api.command(wire, 12.0)
                # Then permission bypasses, configuration changes and invented selectors are absent.
                self.assertEqual(argv[0], name)
                forbidden = {"-z", "--auto", "-t", "--variant", "--fallback-model", "--bare", "-c",
                             "--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox",
                             "login", "install", "--ignore-user-config", "--safe-mode", "app-server"}
                self.assertFalse(forbidden.intersection(argv))
                selector = f"{wire.provider}/{wire.model_id}" if name == "opencode" else wire.model_id
                self.assertIn(selector, argv)
                if name == "claude":
                    self.assertEqual(argv[argv.index("--tools") + 1], "")
                if name == "codex":
                    self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
                if name == "hermes":
                    self.assertEqual(argv[argv.index("--format") + 1], "stream-json")


if __name__ == "__main__":
    unittest.main()
