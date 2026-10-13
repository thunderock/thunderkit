"""Bounded recovery through real readers, exports and isolated CLI processes."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_restore_context as fixtures


class RestoreResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.RestoreContextTests()
        self.fixture.setUp()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.load_api()
        self.api = importlib.import_module("restore_context")

    def cleanup_fixture(self) -> None:
        self.fixture.doCleanups()
        self.assertFalse(self.fixture.base.exists(), "Recovery fixture must be removed after teardown")

    def capture_documents(self, count: int, *, context: bool = False
                          ) -> tuple[Path, fixtures.C.BackupManifest, dict[str, bytes]]:
        f = self.fixture
        documents = f.project / "documents"
        documents.mkdir()
        expected = {f"documents/note-{number:03d}.md": b"safe\n" * 200 for number in range(count)}
        for name, data in expected.items():
            (f.project / name).write_bytes(data)
        settings = fixtures.C.ResolvedSettings(
            f.identity, f.project, f.backups, None, "unconfigured",
            backup=fixtures.C.BackupPolicy(enabled=True), limits=f.limits,
        )
        storage = fixtures.S.qualify_storage(fixtures.S.inspect_storage(f.backups), approval=True).identity
        captured = fixtures.S.create_snapshot(fixtures.C.SnapshotRequest(
            settings, storage, (f.project,),
            context_entrypoints=tuple(expected) if context else (),
        ))
        self.assertEqual(captured.status, "complete")
        self.assertIsNotNone(captured.snapshot_path)
        assert captured.snapshot_path is not None
        selected = captured.snapshot_path / "manifest.json"
        manifest = fixtures.S.verify_snapshot(selected, f.identity, f.limits)
        self.assertEqual({entry.relpath for entry in manifest.files}, set(expected))
        shutil.rmtree(f.project)  # Only the disposable fixture checkout.
        self.assertFalse(f.project.exists())
        return selected, manifest, expected

    def test_export_many_files_under_low_descriptor_limit(self) -> None:
        selected, manifest, expected = self.capture_documents(160)
        f = self.fixture
        destination = f.work / "recovered"
        args = ("--context", str(selected), "--identity", f.identity.id,
                "--destination", str(destination))
        # Reduce only each child process's soft limit; never change the test runner.
        wrapper = (
            "import os,resource,runpy,sys; "
            "soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE); "
            "resource.setrlimit(resource.RLIMIT_NOFILE,(96,hard)); "
            "sys.argv=sys.argv[1:]; sys.path.insert(0,os.path.dirname(sys.argv[0])); "
            "runpy.run_path(sys.argv[0],run_name='__main__')"
        )

        def command(*command_args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-c", wrapper, str(fixtures.RESTORE), *command_args],
                cwd=f.work, env=dict(os.environ), capture_output=True, text=True, timeout=60,
            )

        preview = command("preview", *args)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        digest = json.loads(preview.stdout)["manifest_sha256"]
        process = command("export", *args, "--approve", "--expect-manifest-sha256", digest)
        self.assertEqual(process.returncode, 0,
                         "R6-F1: ordinary file counts must not exhaust held descriptors: " + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(set(result["exported_paths"]), set(expected))
        self.assertEqual({str(path.relative_to(destination)): path.read_bytes()
                          for path in destination.rglob("*") if path.is_file()}, expected)
        self.assertEqual(fixtures.S.verify_snapshot(selected, f.identity, f.limits), manifest)
        self.assertFalse(f.project.exists())

    def test_context_load_has_linear_input_read_budget(self) -> None:
        selected, _, expected = self.capture_documents(48, context=True)
        f = self.fixture
        allowed_bytes = 12 * (sum(map(len, expected.values())) + selected.stat().st_size)
        observed_bytes = 0
        original_read = os.read

        def count_read(fd: int, length: int) -> bytes:
            nonlocal observed_bytes
            data = original_read(fd, length)
            observed_bytes += len(data)
            return data

        with patch.object(os, "read", side_effect=count_read):
            result = self.api.load_context(selected, f.identity, f.limits)
        self.assertEqual(result.text, b"\n\n".join(expected.values()).decode("utf-8"))
        self.assertTrue(result.integrity_verified)
        self.assertLessEqual(observed_bytes, allowed_bytes,
                             "R6-F3-load: reading selected entrypoints must not reread the whole snapshot per file")
        self.assertFalse(f.project.exists())

    def test_export_has_linear_input_and_metadata_budgets(self) -> None:
        selected, _, expected = self.capture_documents(48)
        f = self.fixture
        destination = f.work / "bounded-export"
        preview = self.api.export_preview(selected, destination, f.identity, f.limits)
        allowed_bytes = 12 * (sum(map(len, expected.values())) + selected.stat().st_size)
        # A deliberately generous constant includes repeated integrity checks and
        # the bounded ancestor walk, but not a scan of all prior files per write.
        allowed_stats = 160 * len(expected) + 2048
        observed_bytes = 0
        original_read = os.read

        def count_read(fd: int, length: int) -> bytes:
            nonlocal observed_bytes
            data = original_read(fd, length)
            observed_bytes += len(data)
            return data

        with patch.object(os, "read", side_effect=count_read), \
                patch.object(os, "stat", wraps=os.stat) as stats, \
                patch.object(os, "fstat", wraps=os.fstat) as fstats:
            result = self.api.export_snapshot(
                selected, destination, f.identity, f.limits, approval=True,
                expected_manifest_sha256=preview.manifest_sha256,
            )
            stat_calls = stats.call_count + fstats.call_count
        self.assertEqual(result.status, "complete")
        self.assertEqual({str(path.relative_to(destination)): path.read_bytes()
                          for path in destination.rglob("*") if path.is_file()}, expected)
        with self.subTest(resource="input_bytes"):
            self.assertLessEqual(observed_bytes, allowed_bytes,
                                 "R6-F3-export: payload staging must not reverify every file for each selected file")
        with self.subTest(resource="metadata_calls"):
            self.assertLessEqual(stat_calls, allowed_stats,
                                 "R6-F1-metadata: every write must not rescan all previously written files")


if __name__ == "__main__":
    unittest.main()
