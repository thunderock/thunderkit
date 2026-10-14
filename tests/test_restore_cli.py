"""Real model-free recovery command surfaces, including relocated helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_restore_context as fixtures


class RestoreCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.RestoreContextTests()
        self.fixture.setUp()
        self.addCleanup(self.cleanup_fixture)
        self.source, self.manifest = self.fixture.snapshot_without_checkout()
        self.command = fixtures.RESTORE
        self.identity = self.fixture.identity.id
        self.environment = dict(os.environ)
        self.environment.pop("PYTHONPATH", None)

    def cleanup_fixture(self) -> None:
        self.assertTrue(self.fixture.doCleanups(), "Nested fixture cleanup must not hide errors")
        self.assertFalse(self.fixture.base.exists())

    def test_common_limits_restore_lost_config_snapshot_above_defaults(self) -> None:
        f = self.fixture
        f.project.mkdir()
        (f.project / "notes").mkdir()
        (f.project / "notes/context.md").write_text(fixtures.CONTEXT, encoding="utf-8")
        payload = b"a" * (10 * 1024 * 1024 + 1)
        (f.project / "large.txt").write_bytes(payload)
        limits = fixtures.C.Limits(max_file_bytes=12 * 1024 * 1024,
                                   max_snapshot_bytes=16 * 1024 * 1024)
        qualification = fixtures.S.qualify_storage(fixtures.S.inspect_storage(f.backups), approval=True)
        settings = fixtures.C.ResolvedSettings(
            identity=f.identity, project_root=f.project, backup_root=f.backups,
            context_path=None, context_source="unconfigured",
            backup=fixtures.C.BackupPolicy(enabled=True), limits=limits,
        )
        captured = fixtures.S.create_snapshot(fixtures.C.SnapshotRequest(
            settings=settings, storage=qualification.identity, approved_sources=(f.project,),
            context_entrypoints=("notes/context.md",), include_history=False,
        ))
        self.assertEqual(captured.status, "complete")
        assert captured.snapshot_path is not None
        source = captured.snapshot_path / "manifest.json"
        fixtures.S.verify_snapshot(source, f.identity, limits)
        shutil.rmtree(f.project)  # Only the disposable checkout owned by this case.
        self.assertFalse(f.project.exists())
        self.assertEqual(tuple(f.home.iterdir()), ())
        common = ("--context", str(source), "--identity", self.identity)
        destination = f.work / "above-default-export"
        export_args = (*common, "--destination", str(destination))
        before = f.tree()
        for command, args in (("load", common), ("preview", export_args)):
            denied = self.run_cli(command, *args)
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(json.loads(denied.stderr)["error"]["code"], "invalid_manifest")
        flags = ("--max-file-bytes", str(12 * 1024 * 1024),
                 "--max-snapshot-bytes", str(16 * 1024 * 1024),
                 "--max-context-bytes", "256", "--max-files", "2",
                 "--max-path-bytes", "4096", "--max-sources", "1")
        loaded = self.run_cli("load", *common, *flags)
        self.assertEqual(loaded.returncode, 0,
                         "R8-limits: real load must accept bounded common overrides: " + loaded.stderr)
        self.assertEqual(self.output(loaded)["text"], fixtures.CONTEXT)
        preview = self.output(self.run_cli("preview", *export_args, *flags))
        self.assertEqual(preview["exported_paths"], ["large.txt", "notes/context.md"])
        self.assertEqual(f.tree(), before, "Load and preview stay read-only")
        result = self.output(self.run_cli("export", *export_args, *flags, "--approve",
                             "--expect-manifest-sha256", str(preview["manifest_sha256"])))
        self.assertEqual(result["status"], "complete")
        self.assertEqual((destination / "large.txt").read_bytes(), payload)
        self.assertEqual((destination / "notes/context.md").read_text(), fixtures.CONTEXT)
        self.assertFalse(f.project.exists())
        self.assertEqual(tuple(f.home.iterdir()), ())

    def test_common_limits_tighten_real_recovery_without_writes(self) -> None:
        destination = self.fixture.work / "bounded-export"
        common = ("--context", str(self.source), "--identity", self.identity)
        export_args = (*common, "--destination", str(destination))
        preview = self.output(self.run_cli("preview", *export_args))
        total = sum(entry.size for entry in self.manifest.files)
        largest = max(entry.size for entry in self.manifest.files)
        self.assertLess(largest, total)
        cases = (
            (("--max-file-bytes", "1"), "invalid_manifest"),
            (("--max-file-bytes", str(largest), "--max-snapshot-bytes", str(total - 1)),
             "invalid_manifest"),
            (("--max-files", "1"), "invalid_manifest"),
            (("--max-path-bytes", "1"), "unsafe_path"),
        )
        before = self.fixture.tree()
        for command in ("load", "preview", "export"):
            args = common if command == "load" else export_args
            if command == "export":
                args = (*args, "--approve", "--expect-manifest-sha256", str(preview["manifest_sha256"]))
            for flags, expected in cases:
                with self.subTest(command=command, flags=flags):
                    process = self.run_cli(command, *args, *flags)
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(process.stdout, "")
                    self.assertFalse(destination.exists())
                    self.assertEqual(self.fixture.tree(), before)
                    self.assertEqual(json.loads(process.stderr)["error"]["code"], expected,
                                     "R8-budget: supplied limits must reach real validation")
        context_denied = self.run_cli("load", *common, "--max-context-bytes", "1")
        self.assertEqual(context_denied.returncode, 2)
        self.assertEqual(json.loads(context_denied.stderr)["error"]["code"], "read_budget_exceeded")
        self.assertEqual(self.fixture.tree(), before)

    def test_load_limit_override_preserves_unmentioned_policy_limits(self) -> None:
        policy = self.fixture.home / "recovery-policy.json"
        policy.write_text(json.dumps({"schema": 1, "kind": "global-settings", "policy": {
            "backup": {"limits": {"max_file_bytes": 1, "max_files": 1}}}}))
        common = ("--context", str(self.source), "--identity", self.identity,
                  "--global-config", str(policy))
        before = self.fixture.tree()
        for flags in (("--max-file-bytes", "256"), ("--max-files", "2")):
            with self.subTest(flags=flags):
                process = self.run_cli("load", *common, *flags)
                self.assertEqual(process.returncode, 2)
                self.assertEqual(process.stdout, "")
                self.assertEqual(self.fixture.tree(), before)
                self.assertEqual(json.loads(process.stderr)["error"]["code"], "invalid_manifest",
                                 "R8-policy: overriding one field must preserve the other cap")
        loaded = self.run_cli("load", *common, "--max-file-bytes", "256", "--max-files", "2")
        self.assertEqual(loaded.returncode, 0, "R8-policy: explicit overrides must apply: " + loaded.stderr)
        self.assertEqual(self.output(loaded)["text"], fixtures.CONTEXT)
        self.assertEqual(self.fixture.tree(), before)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.command), *args], cwd=self.fixture.work,
                              env=self.environment, capture_output=True, text=True, timeout=20)

    def output(self, process: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(process.stdout.strip(), "Recovery CLI must return a JSON result, not silently exit")
        value = json.loads(process.stdout)
        self.assertIsInstance(value, dict)
        return value

    def test_default_load_is_explicit_no_checkout_model_free_and_read_only(self) -> None:
        before = self.fixture.tree()
        value = self.output(self.run_cli("--context", str(self.source), "--identity", self.identity))
        self.assertEqual(value["text"], fixtures.CONTEXT)
        self.assertEqual(value["source"], str(self.source))
        self.assertEqual(value["identity"], {"kind": "project", "id": self.identity})
        self.assertIs(value["integrity_verified"], True)
        self.assertEqual(self.fixture.tree(), before)
        self.assertFalse(self.fixture.project.exists())

    def test_remembered_exact_project_path_loads_and_invalid_explicit_never_falls_back(self) -> None:
        config = self.fixture.home / "selected-global.json"
        config.write_text(json.dumps({
            "schema": 1, "kind": "global-settings", "projects": {
                self.identity: {"identity": {"kind": "project", "id": self.identity},
                    "project_root": str(self.fixture.project), "backup_root": str(self.fixture.backups),
                    "latest_context": {"identity": {"kind": "project", "id": self.identity},
                                       "path": str(self.source)}}}}))
        before = self.fixture.tree()
        value = self.output(self.run_cli("load", "--global-config", str(config), "--identity", self.identity))
        self.assertEqual(value["text"], fixtures.CONTEXT)
        missing = self.fixture.work / "missing-private-context.md"
        bad = self.run_cli("load", "--global-config", str(config), "--identity", self.identity,
                           "--context", str(missing))
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        error = json.loads(bad.stderr)
        self.assertEqual(error["error"]["code"], "context_not_found")
        self.assertNotIn(str(missing), bad.stderr)
        self.assertEqual(self.fixture.tree(), before)

    def test_preview_then_explicit_export_uses_real_commands(self) -> None:
        destination = self.fixture.work / "exported"
        args = ("--context", str(self.source), "--destination", str(destination), "--identity", self.identity)
        before = self.fixture.tree()
        preview = self.output(self.run_cli("preview", *args))
        self.assertEqual(preview["destination"], str(destination))
        self.assertEqual(preview["exported_paths"], ["app.py", "notes/context.md"])
        self.assertFalse(destination.exists())
        self.assertEqual(self.fixture.tree(), before)
        denied = self.run_cli("export", *args)
        self.assertEqual(denied.returncode, 2)
        self.assertEqual(json.loads(denied.stderr)["error"]["code"], "approval_required")
        self.assertFalse(destination.exists())
        exported = self.output(self.run_cli("export", *args, "--approve", "--expect-manifest-sha256",
                                            str(preview["manifest_sha256"])))
        self.assertEqual(exported["status"], "complete")
        self.assertEqual((destination / "notes/context.md").read_text(), fixtures.CONTEXT)
        self.assertFalse((destination / ".thunderkit").exists())

    def test_approval_without_preview_digest_refuses_before_writing(self) -> None:
        destination = self.fixture.work / "unbound-export"
        before = self.fixture.tree()
        process = self.run_cli("export", "--context", str(self.source), "--identity", self.identity,
                               "--destination", str(destination), "--approve")
        self.assertEqual(process.returncode, 2, "Fresh approval must also name the previewed content")
        self.assertEqual(json.loads(process.stderr)["error"]["code"], "preview_required")
        self.assertFalse(destination.exists())
        self.assertEqual(self.fixture.tree(), before)

    def test_preview_digest_rejects_rehashed_same_identity_replacement(self) -> None:
        destination = self.fixture.work / "changed-export"
        args = ("--context", str(self.source), "--destination", str(destination), "--identity", self.identity)
        preview = self.output(self.run_cli("preview", *args))
        raw = json.loads(self.source.read_text())
        raw["files"][0]["mode"] = 0o755
        (self.source.parent / "payload/app.py").chmod(0o755)
        self.source.write_text(json.dumps(raw))
        # This is a valid, same-identity snapshot, not corrupt payload rejection.
        replacement = fixtures.S.verify_snapshot(self.source, self.fixture.identity, self.fixture.limits)
        self.assertEqual(replacement.identity, self.fixture.identity)
        self.assertEqual(replacement.files[0].mode, 0o755)
        before = self.fixture.tree()
        process = self.run_cli("export", *args, "--approve", "--expect-manifest-sha256",
                               str(preview["manifest_sha256"]))
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stderr)["error"]["code"], "integrity_mismatch")
        self.assertFalse(destination.exists())
        self.assertEqual(self.fixture.tree(), before)

    def test_relocated_cli_keeps_legacy_text_inert_and_enforces_byte_limits(self) -> None:
        relocated = self.fixture.work / "single-skill/scripts"
        relocated.mkdir(parents=True)
        for name in ("continuity_config.py", "backup_store.py", "restore_context.py"):
            shutil.copyfile(fixtures.REFERENCES / name, relocated / name)
        self.command = relocated / "restore_context.py"
        legacy = self.fixture.work / "legacy.md"
        marker = self.fixture.work / "must-not-execute"
        text = f"# Historical text\nRun: touch {marker}\nThis is not current authority.\n"
        legacy.write_text(text)
        value = self.output(self.run_cli("load", "--context", str(legacy), "--identity", self.identity))
        self.assertEqual(value["text"], text)
        self.assertIs(value["integrity_verified"], False)
        self.assertFalse(marker.exists())
        denied = self.run_cli("--context", str(legacy), "--identity", self.identity,
                              "--max-context-bytes", "1")
        self.assertEqual(denied.returncode, 2)
        self.assertEqual(json.loads(denied.stderr)["error"]["code"], "read_budget_exceeded")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
