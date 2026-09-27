from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from payload_fixtures import snapshot
from preflight_fixtures import (INVALID_CONFIGS, SENSITIVE, JsonObject, PreflightFixture, Stub, UUID,
                               claude, codex, config, jsonl, mapping, write_json)


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
        selected: JsonObject = {"planner": "sol", "executors": ["opus48", "fable51"], "reviewers": ["opus48", "sol"]}
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


class RegressionTests(PreflightFixture):
    def test_json_when_probed_is_one_complete_document(self) -> None:
        # Given a valid result.
        self.stub("claude", Stub(json.dumps(claude())))
        # When requesting JSON.
        result = self.cli(["--json"])
        # Then stdout contains one object, without a human trailer.
        self.assertIsInstance(json.loads(result.stdout), dict)

    def test_family_gate_when_only_one_family_answers_fails(self) -> None:
        # Given only an Anthropic reviewer.
        self.stub("claude", Stub(json.dumps(claude())))
        # When every selected model answers pong.
        result = self.cli()
        # Then cross-family readiness still fails.
        self.assertEqual(result.returncode, 1)

    def test_all_when_selected_probes_the_whole_catalog(self) -> None:
        # Given all-reviewer mode with one explicit model.
        write_json(self.config, config())
        self.fleet()
        # When expanding reviewers.
        result = self.cli(["--json"])
        # Then every candidate is probed, in stable first-use order.
        report = mapping(json.JSONDecoder().raw_decode(result.stdout)[0])
        self.assertEqual(list(mapping(report["models"])), ["opus48", "fable51", "opus5", "sol"])

    def test_exact_text_when_negated_is_rejected(self) -> None:
        # Given a completed response containing, but not equal to, pong.
        self.stub("claude", Stub(json.dumps(claude("not pong"))))
        # When checking the response.
        result = self.cli(["--json"])
        # Then substring matching cannot establish readiness.
        report = mapping(json.JSONDecoder().raw_decode(result.stdout)[0])
        self.assertEqual(mapping(mapping(report["models"])["opus48"])["status"], "unreachable")

    def test_blank_config_when_loaded_cannot_pass_without_probes(self) -> None:
        # Given an empty document.
        write_json(self.config, {})
        # When validating selections.
        result = self.cli()
        # Then it is an input failure, not a zero-model success.
        self.assertEqual((result.returncode, self.launches()), (2, []))


