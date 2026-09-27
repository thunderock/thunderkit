from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from resolution_fixtures import (EXPECTED_HOST_IDENTITIES, HOME_ROWS, KEYS, PROVENANCE_ROWS,
                                 ROLE_ROWS, SCRATCH, SCRIPT, Fixture, JsonObject, JsonValue,
                                 home_variant, make_home, mapping, read_json, sequence,
                                 slot_bindings, text, write_json)


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("tk_resolve", SCRIPT)
        assert spec is not None and spec.loader is not None
        self.api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.api
        spec.loader.exec_module(self.api)
        SCRATCH.mkdir(parents=True, exist_ok=True)

    def fixture(self, host: str = "opencode", skill: str = "tk-plan") -> Fixture:
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        return Fixture(Path(temporary.name), host, skill)

    def resolve(self, fixture: Fixture) -> JsonObject:
        result: JsonObject = self.api.resolve(fixture.arguments())
        return result

    def expect(self, result: JsonObject, outcome: tuple[str, str]) -> None:
        self.assertEqual(set(result), KEYS)
        self.assertEqual((result["decision"], result["reason_code"]), outcome, result["detail"])
        self.assertIsNone(mapping(result["bindings"])["observed"])
        if result["decision"] != "delegate":
            self.assertEqual(mapping(result["bindings"])["effective"], {})
            self.assertIsNone(result["runtime_home"])

    def test_delegate_when_host_models_and_real_peer_bytes_match(self) -> None:
        for host, skill in (("opencode", "tk-plan"),
                           ("hermes", "tk-plan"), ("hermes", "tk-grill"), ("opencode", "tk-execute")):
            with self.subTest(host=host, skill=skill):
                f = self.fixture(host, skill)
                result = self.resolve(f)
                self.expect(result, ("delegate", "compatible"))
                self.assertEqual(mapping(result["target"])["selector"], f.target["selector"])
                self.assertEqual(mapping(result["bindings"])["requested"], f.config["classes"])
                effective = mapping(mapping(result["bindings"])["effective"])
                self.assertEqual(set(effective), set(f.slots))
                for slot, cls in f.slots.items():
                    binding = mapping(mapping(f.snapshot["model_bindings"])[slot])
                    expected = {"class": cls, "descriptor": binding["descriptor"], "method": "configured"}
                    expected.update(mapping(sequence(binding["members"])[0]) if cls == "planner" else {"members": binding["members"]})
                    self.assertEqual(effective[slot], expected)
                self.assertEqual(result["evidence_paths"], ["config.json", "capabilities.json", "lock.json"])

    def test_slot_bindings_when_compared_with_literal_host_identities(self) -> None:
        f = self.fixture()
        for host, key, provider, model in EXPECTED_HOST_IDENTITIES:
            with self.subTest(host=host, key=key):
                result = slot_bindings(f.catalog, host, {"planner": key}, {"root": "planner"})
                self.assertEqual(mapping(result["root"])["members"],
                                 [{"catalog_key": key, "provider": provider, "model_id": model}])

    def test_denial_when_legacy_ready_claims_hide_real_defects(self) -> None:
        for defect, reason in (("bytes", "source_mismatch"), ("model", "model_mismatch"), ("home", "unsafe_runtime_home")):
            with self.subTest(defect=defect):
                f = self.fixture("hermes" if defect == "home" else "opencode", "tk-execute" if defect == "home" else "tk-plan")
                bindings = mapping(f.snapshot["model_bindings"])
                for cls, chosen in mapping(f.config["classes"]).items():
                    effective: JsonValue = "claimed" if isinstance(chosen, str) else ["claimed" for _ in sequence(chosen)]
                    bindings[cls] = {"requested": chosen, "effective": effective}
                if defect == "bytes":
                    Path(text(f.loaded["path"])).write_bytes(b"tampered")
                if defect == "model":
                    mapping(sequence(mapping(bindings["root"])["members"])[0])["model_id"] = "wrong"
                if defect == "home":
                    f.snapshot["runtime_home"] = {"path": str(f.root / ".thunderkit/runs/absent/hermes-home"),
                                                   "task_owned": True, "active_process_home": True}
                with patch("os.getcwd", return_value=str(f.root)):
                    result = self.api.resolve(f.arguments()[:-2])
                self.expect(result, ("fallback" if defect == "bytes" else "blocked", reason))

    def test_provenance_denied_when_peer_identity_drifts(self) -> None:
        for host in ("opencode", "hermes"):
            for field, value, reason in PROVENANCE_ROWS:
                if host == "hermes" and field == "source_commit":
                    continue
                with self.subTest(host=host, field=field):
                    f = self.fixture(host)
                    f.peer[field] = value
                    self.expect(self.resolve(f), ("fallback", reason))

    def test_provenance_denied_when_optional_evidence_is_missing(self) -> None:
        for field in ("package", "version", "source", "root"):
            f = self.fixture()
            f.peer.pop(field)
            self.expect(self.resolve(f), ("fallback", "missing_evidence"))
        for field in ("path", "sha256"):
            f = self.fixture()
            f.loaded.pop(field)
            self.expect(self.resolve(f), ("fallback", "missing_evidence"))

    def test_loaded_fingerprint_when_untrusted_or_claim_only(self) -> None:
        for value, reason in ((None, "missing_evidence"), ("a" * 64, "source_mismatch"), ("claim", "source_mismatch")):
            f = self.fixture()
            f.loaded["sha256"] = value
            self.expect(self.resolve(f), ("fallback", reason))

    def test_metadata_denied_when_identity_fields_are_wrong(self) -> None:
        for host, field, value in (("opencode", "name", "other"), ("opencode", "version", "other"),
                                   ("hermes", "schema_version", True), ("hermes", "package", "other")):
            f = self.fixture(host)
            identity = f.peer_root / ("manifest.json" if host == "hermes" else "package.json")
            document = read_json(identity)
            document[field] = value
            write_json(identity, document)
            self.expect(self.resolve(f), ("fallback", "source_mismatch"))

    def test_install_records_when_contradictory_or_unregistered(self) -> None:
        for defect in ("name", "source", "sha256", "duplicate-name", "duplicate-path", "skills_dir", "missing", "malformed"):
            with self.subTest(defect=defect):
                f = self.fixture("hermes")
                identity = f.peer_root / "manifest.json"
                document = read_json(identity)
                records = sequence(document["skills"])
                record = mapping(records[0])
                match defect:
                    case "name" | "source" | "sha256":
                        record[defect] = "0" * 64
                    case "duplicate-name" | "duplicate-path":
                        records.append({**record, "path" if defect == "duplicate-name" else "name": "other"})
                    case "skills_dir":
                        document["skills_dir"] = str(f.root)
                    case "missing":
                        record["path"] = "other/SKILL.md"
                    case "malformed":
                        records.append(None)
                write_json(identity, document)
                self.expect(self.resolve(f), ("fallback", "peer_missing" if defect == "missing" else "source_mismatch"))

    def test_required_files_when_missing_tampered_or_symlinked(self) -> None:
        for host in ("opencode", "hermes"):
            for kind in ("tamper", "missing", "symlink", "directory"):
                f = self.fixture(host)
                identity = "manifest.json" if host == "hermes" else "package.json"
                for relative in (*sequence(mapping(f.target["provenance"])["files"]), identity):
                    with self.subTest(host=host, kind=kind, file=relative):
                        path = f.peer_root / relative
                        original = path.read_bytes()
                        path.unlink()
                        if kind == "tamper":
                            path.write_bytes(b"tampered")
                        if kind == "symlink":
                            twin = f.root / "identical"
                            twin.write_bytes(original)
                            path.symlink_to(twin)
                        if kind == "directory":
                            path.mkdir()
                        self.expect(self.resolve(f), ("fallback", "source_mismatch"))
                        if path.is_dir():
                            path.rmdir()
                        elif path.is_symlink() or path.exists():
                            path.unlink()
                        path.write_bytes(original)

    def test_roles_when_missing_out_of_class_or_wrong_host(self) -> None:
        for host, skill in (("opencode", "tk-plan"), ("opencode", "tk-execute"), ("hermes", "tk-execute")):
            f = self.fixture(host, skill)
            original = deepcopy(mapping(f.snapshot["model_bindings"]))
            for slot in f.slots:
                for defect, reason in (("missing", "missing_evidence"), ("key", "model_mismatch"),
                                       ("provider", "model_mismatch"), ("model_id", "model_mismatch")):
                    with self.subTest(host=host, slot=slot, defect=defect):
                        bindings = deepcopy(original)
                        if defect == "missing":
                            bindings.pop(slot)
                        else:
                            mapping(sequence(mapping(bindings[slot])["members"])[0])["catalog_key" if defect == "key" else defect] = "foreign"
                        f.snapshot["model_bindings"] = bindings
                        self.expect(self.resolve(f), ("blocked", reason))

    def test_plural_bindings_when_collapsed_reordered_or_duplicated(self) -> None:
        for cls, slot, skill in (("reviewers", "momus", "tk-plan"), ("executors", "worker", "tk-execute")):
            for keys in (["fable51"], ["opus5", "fable51"], ["fable51", "fable51"], ["fable51", "opus5"]):
                f = self.fixture("opencode", skill)
                mapping(f.config["classes"])[cls] = ["fable51", "opus5"]
                f.snapshot["model_bindings"] = slot_bindings(f.catalog, "opencode", mapping(f.config["classes"]), f.slots)
                binding = mapping(mapping(f.snapshot["model_bindings"])[slot])
                binding["members"] = mapping(slot_bindings(f.catalog, "opencode", {cls: [key for key in keys]}, {slot: cls})[slot])["members"]
                self.expect(self.resolve(f), ("delegate", "compatible") if keys == ["fable51", "opus5"] else ("blocked", "capability_missing"))

    def test_all_reviewers_when_native_subset_has_its_own_order(self) -> None:
        for keys, outcome in ((["opus5", "fable51"], ("delegate", "compatible")),
                              (["opus5"], ("delegate", "compatible")),
                              (["opus5", "opus5"], ("blocked", "capability_missing"))):
            f = self.fixture()
            mapping(f.config["classes"])["reviewers"] = "all"
            binding = slot_bindings(f.catalog, "opencode", {"reviewers": [key for key in keys]}, {"momus": "reviewers"})
            mapping(f.snapshot["model_bindings"]).update(binding)
            result = self.resolve(f)
            self.expect(result, outcome)
            self.assertEqual(mapping(mapping(result["bindings"])["requested"])["reviewers"], "all")
            if outcome[0] == "delegate":
                self.assertEqual(mapping(mapping(mapping(result["bindings"])["effective"])["momus"])["members"], mapping(binding["momus"])["members"])

    def test_methods_when_unsupported_or_missing_intent(self) -> None:
        for field, value, reason in ROLE_ROWS:
            f = self.fixture()
            mapping(mapping(f.snapshot["model_bindings"])["root"])[field] = value
            self.expect(self.resolve(f), ("blocked", reason))
        f = self.fixture("hermes", "tk-review")
        mapping(mapping(f.snapshot["model_bindings"])["reviewers"])["method"] = "explicit_dispatch"
        f.snapshot["consents"] = []
        self.expect(self.resolve(f), ("fallback", "capability_missing"))

    def test_component_methods_when_home_proof_is_required(self) -> None:
        for method, home, outcome in (("configured", False, "delegate"), ("explicit_dispatch", False, "delegate"),
                                     ("delegate_route", False, "fallback"), ("delegate_route", True, "delegate")):
            f = self.fixture("hermes", "tk-review")
            mapping(mapping(f.snapshot["model_bindings"])["reviewers"])["method"] = method
            path = make_home(f.root, "run")
            f.snapshot["runtime_home"] = {key: path for key in ("path", "parent_home", "dispatcher_home")} if home else None
            with patch.object(self.api.capability_gates, "read_mountinfo", return_value="1 0 1:1 / / rw - ext4 /dev/a rw\n"):
                result = self.resolve(f)
            self.expect(result, (outcome, "compatible" if outcome == "delegate" else "unsafe_runtime_home"))
            self.assertEqual(result["runtime_home"], path if home and method == "delegate_route" else None)

    def test_home_when_structure_or_process_identity_is_unproven(self) -> None:
        for variant in HOME_ROWS:
            with self.subTest(variant=variant):
                f = self.fixture("hermes", "tk-execute")
                f.snapshot["runtime_home"] = home_variant(f.root, variant)
                self.expect(self.resolve(f), ("blocked", "unsafe_runtime_home"))

    def test_home_when_local_filesystem_is_proven_or_unavailable(self) -> None:
        for filesystem in ("ext4", "xfs", "btrfs", "fuseblk", "overlay", "nfs", None):
            f = self.fixture("hermes", "tk-execute")
            path = make_home(f.root, "run")
            f.snapshot["runtime_home"] = {key: path for key in ("path", "parent_home", "dispatcher_home")}
            data = f"1 0 1:1 / / rw - {filesystem} /dev/a rw\n" if filesystem else None
            with patch.object(self.api.capability_gates, "read_mountinfo", return_value=data):
                result = self.resolve(f)
            valid = filesystem in ("ext4", "xfs", "btrfs")
            self.expect(result, ("delegate", "compatible") if valid else ("blocked", "unsafe_runtime_home"))
            self.assertEqual(result["runtime_home"], path if valid else None)

    def test_mount_type_when_prefixes_escapes_or_records_overlap(self) -> None:
        records = "1 0 1:1 / / rw - overlay overlay rw\n2 1 1:2 / /work rw - ext4 /dev/a rw\n3 1 1:3 / /work/deep rw - nfs host rw\n"
        for path, expected in (("/worker", "overlay"), ("/work/a", "ext4"), ("/work/deep/a", "nfs")):
            self.assertEqual(self.api.capability_gates.mount_type(path, records), expected)
        for encoded, decoded in ((r"a\040b", "a b"), (r"a\134040", r"a\040"), ("日本語", "日本語"), ("a\u2028b", "a\u2028b")):
            data = f"1 0 1:1 / /{encoded} rw - xfs /dev/a rw\n"
            self.assertEqual(self.api.capability_gates.mount_type(f"/{decoded}/child", data), "xfs")
        for malformed in ("", "1 0 1:1 / /work rw -", "1 0 1:1 / /work rw - ext4", "truncated\n",
                          records + "4 1 1:4 / /work/deep rw -\n", "1 0 1:1 / /work\\141 rw - ext4 a rw\n"):
            self.assertIsNone(self.api.capability_gates.mount_type("/work/a", malformed))

    def test_mount_read_when_platform_or_filesystem_data_is_unavailable(self) -> None:
        for platform in ("linux", "darwin"):
            with patch.object(self.api.capability_gates.sys, "platform", platform), patch("builtins.open", side_effect=OSError):
                self.assertIsNone(self.api.capability_gates.read_mountinfo())


if __name__ == "__main__":
    unittest.main()
