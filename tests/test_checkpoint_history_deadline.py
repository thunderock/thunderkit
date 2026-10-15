"""Optional history must not spend the deadline before current files survive.

Real Git fixtures and real S._Git.run pipe/deadline/cancellation machinery are
used throughout. Only one exact, phase-selected local command is redirected to
an interruptible private executable. No timeout, snapshot or Git return value
is fabricated. Each scenario is isolated in a Python child. Six seconds is the
operation budget, not a universal all-host performance guarantee.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, replace
import functools
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_checkpoint_git_runtime as git_fixtures
import test_checkpoint_push as push_fixtures

ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(Path(sys.executable).resolve())
POPEN = subprocess.Popen
DEADLINE = 6.0
PRIOR = b"# Prior verified context\n"
CURRENT = b"# Current saved context\nRetain these exact bytes despite slow history.\n"
PHASES = ("runtime_reachable", "s_prior", "s_current", "s_bundle", "s_staged",
          "s_final", "runtime_final")
FINITE_PHASES = ("s_final_finite", "runtime_final_finite")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_tree(root):
    """Pin bytes, membership, mode and inode, but not read-induced atimes."""
    return {str(p.relative_to(root)): (stat.S_IMODE(p.stat().st_mode),
            p.stat().st_ino, digest(p.read_bytes()))
            for p in sorted(root.rglob("*")) if p.is_file()}


def descriptors():
    result = {}
    for path in Path("/proc/self/fd").iterdir():
        try:
            result[path.name] = os.readlink(path)
        except FileNotFoundError:
            continue  # The directory enumeration's own descriptor is gone.
    return result


def signal_state():
    return (signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL),
            signal.pthread_sigmask(signal.SIG_BLOCK, set()), signal.sigpending())


def children():
    return Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()


class _HistoryProbe(unittest.TestCase):
    """Executed explicitly in a child, never added to unittest discovery."""

    scenario: str
    receipt: Path
    tmp: Path
    optional_cutoff: float

    def setUp(self):
        self.data = {"scenario": self.scenario, "deadline_seconds": DEADLINE}
        self.processes = []
        self.pending = None
        self.events = []
        self.before_fd = descriptors()
        self.before_signals = signal_state()
        self.before_children = children()
        self.addCleanup(self.final_cleanup)
        self.enterContext(patch("socket.socket", side_effect=AssertionError("No network")))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("No network")))
        self.enterContext(patch("os.system", side_effect=AssertionError("No shell")))
        self.enterContext(patch("os.popen", side_effect=AssertionError("No shell")))
        self.enterContext(patch.object(push_fixtures, "POPEN", self.launch))
        self.case = git_fixtures.GitCheckpointRuntimeTests()
        # Register before setup so partial fixture failures also get containment.
        self.addCleanup(self.cleanup_fixture)
        self.case.setUp()
        self.f = self.case.fixture
        self.s, self.h = self.case.s, self.case.api
        self.before_budgets = (self.s._work_budgets.get(), self.h.G._work_budgets.get(),
                               self.s._cleanup_guard.get())
        self.f.config("gc.auto", "0")
        self.f.config("maintenance.auto", "false")
        self.scope = self.case.backups / "projects" / self.f.identity.id
        self.locator = self.scope / "latest.json"
        self.authority_calls = []
        self.data["imports"] = {m.__name__: str(Path(m.__file__).resolve())
                                for m in (self.s, self.h, self.h.G, self.case.c)}
        for value in self.data["imports"].values():
            self.assertTrue(Path(value).is_relative_to(ROOT / "skills/references"))

    def launch(self, argv, *args, **kwargs):
        argv = list(argv)
        self.assertIn(argv[0], ("/usr/bin/git", "/bin/git"))
        self.assertFalse(kwargs.get("shell"))
        self.assertTrue(Path(kwargs["cwd"]).resolve().is_relative_to(self.tmp))
        self.assertEqual(kwargs["env"].get("GIT_ALLOW_PROTOCOL"), "file")
        if self.pending is not None:
            original, cwd, label = self.pending
            self.assertEqual(argv, original)
            self.assertEqual(Path(kwargs["cwd"]), cwd)
            self.assertTrue(kwargs.get("start_new_session"))
            self.assertEqual(digest(self.exe.read_bytes()), self.exe_hash)
            ready = self.receipt.parent / f"ready-{len(self.events)}.json"
            argv = [str(self.exe), str(ready)]
            if self.scenario in FINITE_PHASES:
                argv.extend([str(self.optional_cutoff + 0.2), *original])
            event = {"phase": label, "original_argv": original, "cwd": str(cwd),
                     "ready_path": str(ready), "armed_seconds": signal.getitimer(signal.ITIMER_REAL)[0],
                     "s_budget_count": len(self.s._work_budgets.get()),
                     "g_budget_count": len(self.h.G._work_budgets.get()),
                     "prior_locator_unchanged_at_fault": self.locator.read_bytes() == self.prior_locator,
                     "completed_verifications": list(self.verified)}
            self.assertGreater(event["armed_seconds"], 0)
            self.assertGreater(event["s_budget_count"], 0)
            self.assertGreater(event["g_budget_count"], 0)
            self.events.append(event)
        process = POPEN(argv, *args, **kwargs)
        self.processes.append(process)
        # Retain PID ownership before any deadline/cleanup; diagnostics survive.
        with (self.receipt.parent / "pids.jsonl").open("a") as stream:
            stream.write(json.dumps({"pid": process.pid, "argv": argv}) + "\n")
        if self.pending is not None:
            self.events[-1]["pid"] = process.pid
        return process

    def authority(self, request, *, timeout):
        self.authority_calls.append(request.oid)
        self.assertGreater(timeout, 0)
        return self.f.grant

    def checkpoint(self, *, history=True, authority=None):
        request = replace(self.case.request(), deadline_seconds=DEADLINE)
        if not history:
            request = replace(request, snapshot=replace(request.snapshot, include_history=False,
                                                        history_ref=None))
        started = time.monotonic()
        result = self.h.checkpoint(request, clock=self.case.clock, authority=authority,
                                   driver=self.f.driver)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, DEADLINE + 2.0, "Fixture exceeded deadline plus cleanup tolerance")
        return result, elapsed

    def verify_bytes(self, snapshot, expected):
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(snapshot.snapshot_path)
        manifest = self.s.verify_snapshot(snapshot.snapshot_path, self.f.identity, self.case.settings.limits)
        self.assertEqual(self.s.read_payload(snapshot.snapshot_path, "notes.md", self.f.identity,
                                             self.case.settings.limits), expected)
        self.assertEqual((manifest.ref, manifest.head), (self.f.ref, self.f.oid))
        return manifest

    def prevalidate(self):
        self.case.notes.write_bytes(PRIOR)
        prior, elapsed = self.checkpoint()
        self.assertEqual(prior.status, "captured", prior.warnings)
        manifest = self.verify_bytes(prior.snapshot, PRIOR)
        self.assertEqual((manifest.completion, manifest.history.status), ("complete", "bundled"))
        self.prior = prior.snapshot
        self.prior_tree = file_tree(self.prior.snapshot_path)
        self.prior_locator = self.locator.read_bytes()
        self.prior_state = (self.case.state / "state.json").read_bytes()
        self.remote = self.f.remote_refs()
        self.assertEqual(self.f.remote_oid(), self.f.old)
        objects = self.f.raw("rev-list", "--objects", "--no-object-names", self.f.oid, "--").stdout.splitlines()
        tree = self.f.raw("rev-parse", self.f.oid + "^{tree}").stdout.strip()
        self.assertEqual(set(objects), {self.f.old, self.f.oid, tree})
        self.assertEqual(self.f.raw("ls-tree", self.f.oid).stdout, "")
        self.f.raw("fsck", "--strict", "--no-reflogs", self.f.oid)
        bundle = self.s.read_history(self.prior.snapshot_path, self.f.identity, self.case.settings.limits)
        # Independent Git import, exact object set and advertised ref, not only S.
        verify = self.f.base / "independent.git"
        self.f.raw("init", "--bare", f"--template={self.f.template}", str(verify), cwd=self.f.base)
        bundle_path = self.prior.snapshot_path / "history.bundle"
        self.assertEqual(self.f.raw("bundle", "list-heads", str(bundle_path)).stdout.strip(),
                         f"{self.f.oid} {self.f.ref}")
        self.f.raw("bundle", "verify", str(bundle_path), cwd=verify)
        self.f.raw("fetch", "--no-tags", "--no-write-fetch-head", str(bundle_path),
                   self.f.ref + ":" + self.f.ref, cwd=verify)
        imported = self.f.raw("rev-list", "--objects", "--no-object-names", self.f.ref, "--", cwd=verify)
        self.assertEqual(set(imported.stdout.splitlines()), set(objects))
        self.f.raw("fsck", "--strict", "--no-reflogs", self.f.oid, cwd=verify)
        self.data["prior"] = {"snapshot": str(self.prior.snapshot_path), "elapsed": elapsed,
                              "graph": sorted(objects), "bundle_sha256": digest(bundle),
                              "locator_sha256": digest(self.prior_locator), "independent_git_verified": True}
        self.case.notes.write_bytes(CURRENT)
        self.project_tree = file_tree(self.f.repo)
        self.case.clock.mono += 10
        control, elapsed = self.checkpoint(history=False, authority=self.authority)
        manifest = self.verify_bytes(control.snapshot, CURRENT)
        self.assertEqual((control.status, manifest.completion, manifest.history.status),
                         ("partial", "partial", "unprotected"))
        self.assertLess(elapsed, DEADLINE / 2, "File-only control must leave substantial deadline margin")
        self.assertEqual(self.locator.read_bytes(), self.prior_locator)
        self.assertEqual((self.case.state / "state.json").read_bytes(), self.prior_state)
        self.assertEqual(self.authority_calls, [])
        self.assertEqual(self.f.driver.calls, [])
        control_path = control.snapshot.snapshot_path
        self.data["file_only_control"] = {"elapsed": elapsed, "payload_sha256": digest(CURRENT),
                                          "verified_current": True, "path": str(control_path)}
        # Explicit owned-control reset, before injecting, prevents its already
        # retained bytes from masquerading as preservation by the faulty call.
        self.assertNotEqual(control_path, self.prior.snapshot_path)
        self.assertEqual(control_path.parent, self.scope / "snapshots")
        shutil.rmtree(control_path)
        self.assertFalse(control_path.exists())
        self.assertEqual(list((self.scope / "snapshots").iterdir()), [self.prior.snapshot_path])
        self.data["file_only_control"]["removed_before_fault"] = True

    def install_fault(self, stack):
        self.exe = self.f.base / "private-history-git"
        body = ("#!" + PYTHON + "\nimport json,os,signal,sys,time\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp()}))\n")
        if self.scenario in FINITE_PHASES:
            body += ("time.sleep(max(0.0,float(sys.argv[2])-time.monotonic()))\n"
                     "os.execv(sys.argv[3],sys.argv[3:])\n")
        else:
            body += "os.write(1,b'ready\\n')\nwhile True: signal.pause()\n"
        self.exe.write_text(body)
        self.exe.chmod(0o700)
        self.exe_hash = digest(self.exe.read_bytes())
        self.data["executable"] = {"path": str(self.exe), "sha256": self.exe_hash, "shebang": PYTHON}
        labels = []
        self.verified = []

        def wrap(owner, name, label):
            original = getattr(owner, name)
            @functools.wraps(original)
            def call(*args, **kwargs):
                selected = label(*args, **kwargs) if callable(label) else label
                labels.append(selected)
                try:
                    if name == "create_snapshot" and self.scenario in FINITE_PHASES:
                        self.optional_cutoff = min(b.end for b in self.s._history_budgets.get())
                    value = original(*args, **kwargs)
                    if name == "verify_snapshot":
                        self.verified.append({"phase": selected, "path": str(args[0]),
                                              "completion": value.completion})
                    return value
                finally:
                    labels.pop()
            stack.enter_context(patch.object(owner, name, call))

        def verification_label(path, *args, **kwargs):
            if "s_prior" in labels:
                return "s_prior_verify"
            if ".staging" in Path(path).parts:
                return "s_staged"
            return "s_final" if "s_create" in labels else "runtime_final"

        wrap(self.h._Scope, "reachable", "runtime_reachable")
        wrap(self.s, "create_snapshot", "s_create")
        wrap(self.s, "_prior", "s_prior")
        wrap(self.s, "_capture_history", "s_history")
        wrap(self.s, "_scan_graph", "s_scan")
        wrap(self.s, "verify_snapshot", verification_label)
        run = self.s._Git.run

        def selected_run(git, cwd, args, budget, **kwargs):
            phase = self.scenario.removesuffix("_finite")
            bundle_verify = args == ["bundle", "verify", str(git.scratch / "history.bundle")]
            target = (
                phase == "runtime_reachable" and "runtime_reachable" in labels
                and args == ["rev-list", "--objects", "--no-object-names", self.f.oid, "--"]
                or phase == "s_prior" and "s_prior" in labels and bundle_verify
                or phase == "s_current" and "s_history" in labels and "s_scan" in labels
                and Path(cwd) == self.f.repo and args == ["cat-file", "-t", self.f.oid]
                or phase == "s_bundle" and "s_history" in labels and bundle_verify
                or phase in ("s_staged", "s_final", "runtime_final") and phase in labels and bundle_verify
            )
            if not target:
                return run(git, cwd, args, budget, **kwargs)
            self.assertIsNone(self.pending)
            self.pending = ([git.binary, *git.options, *args], Path(cwd), phase)
            try:
                return run(git, cwd, args, budget, **kwargs)
            finally:
                self.pending = None
        stack.enter_context(patch.object(self.s._Git, "run", selected_run))

    def runTest(self):
        self.prevalidate()
        if self.scenario in FINITE_PHASES:
            self.finite_completion()
            return
        if self.scenario == "file_only_control":
            self.data["control_passed"] = True
            self.resource_check()
            return
        if self.scenario == "small_history_control":
            result, elapsed = self.checkpoint()
            manifest = self.verify_bytes(result.snapshot, CURRENT)
            self.assertEqual((result.status, manifest.completion, manifest.history.status),
                             ("captured", "complete", "bundled"))
            self.assertEqual(set(result.observed_oids), set())  # Already observed exact graph.
            self.assertEqual(self.f.driver.calls, [])
            self.data.update(control_passed=True, elapsed=elapsed, result=asdict(result))
            self.resource_check(allow_new_locator=True, allow_new_state=True)
            return
        with ExitStack() as stack:
            self.install_fault(stack)
            result, elapsed = self.checkpoint(authority=self.authority)
        self.assertTrue(self.events, "Exact history phase was never reached")
        for event in self.events:
            ready = json.loads(Path(event["ready_path"]).read_text())
            self.assertEqual(ready, {"pid": event["pid"], "group": event["pid"]})
            with self.assertRaises(ProcessLookupError):
                os.killpg(ready["group"], 0)
            event["ready"] = ready
            event["process_group_absent"] = True
        self.assertTrue(all(event["phase"] == self.scenario for event in self.events))
        self.data.update(events=self.events, result=asdict(result), elapsed=elapsed)
        self.assertEqual(self.authority_calls, [])
        self.assertEqual(self.f.driver.calls, [])
        self.assertIsNone(result.push)
        self.data["publication"] = {"authority_calls": list(self.authority_calls),
                                     "driver_calls": list(self.f.driver.calls), "push": None}
        retained = []
        for path in sorted((self.scope / "snapshots").iterdir()):
            if path == self.prior.snapshot_path:
                continue
            manifest = self.s.verify_snapshot(path, self.f.identity, self.case.settings.limits)
            payload = self.s.read_payload(path, "notes.md", self.f.identity, self.case.settings.limits)
            self.assertEqual(payload, CURRENT)
            retained.append({"path": str(path), "completion": manifest.completion,
                             "history": manifest.history.status, "payload_sha256": digest(payload)})
        self.data["retained_current"] = retained
        self.data["returned_verified_current"] = False
        if result.snapshot is not None and result.snapshot.snapshot_path is not None:
            self.verify_bytes(result.snapshot, CURRENT)
            self.data["returned_verified_current"] = True
        if self.scenario == "runtime_final":
            assert result.snapshot is not None
            pointer = Path(json.loads(self.locator.read_text())["path"])
            manifest = self.s.verify_snapshot(pointer, self.f.identity, self.case.settings.limits)
            self.assertEqual((manifest.completion, manifest.history.status), ("complete", "bundled"))
            self.assertEqual(self.s.read_payload(pointer, "notes.md", self.f.identity,
                                                 self.case.settings.limits), CURRENT)
            self.assertEqual(pointer.parent, result.snapshot.snapshot_path)
            self.data["late_complete_locator_verified"] = True
        # S-final has a staged-verified tree already renamed into snapshots;
        # runtime-final has a fully S-verified snapshot and updated locator.
        self.resource_check(allow_new_locator=self.scenario == "runtime_final")

    def finite_completion(self):
        """Collect both real outcomes before the supervisor asserts progress."""
        self.data["runs"] = []
        for invocation in range(2):
            self.case.clock.mono += 10
            event_start = len(self.events)
            with ExitStack() as stack:
                self.install_fault(stack)
                result, elapsed = self.checkpoint()
            self.assertEqual(self.authority_calls, [])
            self.assertEqual(self.f.driver.calls, [])
            paths = sorted(p for p in (self.scope / "snapshots").iterdir()
                           if p != self.prior.snapshot_path)
            for path in paths:
                manifest = self.s.verify_snapshot(path, self.f.identity, self.case.settings.limits)
                self.assertEqual((manifest.completion, manifest.history.status), ("complete", "bundled"))
                self.assertEqual(self.s.read_payload(path, "notes.md", self.f.identity,
                                                     self.case.settings.limits), CURRENT)
            locator = Path(json.loads(self.locator.read_text())["path"]).parent
            state = json.loads((self.case.state / "state.json").read_text())
            returned = result.snapshot.snapshot_path if result.snapshot is not None else None
            self.data["runs"].append({"invocation": invocation + 1, "elapsed": elapsed,
                "result": asdict(result), "current_paths": [str(p) for p in paths],
                "returned_is_current": returned in paths, "locator_matches_returned": locator == returned,
                "state_matches_returned": state["last_snapshot"] == str(returned),
                "last_success_monotonic": state["last_success_monotonic"],
                "expected_monotonic": self.case.clock.mono,
                "fault_events": len(self.events) - event_start})
            self.resource_check(allow_new_locator=True, allow_new_state=True)
        self.assertTrue(self.events, "Finite-delay final verification was never reached")
        for event in self.events:
            ready = json.loads(Path(event["ready_path"]).read_text())
            self.assertEqual(ready, {"pid": event["pid"], "group": event["pid"]})
            with self.assertRaises(ProcessLookupError):
                os.killpg(ready["group"], 0)
            event["process_group_absent"] = True
        self.data["events"] = self.events
        self.data["timing_injection"] = "One actual Git command delayed beyond optional cutoff, then exec original argv"

    def resource_check(self, *, allow_new_locator=False, allow_new_state=False):
        self.assertEqual(file_tree(self.prior.snapshot_path), self.prior_tree)
        self.verify_bytes(self.prior, PRIOR)
        self.assertEqual(file_tree(self.f.repo), self.project_tree)
        self.assertEqual(self.case.notes.read_bytes(), CURRENT)
        self.assertEqual(self.f.remote_refs(), self.remote)
        if not allow_new_locator:
            self.assertEqual(self.locator.read_bytes(), self.prior_locator)
        if not allow_new_state:
            self.assertEqual((self.case.state / "state.json").read_bytes(), self.prior_state)
        self.assertFalse((self.case.state / ".checkpoint.lock").exists())
        self.assertFalse((self.scope / ".snapshot.lock").exists())
        self.assertEqual(list((self.scope / ".staging").iterdir()), [])
        self.assertFalse(any(p.name.startswith(("thunderkit-S-git-", ".checkpoint-state-", ".locator-"))
                             for p in self.f.base.rglob("*")))
        self.assertEqual((self.s._work_budgets.get(), self.h.G._work_budgets.get(),
                          self.s._cleanup_guard.get()), self.before_budgets)
        self.assertEqual(signal_state(), self.before_signals)
        self.data["resources"] = {"locks_staging_scratch_absent": True, "contextvars_restored": True,
            "signals_restored": True, "prior_exact": True, "project_exact": True, "remote_exact": True,
            "locator_unchanged": self.locator.read_bytes() == self.prior_locator,
            "cadence_unchanged": (self.case.state / "state.json").read_bytes() == self.prior_state}

    def cleanup_fixture(self):
        # Capture failures BEFORE containment, and never report containment as
        # production cancellation. Normal path needs no extra kill/stream close.
        receipts = []
        live = []
        for process in self.processes:
            code = process.returncode
            streams_closed = all(s is None or s.closed for s in (process.stdin, process.stdout, process.stderr))
            try:
                waited = os.waitpid(process.pid, os.WNOHANG)
            except ChildProcessError:
                waited = None
            row = {"pid": process.pid, "returncode": code, "waitpid_no_child": waited is None,
                   "pid_absent": not Path(f"/proc/{process.pid}").exists(), "streams_closed": streams_closed}
            receipts.append(row)
            if code is None:
                live.append(process)
        self.data["processes"] = receipts
        self.data["containment_required"] = bool(live)
        self.persist()
        try:
            for process in live:
                process.kill()
                process.communicate(timeout=2)
        finally:
            clean = self.case.doCleanups()
        base = getattr(getattr(self.case, "fixture", None), "base", None)
        self.assertTrue(clean, "Composed fixture doCleanups failed")
        self.assertTrue(base is None or not base.exists())
        self.assertFalse(live, "Production left owned processes alive; see pre-containment receipt")
        self.assertTrue(all(r["returncode"] is not None and r["waitpid_no_child"] and r["pid_absent"]
                            and r["streams_closed"] for r in receipts), "Incomplete real-process cleanup")
        self.data["fixture_cleanup"] = {"doCleanups": clean, "root": str(base), "root_absent": True}

    def final_cleanup(self):
        gc.collect()
        self.assertEqual(descriptors(), self.before_fd)
        self.assertEqual(signal_state(), self.before_signals)
        self.assertEqual(children(), self.before_children)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.tmp.rmdir()
        self.data["cleanup"] = {"fd_inventory_restored": True, "signals_restored": True,
                                "children_restored": True, "tmp_absent": not self.tmp.exists()}
        self.persist()

    def persist(self):
        self.receipt.write_text(json.dumps(self.data, indent=2, default=str) + "\n")


class HistoryDeadlineTests(unittest.TestCase):
    def scenario(self, name):
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name an approved fixture parent")
        out = os.environ.get("H14_HISTORY_RECEIPTS")
        with tempfile.TemporaryDirectory(prefix="H14-history-", dir=scratch) as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            (root / "tmp").mkdir()
            (root / "home").mkdir()
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "home"),
                   "TMPDIR": str(root / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"}
            command = [PYTHON, "-B", str(Path(__file__).resolve()), "--history-child", name, str(receipt)]
            with POPEN(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       start_new_session=True) as process:
                try:
                    stdout, stderr = process.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    # The watchdog is test containment, not the operation deadline.
                    process.kill()
                    stdout, stderr = process.communicate()
                    raise AssertionError("History probe exceeded its isolated-process watchdog") from None
            data = json.loads(receipt.read_text())
            data["child_process"] = {"exit": process.returncode, "pid": process.pid,
                "pid_absent": not Path(f"/proc/{process.pid}").exists(),
                "streams_closed": process.stdout.closed and process.stderr.closed}
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            data["child_process"]["waitpid_no_child"] = True
            if out:
                destination = Path(out)
                self.assertTrue(destination.is_dir())
                (destination / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")
                (destination / (name + ".log")).write_bytes(stdout + stderr)
            self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
            self.assertEqual(data["probe"], {"cases": 1, "failures": 0, "errors": 0, "skips": 0})
            self.assertTrue(data["cleanup"]["tmp_absent"])
            self.assertTrue(data["fixture_cleanup"]["root_absent"])
        self.assertFalse(root.exists(), "Supervisor fixture must be absent before regression assertions")
        data["supervisor_root_absent"] = True
        if out:
            (Path(out) / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")
        return data

    def assert_preserved_partial(self, phase):
        data = self.scenario(phase)
        self.assertTrue(data["retained_current"],
            f"H14-files: {phase} consumed optional-history time without retaining any verified current-file snapshot; "
            f"result={data['result']}")
        self.assertTrue(data["returned_verified_current"],
            f"H14-files: {phase} consumed optional-history time without returning a verified current-file snapshot; "
            f"retained_current={data['retained_current']}; result={data['result']}")

        self.assertEqual(data["result"]["status"], "partial")
        self.assertEqual(data["result"]["snapshot"]["history_status"], "unprotected")
        self.assertTrue(data["result"]["snapshot"]["omissions"])
        self.assertTrue(all(r["completion"] == "partial" and r["history"] == "unprotected"
                            for r in data["retained_current"]))
        self.assertTrue(data["resources"]["locator_unchanged"])
        self.assertTrue(data["resources"]["cadence_unchanged"])

    def test_00_same_repository_file_only_control(self):
        self.assertTrue(self.scenario("file_only_control")["control_passed"])

    def test_01_small_history_still_completes(self):
        self.assertTrue(self.scenario("small_history_control")["control_passed"])

    def test_runtime_reachable_preserves_current_files(self):
        self.assert_preserved_partial("runtime_reachable")

    def test_s_prior_history_verification_preserves_current_files(self):
        self.assert_preserved_partial("s_prior")

    def test_s_current_graph_preserves_current_files(self):
        self.assert_preserved_partial("s_current")

    def test_s_capture_bundle_verification_preserves_current_files(self):
        self.assert_preserved_partial("s_bundle")

    def test_s_staged_history_verification_preserves_current_files(self):
        self.assert_preserved_partial("s_staged")

    def assert_late_complete_survives(self, phase, verified_phase):
        data = self.scenario(phase)
        self.assertTrue(data["retained_current"], "Late verified current files must remain discoverable")
        self.assertTrue(any(v["phase"] == verified_phase and v["completion"] == "complete"
                            for v in data["events"][0]["completed_verifications"]))
        self.assertTrue(all(r["completion"] == "complete" and r["history"] == "bundled"
                            for r in data["retained_current"]))
        # Outer exhaustion is current RED's outcome, not a required GREEN
        # mechanism: an earlier history cancellation may report retained files.
        self.assertIn(data["result"]["status"], ("failed", "captured", "partial"))
        if data["elapsed"] >= DEADLINE:
            self.assertEqual(data["result"]["status"], "failed")
            self.assertIn("deadline_exceeded", data["result"]["warnings"])
        self.assertTrue(data["resources"]["cadence_unchanged"])
        return data

    def test_s_final_verification_keeps_already_stage_verified_files(self):
        data = self.assert_late_complete_survives("s_final", "s_staged")
        self.assertTrue(data["resources"]["locator_unchanged"])

    def test_runtime_final_verification_keeps_already_published_complete_files(self):
        data = self.assert_late_complete_survives("runtime_final", "s_final")
        self.assertTrue(data["returned_verified_current"])
        self.assertTrue(data["late_complete_locator_verified"])
        self.assertFalse(data["resources"]["locator_unchanged"])

    def assert_finite_completion(self, phase):
        data = self.scenario(phase)
        self.assertEqual(len(data["runs"]), 2)
        for row in data["runs"]:
            with self.subTest(phase=phase, invocation=row["invocation"]):
                self.assertEqual(row["result"]["status"], "captured",
                                 "Finite final verification must complete under the outer deadline")
                self.assertLess(row["elapsed"], DEADLINE)
                if row["fault_events"]:
                    self.assertGreater(row["elapsed"], DEADLINE / 2)
                self.assertTrue(row["returned_is_current"])
                self.assertTrue(row["locator_matches_returned"])
                self.assertTrue(row["state_matches_returned"])
                self.assertEqual(row["last_success_monotonic"], row["expected_monotonic"])
                self.assertEqual(len(row["current_paths"]), 1, "Retry must not accumulate unreported snapshots")
                self.assertEqual(row["result"]["push"]["status"], "denied")
        self.assertEqual(data["runs"][0]["current_paths"], data["runs"][1]["current_paths"])

    def test_s_final_finite_verification_reports_current_snapshot_on_repeated_calls(self):
        self.assert_finite_completion("s_final_finite")

    def test_runtime_final_finite_verification_advances_cadence_on_repeated_calls(self):
        self.assert_finite_completion("runtime_final_finite")


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(HistoryDeadlineTests)


def child_main(name, receipt):
    if name not in (*PHASES, *FINITE_PHASES, "file_only_control", "small_history_control"):
        raise ValueError("Unknown history probe")
    case = _HistoryProbe()
    case.scenario = name
    case.receipt = Path(receipt).resolve()
    case.tmp = Path(os.environ["TMPDIR"]).resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
    case.data["probe"] = {"cases": result.testsRun, "failures": len(result.failures),
                          "errors": len(result.errors), "skips": len(result.skipped)}
    case.persist()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--history-child":
        raise SystemExit(child_main(sys.argv[2], sys.argv[3]))
    unittest.main()
