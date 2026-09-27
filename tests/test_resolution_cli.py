from __future__ import annotations

from copy import deepcopy
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from resolution_fixtures import (BAD_PATHS, CONFIGLESS_ROWS, KEYS, REFERENCES, SCRATCH,
                                 SCRIPT, SHAPE_ROWS, Fixture, JsonObject, JsonValue,
                                 mapping, sequence, text)


class ResolutionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = Fixture(self.root)

    def cli(self, args: list[str], exit_code: int = 0, script: Path = SCRIPT) -> JsonObject:
        process = subprocess.run([sys.executable, "-B", str(script), *args, "--json"], cwd=self.root,
                                 capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(process.returncode, exit_code, process.stderr)
        result: JsonValue = json.loads(process.stdout)
        record = mapping(result)
        self.assertEqual(set(record), KEYS)
        self.assertIsNone(mapping(record["bindings"])["observed"])
        if exit_code == 2:
            self.assertEqual(record["reason_code"], "invalid_config")
        if record["decision"] != "delegate":
            self.assertEqual(mapping(record["bindings"])["effective"], {})
        return record

    def test_cli_preserves_inputs_when_computing_one_decision(self) -> None:
        args = self.fixture.arguments()
        paths = [path for path in self.root.rglob("*") if path.is_file()]
        paths += [REFERENCES / "dependencies.json", REFERENCES / "models.json"]
        before = {path: path.read_bytes() for path in paths}
        entries = set(self.root.rglob("*"))
        result = self.cli(args)
        self.assertEqual(result["decision"], "delegate")
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        self.assertEqual(set(self.root.rglob("*")), entries)

    def test_missing_evidence_when_native_descriptor_is_blank(self) -> None:
        for host, skill, slot, decision, code in (("opencode", "tk-plan", "root", "blocked", 1),
                                                 ("hermes", "tk-review", "reviewers", "fallback", 0)):
            for descriptor in (" \t ", "", " ", "\t", "\r\n", "\u2003"):
                with self.subTest(host=host, skill=skill, descriptor=descriptor):
                    f = Fixture(self.root, host, skill)
                    mapping(mapping(f.snapshot["model_bindings"])[slot])["descriptor"] = descriptor
                    result = self.cli(f.arguments(), code)
                    self.assertEqual((result["decision"], result["reason_code"]), (decision, "missing_evidence"))

    def test_descriptor_preserved_when_nonblank_text_has_surrounding_whitespace(self) -> None:
        descriptor = " \tfixture:custom descriptor\t "
        for host, skill, slot in (("opencode", "tk-plan", "root"), ("hermes", "tk-review", "reviewers")):
            with self.subTest(host=host, skill=skill):
                f = Fixture(self.root, host, skill)
                mapping(mapping(f.snapshot["model_bindings"])[slot])["descriptor"] = descriptor
                result = self.cli(f.arguments())
                self.assertEqual((result["decision"], result["reason_code"]), ("delegate", "compatible"))
                binding = mapping(mapping(mapping(result["bindings"])["effective"])[slot])
                self.assertEqual(binding["descriptor"], descriptor)

    def test_exit_one_when_selected_model_is_not_effective(self) -> None:
        root = mapping(mapping(self.fixture.snapshot["model_bindings"])["root"])
        mapping(sequence(root["members"])[0])["model_id"] = "wrong"
        result = self.cli(self.fixture.arguments(), 1)
        self.assertEqual(result["reason_code"], "model_mismatch")

    def test_configless_utilities_when_optional_inputs_are_unreadable(self) -> None:
        for skill, operation in CONFIGLESS_ROWS:
            with self.subTest(skill=skill):
                result = self.cli(["--skill", skill, "--operation", operation,
                                   "--config", "unreadable", "--capabilities", "unreadable"])
                self.assertEqual((result["decision"], result["reason_code"]), ("owned", "owned_policy"))
                self.assertEqual(result["evidence_paths"], [])
                self.assertEqual(mapping(result["bindings"])["requested"], {})
        self.assertEqual(self.cli(["--skill", "tk-ask"])["evidence_paths"], [])

    def test_required_config_when_owned_operation_bears_models(self) -> None:
        for skill, operation in (("tk-plan", "plan"), ("tk-router", "route"), ("tk-memory", "save"),
                                  ("tk-handoff", "restore"), ("tk-test", "preflight"), ("tk-review", "plan")):
            with self.subTest(skill=skill):
                result = self.cli(["--skill", skill, "--operation", operation], 2)
                self.assertEqual(result["evidence_paths"], [])

    def test_owned_routes_when_snapshot_is_unread(self) -> None:
        cases: tuple[tuple[list[str], JsonObject, str], ...] = (
            ([], {"delegation": "off"}, "disabled"), ([], {"ecosystems": []}, "owned_policy"),
            (["--skill", "tk-review", "--operation", "plan"], {}, "owned_policy"),
            (["--skill", "tk-grill"], {"ecosystems": ["omo"]}, "owned_policy"))
        for extra, options, reason in cases:
            self.fixture.config = {"classes": deepcopy(mapping(self.fixture.config["classes"])), **options}
            args = self.fixture.arguments() + extra + ["--capabilities", str(self.root.parent / "unread")]
            result = self.cli(args)
            self.assertEqual(result["reason_code"], reason)
            self.assertEqual(result["evidence_paths"], ["config.json"])

    def test_invalid_config_when_delegation_is_off(self) -> None:
        self.fixture.config.update(delegation="off", ecosystems=["foreign"])
        self.cli(self.fixture.arguments(), 2)

    def test_unknown_requests_and_missing_arguments_when_parsing_cli(self) -> None:
        for extra in (["--skill", "tk-ghost"], ["--operation", "ghost"], ["--unknown"],
                      ["--catalog", "missing.json"], ["--project-root", "missing"],
                      ["--capabilities", "missing.json"], ["--project-root", str(self.root / "config.json")]):
            with self.subTest(extra=extra):
                self.cli(self.fixture.arguments() + extra, 2)
        self.cli([], 2)

    def test_malformed_json_when_opened_evidence_is_recorded(self) -> None:
        for source in ("config", "capabilities"):
            for data in ("{", "[]", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e400}'):
                with self.subTest(source=source, data=data):
                    args = self.fixture.arguments()
                    (self.root / f"{source}.json").write_text(data, encoding="utf-8")
                    result = self.cli(args, 2)
                    self.assertEqual(result["evidence_paths"], ["config.json"] if source == "config" else ["config.json", "capabilities.json"])

    def test_outside_evidence_when_project_boundary_is_explicit(self) -> None:
        for source in ("config", "capabilities"):
            args = self.fixture.arguments() + [f"--{source}", str(REFERENCES / "models.json")]
            result = self.cli(args, 2)
            self.assertEqual(result["evidence_paths"], [] if source == "config" else ["config.json"])

    def test_malformed_snapshot_when_top_level_types_disagree(self) -> None:
        original = deepcopy(self.fixture.snapshot)
        for field, value in SHAPE_ROWS:
            self.fixture.snapshot = {**original, field: value}
            self.cli(self.fixture.arguments(), 2)

    def test_malformed_snapshot_when_nested_types_disagree(self) -> None:
        for field, value in (("root", 1), ("root", "bad\x00path"), ("source", None), ("version", False),
                             ("loaded_skills", []), ("package", {})):
            original = self.fixture.peer[field]
            self.fixture.peer[field] = value
            self.cli(self.fixture.arguments(), 2)
            self.fixture.peer[field] = original
        for field, value in (("path", []), ("path", "bad\x7fpath"), ("sha256", 1)):
            original_loaded = self.fixture.loaded[field]
            self.fixture.loaded[field] = value
            self.cli(self.fixture.arguments(), 2)
            self.fixture.loaded[field] = original_loaded

    def test_malformed_bindings_when_slot_or_member_shape_is_wrong(self) -> None:
        bindings = mapping(self.fixture.snapshot["model_bindings"])
        original = deepcopy(mapping(bindings["root"]))
        cases: tuple[tuple[str, JsonValue], ...] = (
            ("descriptor", []), ("method", "invented"), ("method", None), ("members", {}),
            ("members", [None]), ("members", [{"catalog_key": [], "provider": "x", "model_id": "y"}]))
        for field, value in cases:
            bindings["root"] = {**original, field: value}
            self.cli(self.fixture.arguments(), 2)

    def test_manifest_paths_when_nonportable_before_any_peer_read(self) -> None:
        target = self.fixture.target
        provenance = mapping(target["provenance"])
        pin = mapping(mapping(self.fixture.manifest["ecosystems"])["omo"])
        identity = mapping(pin["provenance_root"])
        original = deepcopy(provenance)
        for path in BAD_PATHS:
            for location in ("entrypoint", "files", "identity_file"):
                with self.subTest(path=path, location=location):
                    provenance.clear()
                    provenance.update(deepcopy(original))
                    identity["identity_file"] = "package.json"
                    if location == "identity_file":
                        identity[location] = path
                    elif location == "files":
                        sequence(provenance["files"]).append(path)
                    else:
                        provenance[location] = path
                    self.cli(self.fixture.arguments(), 2)

    def test_manifest_shapes_when_contract_is_malformed(self) -> None:
        original = deepcopy(self.fixture.target)
        cases: tuple[tuple[str, JsonValue], ...] = (
            ("requires", None), ("requires", ["unknown:x"]), ("requires", ["model-binding:other"]),
            ("mode", "invented"), ("native_roles", {}), ("native_roles", {"root": "reviewers"}),
            ("provenance", {}), ("selector", "wrong"), ("canonical_name", "forbidden"))
        for field, value in cases:
            self.fixture.target.clear()
            self.fixture.target.update({**original, field: value})
            self.cli(self.fixture.arguments(), 2)

    def test_unavailable_targets_when_flags_or_other_hosts_claim_readiness(self) -> None:
        for host, reason in (("claude", "unsupported_host"), ("opencode", "peer_missing")):
            self.fixture.snapshot.update(host=host, peers={}, ready=True)
            result = self.cli(self.fixture.arguments())
            self.assertEqual((result["decision"], result["reason_code"]), ("fallback", reason))

    def test_denials_when_required_tools_delivery_or_lookup_consent_are_missing(self) -> None:
        for skill, operation, field, code, reason in (("tk-plan", "plan", "tools", 0, "capability_missing"),
                ("tk-execute", "execute", "consents", 1, "capability_missing"),
                ("tk-handoff", "lookup", "consents", 0, "missing_evidence")):
            f = Fixture(self.root, "opencode", skill)
            f.snapshot[field] = []
            self.assertEqual(self.cli(f.arguments(operation), code)["reason_code"], reason)

    def test_denial_when_forged_loaded_digest_matches_tampered_bytes(self) -> None:
        f = self.fixture
        Path(text(f.loaded["path"])).write_bytes(b"tampered")
        f.loaded["sha256"] = hashlib.sha256(b"tampered").hexdigest()
        self.assertEqual(self.cli(f.arguments())["reason_code"], "source_mismatch")

    def test_model_denial_when_known_key_is_out_of_class_or_unmapped_on_host(self) -> None:
        for key, slot in (("fable51", "root"), ("sol", "momus"), ("opus48", "oracle")):
            f = Fixture(self.root)
            mapping(f.config["classes"])["reviewers"] = "all"
            model = mapping(mapping(f.catalog["models"])[key])
            mapping(mapping(f.snapshot["model_bindings"])[slot])["members"] = [
                {"catalog_key": key, "provider": model["provider"], "model_id": model["model_id"]}]
            self.assertEqual(self.cli(f.arguments(), 1)["reason_code"], "model_mismatch")

    def test_home_shape_when_malformed_inputs_must_not_raise_tracebacks(self) -> None:
        f = Fixture(self.root, "hermes", "tk-execute")
        cases: tuple[JsonValue, ...] = (True, [], {"path": 1, "parent_home": "a", "dispatcher_home": "a"},
                                        {"path": "a\x00b", "parent_home": "a", "dispatcher_home": "a"})
        for value in cases:
            f.snapshot["runtime_home"] = value
            self.cli(f.arguments(), 2)

    def test_relocated_payload_when_only_three_runtime_scripts_exist(self) -> None:
        scripts, references = self.root / "scripts", self.root / "references"
        scripts.mkdir()
        references.mkdir()
        for name in ("tk-resolve.py", "capability_gates.py", "model_config.py", "peer_lock.py"):
            self.assertTrue((REFERENCES / name).is_file(), name)
            shutil.copyfile(REFERENCES / name, scripts / name)
        shutil.copyfile(REFERENCES / "models.json", references / "models.json")
        args = self.fixture.arguments()
        shutil.copyfile(self.root / "dependencies.json", references / "dependencies.json")
        manifest = args.index("--manifest")
        args = args[:manifest] + args[manifest + 2:]
        result = self.cli(args, script=scripts / "tk-resolve.py")
        self.assertEqual(result["decision"], "delegate")
        self.assertFalse((scripts / "__pycache__").exists())

    def test_resource_lookup_when_assets_exist_only_above_the_skill(self) -> None:
        scripts = self.root / "nested/scripts"
        scripts.mkdir(parents=True)
        for name in ("tk-resolve.py", "capability_gates.py", "model_config.py", "peer_lock.py"):
            self.assertTrue((REFERENCES / name).is_file(), name)
            shutil.copyfile(REFERENCES / name, scripts / name)
        shutil.copyfile(REFERENCES / "models.json", self.root / "models.json")
        args = self.fixture.arguments()
        without_manifest = args[:args.index("--manifest")] + args[args.index("--project-root"):]
        self.cli(without_manifest, 2, scripts / "tk-resolve.py")
        result = self.cli(args + ["--catalog", str(self.root / "models.json")], script=scripts / "tk-resolve.py")
        self.assertEqual(result["decision"], "delegate")

    def test_import_when_bytecode_writing_was_enabled(self) -> None:
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("tk-resolve.py", "capability_gates.py", "model_config.py", "peer_lock.py"):
            self.assertTrue((REFERENCES / name).is_file(), name)
            shutil.copyfile(REFERENCES / name, scripts / name)
        code = "import runpy,sys\nsys.dont_write_bytecode=False\nrunpy.run_path(sys.argv[1])\nprint(sys.dont_write_bytecode)"
        process = subprocess.run([sys.executable, "-I", "-c", code, str(scripts / "tk-resolve.py")],
                                 cwd=self.root, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual((process.returncode, process.stdout, process.stderr), (0, "False\n", ""))
        self.assertFalse((scripts / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
