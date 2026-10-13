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

    def test_lock_release_failure_reports_installed_files_and_recovery_state(self) -> None:
        preview = self.api.preview_setup(self.request)
        original = os.rmdir

        def fail_lock(path: object, *args: object, **kwargs: object) -> None:
            if path == ".thunderkit-setup.lock":
                raise PermissionError("injected owned lock release failure")
            original(path, *args, **kwargs)

        with patch.object(self.api.os, "rmdir", side_effect=fail_lock):
            result = self.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "blocked")
        existing = {path for path in (self.private, self.integration / "config.json") if path.exists()}
        self.assertTrue(existing <= set(result.changed_paths) | set(result.retained_paths),
                        "A failed return must disclose installed files")
        if existing:
            self.assertIsNotNone(result.receipt, "Completed installation needs recoverable ownership data")

    def test_user_global_ignore_cannot_approve_unignored_project_private_files(self) -> None:
        (self.project / ".gitignore").write_text("# No private exclusions in this project\n")
        ignore = self.home / "git/ignore"
        ignore.parent.mkdir()
        ignore.write_text(".thunderkit/\n")
        preview = self.api.preview_setup(self.request)
        self.assertEqual(preview.status, "blocked")
        self.assertIn("private_storage_unignored", preview.blockers)
        self.assertEqual(preview.mutations, ())
        self.assertFalse(self.private.exists())

    def test_existing_backup_identity_namespace_is_not_adopted_at_first_setup(self) -> None:
        other = self.base / "other-project"
        other.mkdir()
        note = other / "notes.md"
        note.write_text("Other project owns this backup namespace\n")
        storage = self.s.qualify_storage(self.s.inspect_storage(self.backup), approval=True).identity
        settings = replace(self.settings, project_root=other)
        captured = self.s.create_snapshot(self.c.SnapshotRequest(settings, storage, (note,)))
        self.assertEqual(captured.status, "complete")
        latest = self.backup / "projects/install-fixture/latest.json"
        previous = latest.read_bytes()
        preview = self.api.preview_setup(self.request)
        self.assertEqual(preview.status, "blocked")
        self.assertIn("scope_exists", preview.blockers)
        self.assertEqual(self.api.apply_setup(preview, approval=True).status, "blocked")
        self.assertEqual(latest.read_bytes(), previous)
        self.assertFalse(self.private.exists())

    def test_transient_source_membership_change_cannot_install_unpreviewed_snapshot(self) -> None:
        source = self.project / "notes"
        source.mkdir()
        (self.project / "notes.md").rename(source / "notes.md")
        request = replace(self.request, approved_sources=(source,))
        preview = self.api.preview_setup(request)
        self.assertEqual(preview.status, "approval_required")
        original = self.s.create_snapshot
        source_info = source.stat()

        def changed_capture(snapshot_request: object) -> object:
            extra = source / "unpreviewed.md"
            extra.write_text("Created only while capture was running\n")
            captured = original(snapshot_request)
            extra.unlink()
            os.utime(source, ns=(source_info.st_atime_ns, source_info.st_mtime_ns + 10_000_000_000))
            return captured

        with patch.object(self.s, "create_snapshot", side_effect=changed_capture):
            result = self.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "blocked")
        self.assertIn("stale_preview", result.blockers)
        self.assertFalse(self.private.exists())
        self.assertFalse((self.integration / "config.json").exists())

    def test_whole_committed_project_setup_saves_selected_context_and_history(self) -> None:
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", "--all")
        self.git("commit", "-m", "committed fixture")
        head = self.git("rev-parse", "HEAD")
        (self.project / "notes.md").write_text("# Dirty saved context\n")
        try:
            request = self.api.SetupRequest(
                settings=self.settings, approved_sources=(self.project,),
                integration_root=self.integration, context_entrypoints=("notes.md",),
                include_history=True, history_ref="refs/heads/work",
            )
        except TypeError:
            self.fail("Setup lacks explicit context-entrypoint and branch-history options")
        preview = self.api.preview_setup(request)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        result = self.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        local = json.loads(self.private.read_text())
        selected = Path(local["latest_context"]["path"])
        manifest = self.s.verify_snapshot(selected, self.identity, self.settings.limits)
        self.assertEqual(manifest.history.status, "bundled")
        self.assertEqual(manifest.head, head)
        self.assertEqual(manifest.context_entrypoints, ("notes.md",))
        self.assertEqual(self.s.read_payload(selected, "notes.md", self.identity, self.settings.limits),
                         b"# Dirty saved context\n")
        self.assertFalse(any(item.relpath.startswith(".git/") or
                             item.relpath.startswith(".thunderkit/checkpoint/") or
                             item.relpath == ".thunderkit/continuity.local.json"
                             for item in manifest.files))
        self.assertEqual((self.project / "notes.md").read_text(), "# Dirty saved context\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
