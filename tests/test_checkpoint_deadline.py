"""Late deadline delivery must not strand owned checkpoint resources."""
from __future__ import annotations

import json
import os
import signal
import time
import unittest
from unittest.mock import patch

import test_checkpoint_git_runtime as git_fixtures
import test_checkpoint_runtime as fixtures


def expire_if_armed() -> None:
    """Move the operation's existing alarm into the observed cleanup window."""
    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        signal.setitimer(signal.ITIMER_REAL, 0.005)
        time.sleep(0.025)


class CheckpointDeadlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.RuntimeFixture()
        self.fixture.setUp()
        self.addCleanup(self.cleanup_fixture)
        self.api = self.fixture.runtime_api("checkpoint_runtime_deadline")
        self.handler = signal.getsignal(signal.SIGALRM)
        self.mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def cleanup_fixture(self) -> None:
        self.assertTrue(self.fixture.doCleanups(), "Fixture cleanup must not hide errors")
        self.assertFalse(self.fixture.base.exists())

    def assert_timer_restored(self) -> None:
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), self.handler)
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), self.mask)

    def test_late_alarm_during_lock_cleanup_leaves_no_busy_checkpoint(self) -> None:
        f = self.fixture
        original_stat = os.stat
        reached = []

        def late_stat(path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes], *,
                      dir_fd: int | None = None, follow_symlinks: bool = True) -> os.stat_result:
            # Initial lock admission precedes state creation; this is its later
            # owned cleanup, after the complete snapshot and state were written.
            if path == ".checkpoint.lock" and (f.state_dir / "state.json").exists() and not reached:
                reached.append(True)
                expire_if_armed()
            return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

        with patch.object(os, "stat", side_effect=late_stat):
            result = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(reached, [True], "Reach the actual cleanup window")
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.status, "complete")
        f.assert_snapshot(result.snapshot, fixtures.CONTEXT)
        self.assertFalse((f.state_dir / ".checkpoint.lock").exists(),
                         "H11-lock: a late alarm must not strand the owned checkpoint lock")
        self.assert_timer_restored()
        retried = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(retried.status, "captured", retried.warnings)
        self.assertFalse((f.state_dir / ".checkpoint.lock").exists())

    def test_late_alarm_during_failed_state_cleanup_preserves_prior_state(self) -> None:
        f = self.fixture
        first = self.api.checkpoint(f.request(self.api), clock=f.clock)
        f.assert_captured(first, fixtures.CONTEXT)
        prior = (f.state_dir / "state.json").read_bytes()
        updated = fixtures.CONTEXT + b"New saved work\n"
        f.write_context(updated)
        original_replace = os.replace
        original_stat = os.stat
        failed_replace = []
        reached = []

        def fail_state(source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                       destination: str | bytes | os.PathLike[str] | os.PathLike[bytes], *,
                       src_dir_fd: int | None = None, dst_dir_fd: int | None = None) -> None:
            if destination == "state.json":
                failed_replace.append(True)
                raise OSError("Injected private state publication failure")
            original_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        def late_stat(path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes], *,
                      dir_fd: int | None = None, follow_symlinks: bool = True) -> os.stat_result:
            if (isinstance(path, str) and path.startswith(".checkpoint-state-")
                    and failed_replace and not reached):
                reached.append(True)
                expire_if_armed()
            return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

        with patch.object(os, "replace", side_effect=fail_state), \
                patch.object(os, "stat", side_effect=late_stat):
            result = self.api.checkpoint(f.request(self.api), clock=f.clock)
        self.assertEqual(failed_replace, [True])
        self.assertEqual(reached, [True])
        self.assertEqual(result.status, "failed")
        self.assertIsNotNone(result.snapshot)
        f.assert_snapshot(result.snapshot, updated)
        self.assertEqual((f.state_dir / "state.json").read_bytes(), prior)
        self.assertEqual(tuple(f.state_dir.glob(".checkpoint-state-*")), (),
                         "H11-state: a late alarm must not abandon the owned staging file")
        self.assertFalse((f.state_dir / ".checkpoint.lock").exists())
        self.assert_timer_restored()


class PublicationDeadlineTests(unittest.TestCase):
    def test_deadline_after_real_publication_keeps_unknown_until_fresh_readback(self) -> None:
        case = git_fixtures.GitCheckpointRuntimeTests()
        case.setUp()
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            def after_push() -> None:
                expire_if_armed()

            case.fixture.driver.after_push = after_push
            result = case.with_authority(lambda request, *, timeout=None: case.fixture.grant)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.push.status, "unknown")
            self.assertEqual(case.fixture.driver.writes, 1)
            self.assertEqual(case.fixture.remote_oid(), case.fixture.oid)
            self.assertEqual(result.snapshot.status, "complete")
            manifest = case.s.verify_snapshot(result.snapshot.snapshot_path, case.fixture.identity,
                                               case.settings.limits)
            self.assertEqual(manifest.completion, "complete")
            state = json.loads((case.state / "state.json").read_text())
            self.assertEqual(state["publication_status"], "unknown")
            self.assertIn("publication_unconfirmed", result.coverage_gaps)
            self.assertFalse((case.state / ".checkpoint.lock").exists())
            self.assertEqual(tuple(case.state.glob(".checkpoint-state-*")), ())
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
            case.fixture.fresh_driver()
            case.clock.mono += 1
            retried = case.with_authority(lambda request, *, timeout=None: case.fixture.grant)
            self.assertEqual(retried.status, "captured")
            self.assertEqual(retried.push.status, "already-present")
            self.assertEqual(case.fixture.driver.writes, 0)
            self.assertFalse((case.state / ".checkpoint.lock").exists())
        finally:
            self.assertTrue(case.doCleanups(), "Nested publication fixture cleanup must not hide errors")
            self.assertFalse(case.fixture.base.exists())


if __name__ == "__main__":
    unittest.main()
