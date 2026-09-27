from __future__ import annotations

from collections.abc import Sequence
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Final, TypeAlias
import unittest

from resolution_fixtures import SCRATCH, JsonObject, JsonValue, mapping, read_json, text
from scenario_fixtures import SAMPLE_SKILL as SKILL
import skill_scenarios as scenarios

ROOT: Final = Path(__file__).resolve().parents[1]
SCRIPT: Final = ROOT / "tests/skill_scenarios.py"
FIXTURES: Final = ROOT / "tests/fixtures"
DELEGATES: Final = "omo:ulw-plan omh:ultrawork/ulw-plan"
Route: TypeAlias = tuple[str, str, str | None, str | None, int]
READY: Final[Route] = ("delegate", "compatible", "omo", "ulw-plan", 0)
MISSING: Final[Route] = ("fallback", "peer_missing", "omo", "ulw-plan", 0)


def case(recipe: str | None, route: Route = READY, config: str | None = "opencode") -> JsonObject:
    return {"name": recipe or "absent inputs", "operation": None, "config": config, "capabilities": recipe,
            "expect": dict(zip(("decision", "reason_code", "target_ecosystem", "target_selector", "exit"), route)),
            "sections": ["Delegation", "Fallback"]}


class SkillScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        # Given a controlled skill and independent literal case expectations.
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="scenario-test-", dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)
        self.skills, self.fixtures = self.sandbox / "skills", self.sandbox / "scenarios"
        self.fixtures.mkdir()
        self.subject = "tk-plan"
        self.skill_path = self.skills / self.subject / "SKILL.md"
        self.skill_path.parent.mkdir(parents=True)
        self.skill_path.write_text(SKILL, encoding="utf-8")
        self.ready = case("opencode_omo_full")
        self.ready["frontmatter"] = {"thunderkit-delegates": DELEGATES}
        self.groups: JsonObject = {"happy": [self.ready], "failure": [case("peer_missing", MISSING),
            case("unsupported_host", ("fallback", "unsupported_host", None, None, 0))]}
        self.fixture: JsonObject = {"skill": self.subject, "cases": self.groups}

    def arguments(self, selected: Sequence[str]) -> list[str]:
        (self.fixtures / f"{self.subject}.json").write_text(json.dumps(self.fixture), encoding="utf-8")
        return [*selected, "--skills-root", str(self.skills), "--scenarios-dir", str(self.fixtures)]

    def run_scenarios(self, selected: Sequence[str] = ("--skill", "tk-plan")) -> tuple[int, str]:
        output = io.StringIO()
        # When the real runner evaluates the selected cases.
        with redirect_stdout(output):
            status = scenarios.main(self.arguments(selected))
        return status, output.getvalue()

    def expect_failure(self, field: str) -> None:
        status, output = self.run_scenarios()
        # Then failed contracts cannot report successful coverage.
        self.assertNotEqual(status, 0, output)
        self.assertIn(field, output)
        self.assertIn("FAILED", output)

    def test_ready_and_denied_cases_have_exact_counts(self) -> None:
        status, output = self.run_scenarios()
        self.assertEqual(status, 0, output)
        self.assertIn("CASES=3 PASSED=3 FAILED=0", output)
        self.assertIn("ASSERTIONS=25", output)

    def test_cli_when_run_from_an_unrelated_directory(self) -> None:
        for name in ("models.json", "dependencies.json"):
            (self.sandbox / name).write_text("{}", encoding="utf-8")
        for corrupt, expected in ((False, 0), (True, 1)):
            with self.subTest(corrupt=corrupt):
                if corrupt:
                    mapping(self.ready["expect"])["exit"] = 1
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), *self.arguments(["--all"])],
                                        cwd=self.sandbox, capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertIn("ASSERTIONS=25", result.stdout)

    def test_missing_zero_and_wrong_expectations_are_not_rescued(self) -> None:
        original = deepcopy(self.ready)
        for field in ("decision", "reason_code", "target_ecosystem", "target_selector", "exit"):
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    self.ready.clear()
                    self.ready.update(deepcopy(original))
                    expected = mapping(self.ready["expect"])
                    if missing:
                        expected.pop(field)
                    else:
                        expected[field] = 1 if field == "exit" else "foreign"
                    self.expect_failure(field)
        variants: tuple[JsonObject, ...] = ({}, {"sections": ["Fallback"]}, {"frontmatter": {"thunderkit-contract": "1"}})
        for assertions in variants:
            self.ready.clear()
            self.ready.update({key: value for key, value in original.items() if key not in ("expect", "sections", "frontmatter")})
            self.ready.update(assertions)
            self.expect_failure("expect")

    def test_empty_expectations_and_boolean_exit_fail(self) -> None:
        for value in ({}, {**mapping(self.ready["expect"]), "exit": False}):
            self.ready["expect"] = value
            self.expect_failure("expect")

    def test_frontmatter_when_renamed_legacy_or_excluded(self) -> None:
        for body, error in ((SKILL.replace("name: tk-plan", "name: tk-renamed"), "must match directory"),
                            (SKILL.replace(DELEGATES, "none"), "thunderkit-delegates"),
                            (SKILL.replace(DELEGATES, "omh:advisor/omh-ask"), "thunderkit-delegates"),
                            (SKILL.replace(DELEGATES, "omc:plan"), "thunderkit-delegates"),
                            ('---\nname: tk-plan\ndescription: "Use when checking local contracts."\n'
                             'metadata:\n  thunderkit:\n    role: planner\n---\n', "frontmatter not migrated")):
            with self.subTest(error=error):
                self.skill_path.write_text(body, encoding="utf-8")
                self.expect_failure(error)

    def test_delegates_registry_check_when_explicit_metadata_is_omitted(self) -> None:
        self.ready.pop("frontmatter")
        self.skill_path.write_text(SKILL.replace(DELEGATES, "none"), encoding="utf-8")
        self.expect_failure("thunderkit-delegates")

    def test_explicit_metadata_when_wrong(self) -> None:
        self.ready["frontmatter"] = {"thunderkit-tier": "utility"}
        self.expect_failure("thunderkit-tier")

    def test_sections_ignore_header_and_fenced_body_lookalikes(self) -> None:
        for body in (SKILL.replace("## Fallback\n", "").replace('metadata:', 'compatibility: "## Fallback"\nmetadata:'),
                     SKILL.replace("## Fallback\n", "```markdown\n## Fallback\n```\n"),
                     SKILL.replace("## Fallback\n", "~~~~\n```\n## Fallback\n~~~\n"),
                     SKILL.replace("## Fallback\n", "    ## Fallback\n")):
            with self.subTest(body=body):
                self.skill_path.write_text(body, encoding="utf-8")
                self.expect_failure("sections.Fallback")

    def test_sections_when_real_heading_follows_a_fenced_example(self) -> None:
        self.skill_path.write_text(SKILL.replace("## Fallback", "~~~~md\n## Example\n~~~~\n  ## Fallback ##"), encoding="utf-8")
        status, output = self.run_scenarios()
        self.assertEqual(status, 0, output)

    def test_fixture_identity_and_selected_groups_when_invalid(self) -> None:
        self.fixture["skill"] = "tk-renamed"
        self.expect_failure("skill must match")
        self.fixture["skill"] = "tk-plan"
        variants: tuple[JsonObject, ...] = ({}, {"happy": [], "failure": []}, {"happy": [self.ready]}, {"happy": [self.ready], "failure": "skip"})
        for groups in variants:
            self.fixture["cases"] = groups
            self.expect_failure("case list")

    def test_missing_skills_fixtures_and_empty_selection_fail(self) -> None:
        for selected, error in ((["--skill", "tk-absent"], "tk-absent"), (["--all"], "tk-uncovered")):
            (self.skills / "tk-uncovered").mkdir(exist_ok=True)
            status, output = self.run_scenarios(selected)
            self.assertEqual(status, 1, output)
            self.assertIn(error, output)
        self.skill_path.unlink()
        self.expect_failure("SKILL.md")
        empty = self.sandbox / "empty"
        empty.mkdir()
        with redirect_stdout(io.StringIO()) as captured:
            status = scenarios.main(["--all", "--skills-root", str(empty), "--scenarios-dir", str(empty)])
        self.assertEqual((status, "ASSERTIONS=0" in captured.getvalue()), (1, True))

    def test_all_ignores_examples_and_case_filter_selects_only_requested_group(self) -> None:
        (self.fixtures / "_example.json").write_text("not JSON", encoding="utf-8")
        status, output = self.run_scenarios(["--all"])
        self.assertEqual(status, 0, output)
        self.assertNotIn("_example", output)
        self.groups["failure"] = []
        status, output = self.run_scenarios(["--skill", "tk-plan", "--case", "happy"])
        self.assertEqual(status, 0, output)
        self.assertIn("CASES=1 PASSED=1 FAILED=0", output)

    def test_unknown_references_fields_and_skipped_cases_fail(self) -> None:
        variants: tuple[tuple[str, JsonValue], ...] = (("config", "unknown"), ("capabilities", "unknown"), ("expect", {"unknown": True}),
            ("skip", True), ("sections", []), ("frontmatter", {"description": "unconsumed"}))
        for field, value in variants:
            original = deepcopy(self.ready)
            self.ready[field] = value
            self.expect_failure(field)
            self.ready.clear()
            self.ready.update(original)

    def test_recipes_when_source_bindings_or_trust_are_denied(self) -> None:
        rows: tuple[tuple[str, str, Route], ...] = (
            ("opencode_both_peers", "opencode", READY),
            ("hermes_both_peers", "hermes", ("delegate", "compatible", "omh", "ultrawork/ulw-plan", 0)),
            ("mixed_same_name", "hermes", ("fallback", "source_mismatch", "omh", "ultrawork/ulw-plan", 0)),
            ("opencode_omo_full", "canonical", ("blocked", "model_mismatch", "omo", "ulw-plan", 1)),
            ("opencode_omo_full", "legacy", ("blocked", "model_mismatch", "omo", "ulw-plan", 1)),
            ("opencode_omo_legacy", "legacy_opencode", READY),
            ("peer_missing", "delegation_off", ("owned", "disabled", None, None, 0)),
            ("hermes_omh_full", "omo_only", ("fallback", "unsupported_host", None, None, 0)),
            ("opencode_omo_full", "owned", ("owned", "owned_policy", None, None, 0)),
            *((name, "opencode", ("fallback", "source_mismatch", "omo", "ulw-plan", 0))
              for name in ("tampered_peer", "self_hashed_tamper", "missing_companion")),
            ("missing_role", "opencode", ("blocked", "missing_evidence", "omo", "ulw-plan", 1)),
            *((name, "opencode", ("blocked", "model_mismatch", "omo", "ulw-plan", 1))
              for name in ("binding_mismatch", "wrong_host_bindings")),
        )
        for recipe, profile, expected in rows:
            with self.subTest(recipe=recipe, profile=profile):
                self.groups["happy"] = [case(recipe, expected, profile)]
                status, output = self.run_scenarios()
                self.assertEqual(status, 0, output)

    def test_ready_claim_when_peer_evidence_is_missing(self) -> None:
        self.ready["capabilities"] = "peer_missing"
        self.expect_failure("peer_missing")

    def test_requested_choices_when_all_or_unmapped_are_preserved(self) -> None:
        for profile, expected in (("opencode_all", READY), ("sol", ("blocked", "model_mismatch", "omo", "ulw-plan", 1))):
            candidate = case("opencode_omo_full", expected, profile)
            mapping(candidate["expect"])["requested_bindings"] = (
                {"planner": "opus5", "executors": ["fable51", "opus5"], "reviewers": "all"} if profile == "opencode_all"
                else {"planner": "sol", "executors": ["sol"], "reviewers": ["sol"]})
            self.groups["happy"] = [candidate]
            status, output = self.run_scenarios()
            self.assertEqual(status, 0, output)

    def test_configless_and_unknown_operation_results(self) -> None:
        for config, recipe, operation in ((None, None, None), ("opencode", None, None), ("opencode", "opencode_omo_full", "unknown")):
            candidate = case(recipe, ("blocked", "invalid_config", None, None, 2), config)
            candidate["operation"] = operation
            self.groups["happy"] = [candidate]
            status, output = self.run_scenarios()
            self.assertEqual(status, 0, output)

    def test_model_free_inputs_when_explicitly_absent(self) -> None:
        self.subject = "tk-ask"
        path = self.skills / self.subject / "SKILL.md"
        path.parent.mkdir()
        path.write_text(SKILL.replace("tk-plan", self.subject).replace(DELEGATES, "none"), encoding="utf-8")
        candidate = case(None, ("owned", "owned_policy", None, None, 0), None)
        mapping(candidate["expect"])["requested_bindings"] = {}
        self.fixture = {"skill": self.subject, "cases": {"happy": [candidate], "failure": [candidate]}}
        status, output = self.run_scenarios(["--skill", self.subject])
        self.assertEqual(status, 0, output)

    def test_prepared_peer_trust_and_input_bytes_survive_resolution(self) -> None:
        library = scenarios.FixtureLibrary.load(FIXTURES)
        originals = deepcopy((library.configs, library.recipes, library.manifest))
        prepared = library.prepare(self.sandbox, scenarios.CaseInputs("tk-plan", None, "opencode", "self_hashed_tamper"))
        peer = mapping(mapping(read_json(self.sandbox / "capabilities.json")["peers"])["omo"])
        loaded = mapping(mapping(peer["loaded_skills"])["ulw-plan"])
        self.assertEqual(loaded["sha256"], hashlib.sha256(Path(text(loaded["path"])).read_bytes()).hexdigest())
        before = {path: path.read_bytes() for path in self.sandbox.rglob("*") if path.is_file()}
        result, status = scenarios._resolver()(prepared)
        self.assertEqual((result["decision"], result["reason_code"], status), ("fallback", "source_mismatch", 0))
        self.assertEqual(before, {path: path.read_bytes() for path in self.sandbox.rglob("*") if path.is_file()})
        self.assertEqual(originals, (library.configs, library.recipes, library.manifest))

    def test_execution_when_task_home_or_consent_is_unproven(self) -> None:
        library = scenarios.FixtureLibrary.load(FIXTURES)
        for index, (recipe, reason, mount) in enumerate((("hermes_omh_full", "compatible", True),
                ("hermes_omh_full", "unsafe_runtime_home", False), ("hermes_omh_shared_home", "unsafe_runtime_home", True),
                ("no_consents", "capability_missing", True), ("hermes_missing_role", "missing_evidence", True))):
            with self.subTest(recipe=recipe, mount=mount):
                root = self.sandbox / str(index)
                root.mkdir()
                profile = "opencode" if recipe == "no_consents" else "hermes"
                prepared = library.prepare(root, scenarios.CaseInputs("tk-execute", "execute", profile, recipe))
                if recipe == "hermes_omh_full":
                    self.assertTrue(Path(text(mapping(read_json(root / "capabilities.json")["runtime_home"])["path"])).is_dir())
                result, status = scenarios._resolver()(prepared if mount else replace(prepared, mountinfo=None))
                self.assertEqual((result["decision"], result["reason_code"], status),
                                 ("delegate", "compatible", 0) if reason == "compatible" else ("blocked", reason, 1))


if __name__ == "__main__":
    unittest.main()
