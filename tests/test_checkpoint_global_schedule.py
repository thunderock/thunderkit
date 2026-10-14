"""H26 global periodic-installation RED; no daemon or host configuration.

Five bounded cases, one installation lifecycle. Missing checkpoint_global is an
ENTRYPOINT-ONLY RED: the lifecycle tails are contracts, not executed evidence.
C/S/H and the local executable transport are independently real controls.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
POPEN = subprocess.Popen
EVIDENCE = os.environ.get("H26_GLOBAL_RED_EVENTS")
NOTE = b"# Global notes\nHistorical text: touch SHOULD-NOT-EXIST; not authority.\n"
ENTRY = "sources/0/notes.md"
UNRELATED = b'# unrelated UTF-8: \xc3\xa9\nMAILTO=""\n0 0 * * * /usr/bin/true\n'
LATER = b"# later unrelated job\n15 2 * * * /usr/bin/true\n"
# Applied only in isolated test children; a tick permits NO subprocesses. Fresh
# removal permits only this test's explicit executable, never PATH crontab/Git.
CHILD_GUARD = """
import atexit, os, sys
rejected = []
allowed = sys.argv[1]
def guard(event, args):
    if event == 'subprocess.Popen':
        argv = args[1]
        if allowed and isinstance(argv, (tuple, list)) and list(argv) in ([allowed, '-l'], [allowed, '-']):
            return
    elif not (event.startswith(('socket.', 'os.exec', 'os.spawn')) or event in
              ('os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty')):
        return
    rejected.append(event)
    raise RuntimeError('fixture forbids network or unapproved process')
def finish():
    if rejected:
        sys.stderr.write('Denied operations: ' + ','.join(rejected) + '\\n')
        sys.stderr.flush()
        os._exit(97)
