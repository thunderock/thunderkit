"""Qualified crontab transport is a fixture; periodic capture is real Python."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shlex
import signal
import subprocess
import unittest

import test_checkpoint_host as host_tests

POPEN = subprocess.Popen


class SchedulerFixture:
    """Only the host crontab read/compare/replace boundary is substituted."""
    def __init__(self) -> None:
        self.table = b"# existing user task\n0 0 * * * /usr/bin/true\n"
        self.writes: list[bytes] = []

    def read(self) -> bytes:
        return self.table

    def replace(self, expected: bytes, content: bytes) -> bool:
        if self.table != expected:
            return False
        self.table = content
        self.writes.append(content)
        return True


class CheckpointScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = host_tests.CheckpointHostTests()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.api = self.case.host()
        self.installation = self.api.apply_connections(
            self.api.preview_connections(self.case.request(self.api)), approval=True)
        self.assertEqual(self.installation.status, "configured", self.installation.blockers)
        self.scheduler = SchedulerFixture()
        self.original = self.scheduler.table

    def clean(self) -> None:
        self.assertTrue(self.case.doCleanups())
        self.assertFalse(self.case.fixture.base.exists())

    def preview(self, *, qualified=True, exclusive=True):
        operation = getattr(self.api, "preview_schedule", None)
        self.assertTrue(callable(operation), "Missing qualified existing-scheduler integration")
        return operation(self.installation.receipt, self.scheduler,
                         service_qualified=qualified, exclusive_writer=exclusive)

    def test_unqualified_or_unapproved_schedule_makes_zero_writes(self) -> None:
        blocked = self.preview(qualified=False)
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("scheduler_unqualified", blocked.blockers)
        self.assertEqual(self.preview(exclusive=False).status, "blocked")
        preview = self.preview()
        self.assertEqual(preview.status, "approval_required")
        self.assertEqual(self.api.apply_schedule(preview, self.scheduler).status, "approval_required")
        self.assertEqual(self.scheduler.table, self.original)
        self.assertEqual(self.scheduler.writes, [])

    def test_installed_tick_runs_actual_checkpoint_and_uninstall_preserves_other_jobs(self) -> None:
        preview = self.preview()
        result = self.api.apply_schedule(preview, self.scheduler, approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        self.assertTrue(self.scheduler.table.startswith(self.original))
        self.assertIn(b"* * * * * ", self.scheduler.table)
        command = shlex.split(self.installation.receipt.periodic_command)
        process = POPEN(command, cwd=self.case.fixture.project, env=self.case.fixture.env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=65)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            self.fail("Periodic checkpoint exceeded bounded fixture watchdog")
        self.assertEqual(process.returncode, 0, err)
        data = self.case.fixture.c.decode_json(out)
        self.assertEqual(data["status"], "captured")
        self.assertEqual(data["push"]["status"], "denied")
        self.assertTrue((self.case.state / "last-result.json").is_file())
        extra = b"# later unrelated task\n15 2 * * * /usr/bin/true\n"
        self.scheduler.table += extra
        removed = self.api.remove_schedule(result.installation, self.scheduler, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        self.assertEqual(self.scheduler.table, self.original + extra)
        self.assertTrue(self.case.hook.exists(), "Schedule removal must not silently remove the hook")

    def test_stale_preview_and_changed_owned_block_are_not_overwritten(self) -> None:
        preview = self.preview()
        self.scheduler.table += b"# concurrent edit\n"
        current = self.scheduler.table
        blocked = self.api.apply_schedule(preview, self.scheduler, approval=True)
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(self.scheduler.table, current)
        self.assertEqual(self.scheduler.writes, [])
        installed = self.api.apply_schedule(self.preview(), self.scheduler, approval=True)
        self.assertEqual(installed.status, "configured")
        self.scheduler.table = self.scheduler.table.replace(b"* * * * * ", b"0 * * * * ")
        current = self.scheduler.table
        removed = self.api.remove_schedule(installed.installation, self.scheduler, approval=True)
        self.assertEqual(removed.status, "blocked")
        self.assertEqual(self.scheduler.table, current)


if __name__ == "__main__":
    unittest.main()
