"""H22 production crontab boundary: real executable, simulated table/service.

The executable is created under the disposable fixture. It implements only -l
and -, never delegates to host crontab and never proves a running cron service.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import test_checkpoint_host_lifecycle as lifecycle
import test_checkpoint_schedule as schedule_tests


class CheckpointCrontabTests(lifecycle.HostLifecycleFixture):
    def setUp(self):
        super().setUp()
        self.executable = self.f.base / "fixture-crontab"
        self.table = self.f.base / "fixture-table"
        self.mode = self.f.base / "fixture-mode"
        self.log = self.f.base / "fixture-calls.jsonl"
        self.original = b"# exact unrelated bytes\nMAILTO=\"\"\n0 0 * * * /usr/bin/true\n"
        self.table.write_bytes(self.original)
        self.mode.write_text("normal")
        body = (
            f"#!{Path(sys.executable).resolve()} -I\n"
            "import json,os,sys,time\nfrom pathlib import Path\n"
            f"table=Path({str(self.table)!r}); mode=Path({str(self.mode)!r}); log=Path({str(self.log)!r})\n"
            "args=sys.argv[1:]\n"
            "with log.open('a') as stream: stream.write(json.dumps({'args':args,'pid':os.getpid()})+'\\n')\n"
            "action=mode.read_text()\n"
            "if args==['-l']:\n"
            " if action=='stall-output': time.sleep(30)\n"
            " if action=='overflow': sys.stdout.buffer.write(b'x'*(2*1024*1024)); raise SystemExit(0)\n"
            " if action=='read-error': print('fixture transport unavailable',file=sys.stderr); raise SystemExit(1)\n"
            " sys.stdout.buffer.write(table.read_bytes())\n"
            "elif args==['-']:\n"
            " if action=='stall-input': time.sleep(30)\n"
            " data=sys.stdin.buffer.read(); table.write_bytes(data)\n"
            " if action=='corrupt-readback': table.write_bytes(data+b'# unexpected concurrent data\\n')\n"
            "else: raise SystemExit(64)\n"
        )
        self.executable.write_text(body)
        self.executable.chmod(0o700)

        def local_process(argv, *args, **kwargs):
            if (args or kwargs.get("shell") or not isinstance(argv, (tuple, list))
                    or str(argv[0]) not in (str(self.executable), "/usr/bin/git")
                    or not Path(kwargs.get("cwd", self.f.base)).is_relative_to(self.f.base)):
                raise AssertionError("Crontab fixture permits only the exact local executable or local Git")
            self.assertTrue(kwargs.get("start_new_session"), "Own each scheduler child process group")
            process = lifecycle.POPEN(argv, **kwargs)
            self.child_processes.append(process)
            return process

        self.enterContext(patch.object(subprocess, "Popen", side_effect=local_process))

    def calls(self):
        return [] if not self.log.exists() else [json.loads(s) for s in self.log.read_text().splitlines()]

    def driver(self, *, qualified=True, exclusive=True, timeout=1.0):
        factory = getattr(self.api, "CrontabDriver", None)
        self.assertTrue(callable(factory), "H22 ENTRYPOINT: missing pinned production CrontabDriver")
        assert callable(factory)
        return factory(self.executable, service_qualified=qualified, exclusive_writer=exclusive,
                       timeout_seconds=timeout)

    def test_real_executable_fixture_control_reads_and_replaces_exact_bytes(self):
        code, out, err, _ = self.child([str(self.executable), "-l"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.original)
        desired = self.original + b"# preserved UTF-8: \xc3\xa9\n"
        code, out, err, _ = self.child([str(self.executable), "-"], data=desired)
        self.assertEqual((code, out, err), (0, b"", b""))
        code, out, err, _ = self.child([str(self.executable), "-l"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, desired)
        self.assertEqual([row["args"] for row in self.calls()], [["-l"], ["-"], ["-l"]])

    def test_production_driver_requires_both_qualifications_then_roundtrips_schedule(self):
        factory = getattr(self.api, "CrontabDriver", None)
        self.assertTrue(callable(factory), "H22 ENTRYPOINT: missing pinned production CrontabDriver")
        for qualified, exclusive in ((False, False), (False, True), (True, False)):
            with self.subTest(service=qualified, exclusive=exclusive):
                with self.assertRaises(self.f.c.ContinuityError) as caught:
                    driver = self.driver(qualified=qualified, exclusive=exclusive)
                    driver.read()
                self.assertEqual(caught.exception.code, "scheduler_unqualified")
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.table.read_bytes(), self.original)
        receipt = self.install()
        driver = self.driver()
        self.assertEqual(driver.read(), self.original)
        preview = self.api.preview_schedule(receipt, driver, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        before = list(self.calls())
        self.assertEqual(self.api.apply_schedule(preview, driver).status, "approval_required")
        self.assertEqual(self.calls(), before)
        installed = self.api.apply_schedule(preview, driver, approval=True)
        self.assertEqual(installed.status, "configured", installed.blockers)
        self.assertEqual(self.table.read_bytes(), self.original + preview.block)
        later = b"# unrelated later job\n15 2 * * * /usr/bin/true\n"
        self.table.write_bytes(self.table.read_bytes() + later)
        removed = self.api.remove_schedule(installed.installation, driver, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        self.assertEqual(self.table.read_bytes(), self.original + later)
        self.assertTrue(self.hook.exists())

    def test_production_driver_refuses_stale_bytes_and_changed_executable_without_write(self):
        driver = self.driver()
        self.assertEqual(driver.read(), self.original)
        concurrent = self.original + b"# somebody else changed the table\n"
        self.table.write_bytes(concurrent)
        self.assertFalse(driver.replace(self.original, self.original + b"# owned job\n"))
        self.assertEqual(self.table.read_bytes(), concurrent)
        self.assertNotIn(["-"], [row["args"] for row in self.calls()])
        calls = list(self.calls())
        old = self.executable.with_name("original-fixture-crontab")
        self.executable.rename(old)
        self.executable.write_bytes(old.read_bytes())
        self.executable.chmod(0o700)
        with self.assertRaises(self.f.c.ContinuityError) as caught:
            driver.read()
        self.assertEqual(caught.exception.code, "scheduler_executable_changed")
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.table.read_bytes(), concurrent)

    def test_production_driver_reports_readback_mismatch_and_nonzero_read_not_absence(self):
        driver = self.driver()
        self.mode.write_text("corrupt-readback")
        desired = self.original + b"# prospective owned block\n"
        with self.assertRaises(self.f.c.ContinuityError) as caught:
            driver.replace(self.original, desired)
        self.assertEqual(caught.exception.code, "schedule_readback_failed")
        self.assertEqual(self.table.read_bytes(), desired + b"# unexpected concurrent data\n")
        self.mode.write_text("read-error")
        with self.assertRaises(self.f.c.ContinuityError) as caught:
            driver.read()
        self.assertEqual(caught.exception.code, "scheduler_read_failed")

    def test_production_driver_bounds_real_stdin_stdout_and_reaps_owned_groups(self):
        driver = self.driver(timeout=0.25)
        handler = lifecycle.signals()
        # A legal near-limit table fills a pipe if the command never reads stdin.
        content = b"#" + b"x" * (self.f.c.CONFIG_MAX_BYTES - 2) + b"\n"
        for mode in ("stall-input", "stall-output", "overflow"):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                started = time.monotonic()
                with self.assertRaises(self.f.c.ContinuityError) as caught:
                    if mode == "stall-input":
                        driver.replace(self.original, content)
                    else:
                        driver.read()
                elapsed = time.monotonic() - started
                self.assertIn(caught.exception.code, ("scheduler_timeout", "invalid_crontab"))
                self.assertLess(elapsed, 1.0, "Entire scheduler operation exceeded bounded I/O budget")
                self.assertEqual(self.table.read_bytes(), self.original)
                self.assertEqual(lifecycle.signals(), handler)
                self.assertTrue(all(p.returncode is not None for p in self.child_processes))
                self.assertTrue(all(not Path(f"/proc/{p.pid}").exists() for p in self.child_processes))
