"""Explicit private installation and owned uninstall, isolated from host state."""
from __future__ import annotations

from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"


class CheckpointInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="tk-install-", dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / "project"
        self.backup = self.base / "approved-store"
        self.home = self.base / "empty-home"
        self.template = self.base / "template"
        for directory in (self.project, self.backup, self.home, self.template):
            directory.mkdir()
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home),
            "TMPDIR": str(self.base), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file", "GIT_OPTIONAL_LOCKS": "0",
        }
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.enterContext(patch.object(sys, "path", [str(REFERENCES), *sys.path]))
        self.enterContext(patch.dict(sys.modules))
        self.c = importlib.import_module("continuity_config")
        self.s = importlib.import_module("backup_store")
        self.api = importlib.import_module("checkpoint_setup")
        self.git("init", "--initial-branch=work", f"--template={self.template}")
        (self.project / ".thunderkit").mkdir()
        (self.project / ".gitignore").write_text(".thunderkit/*\n")
        (self.project / "notes.md").write_text("# Work to preserve\n")
        self.identity = self.c.ScopeIdentity("project", "install-fixture")
        settings = self.c.resolve_continuity(self.project, expected_identity=self.identity, environment=self.env)
        self.settings = replace(settings, backup_root=self.backup, backup=self.c.BackupPolicy(enabled=True))
        self.integration = self.project / ".thunderkit/checkpoint"
        self.private = self.project / ".thunderkit/continuity.local.json"
        self.request = self.api.SetupRequest(self.settings, (self.project / "notes.md",), self.integration)

    def git(self, *args: str) -> str:
        process = subprocess.run(["/usr/bin/git", *args], cwd=self.project, env=self.env,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, "Isolated Git fixture failed")
        return process.stdout.strip()

    def test_approved_setup_records_first_snapshot_and_uninstall_preserves_backup(self) -> None:
        preview = self.api.preview_setup(self.request)
        self.assertEqual(preview.status, "approval_required")
        config_before = (self.project / ".git/config").read_bytes()
        result = self.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        self.assertEqual(set(result.changed_paths), {self.private, self.integration / "config.json"})
        self.assertIsNotNone(result.receipt)
        local = json.loads(self.private.read_text())
        self.c.validate_continuity_document(local, kind="project-settings")
        self.assertEqual(local["identity"], {"kind": "project", "id": "install-fixture"})
        self.assertEqual(local["backup_root"], str(self.backup))
        manifest_path = Path(local["latest_context"]["path"])
        manifest = self.s.verify_snapshot(manifest_path, self.identity, self.settings.limits)
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual({item.relpath for item in manifest.files}, {"notes.md"})
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.integration / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.project / ".thunderkit/config.json").exists())
        self.assertEqual((self.project / ".git/config").read_bytes(), config_before)
        self.assertEqual(tuple(self.home.iterdir()), ())
        uninstall = getattr(self.api, "uninstall_setup", None)
        self.assertTrue(callable(uninstall), "Missing owned uninstall")
        assert callable(uninstall)
        denied = uninstall(result.receipt)
        self.assertEqual(denied.status, "approval_required")
        self.assertTrue(self.private.is_file())
        removed = uninstall(result.receipt, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        self.assertFalse(self.private.exists())
        self.assertFalse((self.integration / "config.json").exists())
        self.assertEqual(self.s.verify_snapshot(manifest_path, self.identity, self.settings.limits), manifest)
        self.assertEqual((self.project / ".git/config").read_bytes(), config_before)

    def test_stale_preview_cannot_write_private_state_or_probe_replacement_root(self) -> None:
        preview = self.api.preview_setup(self.request)
        self.assertEqual(preview.status, "approval_required")
        self.backup.rename(self.base / "original-store")
        self.backup.mkdir()
        result = self.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "blocked")
        self.assertIn("stale_preview", result.blockers)
        self.assertFalse(self.private.exists())
        self.assertFalse(self.integration.exists())
        self.assertEqual(tuple(self.backup.iterdir()), ())

    def test_uninstall_does_not_remove_changed_owned_or_unrelated_files(self) -> None:
        result = self.api.apply_setup(self.api.preview_setup(self.request), approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        uninstall = getattr(self.api, "uninstall_setup", None)
        self.assertTrue(callable(uninstall), "Missing owned uninstall")
        assert callable(uninstall)
        extra = self.integration / "user-note.md"
        extra.write_text("Unrelated user addition\n")
        original = self.private.read_bytes()
        self.private.write_bytes(original + b" ")
        blocked = uninstall(result.receipt, approval=True)
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("owned_file_changed", blocked.blockers)
        self.assertEqual(self.private.read_bytes(), original + b" ")
        self.assertTrue((self.integration / "config.json").is_file())
        self.assertEqual(extra.read_text(), "Unrelated user addition\n")


if __name__ == "__main__":
    unittest.main()