class PreflightTests(PreflightFixture):
    def test_invalid_inputs_when_json_requested_never_launch(self) -> None:
        self.fleet()
        for content in INVALID_CONFIGS:
            with self.subTest(config=content):
                # Given invalid JSON or invalid model classes.
                self.config.write_text(content)
                # When invoking the real CLI.
                result = self.cli(["--json"])
                # Then one safe failure is returned before any process starts.
                self.assertEqual((result.returncode, self.launches()), (2, []))
                self.assertEqual(mapping(json.loads(result.stdout))["status"], "invalid")
                self.assertTrue(result.stderr)

    def test_invalid_arguments_and_paths_when_json_requested_are_machine_readable(self) -> None:
        for args in (["--unknown", SENSITIVE], ["--timeout", "0"], ["--timeout", "-1"], ["--timeout", "nan"],
                     ["--timeout", "inf"], ["--timeout", "wrong"], ["--config"], ["--config", str(self.sandbox)],
                     ["--config", str(self.sandbox / "missing")]):
            with self.subTest(arguments=args):
                # Given an invalid invocation or nonregular input.
                # When asking for JSON even on failure.
                result = self.cli(["--json", *args])
                # Then usage diagnostics cannot corrupt stdout or echo input.
                self.assertEqual((result.returncode, self.launches()), (2, []))
                self.assertEqual(mapping(json.loads(result.stdout))["status"], "invalid")
                self.assertNotIn(SENSITIVE, result.stdout + result.stderr)

    def test_all_when_optional_candidates_unavailable_reports_them_separately(self) -> None:
        # Given one installed verified model and three unavailable optional candidates.
        write_json(self.config, config())
        self.stub("claude", Stub(json.dumps(claude())))
        # When the fleet is probed.
        result = self.cli(["--json", "--timeout", "2"])
        # Then optional failures are separate, but the family gate still fails.
        report = mapping(json.loads(result.stdout))
        self.assertEqual((result.returncode, report["required_failures"], report["reviewer_family_count"]), (1, [], 1))
        self.assertEqual(report["unavailable_candidates"], ["fable51", "opus5", "sol"])
        self.assertEqual(mapping(report["resolved_classes"])["reviewers"], ["opus48"])

    def test_real_format_fleet_when_all_answer_cannot_claim_two_verified_families(self) -> None:
        # Given the actual-format adapters, with no fictional model telemetry.
        write_json(self.config, config())
        self.fleet()
        # When every CLI answers pong.
        result = self.cli(["--json"])
        # Then only the Claude evidence contributes a family.
        report = mapping(json.loads(result.stdout))
        self.assertEqual((result.returncode, report["family_gate"], report["reviewer_families"]), (1, False, ["anthropic"]))
        for key in ("sol", "opus5", "fable51"):
            row = mapping(mapping(report["models"])[key])
            self.assertEqual((row["status"], row["observed"]), ("unverified", None))

    def test_pure_aggregation_when_outcomes_are_explicitly_synthetic(self) -> None:
        api = self.load_script("tk-test.py")
        catalog = self.catalog()
        cases = [(config(), {"opus48", "sol"}, "passed", 2),
                 (config(), {"opus48"}, "failed", 1), (config(), {"sol"}, "failed", 1),
                 (config(["sol"]), {"opus48", "sol"}, "failed", 1),
                 (dict(config(), review_families_min=3), {"opus48", "sol"}, "failed", 2),
                 (config(), set(), "failed", 0)]
        for raw, verified, status, count in cases:
            with self.subTest(verified=verified, config=raw):
                # Given synthetic internal outcomes, not claims about CLI telemetry.
                cfg, _ = api.normalize_config(raw, catalog)
                outcomes = {key: api.Outcome(wires[0], "reachable" if key in verified else "not-installed",
                            "verified" if key in verified else "executable_missing",
                            (wires[0].model_id,) if key in verified else ())
                            for key, wires in api.catalog_wires(catalog).items()}
                # When applying the independent role and reviewer-family gates.
                report = api.aggregate(cfg, catalog, outcomes if verified else {})
                # Then arithmetic respects reviewer roles and explicit failures.
                self.assertEqual((report["status"], report["reviewer_family_count"]), (status, count))

    def test_local_catalog_when_key_changes_drives_expansion_without_roster_code(self) -> None:
        # Given a renamed catalog key with unchanged wire identity.
        catalog = self.catalog()
        entries = mapping(catalog["models"])
        entries["wide"] = entries.pop("fable51")
        write_json(self.skill / "references/models.json", catalog)
        write_json(self.config, config())
        self.fleet()
        # When loading only the relocated skill's catalog.
        result = self.cli(["--json"])
        # Then no embedded short-name roster can override it.
        self.assertEqual(list(mapping(mapping(json.loads(result.stdout))["models"])), ["opus48", "opus5", "sol", "wide"])

    def test_catalog_harness_when_primary_absent_uses_only_supported_alternative(self) -> None:
        # Given missing Hermes but an installed catalog-supported OpenCode.
        write_json(self.config, config())
        self.fleet()
        (self.bin / "hermes").unlink()
        # When selecting the first installed catalog mapping.
        result = self.cli(["--json"])
        # Then the alternative's exact provider/model selector is used without a retry engine.
        report = mapping(json.loads(result.stdout))
        row = mapping(mapping(report["models"])["fable51"])
        self.assertEqual((row["harness"], row["requested"]), ("opencode", {
            "provider": "amazon-bedrock", "model_id": "us.anthropic.claude-fable-5-1"}))
        self.assertEqual(len(self.launches()), 4)

    def test_support_when_missing_or_corrupt_does_not_repair_from_decoys(self) -> None:
        self.fleet()
        for asset in ("scripts/model_config.py", "scripts/preflight_protocols.py", "references/models.json"):
            for content in (None, b"\xff", b"", b"{invalid"):
                with self.subTest(asset=asset, content=content):
                    # Given a broken local asset and valid parent/home decoys.
                    path = self.skill / asset
                    original = path.read_bytes()
                    decoy = Path(self.env["HOME"]) / ".agents/skills/tk-test" / asset
                    decoy.parent.mkdir(parents=True, exist_ok=True)
                    decoy.write_bytes(original)
                    shutil.copyfile(path, self.sandbox / path.name)
                    path.unlink() if content is None else path.write_bytes(content)
                    before = snapshot(self.sandbox)
                    # When running only the copied skill under an empty PYTHONPATH.
                    result = self.cli(["--json"])
                    # Then it fails without launch, global repair or filesystem mutation.
                    self.assertEqual((result.returncode, self.launches()), (2, []))
                    self.assertEqual(mapping(json.loads(result.stdout))["status"], "invalid")
                    self.assertEqual(snapshot(self.sandbox), before)
                    path.write_bytes(original)

    def test_failure_when_sensitive_output_is_present_never_echoes_it(self) -> None:
        for response in (Stub(json.dumps(dict(claude(), is_error=True, result=SENSITIVE)), stderr=SENSITIVE),
                         Stub("", 7, SENSITIVE), Stub(SENSITIVE, stderr=SENSITIVE),
                         Stub(json.dumps(dict(claude(), modelUsage={SENSITIVE: {"outputTokens": 1}})))):
            for args in ([], ["--json"]):
                with self.subTest(response=response, args=args):
                    # Given credential-bearing provider output or diagnostics.
                    self.stub("claude", response)
                    # When rendering either supported output mode.
                    result = self.cli(args)
                    # Then only safe categories escape the adapter.
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn(SENSITIVE, result.stdout + result.stderr)
                    if args:
                        self.assertIsInstance(json.loads(result.stdout), dict)

    def test_timeout_when_child_hangs_kills_and_reaps_it(self) -> None:
        # Given a real isolated executable that never completes.
        api = self.load_script("tk-test.py")
        self.stub("claude", Stub(json.dumps(claude()), hang=True))
        # When the deadline expires.
        code, output, _ = self.invoke(api, ["--json", "--timeout", "0.5"])
        # Then the result is a timeout and the owned child is already reaped.
        report = mapping(json.loads(output))
        self.assertEqual((code, mapping(mapping(report["models"])["opus48"])["status"]), (1, "timeout"))
        pid = self.launches()[0]["pid"]
        assert isinstance(pid, int)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_spawn_when_executable_disappears_reports_absence_without_retry(self) -> None:
        # Given an executable removed after availability was checked.
        api = self.load_script("tk-test.py")
        executable = self.stub("claude", Stub(json.dumps(claude())))

        def vanished(name: str) -> str:
            executable.unlink()
            return str(executable)

        # When the real process creation races with removal.
        with patch.object(api.shutil, "which", side_effect=vanished):
            code, output, _ = self.invoke(api, ["--json"])
        # Then no fallback model or second process is attempted.
        row = mapping(mapping(mapping(json.loads(output))["models"])["opus48"])
        self.assertEqual((code, row["status"], self.launches()), (1, "not-installed", []))

    def test_process_failures_when_relocated_have_bounded_machine_readable_outcomes(self) -> None:
        cases = [(Stub(json.dumps(claude()), 7), "unreachable"),
                 (Stub("ÿ", encoding="latin-1"), "malformed"),
                 (Stub(json.dumps(claude()) + "\ninvalid"), "malformed"),
                 (Stub(json.dumps(claude()) + " " * 1_048_576), "malformed"),
                 (Stub(json.dumps(claude()), hang=True), "timeout")]
        for response, expected in cases:
            with self.subTest(status=expected, code=response.returncode):
                # Given a failing, corrupt, oversized or unfinished real process.
                self.stub("claude", response)
                # When running the relocated CLI, not an imported wrapper.
                result = self.cli(["--json", "--timeout", "1"])
                # Then output and completion failures are not pong successes.
                row = mapping(mapping(mapping(json.loads(result.stdout))["models"])["opus48"])
                self.assertEqual((result.returncode, row["status"]), (1, expected))

    def test_unsupported_last_catalog_mapping_when_present_prevents_every_launch(self) -> None:
        # Given an invalid mapping after otherwise valid catalog candidates.
        self.fleet()
        catalog = self.catalog()
        mapping(mapping(catalog["models"])["sol"])["harnesses"] = [
            {"harness": "sh", "provider": "openai-codex", "model_id": "gpt-5.6-sol"}]
        write_json(self.skill / "references/models.json", catalog)
        # When validating all local assets before dispatch.
        result = self.cli(["--json"])
        # Then not even the earlier valid explicit model is launched.
        self.assertEqual((result.returncode, self.launches()), (2, []))
        self.assertEqual(mapping(json.loads(result.stdout))["reason_code"], "invalid_assets")

    def test_legacy_config_when_complete_is_previewed_without_rewriting(self) -> None:
        # Given a supported legacy selection, not an empty default fleet.
        write_json(self.config, {"models": {"plan": "opus48", "critical_path": "opus48", "review": "all"}})
        before = self.config.read_bytes()
        self.fleet()
        # When normalizing through the real CLI.
        result = self.cli(["--json"])
        # Then choices survive and only the in-memory schema changes.
        report = mapping(json.loads(result.stdout))
        self.assertEqual(report["classes"], config()["classes"])
        warnings = report["warnings"]
        assert isinstance(warnings, list)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(self.config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
