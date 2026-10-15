"""Runtime deadlines must not interrupt nested snapshot-store cleanup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import sys
import unittest
from typing import Any
from unittest.mock import patch

import test_checkpoint_deadline as deadlines
import test_checkpoint_git_runtime as git_fixtures
import test_checkpoint_runtime as fixtures


def in_snapshot_removal() -> bool:
    frame = sys._getframe(1)
    while frame is not None:
        if (frame.f_code.co_name == "_owned_remove"
                and frame.f_code.co_filename.endswith("backup_store.py")):
            return True
        frame = frame.f_back
    return False


class SnapshotDeadlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.RuntimeFixture()
        self.fixture.setUp()
        self.addCleanup(self.cleanup_fixture)
        self.api = self.fixture.runtime_api("snapshot_deadline_runtime")
        self.handler = signal.getsignal(signal.SIGALRM)
        self.mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def cleanup_fixture(self) -> None:
        self.assertTrue(self.fixture.doCleanups(), "Nested fixture cleanup must not hide errors")
        self.assertFalse(self.fixture.base.exists())
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), self.handler)
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), self.mask)
        self.assertNotIn(signal.SIGALRM, signal.sigpending())

    def test_completed_capture_cleanup_alarm_releases_inner_lock_for_retry(self) -> None:
        f = self.fixture
        original_stat = os.stat
        armed: list[bool] = []

        def late_stat(path: Any, **kwargs: Any) -> os.stat_result:
            if path == ".snapshot.lock" and in_snapshot_removal() and not armed:
                armed.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
                deadlines.expire_if_armed()
            return original_stat(path, **kwargs)

        with patch.object(os, "stat", side_effect=late_stat):
            result = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(len(armed), 1, "Reach the actual nested cleanup window")
        if armed[0]:
            self.assertEqual(result.status, "failed")
            self.assertIn("deadline_exceeded", result.warnings)
        else:
            self.assertIn(result.status, ("failed", "captured"))
        manifest_path = Path(json.loads(f.locator.read_text())["path"])
        manifest = f.s.verify_snapshot(manifest_path, f.identity, f.settings.limits)
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(f.s.read_payload(manifest_path, "notes.md", f.identity, f.settings.limits), fixtures.CONTEXT)
        self.assertFalse((f.state_dir / ".checkpoint.lock").exists())
        self.assertFalse((f.locator.parent / ".snapshot.lock").exists(),
                         "H12-lock: nested snapshot cleanup must release its owned lock")
        retry = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(retry.status, "captured", retry.warnings)
        self.assertEqual(retry.snapshot.status, "unchanged")

    def test_failed_capture_cleanup_alarm_preserves_prior_locator(self) -> None:
        f = self.fixture
        prior = f.locator.read_bytes()
        actual_create = f.s.create_snapshot
        original_stat = os.stat
        failed: list[bool] = []
        armed: list[bool] = []

        def create_with_fault(request: Any, *, fault: Any = None, **kwargs: Any) -> Any:
            def before_publish(event: str, path: Path | None) -> None:
                if fault is not None:
                    fault(event, path)
                if event == "before_manifest_publish":
                    failed.append(True)
                    raise OSError("Injected snapshot publication failure")
            return actual_create(request, fault=before_publish, **kwargs)

        def late_stat(path: Any, **kwargs: Any) -> os.stat_result:
            parent = kwargs.get("dir_fd")
            if (failed and not armed and in_snapshot_removal() and isinstance(path, str)
                    and parent is not None and os.readlink(f"/proc/self/fd/{parent}").endswith("/.staging")):
                armed.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
                deadlines.expire_if_armed()
            return original_stat(path, **kwargs)

        with patch.object(f.s, "create_snapshot", side_effect=create_with_fault), \
                patch.object(os, "stat", side_effect=late_stat):
            result = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(failed, [True])
        self.assertEqual(len(armed), 1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(f.locator.read_bytes(), prior)
        f.assert_snapshot(f.control, fixtures.INITIAL_CONTEXT)
        self.assertFalse((f.state_dir / ".checkpoint.lock").exists())
        self.assertEqual(tuple((f.locator.parent / ".staging").iterdir()), (),
                         "H12-stage: failed capture must remove its exact owned staging tree")
        self.assertFalse((f.locator.parent / ".snapshot.lock").exists())
        self.assertIn("checkpoint_io_or_input_failed", result.warnings,
                      "A deferred deadline must not replace the primary capture error")
        retry = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(retry.status, "captured", retry.warnings)


class SnapshotGitDeadlineTests(unittest.TestCase):
    def test_alarm_during_internal_git_teardown_leaves_no_orphan(self) -> None:
        case = git_fixtures.GitCheckpointRuntimeTests()
        case.setUp()
        root = case.fixture.base
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            actual_rmtree = shutil.rmtree
            locator = case.backups / "projects" / case.fixture.identity.id / "latest.json"
            armed: list[bool] = []

            def late_remove(path: Any, *args: Any, **kwargs: Any) -> None:
                # Only the nested snapshot session: outer-runtime teardown runs
                # after state.json is written and is covered by runtime tests.
                if (Path(path).name.startswith("thunderkit-S-git-") and locator.exists()
                        and not (case.state / "state.json").exists() and not armed):
                    armed.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
                    deadlines.expire_if_armed()
                actual_rmtree(path, *args, **kwargs)

            with patch.object(shutil, "rmtree", side_effect=late_remove):
                result = case.api.checkpoint(case.request(), clock=case.clock)
            self.assertEqual(len(armed), 1)
            if armed[0]:
                self.assertEqual(result.status, "failed")
                self.assertIn("deadline_exceeded", result.warnings)
            else:
                self.assertIn(result.status, ("failed", "captured"))
            manifest_path = Path(json.loads(locator.read_text())["path"])
            manifest = case.s.verify_snapshot(manifest_path, case.fixture.identity, case.settings.limits)
            self.assertEqual((manifest.completion, manifest.history.status), ("complete", "bundled"))
            self.assertEqual(tuple(root.glob("thunderkit-S-git-*")), (),
                             "H12-git: snapshot-owned scratch must finish cleanup before deadline propagation")
            self.assertFalse((locator.parent / ".snapshot.lock").exists())
            self.assertFalse((case.state / ".checkpoint.lock").exists())
            retry = case.api.checkpoint(case.request(), clock=case.clock)
            self.assertEqual(retry.status, "captured", retry.warnings)
            self.assertEqual(retry.snapshot.status, "unchanged")
        finally:
            self.assertTrue(case.doCleanups(), "Nested Git fixture cleanup must not hide errors")
            self.assertFalse(root.exists())
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
            self.assertNotIn(signal.SIGALRM, signal.sigpending())


if __name__ == "__main__":
    unittest.main()
