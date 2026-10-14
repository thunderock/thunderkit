"""Small model-free CLI tracer: preview -> consent -> setup -> later capture.

Initial RED is missing-entrypoint evidence only; behavioral tails have not run.
The existing-API control qualifies the real local Git/S/H fixture independently.
Proposed argv and rationale: .omh/artifacts/continuity/H17-cli-contract.md.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_checkpoint_install as fixtures

CLI = fixtures.REFERENCES / "checkpoint_cli.py"
POPEN = subprocess.Popen
_GIT_OPTIONS = (
    "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=",
    "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
    "-c", "gc.auto=0", "-c", "maintenance.auto=false",
    "-c", "pack.threads=1", "-c", "pack.window=0", "-c", "pack.depth=0",
    "-c", "core.attributesFile=/dev/null",
)


def local_git(executable, argv, cwd, env, base):
    """Only source-inspected S/H Git argv; no transport or arbitrary programs."""
    if (str(executable) != "/usr/bin/git" or not isinstance(argv, (list, tuple))
            or not argv or str(argv[0]) != "/usr/bin/git" or cwd is None or env is None
            or env.get("GIT_ALLOW_PROTOCOL") != "file"
            or env.get("GIT_CONFIG_NOSYSTEM") != "1"
            or env.get("GIT_CONFIG_GLOBAL") != os.devnull
            or not Path(cwd).resolve().is_relative_to(base)):
        return False
    args = tuple(str(arg) for arg in argv[1:])
    if args[:len(_GIT_OPTIONS)] == _GIT_OPTIONS:
        args = args[len(_GIT_OPTIONS):]
    elif args[:2] == ("-c", "core.excludesFile=/dev/null"):
        args = args[2:]
    fixed = {
        ("rev-parse", "HEAD"), ("rev-parse", "--verify", "HEAD"),
        ("rev-parse", "--verify", "refs/heads/work"),
        ("rev-parse", "--show-toplevel"), ("rev-parse", "--absolute-git-dir"),
        ("rev-parse", "--is-bare-repository"), ("symbolic-ref", "-q", "HEAD"),
        ("symbolic-ref", "--quiet", "--no-recurse", "HEAD"),
        ("config", "--null", "--show-origin", "--no-includes", "--list"),
        ("config", "--null", "--no-includes", "--local", "--list"),
        ("ls-files", "-z", "--", ".thunderkit/continuity.local.json", ".thunderkit/checkpoint"),
        ("check-ignore", "--quiet", "--", ".thunderkit/continuity.local.json"),
        ("check-ignore", "--quiet", "--", ".thunderkit/checkpoint/config.json"),
        ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        ("ls-files", "-z", "--deleted"),
        ("ls-tree", "-r", "--name-only", "-z", "HEAD"),
        ("for-each-ref", "--format=%(refname)", "refs/replace/"),
        ("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/work"),
        ("bundle", "create", "-", "refs/heads/work"),
    }
    if args in fixed:
        return True
    oid = lambda value: re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None
    if len(args) == 3 and args[0] == "cat-file" and args[1] in ("-t", "-s", "commit", "tree", "blob"):
        return oid(args[2])
    if (len(args) == 9 and args[:7] == ("diff-index", "-z", "--name-only", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--diff-filter=D")):
        return oid(args[7]) and args[8] == "--"
    if len(args) == 5 and args[:3] in (
            ("rev-list", "--objects", "--no-object-names"),
            ("rev-list", "--reverse", "--topo-order")):
        return oid(args[3]) and args[4] == "--"
    # S verifies its actual bundle by importing into a disposable bare repository.
    if Path(cwd).resolve() != base / "project":
        if args == ("init", "--bare", "--template=", "--object-format=sha1"):
            return True
        if len(args) == 4 and args[:3] == ("fsck", "--strict", "--no-reflogs"):
            return oid(args[3])
        if len(args) == 3 and args[:2] == ("bundle", "verify"):
            return Path(args[2]).resolve().is_relative_to(base) and Path(args[2]).name == "history.bundle"
        if (len(args) == 6 and args[:4] ==
                ("fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules")):
            return (Path(args[4]).resolve().is_relative_to(base)
                    and Path(args[4]).is_file() and Path(args[4]).name == "history.bundle"
                    and args[5] == "refs/heads/work:refs/heads/work")
    return False


_OFFLINE_CLI = """
import atexit, os, runpy, sys
sys.path.insert(0, sys.argv[1])
from test_checkpoint_cli import local_git
from pathlib import Path
base = Path(sys.argv[2])
denied = []
def guard(event, args):
    bad = event.startswith(('socket.', 'os.exec', 'os.spawn')) or event in (
        'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty')
    if event == 'subprocess.Popen':
        bad = not local_git(*args, base)
    if bad:
        denied.append(event)
        raise RuntimeError('Checkpoint CLI attempted nonlocal execution')
