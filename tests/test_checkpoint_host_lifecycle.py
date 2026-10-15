"""H22: exact local ownership, safe removal and bounded installed execution.

Scheduler storage is explicitly a transport simulation, never service evidence.
Only disposable fixture repositories are committed; no host scheduler is invoked.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

# An isolated child deliberately opts into this test directory, not PYTHONPATH.
if __name__ == "__main__" and "--deadline-child" in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_checkpoint_host as host_tests
import test_checkpoint_schedule as schedule_tests

POPEN = host_tests.POPEN
REFERENCES = host_tests.fixtures.REFERENCES
RECEIPTS = os.environ.get("H22_HOST_RECEIPTS")


def record(value):
    if RECEIPTS:
        with open(RECEIPTS, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")


def tree(root):
    """No-follow fixture inventory including exact bytes/mode/inode."""
    rows = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        rows[str(path.relative_to(root))] = (
            info.st_dev, info.st_ino, info.st_mode,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
    return rows


def signals():
    return {
        "handler": str(signal.getsignal(signal.SIGALRM)),
        "timer": signal.getitimer(signal.ITIMER_REAL),
        "mask": sorted(map(int, signal.pthread_sigmask(signal.SIG_BLOCK, set()))),
        "pending": sorted(map(int, signal.sigpending())),
    }


class HostLifecycleFixture(unittest.TestCase):
    """Compose the unchanged host fixture without rediscovering its test methods."""
    def setUp(self):
        self.case = host_tests.CheckpointHostTests()
        self.child_processes = []
        self.initial_signals = signals()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.api = self.case.host()
        self.f = self.case.fixture
        self.state = self.case.state
        self.hook = self.case.hook
        self.binding = self.state / "connection.json"
        self.ownership = self.state / "connection-receipt.json"

    def clean(self):
        checks = []
        for process in self.child_processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
            checks.append({"pid": process.pid, "exit": process.returncode,
                           "absent": not Path(f"/proc/{process.pid}").exists()})
        clean_ok = self.case.doCleanups()
        root = getattr(getattr(self.case, "fixture", None), "base", None)
        absent = root is not None and not root.exists()
        record({"event": "after_doCleanups", "case": self.id(), "root": str(root),
                "root_absent": absent, "nested_cleanup_ok": clean_ok,
                "processes": checks, "signals_restored": signals() == self.initial_signals})
        self.assertTrue(clean_ok, "Composed fixture cleanup must propagate cleanup failures")
        self.assertTrue(absent, "Observe root removal after nested doCleanups")
        self.assertTrue(all(p["absent"] and p["exit"] is not None for p in checks))
        self.assertEqual(signals(), self.initial_signals)

    def install(self, *, local_runtime=False):
        if local_runtime:
            helper = self.f.base / "pinned-runtime"
            helper.mkdir(mode=0o700)
            for name in self.api._MODULES:
                shutil.copyfile(REFERENCES / (name + ".py"), helper / (name + ".py"))
            with patch.object(self.api, "__file__", str(helper / "checkpoint_host.py")):
                result = self.api.apply_connections(
                    self.api.preview_connections(self.case.request(self.api)), approval=True)
            self.runtime = helper
        else:
            result = self.api.apply_connections(
                self.api.preview_connections(self.case.request(self.api)), approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        self.assertIsNotNone(result.receipt)
        return result.receipt

    def child(self, argv, *, data=b"", keep_stdin_open=False, timeout=10):
        started = time.monotonic()
        process = POPEN(argv, cwd=self.f.project, env=self.f.env, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.child_processes.append(process)
        expired = False
        try:
            if keep_stdin_open:
                process.wait(timeout=timeout)
                process.stdin.close()
                process.stdin = None
                out, err = process.communicate(timeout=2)
            else:
                out, err = process.communicate(input=data, timeout=timeout)
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate(timeout=5)
        elapsed = time.monotonic() - started
        record({"event": "child", "case": self.id(), "pid": process.pid,
                "argv": list(map(str, argv)), "exit": process.returncode,
                "watchdog": expired, "elapsed": elapsed,
                "stdout": out.decode(errors="replace"), "stderr": err.decode(errors="replace")})
        self.assertFalse(expired, "Owned fixture watchdog fired, not a production deadline result")
        self.assertFalse(Path(f"/proc/{process.pid}").exists())
        return process.returncode, out, err, elapsed

    def loader(self):
        operation = getattr(self.api, "load_connections", None)
        self.assertTrue(callable(operation), "H22 ENTRYPOINT: missing exact persisted ownership loader")
        return operation


class CheckpointHostLifecycleTests(HostLifecycleFixture):
    def test_persisted_install_run_fresh_process_remove_requires_fresh_consent(self):
        receipt = self.install()
        code, out, err, _ = self.child([str(self.hook), "--tick"], timeout=65)
        self.assertEqual(code, 0, err)
        value = self.f.c.decode_json(out)
        self.assertEqual(value["status"], "captured")
        self.assertEqual(value["push"]["status"], "denied")
        snapshot = Path(value["snapshot"]["snapshot_path"])
        saved = tree(self.f.backup)
        self.assertEqual(self.f.s.verify_snapshot(snapshot, self.f.identity,
                                                 self.f.settings.limits).completion, "complete")
        self.assertTrue(self.ownership.is_file(), "H22 PERSISTENCE: install returned without durable ownership")
        self.assertEqual(self.ownership.stat().st_mode & 0o777, 0o600)
        self.loader()
        # Fresh child gets trusted literal scope only; never asdict(receipt) or an inherited object.
        script = (
            "import json,sys; from pathlib import Path; "
            f"sys.path.insert(0,{str(REFERENCES)!r}); import checkpoint_host as A; "
            "r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); "
            "v=A.remove_connections(r,approval=(sys.argv[3]=='yes')); "
            "print(json.dumps({'status':v.status,'blockers':v.blockers})); "
            "raise SystemExit(0 if v.status in ('removed','approval_required') else 2)"
        )
        argv = [str(Path(sys.executable).resolve()), "-I", "-B", "-c", script,
                str(self.state), str(self.f.project)]
        before = tree(self.f.base)
        code, out, err, _ = self.child([*argv, "no"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "approval_required")
        self.assertEqual(tree(self.f.base), before)
        code, out, err, _ = self.child([*argv, "yes"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "removed")
        self.assertFalse(self.hook.exists())
        self.assertFalse(self.binding.exists())
        self.assertFalse(self.ownership.exists())
        self.assertEqual(tree(self.f.backup), saved)
        self.assertTrue((self.state / "state.json").is_file())
        self.assertEqual((self.f.project / ".git/config").read_bytes(), self.case.before_git_config)
        self.assertEqual(receipt.state_dir, self.state)

    def test_persisted_decoder_rejects_malformed_membership_types_and_foreign_scope(self):
        receipt = self.install()
        load = self.loader()
        self.assertEqual(load(self.state, project_root=self.f.project), receipt)
        original = self.ownership.read_bytes()
        raw = json.loads(original)
        self.assertEqual(raw["schema"], 1)
        self.assertEqual(raw["kind"], "checkpoint-installation")
        invalid = []
        invalid.append(("unknown-key", dict(raw, unexpected=True)))
        invalid.append(("bool-schema", dict(raw, schema=True)))
        invalid.append(("invalid-directories", dict(raw, directories=[[str(self.state), [True]]])))
        rows = json.loads(original)
        rows["files"][0]["inode"] = True
        invalid.append(("bool-inode", rows))
        rows = json.loads(original)
        rows["files"].append(dict(rows["files"][0], path=str(self.f.project / "notes.md")))
        invalid.append(("foreign-file", rows))
        for name, value in invalid:
            with self.subTest(case=name):
                self.ownership.write_text(json.dumps(value))
                before = tree(self.f.base)
                with self.assertRaises(self.f.c.ContinuityError):
                    load(self.state, project_root=self.f.project)
                self.assertEqual(tree(self.f.base), before)
        self.ownership.write_bytes(original.rstrip()[:-1] + b',"schema":1}\n')
        before = tree(self.f.base)
        with self.assertRaises(self.f.c.ContinuityError):
            load(self.state, project_root=self.f.project)
        self.assertEqual(tree(self.f.base), before)
        self.ownership.write_bytes(original)
        foreign = self.f.base / "foreign-project"
        foreign.mkdir()
        before = tree(self.f.base)
        with self.assertRaises(self.f.c.ContinuityError):
            load(self.state, project_root=foreign)
        self.assertEqual(tree(self.f.base), before)

    def test_replaced_state_root_cannot_be_adopted_from_copied_ownership(self):
        receipt = self.install()
        old = self.f.base / "original-host-state"
        self.state.rename(old)
        shutil.copytree(old, self.state)
        before = tree(self.f.base)
        result = self.api.remove_connections(receipt, approval=True)
        self.assertEqual(result.status, "blocked")
        self.assertIn("connection_path_changed", result.blockers)
        self.assertEqual(tree(self.f.base), before)
        load = self.loader()
        with self.assertRaises(self.f.c.ContinuityError):
            load(self.state, project_root=self.f.project)
        self.assertEqual(tree(self.f.base), before)

    def test_changed_hook_or_binding_blocks_before_any_owned_deletion(self):
        receipt = self.install()
        # Existing API positive safety control: both orderings of whole-set preflight.
        for path in (self.hook, self.binding):
            original = path.read_bytes()
            with self.subTest(path=path.name):
                path.write_bytes(original + b"\n# changed by another owner\n")
                before = tree(self.f.base)
                result = self.api.remove_connections(receipt, approval=True)
                self.assertEqual(result.status, "blocked")
                self.assertIn("owned_connection_changed", result.blockers)
                self.assertEqual(tree(self.f.base), before)
                self.assertTrue(self.hook.exists())
                self.assertTrue(self.binding.exists())
            path.write_bytes(original)

    def test_live_owned_schedule_blocks_hook_removal_even_with_original_receipt(self):
        receipt = self.install()
        driver = schedule_tests.SchedulerFixture()
        preview = self.api.preview_schedule(receipt, driver, service_qualified=True, exclusive_writer=True)
        scheduled = self.api.apply_schedule(preview, driver, approval=True)
        self.assertEqual(scheduled.status, "configured", scheduled.blockers)
        table = driver.table
        before = tree(self.f.base)
        result = self.api.remove_connections(receipt, approval=True)
        self.assertEqual(driver.table, table)
        self.assertTrue(self.hook.is_file(), "H22 DANGLING JOB: live schedule lost its installed hook")
        self.assertEqual(result.status, "blocked")
        self.assertIn("owned_schedule_present", result.blockers)
        self.assertEqual(tree(self.f.base), before)
        removed = self.api.remove_schedule(scheduled.installation, driver, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        self.assertEqual(self.api.remove_connections(receipt, approval=True).status, "removed")

    def test_partial_remove_discloses_remaining_owned_files_and_recoverable_receipt(self):
        receipt = self.install()
        original = os.unlink
        failures = []

        def fail_binding(path, *args, **kwargs):
            if path == "connection.json":
                failures.append(str(path))
                raise PermissionError("injected fixture binding unlink failure")
            return original(path, *args, **kwargs)

        with patch.object(self.api.os, "unlink", side_effect=fail_binding):
            result = self.api.remove_connections(receipt, approval=True)
        self.assertEqual(failures, ["connection.json"])
        self.assertEqual(result.status, "blocked")
        remaining = {item.path for item in receipt.files if item.path.exists()}
        self.assertTrue(remaining)
        self.assertTrue(remaining <= set(result.retained_paths),
                        "H22 RECOVERY: partial removal returned no exact retained ownership")
        self.assertTrue(result.receipt is not None or self.ownership.is_file(),
                        "Retained partial installation must remain recoverable")
        self.assertFalse((self.state / ".connection-install.lock").exists())

    def test_unconfirmed_schedule_write_keeps_ownership_and_blocks_hook_removal(self):
        receipt = self.install()

        class ReadbackFailure(schedule_tests.SchedulerFixture):
            def read(self):
                if self.writes:
                    raise OSError("injected fixture readback unavailable")
                return super().read()

        driver = ReadbackFailure()
        preview = self.api.preview_schedule(receipt, driver, service_qualified=True, exclusive_writer=True)
        result = self.api.apply_schedule(preview, driver, approval=True)
        self.assertEqual(len(driver.writes), 1, "Observe actual simulated storage replacement before read failure")
        self.assertEqual(driver.table, preview.previous + preview.block)
        self.assertEqual(result.status, "blocked")
        self.assertIsNotNone(result.installation, "H22 UNKNOWN: successful write lost recoverable schedule ownership")
        self.assertIn("schedule_readback_failed", result.blockers)
        blocked = self.api.remove_connections(receipt, approval=True)
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("owned_schedule_present", blocked.blockers)
        self.assertTrue(self.hook.is_file())

    def test_missing_pinned_runtime_is_inert_before_commit_and_safe_after_commit(self):
        self.install(local_runtime=True)
        (self.runtime / "checkpoint_runtime.py").unlink()
        before = tree(self.f.base)
        outcomes = {}
        for phase in ("preparing", "prepared", "aborted", "committed"):
            code, out, err, _ = self.child([str(self.hook), phase])
            outcomes[phase] = (code, out, err)
        self.assertEqual(tree(self.f.base), before)
        for phase in ("preparing", "prepared", "aborted"):
            self.assertEqual(outcomes[phase], (0, b"", b""))
        code, out, err = outcomes["committed"]
        self.assertEqual(code, 2, "H22 SAFE ERROR: missing pinned helper escaped bootstrap diagnostics")
        self.assertEqual(out, b"")
        self.assertNotIn(b"Traceback", err)
        self.assertNotIn(str(self.f.base).encode(), err)
        self.assertTrue(err.startswith(b"{"), err)
        self.assertEqual(json.loads(err)["error"]["code"], "connection_runtime_changed")

    def deadline_case(self, mode):
        receipt = self.install()
        digest = next(item.sha256 for item in receipt.files if item.path == self.binding)
        signal_receipt = self.f.base / (mode + "-signals.json")
        argv = [str(Path(sys.executable).resolve()), "-I", "-B", str(Path(__file__).resolve()),
                "--deadline-child", mode, str(self.binding), digest, str(signal_receipt)]
        before = tree(self.state)
        code, out, err, elapsed = self.child(argv, keep_stdin_open=(mode == "stdin"), timeout=8)
        self.assertEqual(code, 2, (out, err))
        self.assertTrue(signal_receipt.is_file(), "Child must return through signal/cleanup observer")
        observed = json.loads(signal_receipt.read_bytes())
        record({"event": "deadline_observation", "case": self.id(), **observed})
        self.assertTrue(observed["binding_prevalidated"])
        if mode == "precheck":
            self.assertTrue(observed["children"], "Reach actual Git precheck, not a fixture validation guard")
        self.assertEqual(observed["before"], observed["after"], "Caller signal state changed")
        self.assertTrue(all(row["reaped"] for row in observed["children"]))
        self.assertEqual(tree(self.state), before)
        self.assertLess(elapsed, 1.0, "H22 DEADLINE: stdin/prechecks escaped the 0.25s total connection budget")
        self.assertNotIn(b"Traceback", err)
        self.assertTrue(err.startswith(b"{"), err)
        self.assertIn(json.loads(err)["error"]["code"], ("connection_deadline", "ref_input_timeout", "git_inspection_timeout"))

    def test_whole_connection_deadline_includes_stalled_committed_stdin(self):
        self.deadline_case("stdin")

    def test_whole_connection_deadline_includes_actual_delayed_git_precheck(self):
        self.deadline_case("precheck")


def deadline_child(argv):
    # Isolated child: no alarm injection or new production seam. Lower the
    # already-existing maximum and introduce real latency at the OS transport.
    mode, binding, digest, output = argv
    sys.path.insert(0, str(REFERENCES))
    api = importlib.import_module("checkpoint_host")
    # First prove the exact installed binding works under the unmodified API.
    api._load_binding(Path(binding), digest)
    before = signals()
    children = []
    original = subprocess.Popen
    defaults = api.H.CheckpointRequest.__init__.__defaults__
    assert defaults == ("event", None, 60.0)

    def local_process(command, *args, **kwargs):
        if str(command[0]) != "/usr/bin/git" or args or kwargs.get("shell"):
            raise AssertionError("Deadline fixture permits only local Git")
        if mode == "precheck":
            command = [str(Path(sys.executable).resolve()), "-I", "-B", "-c",
                       "import os,sys,time; time.sleep(0.4); os.execv('/usr/bin/git',['/usr/bin/git',*sys.argv[1:]])",
                       *command[1:]]
        process = original(command, **kwargs)
        children.append(process)
        return process

    try:
        # Keep the dataclass default legal under the lowered existing maximum;
        # neither validation nor its real request constructor is bypassed.
        with patch.object(api.H, "MAX_DEADLINE_SECONDS", 0.25), \
                patch.object(api.H.CheckpointRequest.__init__, "__defaults__", (*defaults[:-1], 0.25)), \
                patch.object(subprocess, "Popen", side_effect=local_process), \
                patch("socket.socket", side_effect=AssertionError("No network")):
            args = ["run", "--binding", binding, "--expect", digest]
            if mode == "stdin":
                args += ["--trigger", "ref", "--phase", "committed"]
            return api.main(args)
    finally:
        after = signals()
        for process in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        Path(output).write_text(json.dumps({"before": before, "after": after, "binding_prevalidated": True,
            "children": [{"pid": p.pid, "exit": p.returncode,
                          "reaped": not Path(f"/proc/{p.pid}").exists()} for p in children]}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--deadline-child":
        # -I excludes the script directory; explicit test-only fixture import path.
        raise SystemExit(deadline_child(sys.argv[2:]))
    unittest.main()
