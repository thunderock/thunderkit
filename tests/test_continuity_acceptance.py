"""Integrated setup/capture/loss/recovery proof, never real-host activation."""
from __future__ import annotations

from dataclasses import replace
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_checkpoint_host as fixtures


class ContinuityAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = fixtures.CheckpointHostTests()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.f = self.case.fixture
        self.host = self.case.host()
        self.restore = importlib.import_module("restore_context")

    def clean(self) -> None:
        self.assertTrue(self.case.doCleanups())
        self.assertFalse(self.case.fixture.base.exists())

    def test_manual_commit_then_loss_recovers_current_files_and_unpublished_graph(self) -> None:
        f, case = self.f, self.case
        (f.project / ".gitignore").write_text(".thunderkit/*\nignored.md\n")
        obsolete = f.project / "obsolete.txt"
        obsolete.write_bytes(b"deleted after this commit\n")
        f.git("add", "--", ".gitignore", "obsolete.txt")
        f.git("commit", "-m", "record deletion candidate")
        obsolete.unlink()
        program = f.project / "run.sh"
        program.write_bytes(b"#!/bin/sh\nprintf should-not-run\n")
        program.chmod(0o755)
        untracked = f.project / "new.txt"
        untracked.write_bytes(b"uncommitted file remains recoverable\n")
        ignored = f.project / "ignored.md"
        ignored.write_bytes(b"# Explicitly approved ignored context\n")
        # Root scope covers ordinary tracked/untracked files; exact ignored file
        # approval is independent and must not be inferred from root membership.
        snapshot = replace(case.snapshot, approved_sources=(f.project, ignored),
                           context_entrypoints=("notes.md", "ignored.md"))
        installation = self.host.apply_connections(self.host.preview_connections(
            self.host.HostRequest(snapshot, case.state, case.request(self.host).python)), approval=True)
        self.assertEqual(installation.status, "configured", installation.blockers)
        current = b"# Current saved context before machine loss\n"
        (f.project / "notes.md").write_bytes(current)
        f.git("commit", "--allow-empty", "-m", "unpublished manual event")
        head = f.git("rev-parse", "HEAD")
        result = f.c.read_json(case.state / "last-result.json")
        self.assertEqual(result["status"], "captured", result)
        self.assertEqual(result["push"]["status"], "denied")
        saved = Path(result["snapshot"]["snapshot_path"])
        manifest = f.s.verify_snapshot(saved, f.identity, f.settings.limits)
        self.assertEqual(manifest.completion, "complete", manifest.omissions)
        self.assertIn("obsolete.txt", manifest.deleted_paths)
        self.assertEqual({item.relpath for item in manifest.files},
                         {".gitignore", "notes.md", "new.txt", "run.sh", "ignored.md"})
        self.assertEqual(manifest.head, head)
        removed = self.host.remove_connections(installation.receipt, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        shutil.rmtree(f.project)
        shutil.rmtree(case.state)
        self.assertFalse(f.project.exists())
        self.assertFalse(case.state.exists())
        loaded = self.restore.load_context(saved, f.identity, f.settings.limits)
        self.assertTrue(loaded.integrity_verified)
        self.assertIn(current.decode(), loaded.text)
        destination = f.base / "recovered"
        preview = self.restore.export_preview(saved, destination, f.identity, f.settings.limits)
        self.assertEqual(preview.working_tree_omissions, ())
        self.assertFalse(destination.exists())
        with self.assertRaises(f.c.ContinuityError) as denied:
            self.restore.export_snapshot(saved, destination, f.identity, f.settings.limits)
        self.assertEqual(denied.exception.code, "approval_required")
        self.assertFalse(destination.exists())
        exported = self.restore.export_snapshot(saved, destination, f.identity, f.settings.limits,
            approval=True, expected_manifest_sha256=preview.manifest_sha256)
        self.assertEqual(exported.status, "complete", exported)
        for name, data in (("notes.md", current), ("new.txt", b"uncommitted file remains recoverable\n"),
                           ("run.sh", b"#!/bin/sh\nprintf should-not-run\n"),
                           ("ignored.md", b"# Explicitly approved ignored context\n")):
            self.assertEqual((destination / name).read_bytes(), data)
        self.assertEqual((destination / "run.sh").stat().st_mode & 0o777, 0o755)
        self.assertFalse((destination / "obsolete.txt").exists())
        process = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=destination,
                                 env=f.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), head)
        for path in (destination / ".git/hooks", destination / ".thunderkit/continuity.local.json"):
            self.assertFalse(path.exists())
        self.assertFalse(f.project.exists(), "Recovery must not recreate original checkout")
        self.assertFalse(case.state.exists(), "Recovery must not restore runtime authority")


if __name__ == "__main__":
    unittest.main()