def finish():
    if denied:
        os._exit(97)
atexit.register(finish)
sys.addaudithook(guard)
sys.argv = sys.argv[3:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class CheckpointCLITests(unittest.TestCase):
    def setUp(self):
        self.child_runs = []
        self.fixture = fixtures.CheckpointInstallTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        f = self.fixture
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("gc.auto", "0"), ("maintenance.auto", "false")):
            f.git("config", key, value)
        f.git("add", "--", ".gitignore", "notes.md")
        f.git("commit", "-m", "checkpoint CLI fixture")
        self.head = f.git("rev-parse", "HEAD")
        self.notes = f.project / "notes.md"
        self.notes.write_text("# Current uncommitted context\n", encoding="utf-8")
        self.state = f.base / "runtime-state"
        self.state.mkdir(mode=0o700)
        self.request = replace(f.request, context_entrypoints=("notes.md",),
                               include_history=True, history_ref="refs/heads/work")
        self.prefix = [sys.executable, "-c", _OFFLINE_CLI, str(Path(__file__).parent),
                       str(f.base), str(CLI)]
        self.denied = []
        self.addCleanup(self.assert_no_denied)

        def only_local(argv, *args, **kwargs):
            if (args or kwargs.get("shell", False) or
                    not (isinstance(argv, list) and argv[:len(self.prefix)] == self.prefix)
                    and not local_git(argv[0], argv, kwargs.get("cwd"), kwargs.get("env"), f.base)):
                self.denied.append("process")
                raise AssertionError("Only exact CLI launcher and local S/H Git are allowed")
            return POPEN(argv, **kwargs)

        def no_socket(*args, **kwargs):
            self.denied.append("socket")
            raise AssertionError("Checkpoint CLI must stay offline")

        self.enterContext(patch.object(subprocess, "Popen", side_effect=only_local))
        for name in ("socket", "create_connection", "getaddrinfo"):
            self.enterContext(patch.object(socket, name, side_effect=no_socket))

    def cleanup_fixture(self):
        cleaned = self.fixture.doCleanups()
        self.fixture_removed = not self.fixture.base.exists()
        self.assertTrue(self.fixture_removed, "Fixture survived doCleanups")
        self.assertTrue(cleaned, "Composed fixture cleanup reported an error")

    def assert_no_denied(self):
        self.assertEqual(self.denied, [], "A swallowed firewall refusal is not offline success")

    def argv(self, command):
        f = self.fixture
        result = [command, "--project-root", str(f.project), "--scope", "project",
                  "--identity", f.identity.id, "--backup-root", str(f.backup),
                  "--source", str(self.notes), "--context-entrypoint", "notes.md",
                  "--include-history", "--history-ref", "refs/heads/work"]
        result += (["--state-dir", str(self.state)] if command == "capture" else
                   ["--integration-root", str(f.integration)])
        return result

    def run_cli(self, *args):
        command = [*self.prefix, *args]
        process = POPEN(command, cwd=self.fixture.base, env=self.fixture.env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=75)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate()
            self.child_runs.append({"argv": list(args), "exit": process.returncode,
                                    "stdout": out, "stderr": err, "timeout": True})
            self.fail("Checkpoint CLI exceeded the outer 75-second fixture bound")
        self.child_runs.append({"argv": list(args), "exit": process.returncode,
                                "stdout": out, "stderr": err, "timeout": False})
        return subprocess.CompletedProcess(command, process.returncode, out, err)

    def output(self, process):
        self.assertEqual(process.returncode, 0, "Missing/failed checkpoint CLI: " + process.stderr)
        self.assertTrue(process.stdout.strip(), "CLI must emit its actual JSON result")
        value = json.loads(process.stdout)
        self.assertIsInstance(value, dict)
        return value

    def tree(self, root=None):
        root = root or self.fixture.base
        return {str(p.relative_to(root)): (p.lstat().st_mode, p.lstat().st_ino,
                p.lstat().st_mtime_ns, p.lstat().st_ctime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                for p in (root, *sorted(root.rglob("*")))}

    def verify(self, path, content):
        f = self.fixture
        path = Path(path)
        self.assertTrue(path.is_relative_to(f.backup))
        manifest = f.s.verify_snapshot(path, f.identity, f.settings.limits)
        self.assertEqual((manifest.completion, manifest.head, manifest.ref),
                         ("complete", self.head, "refs/heads/work"))
        self.assertEqual(manifest.history.status, "bundled")
        self.assertEqual(manifest.context_entrypoints, ("notes.md",))
        self.assertEqual({entry.relpath for entry in manifest.files}, {"notes.md"})
        self.assertEqual(f.s.read_payload(path, "notes.md", f.identity, f.settings.limits), content)
        return manifest

    def test_00_existing_setup_and_runtime_fixture_control(self):
        f = self.fixture
        before = self.tree()
        preview = f.api.preview_setup(self.request)
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        self.assertEqual(f.api.apply_setup(preview).status, "approval_required")
        self.assertEqual(self.tree(), before)
        result = f.api.apply_setup(preview, approval=True)
        self.assertEqual(result.status, "configured", result.blockers)
        self.verify(result.receipt.manifest_path, self.notes.read_bytes())
        self.notes.write_text("# Later API control context\n", encoding="utf-8")
        runtime = importlib.import_module("checkpoint_runtime")
        storage = f.s.qualify_storage(f.s.inspect_storage(f.backup), approval=True).identity
        snapshot = f.c.SnapshotRequest(f.settings, storage, (self.notes,), ("notes.md",),
                                      True, "refs/heads/work")
        captured = runtime.checkpoint(runtime.CheckpointRequest(snapshot, self.state))
        self.assertEqual(captured.status, "captured", captured.warnings)
        self.assertEqual(captured.push.status, "denied")
        self.verify(captured.snapshot.snapshot_path, self.notes.read_bytes())

    def test_help_is_model_free_and_read_only(self):
        before = self.tree()
        process = self.run_cli("--help")
        self.assertEqual(process.returncode, 0, "Missing checkpoint CLI --help: " + process.stderr)
        for command in ("preview", "setup", "capture"):
            self.assertIn(command, process.stdout)
        self.assertEqual(self.tree(), before)

    def test_preview_reports_explicit_scope_and_private_writes_without_mutation(self):
        f = self.fixture
        before = self.tree()
        value = self.output(self.run_cli(*self.argv("preview")))
        self.assertEqual(value["status"], "approval_required")
        request = value["request"]
        self.assertEqual(request["settings"]["identity"], {"kind": "project", "id": f.identity.id})
        self.assertEqual(request["settings"]["project_root"], str(f.project))
        self.assertEqual(request["settings"]["backup_root"], str(f.backup))
        self.assertEqual(request["approved_sources"], [str(self.notes)])
        self.assertEqual(request["integration_root"], str(f.integration))
        self.assertEqual(value["storage"]["durability"], "unqualified")
        self.assertEqual(value["storage"]["inode"], f.backup.stat().st_ino)
        self.assertEqual({m["path"] for m in value["mutations"]},
                         {str(f.private), str(f.integration / "config.json")})
        self.assertTrue(all(m["operation"] == "create" and m["mode"] == 0o600
                            for m in value["mutations"]))
        self.assertIn("setup_write", value["missing_approvals"])
        self.assertEqual({c["name"]: c["status"] for c in value["capabilities"]},
                         {"periodic": "disabled", "ref_events": "disabled",
                          "auto_push": "blocked", "full_target": "blocked"})
        self.assertEqual(self.tree(), before)

    def test_missing_command_approval_makes_zero_writes(self):
        for command in ("setup", "capture"):
            with self.subTest(command=command):
                before = self.tree()
                process = self.run_cli(*self.argv(command))
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertEqual(process.stdout, "")
                self.assertEqual(json.loads(process.stderr)["error"]["code"], "approval_required")
                self.assertEqual(self.tree(), before)

    def test_approved_setup_then_fresh_capture_preserves_context_and_denies_push(self):
        f = self.fixture
        git_before = self.tree(f.project / ".git")
        first_bytes = self.notes.read_bytes()
        setup = self.output(self.run_cli(*self.argv("setup"), "--approve"))
        self.assertEqual(setup["status"], "configured")
        self.assertTrue({str(f.private), str(f.integration / "config.json")} <= set(setup["changed_paths"]))
        local = f.c.read_json(f.private)
        f.c.validate_continuity_document(local, kind="project-settings")
        first = Path(local["latest_context"]["path"])
        self.assertEqual(str(first), setup["receipt"]["manifest_path"])
        self.verify(first, first_bytes)
        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), setup["receipt"]["manifest_sha256"])
        for path in (f.private, f.integration / "config.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        integration = f.c.read_json(f.integration / "config.json")
        self.assertEqual(integration["approved_sources"], [str(self.notes)])
        self.assertTrue(integration["settings"]["git"]["push_after_commit"])
        self.notes.write_text("# Newly saved context after setup\n", encoding="utf-8")
        before = self.tree()
        denied = self.run_cli(*self.argv("capture"))
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertEqual(json.loads(denied.stderr)["error"]["code"], "approval_required")
        self.assertEqual(self.tree(), before, "Saved settings cannot authorize later capture")
        result = self.output(self.run_cli(*self.argv("capture"), "--approve"))
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["push"]["status"], "denied")
        self.assertEqual(result["push"]["oid"], self.head)
        self.assertEqual(result["push"]["destination_ref"], "refs/heads/work")
        self.assertIn("fresh_authority_required", result["coverage_gaps"])
        self.assertIn("reachable_history_only", result["coverage_gaps"])
        current = Path(result["snapshot"]["snapshot_path"])
        self.assertNotEqual(current, first.parent)
        self.verify(current, self.notes.read_bytes())
        self.verify(first, first_bytes)
        self.assertEqual(result["snapshot"]["manifest_sha256"],
                         hashlib.sha256((current / "manifest.json").read_bytes()).hexdigest())
        locator = f.c.read_json(f.backup / "projects" / f.identity.id / "latest.json")
        self.assertEqual(locator["path"], str(current / "manifest.json"))
        state = f.c.read_json(self.state / "state.json")
        self.assertEqual(state["last_snapshot"], str(current))
        self.assertEqual(state["publication_status"], "denied")
        self.assertFalse({"grant", "authority", "commands", "environment"} & state.keys())
        self.assertFalse((self.state / ".checkpoint.lock").exists())
        self.assertEqual(self.tree(f.project / ".git"), git_before)
        self.assertFalse((f.project / ".thunderkit/config.json").exists())
        self.assertEqual(tuple(f.home.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
