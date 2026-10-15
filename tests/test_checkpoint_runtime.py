"""H2 initial runtime RED: real C/S inputs, no host/Git/model activation.

Frozen entrypoint: checkpoint(CheckpointRequest(snapshot, state_dir,
trigger="event", event_id=None, deadline_seconds=60.0), *, clock=None).
Clock supplies monotonic(), time(), boot_id(); the frozen result separates
status, snapshot, push, context_age_seconds, observed_oids, coverage_gaps and
next_action. State lives in caller-approved state_dir/state.json; the exclusive
lock is state_dir/.checkpoint.lock. The directory is outside source and store.

Only four initial behaviors are prepared. While the module is absent, each RED
proves its missing callable AFTER real S qualification/capture/readback; it does
not prove the behavioral assertions below that boundary have executed.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
MODULE = REFERENCES / "checkpoint_runtime.py"
MISSING = "Missing checkpoint runtime callable: skills/references/checkpoint_runtime.py:checkpoint"
CONTEXT = b"# Saved context\nUnfinished work remains; this is not a new model summary.\n"
INITIAL_CONTEXT = b"# Earlier saved context\nIndependent S fixture control.\n"
GAPS = {"periodic_unconfigured", "ref_events_unconfigured", "publication_disabled"}
STATE_MAX_BYTES = 64 * 1024


class FakeClock:
    def __init__(self, *, mono=100.0, wall=1_700_000_300.0, boot="h2-boot-a"):
        self.mono = mono
        self.wall = wall
        self.boot = boot

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall

    def boot_id(self):
        return self.boot


class RuntimeFixture(unittest.TestCase):
    """No test methods: reusable independent control, not an extra suite."""

    def setUp(self):
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        assert scratch is not None
        self.assertTrue(Path(scratch).is_absolute())
        self.temp = tempfile.TemporaryDirectory(prefix="thunderkit-H2-red-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.backups = self.base / "approved-store"
        self.state_dir = self.base / "approved-runtime-state"
        self.home = self.base / "empty-home"
        for directory in (self.project, self.backups, self.state_dir, self.home):
            directory.mkdir(mode=0o700)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home), "TMPDIR": str(self.base),
            "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
        }, clear=True))
        previous = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)
        stack.enter_context(patch.object(sys, "path", [str(REFERENCES), *sys.path]))
        stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        stack.enter_context(patch.dict(sys.modules))
        # A non-Git capture needs no process, transport, credentials or model.
        for target in ("subprocess.Popen", "os.system", "os.popen",
                       "socket.socket", "socket.create_connection"):
            stack.enter_context(patch(target, side_effect=AssertionError("H2 must remain offline/process-free")))
        self.c = importlib.import_module("continuity_config")
        self.s = importlib.import_module("backup_store")
        stack.enter_context(patch.object(self.c, "resolve_continuity", side_effect=AssertionError(
            "Runtime consumes the explicit request; never resolve ambient configuration")))
        self.clock = FakeClock()
        self.notes = self.project / "notes.md"
        self.write_context(INITIAL_CONTEXT)
        self.identity = self.c.ScopeIdentity("project", "h2-fixture")
        self.settings = self.c.ResolvedSettings(
            identity=self.identity, project_root=self.project, backup_root=self.backups,
            context_path=None, context_source="unconfigured",
            backup=self.c.BackupPolicy(enabled=True, interval_minutes=2, prune=False),
            git=self.c.GitPolicy(push_after_commit=False), approved_sources=(self.notes,),
        )
        self.qualification = self.s.qualify_storage(self.s.inspect_storage(self.backups), approval=True)
        self.assertTrue(self.qualification.readback_verified)
        self.assertFalse(self.qualification.backing_durability_verified)
        self.snapshot_request = self.c.SnapshotRequest(
            settings=self.settings, storage=self.qualification.identity,
            approved_sources=(self.notes,), context_entrypoints=("notes.md",),
            include_history=False,
        )
        self.locator = self.backups / "projects/h2-fixture/latest.json"
        self.control = self.s.create_snapshot(self.snapshot_request)
        self.assert_snapshot(self.control, INITIAL_CONTEXT)
        self.assertEqual(self.control.status, "complete")
        self.assertFalse((self.project / ".git").exists())
        self.assertEqual(list(self.state_dir.iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertFalse(self.state_dir.is_relative_to(self.backups))
        self.assertFalse(self.state_dir.is_relative_to(self.project))
        # Make the later runtime capture distinguishable from S's valid control.
        self.write_context(CONTEXT)
        self.fixture_verified = True

    def write_context(self, payload, *, saved_at=1_700_000_000.0):
        self.notes.write_bytes(payload)
        os.utime(self.notes, (saved_at, saved_at))

    def tree(self, root):
        result = {}
        for path in (root, *sorted(root.rglob("*"))):
            info = path.lstat()
            self.assertFalse(stat.S_ISLNK(info.st_mode), "Unexpected fixture symlink")
            regular = stat.S_ISREG(info.st_mode)
            # Runtime lock acquisition may change directory times, not files.
            result[str(path.relative_to(root))] = (
                info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_mtime_ns if regular else None,
                info.st_ctime_ns if regular else None,
                path.read_bytes() if regular else None,
            )
        return result

    def runtime_api(self, name="checkpoint_runtime_h2"):
        self.assertTrue(self.fixture_verified, "Prove the actual S fixture before runtime RED")
        api = None
        if MODULE.is_file():
            spec = importlib.util.spec_from_file_location(name, MODULE)
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertIsNotNone(spec.loader)
            assert spec.loader is not None
            api = importlib.util.module_from_spec(spec)
            sys.modules[name] = api
            spec.loader.exec_module(api)
        self.assertTrue(callable(getattr(api, "checkpoint", None)), MISSING)
        assert api is not None
        self.assertTrue(callable(getattr(api, "CheckpointRequest", None)), "Missing CheckpointRequest")
        return api

    def request(self, api, *, trigger="event", event_id=None):
        return api.CheckpointRequest(
            snapshot=self.snapshot_request, state_dir=self.state_dir,
            trigger=trigger, event_id=event_id, deadline_seconds=60.0,
        )

    def assert_snapshot(self, snapshot, payload):
        self.assertIsInstance(snapshot, self.c.SnapshotResult)
        self.assertEqual(snapshot.status, "complete")
        self.assertEqual(snapshot.identity, self.identity)
        self.assertEqual(snapshot.omissions, ())
        self.assertIsNotNone(snapshot.snapshot_path)
        manifest = self.s.verify_snapshot(snapshot.snapshot_path, self.identity, self.settings.limits)
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(manifest.context_entrypoints, ("notes.md",))
        self.assertEqual(tuple(entry.relpath for entry in manifest.files), ("notes.md",))
        self.assertEqual(manifest.history.status, "not-applicable")
        self.assertEqual(self.s.read_payload(snapshot.snapshot_path, "notes.md", self.identity,
                                             self.settings.limits), payload)
        manifest_path = snapshot.snapshot_path / "manifest.json"
        self.assertEqual(snapshot.manifest_sha256, hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        locator = self.c.decode_locator(self.c.read_json(self.locator), pointer=True)
        self.assertEqual(locator.identity, self.identity)
        self.assertEqual(locator.path, str(manifest_path))

    def assert_captured(self, result, payload):
        self.assertEqual(result.status, "captured")
        self.assert_snapshot(result.snapshot, payload)
        self.assertIsNone(result.push)
        self.assertEqual(result.observed_oids, ())
        self.assertIsInstance(result.coverage_gaps, tuple)
        self.assertTrue(GAPS <= set(result.coverage_gaps), "Do not imply installed/full-target protection")
        self.assertIsInstance(result.next_action, str)
        self.assertTrue(result.next_action.strip())
        self.assertTrue(type(result).__dataclass_params__.frozen, "CheckpointResult is immutable data")

    def assert_state(self, result, clock, *, event_id=None):
        state_path = self.state_dir / "state.json"
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        state = self.c.read_json(state_path, max_bytes=STATE_MAX_BYTES)
        self.assertEqual(state["schema"], 1)
        self.assertEqual(state["identity"], asdict(self.identity))
        self.assertEqual(state["project_root"], str(self.project))
        self.assertEqual(state["backup_root"], str(self.backups))
        self.assertEqual(state["approved_sources"], [str(self.notes)])
        self.assertEqual(state["boot_id"], clock.boot_id())
        self.assertEqual(state["last_success_monotonic"], clock.monotonic())
        self.assertEqual(state["last_event_id"], event_id)
        self.assertEqual(state["last_snapshot"], str(result.snapshot.snapshot_path))
        self.assertFalse({"grant", "grants", "authority", "environment", "env", "commands"} & state.keys())
        self.assertFalse((self.state_dir / ".checkpoint.lock").exists())


class CheckpointRuntimeTests(RuntimeFixture):
    def test_event_captures_real_saved_context_with_its_actual_age(self):
        before_source = self.tree(self.project)
        with patch.object(self.s, "create_snapshot", wraps=self.s.create_snapshot) as capture:
            api = self.runtime_api()
            result = api.checkpoint(self.request(api, event_id="artifact-save-1"), clock=self.clock)
            capture.assert_called_once()
        self.assert_captured(result, CONTEXT)
        self.assertNotEqual(result.snapshot.snapshot_path, self.control.snapshot_path)
        self.assertEqual(result.context_age_seconds, self.clock.time() - self.notes.stat().st_mtime)
        self.assertGreater(result.context_age_seconds, 0)
        self.assert_state(result, self.clock, event_id="artifact-save-1")
        self.assertEqual(self.tree(self.project), before_source)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_due_uses_policy_monotonic_interval_despite_backwards_wall_clock(self):
        with patch.object(self.s, "create_snapshot", wraps=self.s.create_snapshot) as capture:
            api = self.runtime_api()
            first = api.checkpoint(self.request(api), clock=self.clock)
            self.assert_captured(first, CONTEXT)
            self.assert_state(first, self.clock)
            interval = self.settings.backup.interval_minutes * 60
            start = self.clock.mono
            self.clock.mono = start + interval - 1
            self.clock.wall -= 600
            before = (self.tree(self.backups), self.tree(self.state_dir), self.tree(self.project))
            early = api.checkpoint(self.request(api, trigger="due"), clock=self.clock)
            self.assertEqual(early.status, "not-due")
            self.assertIsNone(early.snapshot)
            self.assertIsNone(early.push)
            self.assertEqual(capture.call_count, 1, "An early tick must not even dedupe through S")
            self.assertEqual((self.tree(self.backups), self.tree(self.state_dir), self.tree(self.project)), before)
            updated = CONTEXT + b"Another saved artifact.\n"
            # Source mtime is now in the future relative to the regressed clock.
            self.write_context(updated)
            self.clock.mono = start + interval
            due = api.checkpoint(self.request(api, trigger="due"), clock=self.clock)
            self.assertEqual(capture.call_count, 2)
        self.assert_captured(due, updated)
        self.assertIsNone(due.context_age_seconds, "Negative context age is unknown, never fresh")
        self.assert_state(due, self.clock)

    def test_fresh_instances_preserve_same_boot_due_state_but_not_clock_resets(self):
        api = self.runtime_api("checkpoint_runtime_h2_first")
        first = api.checkpoint(self.request(api), clock=self.clock)
        self.assert_captured(first, CONTEXT)
        self.assert_state(first, self.clock)
        fresh = self.runtime_api("checkpoint_runtime_h2_restart")
        self.assertIsNot(fresh, api)
        resumed_clock = FakeClock(mono=self.clock.mono + 1, wall=self.clock.wall, boot=self.clock.boot)
        before = (self.tree(self.backups), self.tree(self.state_dir))
        resumed = fresh.checkpoint(self.request(fresh, trigger="due"), clock=resumed_clock)
        self.assertEqual(resumed.status, "not-due")
        self.assertIsNone(resumed.snapshot)
        self.assertEqual((self.tree(self.backups), self.tree(self.state_dir)), before)
        for label, clock in (
            ("new_boot", FakeClock(mono=102.0, wall=self.clock.wall, boot="h2-boot-b")),
            ("decreased_monotonic", FakeClock(mono=101.0, wall=self.clock.wall, boot="h2-boot-b")),
        ):
            # Both are inside the interval; only boot/monotonic invalidation is due.
            with self.subTest(reset=label):
                payload = CONTEXT + label.encode("ascii") + b"\n"
                self.write_context(payload)
                restarted = self.runtime_api("checkpoint_runtime_h2_" + label)
                result = restarted.checkpoint(self.request(restarted, trigger="due"), clock=clock)
                self.assert_captured(result, payload)
                self.assert_state(result, clock)

    def test_unknown_lock_is_preserved_and_busy_does_not_capture_or_write_state(self):
        lock = self.state_dir / ".checkpoint.lock"
        lock.mkdir(mode=0o700)
        (lock / "unknown-owner.txt").write_bytes(b"Foreign lock: no deletion authority.\n")
        os.utime(lock, (1.0, 1.0))
        before = (self.tree(self.backups), self.tree(self.state_dir), self.tree(self.project))
        with patch.object(self.s, "create_snapshot", wraps=self.s.create_snapshot) as capture:
            api = self.runtime_api()
            result = api.checkpoint(self.request(api), clock=self.clock)
            capture.assert_not_called()
        self.assertEqual(result.status, "busy")
        self.assertIsNone(result.snapshot)
        self.assertIsNone(result.push)
        self.assertEqual(result.observed_oids, ())
        self.assertTrue(result.next_action.strip())
        self.assertFalse((self.state_dir / "state.json").exists())
        self.assertEqual((self.tree(self.backups), self.tree(self.state_dir), self.tree(self.project)), before)
        self.assertEqual(lock.stat().st_mtime, 1.0)


if __name__ == "__main__":
    unittest.main()
