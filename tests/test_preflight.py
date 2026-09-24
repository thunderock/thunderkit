from __future__ import annotations

import json
import unittest

from preflight_fixtures import PreflightFixture, Stub, UUID, claude, codex, config, jsonl, mapping, write_json


class BaselineTests(PreflightFixture):
    def test_help_when_requested_starts_no_model(self) -> None:
        # Given no installed model executable.
        # When requesting usage.
        result = self.cli(["--help"])
        # Then help succeeds without launching a model.
        self.assertEqual((result.returncode, self.launches()), (0, []))

    def test_missing_executable_when_selected_fails(self) -> None:
        # Given an explicit model with no executable on PATH.
        # When probing it.
        result = self.cli()
        # Then absence is reported rather than success.
        self.assertEqual(result.returncode, 1)
        self.assertIn("not-installed", result.stdout)
        self.assertEqual(self.launches(), [])

    def test_claude_when_answered_records_model_and_session(self) -> None:
        # Given a complete output-bearing Claude result.
        self.stub("claude", Stub(json.dumps(claude())))
        # When the selected model answers.
        result = self.cli(["--json"])
        # Then its successful individual outcome and genuine session survive.
        report = mapping(json.JSONDecoder().raw_decode(result.stdout)[0])
        row = mapping(mapping(report["models"])["opus48"])
        self.assertEqual((row["status"], row["harness"]), ("reachable", "claude"))
        self.assertIn(UUID, result.stdout)

    def test_claude_when_error_flagged_cannot_succeed(self) -> None:
        # Given a pong carrying an explicit API failure.
        self.stub("claude", Stub(json.dumps(dict(claude(), is_error=True))))
        # When the process exits zero.
        result = self.cli(["--json"])
        # Then the error still defeats the text.
        report = mapping(json.JSONDecoder().raw_decode(result.stdout)[0])
        self.assertEqual(mapping(mapping(report["models"])["opus48"])["status"], "unreachable")
        self.assertEqual(result.returncode, 1)

    def test_explicit_classes_when_overlapping_preserve_order_and_source(self) -> None:
        # Given ordered, overlapping selections.
        selected = {"planner": "sol", "executors": ["opus48", "fable51"], "reviewers": ["opus48", "sol"]}
        write_json(self.config, dict(config(), classes=selected))
        before = self.config.read_bytes()
        self.fleet()
        # When probing each distinct choice.
        result = self.cli(["--json"])
        # Then first-use order, class choices and the saved configuration survive.
        report = mapping(json.JSONDecoder().raw_decode(result.stdout)[0])
        self.assertEqual(list(mapping(report["models"])), ["sol", "opus48", "fable51"])
        self.assertEqual(report["classes"], selected)
        self.assertEqual(self.config.read_bytes(), before)

    def test_codex_when_completed_preserves_thread_id(self) -> None:
        # Given the documented persisted-thread events.
        write_json(self.config, {"classes": {"planner": "sol", "executors": ["sol"], "reviewers": ["sol"]}})
        self.stub("codex", Stub(jsonl(codex())))
        # When probing the thread.
        result = self.cli(["--json"])
        # Then the genuine identifier is available without inspecting histories.
        self.assertIn(UUID, result.stdout)
        self.assertEqual(len(self.launches()), 1)


if __name__ == "__main__":
    unittest.main()