atexit.register(finish)
sys.addaudithook(guard)
"""


def record(value):
    if EVIDENCE:
        with open(EVIDENCE, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")


def tree(root):
    return {str(p.relative_to(root)): (p.lstat().st_dev, p.lstat().st_ino,
            p.lstat().st_mode, p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in (root, *sorted(root.rglob("*")))}


class LocalTable:
    """Trusted callback with real local compare/write, not invented responses."""
    def __init__(self, path):
        self.path, self.reads, self.writes = path, 0, []
        self.unknown_after_write = False

    def read(self):
        self.reads += 1
        if self.unknown_after_write and self.writes:
            raise OSError("fixture readback unavailable after real write")
        return self.path.read_bytes()

    def replace(self, expected, content):
        if self.path.read_bytes() != expected:
            return False
        self.path.write_bytes(content)
        self.writes.append(content)
        return True


class CheckpointGlobalScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="H26-global-red-fixture-", dir=os.environ["TMPDIR"])
        self.base = Path(self.temporary.name)
        self.children, self.denied = [], []
        self.addCleanup(self.clean)
        self.home, self.backup, self.state, self.sources, self.control_state = (
            self.base / name for name in ("empty-home", "approved-store", "schedule-state", "notes", "control-state"))
        for path in (self.home, self.backup, self.state, self.sources, self.control_state):
            path.mkdir(mode=0o700)
        self.notes, self.other = self.sources / "notes.md", self.sources / "other.md"
        self.notes.write_bytes(NOTE)
        self.other.write_bytes(b"Approved but NOT selected.\n")
        self.config = self.base / "global-settings.json"
        self.python = Path(sys.executable).resolve()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.base),
                    "XDG_CONFIG_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"}
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.enterContext(patch.object(sys, "path", [str(REFERENCES), *sys.path]))
        self.enterContext(patch.dict(sys.modules))
        self.enterContext(patch.object(sys, "dont_write_bytecode", True))
        self.c = importlib.import_module("continuity_config")
        self.s = importlib.import_module("backup_store")
        self.h = importlib.import_module("checkpoint_runtime")
        self.host = importlib.import_module("checkpoint_host")
        self.identity = self.c.ScopeIdentity("global", "h26-global-fixture")
        self.document = {"schema": 1, "kind": "global-settings",
            "policy": {"schema": 1, "backup": {"enabled": True, "interval_minutes": 15},
                       "git": {"push_after_commit": False}},
            "global": {"identity": asdict(self.identity), "backup_root": str(self.backup),
                       "approved_sources": [str(self.notes), str(self.other)]}}
        self.c.validate_continuity_document(self.document, kind="global-settings")
        self.config.write_text(json.dumps(self.document))
        before = tree(self.base)
        self.settings = self.c.resolve_continuity(global_config=self.config, scope="global",
            expected_identity=self.identity, environment=self.env)
        self.assertEqual(tree(self.base), before)
        self.assertEqual(self.settings.approved_sources, (self.notes, self.other))
        self.assertIsNone(self.settings.project_root)
        self.table, self.log, self.monitor = (self.base / n for n in ("table", "calls.jsonl", "owned.json"))
        self.table.write_bytes(UNRELATED)
        self.monitor.write_text("[]")
        self.executable = self.base / "fixture-crontab"
        self.executable.write_text(
            f"#!{self.python} -I\nimport hashlib,json,os,sys\nfrom pathlib import Path\n"
            f"table=Path({str(self.table)!r}); log=Path({str(self.log)!r}); monitor=Path({str(self.monitor)!r})\n"
            "args=sys.argv[1:]\n"
            "row={'args':args,'pid':os.getpid(),'pgid':os.getpgrp(),"
            "'owned_present':[Path(p).is_file() for p in json.loads(monitor.read_text())]}\n"
            "with log.open('a') as stream: stream.write(json.dumps(row)+'\\n')\n"
            "if args==['-l']: sys.stdout.buffer.write(table.read_bytes())\n"
            "elif args==['-']: table.write_bytes(sys.stdin.buffer.read())\n"
            "else: raise SystemExit(64)\n")
        self.executable.chmod(0o700)
        self.driver = LocalTable(self.table)
        self.enterContext(patch.object(subprocess, "Popen", side_effect=self.local_process))
        for module, name in ((os, "system"), (os, "popen"), (socket, "socket"),
                             (socket, "create_connection"), (socket, "getaddrinfo")):
            self.enterContext(patch.object(module, name, side_effect=self.deny))

    def deny(self, *args, **kwargs):
        self.denied.append("forbidden process/socket")
        raise AssertionError(self.denied[-1])

    def local_process(self, argv, *args, **kwargs):
        if (args or kwargs.get("shell") or list(argv) not in
                ([str(self.executable), "-l"], [str(self.executable), "-"])
                or not kwargs.get("start_new_session")
                or Path(kwargs.get("cwd", "/")) != self.base):
            return self.deny()
        process = POPEN(argv, **kwargs)
        self.children.append(process)
        return process

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def clean(self):
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=5)
        pids = {p.pid for p in self.children} | {row["pid"] for row in self.calls()}
        groups_absent = True
        for pid in pids:
            try:
                os.killpg(pid, 0)
                groups_absent = False
            except ProcessLookupError:
                pass
        reaped = all(not Path(f"/proc/{pid}").exists() for pid in pids)
        self.temporary.cleanup()
        absent = not self.base.exists()
        record({"event": "cleanup", "test": self.id(), "root": str(self.base), "root_absent": absent,
                "pids": sorted(pids), "reaped": reaped, "groups_absent": groups_absent, "denied": self.denied})
        self.assertTrue(absent and reaped and groups_absent)
        self.assertEqual(self.denied, [])

    def child(self, body, *args, allow_transport=False):
        command = [str(self.python), "-I", "-B", "-c", CHILD_GUARD + body,
                   str(self.executable) if allow_transport else "", *map(str, args)]
        process = POPEN(command, cwd=self.base, env=self.env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.children.append(process)
        expired = False
        try:
            out, err = process.communicate(timeout=75)
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate(timeout=5)
        record({"event": "child", "test": self.id(), "pid": process.pid, "exit": process.returncode,
                "timeout": expired, "stdout": out, "stderr": err, "transport_permitted": allow_transport})
        self.assertFalse(expired)
        self.assertNotEqual(process.returncode, 97, err)
        return process.returncode, out, err

    def snapshot_request(self):
        q = self.s.qualify_storage(self.s.inspect_storage(self.backup), approval=True)
        self.assertTrue(q.readback_verified)
        self.assertFalse(q.backing_durability_verified)
        return self.c.SnapshotRequest(self.settings, q.identity, (self.notes,), (ENTRY,))

    def verify_snapshot(self, path, payload):
        path = Path(path)
        manifest = self.s.verify_snapshot(path, self.identity, self.settings.limits)
        self.assertEqual(path.parent, self.backup / "global" / self.identity.id / "snapshots")
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(tuple(f.relpath for f in manifest.files), (ENTRY,))
        self.assertEqual(manifest.context_entrypoints, (ENTRY,))
        self.assertEqual(manifest.history.status, "not-applicable")
        self.assertEqual((manifest.repository, manifest.ref, manifest.head, manifest.base), (None,) * 4)
        self.assertEqual(self.s.read_payload(path, ENTRY, self.identity, self.settings.limits), payload)
        self.assertFalse((self.base / "SHOULD-NOT-EXIST").exists())
        return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()

    def api(self):
        self.assertTrue((REFERENCES / "checkpoint_global.py").is_file(),
                        "H26 ENTRYPOINT-ONLY RED: missing explicit global periodic installation API")
        api = importlib.import_module("checkpoint_global")
        for name in ("GlobalScheduleRequest", "preview_global", "apply_global", "load_global", "remove_global"):
            self.assertTrue(callable(getattr(api, name, None)), "Missing global API: " + name)
        return api

    def preview(self, api, request=None, **qualifications):
        request = request or self.snapshot_request()
        schedule = api.GlobalScheduleRequest(snapshot=request, state_dir=self.state,
                                             python=self.python, global_config=self.config)
        return api.preview_global(schedule, self.driver, **qualifications)

    def install(self, api, request=None):
        request = request or self.snapshot_request()
        first = self.s.create_snapshot(request)
        self.assertEqual(first.status, "complete")
        before = tree(self.base)
        preview = self.preview(api, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        self.assertEqual(tree(self.base), before, "Qualified preview must be read-only")
        result = api.apply_global(preview, self.driver, approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        installed = result.installation
        self.assertIsNotNone(installed)
        self.assertEqual(installed.state_dir, self.state)
        self.assertEqual(api.load_global(self.state, identity=self.identity), installed)
        command = shlex.split(installed.periodic_command)
        self.assertEqual(command[1:], ["--tick"])
        self.assertEqual(Path(command[0]).parent, self.state)
        self.assertTrue(Path(command[0]).stat().st_mode & 0o111)
        shebang = Path(command[0]).read_text().splitlines()[0]
        self.assertTrue(shebang.startswith("#!"))
        self.assertEqual(shlex.split(shebang[2:])[0], str(self.python), "Pin the approved interpreter")
        self.assertIn(Path(command[0]), {f.path for f in installed.files})
        self.assertTrue(all(f.path.is_relative_to(self.state) for f in installed.files))
        # Expected scheduler command is derived from the public record, not a
        # copied preview payload; the unrelated bytes remain literal fixtures.
        self.assertTrue(self.table.read_bytes().startswith(UNRELATED))
        self.assertIn(b"* * * * * " + installed.periodic_command.encode() + b"\n", self.table.read_bytes())
        return installed

    def tick(self, installed):
        argv = shlex.split(installed.periodic_command)
        return self.child("\nimport runpy\nsys.argv=sys.argv[2:]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n", *argv)

    def fresh_remove(self, approval):
        body = (f"\nfrom pathlib import Path\nimport json\nsys.path.insert(0,{str(REFERENCES)!r})\n"
                "import checkpoint_global as A, checkpoint_host as Host, continuity_config as C\n"
                "r=A.load_global(Path(sys.argv[2]),identity=C.ScopeIdentity('global',sys.argv[3]))\n"
                "d=Host.CrontabDriver(Path(sys.argv[1]),service_qualified=True,exclusive_writer=True)\n"
                "v=A.remove_global(r,d,approval=sys.argv[4]=='yes')\n"
                "print(json.dumps({'status':v.status,'blockers':v.blockers,'retained_paths':list(map(str,v.retained_paths))}))\n")
        code, out, err = self.child(body, self.state, self.identity.id, "yes" if approval else "no", allow_transport=True)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_00_real_global_control_then_installed_tick_revocation_and_fresh_removal(self):
        request = self.snapshot_request()
        first = self.s.create_snapshot(request)
        self.assertEqual(first.status, "complete")
        digest = self.verify_snapshot(first.snapshot_path, NOTE)
        captured = self.h.checkpoint(self.h.CheckpointRequest(request, self.control_state, trigger="due"))
        self.assertEqual(captured.status, "captured", captured.warnings)
        self.assertIsNone(captured.push)
        self.assertEqual(captured.observed_oids, ())
        # Frozen H reports unknown age for global roots. The new installed
        # wrapper contract below must report the selected note's actual age.
        self.assertIsNone(captured.context_age_seconds)
        self.verify_snapshot(captured.snapshot.snapshot_path, NOTE)
        again = self.h.checkpoint(self.h.CheckpointRequest(request, self.control_state, trigger="due"))
        self.assertEqual(again.status, "not-due", again.warnings)
        host = self.host.preview_connections(self.host.HostRequest(request, self.state, self.python))
        self.assertEqual(host.blockers, ("project_history_required",))
        record({"event": "global_control", "test": self.id(), "scope": asdict(self.identity),
                "allowlist": list(map(str, self.settings.approved_sources)), "selected": [str(self.notes)],
                "snapshot": str(first.snapshot_path), "manifest_sha256": digest,
                "runtime_status": captured.status, "due_status": again.status, "push": captured.push,
                "core_context_age_seconds": captured.context_age_seconds,
                "host_blockers": host.blockers, "backing_identity": asdict(request.storage) | {"root": str(self.backup)}})
        api = self.api()  # Independent real control above survives missing entrypoint.
        installed = self.install(api, request)
        owned = {p for p in self.state.rglob("*") if p.is_file()}
        later_note = NOTE + b"New note captured by installed periodic tick.\n"
        self.notes.write_bytes(later_note)
        code, out, err = self.tick(installed)
        self.assertEqual((code, err), (0, ""))
        value = json.loads(out)
        self.assertEqual(value["status"], "captured")
        self.assertIsNone(value["push"])
        self.assertIsInstance(value["context_age_seconds"], (int, float))
        self.assertGreaterEqual(value["context_age_seconds"], 0)
        self.verify_snapshot(value["snapshot"]["snapshot_path"], later_note)
        saved = tree(self.backup)
        code, out, err = self.tick(installed)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "not-due")
        self.assertEqual(tree(self.backup), saved)
        original = self.config.read_bytes()
        self.document["policy"]["backup"]["enabled"] = False
        for policy in (json.dumps(self.document).encode(), b'{"schema":true}'):
            self.config.write_bytes(policy)
            code, out, err = self.tick(installed)
            self.assertNotEqual(code, 0, "Disabled/invalid current global policy must fail closed")
            self.assertEqual(tree(self.backup), saved)
        self.config.write_bytes(original)
        self.table.write_bytes(self.table.read_bytes() + LATER)
        self.monitor.write_text(json.dumps(sorted(map(str, owned))))
        before = tree(self.base)
        self.assertEqual(self.fresh_remove(False)["status"], "approval_required")
        self.assertEqual(tree(self.base), before, "Unapproved removal must not even launch transport")
        self.assertEqual(self.fresh_remove(True)["status"], "removed")
        self.assertEqual(self.table.read_bytes(), UNRELATED + LATER)
        self.assertTrue(all(not p.exists() for p in owned))
        self.assertEqual(tree(self.backup), saved)
        calls = self.calls()
        writes = [i for i, row in enumerate(calls) if row["args"] == ["-"]]
        self.assertEqual(len(writes), 1)
        self.assertTrue(all(calls[writes[0]]["owned_present"]), "Remove scheduler BEFORE owned files")
        readback = next(row for row in calls[writes[0] + 1:] if row["args"] == ["-l"])
        self.assertTrue(all(readback["owned_present"]), "Confirm removal BEFORE owned file deletion")
        self.assertEqual(tuple(self.home.iterdir()), ())

    def test_01_real_file_backed_crontab_control(self):
        driver = self.host.CrontabDriver(self.executable, service_qualified=True, exclusive_writer=True)
        self.assertEqual(driver.read(), UNRELATED)
        self.assertTrue(driver.replace(UNRELATED, UNRELATED + LATER))
        self.assertEqual(driver.read(), UNRELATED + LATER)
        self.assertEqual(self.table.read_bytes(), UNRELATED + LATER)
        self.assertFalse(driver.replace(UNRELATED, b"# stale replacement\n"))
        self.assertEqual(self.table.read_bytes(), UNRELATED + LATER)
        body = (f"\nfrom pathlib import Path\nimport json\nsys.path.insert(0,{str(REFERENCES)!r})\n"
                "import checkpoint_host as Host\n"
                "d=Host.CrontabDriver(Path(sys.argv[1]),service_qualified=True,exclusive_writer=True)\n"
                "print(json.dumps({'table_hex':d.read().hex()}))\n")
        code, out, err = self.child(body, allow_transport=True)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(bytes.fromhex(json.loads(out)["table_hex"]), UNRELATED + LATER)
        self.assertTrue(all(row["pid"] == row["pgid"] for row in self.calls()))
        self.assertEqual(sum(row["args"] == ["-"] for row in self.calls()), 1)
        record({"event": "transport_control", "test": self.id(), "calls": self.calls(),
                "table_sha256": hashlib.sha256(self.table.read_bytes()).hexdigest(), "daemon_proven": False})

    def test_02_qualification_and_fresh_approval_precede_writes_or_commands(self):
        api = self.api()
        request = self.snapshot_request()
        for flags in ({}, {"service_qualified": True}, {"exclusive_writer": True}):
            before = tree(self.base)
            reads = self.driver.reads
            preview = self.preview(api, request, **flags)
            self.assertEqual(preview.status, "blocked")
            self.assertIn("scheduler_unqualified", preview.blockers)
            self.assertEqual(api.apply_global(preview, self.driver, approval=True).status, "blocked")
            self.assertEqual(tree(self.base), before)
            self.assertEqual(self.driver.reads, reads)
        first = self.s.create_snapshot(request)
        self.assertEqual(first.status, "complete")
        preview = self.preview(api, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        before, reads = tree(self.base), self.driver.reads
        self.assertEqual(api.apply_global(preview, self.driver).status, "approval_required")
        self.assertEqual(tree(self.base), before)
        self.assertEqual(self.driver.reads, reads)
        self.assertEqual(self.driver.writes, [])
        self.assertEqual(self.calls(), [])

    def test_03_exact_ownership_refuses_changed_file_scope_or_replacement_store(self):
        api = self.api()
        installed = self.install(api)
        before = tree(self.base)
        with self.assertRaises(self.c.ContinuityError):
            api.load_global(self.state, identity=self.c.ScopeIdentity("global", "different-scope"))
        self.assertEqual(tree(self.base), before)
        launcher = Path(shlex.split(installed.periodic_command)[0])
        original = launcher.read_bytes()
        launcher.write_bytes(original + b"\n# changed owned file\n")
        before = tree(self.base)
        with self.assertRaises(self.c.ContinuityError):
            api.load_global(self.state, identity=self.identity)
        self.assertEqual(api.remove_global(installed, self.driver, approval=True).status, "blocked")
        self.assertEqual(tree(self.base), before)
        launcher.write_bytes(original)
        old = self.backup.with_name("original-store")
        self.backup.rename(old)
        shutil.copytree(old, self.backup)  # Same bytes, different backing inode.
        before, backing_before = tree(self.base), tree(self.backup)
        with self.assertRaises(self.c.ContinuityError):
            api.load_global(self.state, identity=self.identity)
        code, out, err = self.tick(installed)
        self.assertNotEqual(code, 0, "Installed scope must never requalify a replacement store")
        self.assertEqual(tree(self.backup), backing_before)
        self.assertEqual(api.remove_global(installed, self.driver, approval=True).status, "blocked")
        # A safe diagnostic result may update state; neither backing tree nor
        # scheduler/owned bytes may be adopted or deleted on this path.
        for name, value in before.items():
            if name.startswith(("approved-store", "original-store")) or name == "table":
                self.assertEqual(tree(self.base)[name], value)
        self.assertEqual(launcher.read_bytes(), original)

    def test_04_unknown_postwrite_readback_retains_fresh_recoverable_ownership(self):
        api = self.api()
        request = self.snapshot_request()
        self.assertEqual(self.s.create_snapshot(request).status, "complete")
        preview = self.preview(api, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        self.driver.unknown_after_write = True
        result = api.apply_global(preview, self.driver, approval=True)
        self.assertEqual(len(self.driver.writes), 1, "Failure must follow a real local table write")
        self.assertNotEqual(self.table.read_bytes(), UNRELATED)
        self.assertEqual(result.status, "blocked")
        self.assertIsNotNone(result.installation)
        owned = {p for p in self.state.rglob("*") if p.is_file()}
        self.assertTrue({f.path for f in result.installation.files} <= owned)
        self.assertTrue(owned <= set(result.retained_paths))
        saved = tree(self.backup)
        before = tree(self.base)
        self.assertEqual(self.fresh_remove(False)["status"], "approval_required")
        self.assertEqual(tree(self.base), before)
        self.assertEqual(self.fresh_remove(True)["status"], "removed")
        self.assertEqual(self.table.read_bytes(), UNRELATED)
        self.assertEqual(tree(self.backup), saved)
        self.assertTrue(all(not p.exists() for p in owned))


if __name__ == "__main__":
    unittest.main()
