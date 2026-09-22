from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Final, TypeAlias
import unittest

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
ROOT: Final = Path(__file__).resolve().parents[1]
REFERENCES: Final = ROOT / "skills" / "references"
SCRIPT: Final = REFERENCES / "tk-resolve.py"
SCRATCH: Final = ROOT.parent.parent / ".omo" / "evidence" / "thunderkit-skill-deps-review"
KEYS: Final = {"schema_version", "skill", "operation", "decision", "reason_code", "detail",
               "target", "bindings", "runtime_home", "evidence_paths"}
CLASSES: Final[JsonObject] = {"planner": "opus5", "executors": ["fable51"], "reviewers": ["sol"]}


def mapping(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def replace(doc: JsonObject, path: tuple[str, ...], value: JsonValue) -> None:
    parent = doc
    for key in path[:-1]:
        parent = mapping(parent[key])
    parent[path[-1]] = value


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "native resolver implementation is missing")
        spec = importlib.util.spec_from_file_location("tk_resolve", SCRIPT)
        assert spec is not None and spec.loader is not None
        self.api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.api
        spec.loader.exec_module(self.api)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)
        self.config_path = self.sandbox / "config.json"
        self.capabilities_path = self.sandbox / "capabilities.json"
        self.config: JsonObject = {"schema_version": 2, "classes": deepcopy(CLASSES)}
        self.snapshot: JsonObject = {
            "schema_version": 1, "host": "opencode",
            "peers": {
                "omo": {"package": "oh-my-openagent", "version": "5.0.0-beta.81",
                        "source": "https://github.com/code-yeongyu/oh-my-openagent",
                        "loaded_skills": {
                            name: {"path": f"/packages/oh-my-openagent/dist/skills/{name}/SKILL.md",
                                   "sha256": None}
                            for name in ("ulw-plan", "ulw-execute", "ulw-research", "coding-agent-sessions")}},
                "omh": {"package": "oh-my-hermes", "version": "2.0.3", "skills_root": "/skills",
                        "loaded_skills": {
                            name: {"path": f"/skills/{name}/SKILL.md", "sha256": None}
                            for name in ("ultrawork/ulw-plan", "ultrawork/ulw-interview", "ultrawork/ulw-work",
                                         "reviewer/omh-code-review")}},
            },
            "tools": ["skill", "delegate_task", "omh_delegate_route"],
            "model_bindings": {
                "planner": {"requested": "opus5", "effective": "amazon-bedrock/us.anthropic.claude-opus-5",
                            "source": "agent:oracle"},
                "executors": {"requested": ["fable51"],
                              "effective": ["amazon-bedrock/us.anthropic.claude-fable-5-1"],
                              "source": "category:deep-low"},
                "reviewers": {"requested": ["sol"], "effective": ["openai-codex/gpt-5.6-sol"],
                              "source": "agent:reviewer"},
            },
            "runtime_home": {"path": str(self.sandbox / ".thunderkit/runs/current/hermes-home"),
                             "task_owned": True, "active_process_home": True},
            "consents": ["dispatch", "delivery:disabled", "lookup"],
        }

    def arguments(self, skill: str = "tk-plan", operation: str | None = None) -> list[str]:
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.capabilities_path.write_text(json.dumps(self.snapshot), encoding="utf-8")
        args = ["--skill", skill, "--config", str(self.config_path),
                "--capabilities", str(self.capabilities_path)]
        return args + (["--operation", operation] if operation is not None else [])

    def resolve(self, skill: str = "tk-plan", operation: str | None = None) -> JsonObject:
        result: JsonObject = self.api.resolve(self.arguments(skill, operation))
        return result

    def cli(self, args: list[str], script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(script), *args, "--json"],
                              cwd=self.sandbox, capture_output=True, text=True, check=False, timeout=15)

    def expect(self, result: JsonObject, outcome: tuple[str, str]) -> None:
        self.assertEqual(set(result), KEYS)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual((result["decision"], result["reason_code"]), outcome)
        self.assertTrue(result["detail"])
        self.assertIsNone(mapping(result["bindings"])["observed"])
        self.assertEqual(result["evidence_paths"], [str(self.config_path), str(self.capabilities_path)])

    def test_compatible_targets_follow_the_active_host(self) -> None:
        for skill, host, ecosystem, selector, mode in (
            ("tk-plan", "opencode", "omo", "ulw-plan", "handoff"),
            ("tk-plan", "codex", "omo", "ulw-plan", "handoff"),
            ("tk-plan", "hermes", "omh", "ultrawork/ulw-plan", "handoff"),
            ("tk-grill", "hermes", "omh", "ultrawork/ulw-interview", "component"),
        ):
            with self.subTest(skill=skill, host=host):
                self.snapshot["host"] = host
                result = self.resolve(skill)
                self.expect(result, ("delegate", "compatible"))
                peer = mapping(mapping(self.snapshot["peers"])[ecosystem])
                self.assertEqual(result["target"], {"ecosystem": ecosystem, "package": peer["package"],
                                 "version": peer["version"], "skill_name": selector.split("/")[-1],
                                 "selector": selector, "mode": mode})
                self.assertEqual(mapping(result["bindings"])["requested"], CLASSES)
                self.assertIsNone(result["runtime_home"])

    def test_incompatible_snapshots_fail_the_specific_gate(self) -> None:
        cases: tuple[tuple[str, str, tuple[str, ...], JsonValue, str, str], ...] = (
            ("tk-grill", "opencode", ("peers", "omo"), None, "fallback", "unsupported_host"),
            ("tk-plan", "claude", ("tools",), [], "fallback", "unsupported_host"),
            ("tk-plan", "opencode", ("peers",), {}, "fallback", "peer_missing"),
            ("tk-plan", "opencode", ("peers", "omo", "version"), "4.19.4", "fallback", "version_mismatch"),
            ("tk-plan", "opencode", ("peers", "omo", "package"), "ghostkit", "fallback", "source_mismatch"),
            ("tk-plan", "opencode", ("peers", "omo", "loaded_skills"), {}, "fallback", "peer_missing"),
            ("tk-plan", "opencode", ("peers", "omo", "loaded_skills", "ulw-plan", "path"),
             "/packages/not-oh-my-openagent/ulw-plan/SKILL.md", "fallback", "source_mismatch"),
            ("tk-plan", "opencode", ("peers", "omo", "loaded_skills", "ulw-plan", "path"),
             "/packages/oh-my-openagent/../other/ulw-plan/SKILL.md", "fallback", "source_mismatch"),
            ("tk-plan", "hermes", ("peers", "omh", "loaded_skills"),
             {"ulw-plan": {"path": "/skills/ulw-plan/SKILL.md", "sha256": None}}, "fallback", "source_mismatch"),
            ("tk-plan", "opencode", ("tools",), ["delegate_task"], "fallback", "capability_missing"),
            ("tk-plan", "opencode", ("model_bindings", "planner", "effective"), None, "blocked", "model_mismatch"),
            ("tk-plan", "opencode", ("model_bindings", "planner", "requested"), "opus48", "blocked", "model_mismatch"),
            ("tk-spec", "hermes", ("model_bindings",), {}, "fallback", "model_mismatch"),
            ("tk-review", "hermes", ("model_bindings", "reviewers", "effective"), [], "fallback", "model_mismatch"),
            ("tk-execute", "opencode", ("consents",), ["dispatch"], "blocked", "capability_missing"),
        )
        original = deepcopy(self.snapshot)
        for skill, host, path, value, decision, reason in cases:
            with self.subTest(skill=skill, host=host, path=path, value=value):
                self.snapshot = deepcopy(original)
                self.snapshot["host"] = host
                replace(self.snapshot, path, value)
                self.expect(self.resolve(skill), (decision, reason))

    def test_execution_requires_a_task_owned_active_home(self) -> None:
        self.snapshot["host"] = "hermes"
        home = deepcopy(mapping(self.snapshot["runtime_home"]))
        for patch in ({"path": "~/.hermes"}, {"task_owned": False}, {"active_process_home": False},
                      {"path": str(self.sandbox / ".thunderkit/runs/../../shared")}, {"task_owned": "true"}):
            with self.subTest(patch=patch):
                self.snapshot["runtime_home"] = {**home, **patch}
                self.expect(self.resolve("tk-execute"), ("blocked", "unsafe_runtime_home"))

    def test_execution_records_only_a_safe_resolved_home(self) -> None:
        self.snapshot["host"] = "hermes"
        result = self.resolve("tk-execute")
        self.expect(result, ("delegate", "compatible"))
        self.assertEqual(result["runtime_home"], mapping(self.snapshot["runtime_home"])["path"])

    def test_execution_rejects_a_symlink_escaped_home(self) -> None:
        self.snapshot["host"] = "hermes"
        outside = self.sandbox / "shared"
        outside.mkdir()
        link = self.sandbox / ".thunderkit/runs/escaped"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside, target_is_directory=True)
        mapping(self.snapshot["runtime_home"])["path"] = str(link / "hermes-home")
        self.expect(self.resolve("tk-execute"), ("blocked", "unsafe_runtime_home"))

    def test_lookup_requires_explicit_consent(self) -> None:
        self.snapshot["consents"] = ["dispatch"]
        self.expect(self.resolve("tk-handoff", "lookup"), ("fallback", "missing_evidence"))

    def test_owned_routes_do_not_read_the_snapshot(self) -> None:
        cases: tuple[tuple[str, str | None, JsonObject, str], ...] = (
            ("tk-plan", None, {"delegation": "off"}, "disabled"),
            ("tk-plan", None, {"ecosystems": []}, "owned_policy"),
            ("tk-ask", None, {}, "owned_policy"),
            ("tk-review", "plan", {}, "owned_policy"),
            ("tk-handoff", "save", {}, "owned_policy"),
            ("tk-grill", None, {"ecosystems": ["omo"]}, "owned_policy"),
        )
        for skill, operation, options, reason in cases:
            with self.subTest(skill=skill, operation=operation, options=options):
                self.config = {"classes": deepcopy(CLASSES), **options}
                args = self.arguments(skill, operation)
                self.capabilities_path.unlink()
                result = self.api.resolve(args)
                self.expect(result, ("owned", reason))
                self.assertIsNone(result["target"])

    def test_ready_flags_never_replace_peer_evidence(self) -> None:
        def inject(value: JsonValue) -> None:
            if isinstance(value, dict):
                for child in tuple(value.values()):
                    inject(child)
                value["ready"] = True
            elif isinstance(value, list):
                for child in value:
                    inject(child)
        self.snapshot["peers"] = {}
        inject(self.snapshot)
        self.expect(self.resolve(), ("fallback", "peer_missing"))

    def test_whole_executor_selection_requires_whole_binding(self) -> None:
        mapping(self.config["classes"])["executors"] = ["fable51", "opus5"]
        binding = mapping(mapping(self.snapshot["model_bindings"])["executors"])
        binding["requested"] = ["fable51", "opus5"]
        self.expect(self.resolve("tk-execute"), ("blocked", "model_mismatch"))
        binding["effective"] = ["amazon-bedrock/us.anthropic.claude-fable-5-1",
                                "amazon-bedrock/us.anthropic.claude-opus-5"]
        result = self.resolve("tk-execute")
        self.expect(result, ("delegate", "compatible"))
        self.assertEqual(mapping(result["bindings"])["requested"], self.config["classes"])

    def test_single_member_class_accepts_scalar_binding(self) -> None:
        binding = mapping(mapping(self.snapshot["model_bindings"])["executors"])
        binding.update(requested="fable51", effective="amazon-bedrock/us.anthropic.claude-fable-5-1")
        self.expect(self.resolve("tk-execute"), ("delegate", "compatible"))

    def test_all_reviewers_remain_catalog_candidates(self) -> None:
        mapping(self.config["classes"])["reviewers"] = "all"
        catalog: JsonObject = json.loads((REFERENCES / "models.json").read_text(encoding="utf-8"))
        result = self.resolve()
        self.assertEqual(mapping(mapping(result["bindings"])["requested"])["reviewers"],
                         sorted(mapping(catalog["models"])))

    def test_cli_emits_one_object_and_preserves_input_files(self) -> None:
        args = self.arguments()
        before = {path: path.read_bytes() for path in (self.config_path, self.capabilities_path)}
        process = self.cli(args)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.expect(json.loads(process.stdout), ("delegate", "compatible"))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(set(self.sandbox.iterdir()), set(before))

    def test_cli_blocked_model_has_exit_one(self) -> None:
        replace(self.snapshot, ("model_bindings", "planner", "effective"), None)
        process = self.cli(self.arguments())
        self.assertEqual(process.returncode, 1, process.stderr)
        self.expect(json.loads(process.stdout), ("blocked", "model_mismatch"))

    def test_cli_malformed_inputs_have_exit_two_and_one_object(self) -> None:
        for source, text in (("config", "{"), ("config", "[]"), ("capabilities", "{"),
                             ("capabilities", '{"schema_version":true,"host":"opencode"}')):
            with self.subTest(source=source, text=text):
                args = self.arguments()
                path = self.config_path if source == "config" else self.capabilities_path
                path.write_text(text, encoding="utf-8")
                process = self.cli(args)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.expect(json.loads(process.stdout), ("blocked", "invalid_config"))

    def test_cli_rejects_unknown_requests_and_missing_arguments(self) -> None:
        for extra in (["--skill", "tk-ghost"], ["--operation", "ghost"], ["--unknown"],
                      ["--catalog", str(self.sandbox / "missing.json")]):
            with self.subTest(extra=extra):
                process = self.cli(self.arguments() + extra)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.expect(json.loads(process.stdout), ("blocked", "invalid_config"))
        process = self.cli(["--skill", "tk-plan"])
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["reason_code"], "invalid_config")

    def test_cli_malformed_manifest_has_exit_two_and_one_object(self) -> None:
        manifest: JsonObject = json.loads((REFERENCES / "dependencies.json").read_text(encoding="utf-8"))
        targets = mapping(mapping(manifest["skills"])["tk-plan"])["targets"]
        assert isinstance(targets, list)
        mapping(targets[0]).pop("requires")
        path = self.sandbox / "dependencies.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        process = self.cli(self.arguments() + ["--manifest", str(path)])
        self.assertEqual(process.returncode, 2, process.stderr)
        self.expect(json.loads(process.stdout), ("blocked", "invalid_config"))

    def test_invalid_config_blocks_even_when_delegation_is_off(self) -> None:
        self.config.update(delegation="off", ecosystems=["ghostkit"])
        self.expect(self.resolve(), ("blocked", "invalid_config"))

    def test_copied_scripts_import_their_sibling_and_use_sibling_references(self) -> None:
        scripts, references = self.sandbox / "scripts", self.sandbox / "references"
        scripts.mkdir()
        references.mkdir()
        for name in ("tk-resolve.py", "model_config.py"):
            shutil.copyfile(REFERENCES / name, scripts / name)
        for name in ("models.json", "dependencies.json"):
            shutil.copyfile(REFERENCES / name, references / name)
        process = self.cli(self.arguments(), scripts / "tk-resolve.py")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.expect(json.loads(process.stdout), ("delegate", "compatible"))

    def test_resource_lookup_never_climbs_beyond_sibling_references(self) -> None:
        scripts = self.sandbox / "nested/scripts"
        scripts.mkdir(parents=True)
        for name in ("tk-resolve.py", "model_config.py"):
            shutil.copyfile(REFERENCES / name, scripts / name)
        for name in ("models.json", "dependencies.json"):
            shutil.copyfile(REFERENCES / name, self.sandbox / name)
        args = self.arguments()
        process = self.cli(args, scripts / "tk-resolve.py")
        self.assertEqual(process.returncode, 2, process.stderr)
        self.expect(json.loads(process.stdout), ("blocked", "invalid_config"))
        process = self.cli(args + ["--catalog", str(self.sandbox / "models.json"),
                           "--manifest", str(self.sandbox / "dependencies.json")], scripts / "tk-resolve.py")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.expect(json.loads(process.stdout), ("delegate", "compatible"))


if __name__ == "__main__":
    unittest.main()
