"""Global CLI tracer: real C/S/H control, explicit scope and fresh consent.

The baseline CLI only accepts project scope. RED must be its real structured
argument refusal, never an invalid nested policy or a synthetic global request.
Exact contract/evidence: .omh/artifacts/continuity/H20-cli-contract.md.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
CLI = REFERENCES / "checkpoint_cli.py"
POPEN = subprocess.Popen
CONTEXT = b"# Global notes\nHistorical text: touch SHOULD-NOT-EXIST; this is not authority.\n"
RELPATH = "sources/0/notes.md"
_OFFLINE_CLI = """
import atexit, os, runpy, sys
rejected = []
def guard(event, args):
    if event.startswith(('socket.', 'os.exec', 'os.spawn')) or event in (
        'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty'):
        rejected.append(event)
        raise RuntimeError('Global capture must not start a process or socket')
def finish():
    if rejected:
        sys.stderr.write('Denied global CLI operations: ' + ','.join(rejected) + '\\n')
        sys.stderr.flush()
        os._exit(97)
atexit.register(finish)
sys.addaudithook(guard)
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class CheckpointCLIGlobalTests(unittest.TestCase):
    def setUp(self):
        self.child_runs = []
        self.control_receipts = []
        self.denied = []
        temporary = tempfile.TemporaryDirectory(prefix="tk-cli-global-", dir=os.environ["TMPDIR"])
        self.base = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        self.home = self.base / "empty-home"
        self.backup = self.base / "approved-store"
        self.state = self.base / "runtime-state"
        self.sources = self.base / "approved-notes"
        for directory in (self.home, self.backup, self.state, self.sources):
            directory.mkdir(mode=0o700)
        self.notes = self.sources / "notes.md"
        self.notes.write_bytes(CONTEXT)
        self.other = self.sources / "other-approved.md"
        self.other.write_bytes(b"Approved but not selected for this capture.\n")
        self.config = self.base / "global-continuity.json"
        self.env = {
            "PATH": os.environ["PATH"], "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home), "TMPDIR": str(self.base),
            "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C",
        }
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.enterContext(patch.object(sys, "path", [str(REFERENCES), *sys.path]))
        self.enterContext(patch.dict(sys.modules))
        self.enterContext(patch.object(sys, "dont_write_bytecode", True))
        previous = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)

        def deny(*args, **kwargs):
            self.denied.append("process_or_socket")
            raise AssertionError("Global API capture must be process-free and offline")

        self.addCleanup(self.assert_no_denied)
        for module, name in ((subprocess, "Popen"), (os, "system"), (os, "popen"),
                             (socket, "socket"), (socket, "create_connection"), (socket, "getaddrinfo")):
            self.enterContext(patch.object(module, name, side_effect=deny))
        self.c = importlib.import_module("continuity_config")
        self.s = importlib.import_module("backup_store")
        self.h = importlib.import_module("checkpoint_runtime")
        self.identity = self.c.ScopeIdentity("global", "h20-global-fixture")
        self.document = {
            "schema": 1, "kind": "global-settings",
            "policy": {"schema": 1, "backup": {"enabled": True},
                       "git": {"push_after_commit": False}},
            "global": {"identity": asdict(self.identity), "backup_root": str(self.backup),
                       "approved_sources": [str(self.notes), str(self.other)]},
        }
        self.save_settings()

    def assert_no_denied(self):
        self.assertEqual(self.denied, [], "Swallowed process/socket refusal is not success")

    def save_settings(self):
        # Validate the actual nested policy schema BEFORE exercising any CLI.
        self.c.validate_continuity_document(self.document, kind="global-settings")
        self.config.write_text(json.dumps(self.document), encoding="utf-8")
        before = self.tree()
        self.settings = self.c.resolve_continuity(
            global_config=self.config, scope="global", expected_identity=self.identity,
            environment=self.env,
        )
        self.assertEqual(self.tree(), before, "Global settings resolution must be read-only")
        self.assertIsNone(self.settings.project_root)
        self.assertEqual(self.settings.identity, self.identity)
        self.assertEqual(self.settings.backup_root, self.backup)
        self.assertEqual(self.settings.approved_sources,
                         tuple(Path(p) for p in self.document["global"]["approved_sources"]))
        self.assertEqual(self.settings.backup.enabled, self.document["policy"]["backup"]["enabled"])
        self.assertFalse(self.settings.git.push_after_commit)

    def tree(self, root=None):
        root = self.base if root is None else root
        return {str(p.relative_to(root)): (p.lstat().st_mode, p.lstat().st_ino,
                p.lstat().st_mtime_ns, p.lstat().st_ctime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                for p in (root, *sorted(root.rglob("*")))}

    def argv(self, *, source=None):
        return ["capture", "--scope", "global", "--identity", self.identity.id,
                "--global-config", str(self.config), "--backup-root", str(self.backup),
                "--source", str(self.notes if source is None else source),
                "--context-entrypoint", RELPATH, "--state-dir", str(self.state)]

    def run_cli(self, *args):
        command = [sys.executable, "-c", _OFFLINE_CLI, str(CLI), *args]
        process = POPEN(command, cwd=self.base, env=self.env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        start_new_session=True)
        timed_out = False
        try:
            out, err = process.communicate(timeout=75)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate()
        self.child_runs.append({"argv": list(args), "exit": process.returncode,
                                "stdout": out, "stderr": err, "timeout": timed_out})
        self.assertFalse(timed_out, "Global CLI exceeded its outer process-group deadline")
        self.assertNotEqual(process.returncode, 97, "Global CLI attempted a process or socket")
        return subprocess.CompletedProcess(command, process.returncode, out, err)

    def refused(self, process, code, before):
        # Observe zero writes even when the baseline emits the wrong error code.
        self.assertEqual(self.tree(), before, "Refusal must precede storage/state writes")
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(process.stdout, "")
        error = json.loads(process.stderr)["error"]
        self.assertEqual(error["code"], code)
        self.assertTrue(error["detail"])
        self.assertNotIn(str(self.base), error["detail"])

    def verify_snapshot(self, path, payload):
        path = Path(path)
        self.assertEqual(path.parent, self.backup / "global" / self.identity.id / "snapshots")
        manifest = self.s.verify_snapshot(path, self.identity, self.settings.limits)
        self.assertEqual(manifest.identity, self.identity)
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual((manifest.repository, manifest.ref, manifest.head, manifest.base), (None,) * 4)
        self.assertEqual(manifest.history.status, "not-applicable")
        self.assertEqual(manifest.omissions, ())
        self.assertEqual(manifest.context_entrypoints, (RELPATH,))
        self.assertEqual(tuple(entry.relpath for entry in manifest.files), (RELPATH,))
        self.assertEqual(self.s.read_payload(path, RELPATH, self.identity, self.settings.limits), payload)
        self.assertEqual(manifest.files[0].size, len(payload))
        self.assertEqual(manifest.files[0].sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual((path / "payload" / RELPATH).read_bytes(), payload)
        locator = self.c.decode_locator(self.c.read_json(
            self.backup / "global" / self.identity.id / "latest.json"), pointer=True)
        self.assertEqual(locator.identity, self.identity)
        self.assertEqual(locator.path, str(path / "manifest.json"))
        self.assertFalse((self.backup / "projects").exists())
        self.assertFalse((self.base / "SHOULD-NOT-EXIST").exists())
        self.assertEqual(tuple(self.home.iterdir()), ())
        self.assertFalse(any(p.name in (".git", ".thunderkit") for p in self.base.rglob("*")))
        return {"manifest": asdict(manifest), "path": str(path),
                "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
                "payload_sha256": hashlib.sha256(payload).hexdigest(), "payload_hex": payload.hex()}

    def test_00_real_global_config_store_and_runtime_control(self):
        qualification = self.s.qualify_storage(self.s.inspect_storage(self.backup), approval=True)
        self.assertTrue(qualification.readback_verified)
        self.assertFalse(qualification.backing_durability_verified)
        request = self.c.SnapshotRequest(self.settings, qualification.identity, (self.notes,), (RELPATH,))
        first = self.s.create_snapshot(request)
        self.assertEqual(first.status, "complete")
        receipt = self.verify_snapshot(first.snapshot_path, CONTEXT)
        self.assertEqual(first.manifest_sha256, receipt["manifest_sha256"])
        self.control_receipts.append({"api": "S.create_snapshot", **receipt})
        later = CONTEXT + b"Later saved global note.\n"
        self.notes.write_bytes(later)
        captured = self.h.checkpoint(self.h.CheckpointRequest(request, self.state))
        self.assertEqual(captured.status, "captured", captured.warnings)
        self.assertIsNone(captured.push)
        self.assertEqual(captured.observed_oids, ())
        self.assertNotEqual(captured.snapshot.snapshot_path, first.snapshot_path)
        receipt = self.verify_snapshot(captured.snapshot.snapshot_path, later)
        self.assertEqual(captured.snapshot.manifest_sha256, receipt["manifest_sha256"])
        self.control_receipts.append({"api": "H.checkpoint", **receipt})

    def test_approved_global_capture_preserves_only_selected_inert_notes(self):
        sources_before = self.tree(self.sources)
        config_before = self.config.read_bytes()
        process = self.run_cli(*self.argv(), "--approve")
        self.assertEqual(process.returncode, 0, "Global CLI capture is unsupported: " + process.stderr)
        self.assertEqual(process.stderr, "")
        value = json.loads(process.stdout)
        self.assertEqual(value["status"], "captured")
        self.assertIsNone(value["push"])
        self.assertEqual(value["observed_oids"], [])
        self.assertTrue({"periodic_unconfigured", "ref_events_unconfigured", "publication_disabled"}
                        <= set(value["coverage_gaps"]))
        snapshot = value["snapshot"]
        self.assertEqual(snapshot["identity"], asdict(self.identity))
        self.assertEqual(snapshot["status"], "complete")
        receipt = self.verify_snapshot(snapshot["snapshot_path"], CONTEXT)
        self.assertEqual(snapshot["manifest_sha256"], receipt["manifest_sha256"])
        self.control_receipts.append({"api": "CLI.capture", **receipt})
        state = self.c.read_json(self.state / "state.json")
        self.assertEqual(state["identity"], asdict(self.identity))
        self.assertIsNone(state["project_root"])
        self.assertEqual(state["approved_sources"], [str(self.notes)])
        self.assertEqual(state["last_snapshot"], snapshot["snapshot_path"])
        self.assertFalse({"grant", "authority", "commands", "environment"} & state.keys())
        self.assertFalse((self.state / ".checkpoint.lock").exists())
        self.assertEqual(self.tree(self.sources), sources_before)
        self.assertEqual(self.config.read_bytes(), config_before)

    def test_global_capture_without_fresh_approval_makes_zero_writes(self):
        before = self.tree()
        self.refused(self.run_cli(*self.argv()), "approval_required", before)

    def test_global_capture_refuses_project_only_root(self):
        project = self.base / "unrelated-project"
        project.mkdir()
        before = self.tree()
        self.refused(self.run_cli(*self.argv(), "--project-root", str(project), "--approve"),
                     "invalid_arguments", before)

    def test_global_capture_cannot_expand_configured_source_allowlist(self):
        unapproved = self.sources / "unapproved.md"
        unapproved.write_bytes(b"Same directory is not source approval.\n")
        with self.assertRaises(self.c.ContinuityError) as raised:
            self.c.SnapshotRequest(self.settings, self.s.inspect_storage(self.backup), (unapproved,))
        self.assertEqual(raised.exception.code, "identity_mismatch")
        before = self.tree()
        self.refused(self.run_cli(*self.argv(source=unapproved), "--approve"), "identity_mismatch", before)

    def test_global_capture_refuses_even_allowlisted_backup_overlap(self):
        self.document["global"]["approved_sources"].append(str(self.backup))
        self.save_settings()
        before = self.tree()
        request = self.c.SnapshotRequest(self.settings, self.s.inspect_storage(self.backup), (self.backup,))
        with self.assertRaises(self.c.ContinuityError) as raised:
            self.s.create_snapshot(request)
        self.assertEqual(raised.exception.code, "unsafe_path")
        self.assertEqual(self.tree(), before)
        self.refused(self.run_cli(*self.argv(source=self.backup), "--approve"), "unsafe_path", before)

    def test_explicit_global_disabled_policy_is_not_overridden_by_approval(self):
        self.document["policy"]["backup"]["enabled"] = False
        self.save_settings()
        self.assertFalse(self.settings.backup.enabled)
        before = self.tree()
        self.refused(self.run_cli(*self.argv(), "--approve"), "backup_disabled", before)


if __name__ == "__main__":
    unittest.main()
