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
        self.addCleanup(self.fixture.doCleanups)
        self.source, self.manifest = self.fixture.snapshot_without_checkout()
        self.command = fixtures.RESTORE
        self.identity = self.fixture.identity.id
        self.environment = dict(os.environ)
        self.environment.pop("PYTHONPATH", None)

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
