"""Opt-in local Git connection, real commit -> bounded capture -> owned removal."""
from __future__ import annotations

from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_checkpoint_install as fixtures

MODULE = fixtures.REFERENCES / "checkpoint_host.py"
POPEN = subprocess.Popen


class CheckpointHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CheckpointInstallTests()
        self.addCleanup(self.clean_fixture)
        self.fixture.setUp()
        f = self.fixture
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("gc.auto", "0"), ("maintenance.auto", "false")):
            f.git("config", key, value)
        f.git("add", "--", ".gitignore", "notes.md")
        f.git("commit", "-m", "initial local connection fixture")
        request = replace(f.request, context_entrypoints=("notes.md",),
                          include_history=True, history_ref="refs/heads/work")
        self.installed = f.api.apply_setup(f.api.preview_setup(request), approval=True)
        self.assertEqual(self.installed.status, "configured", self.installed.blockers)
        self.state = f.base / "host-state"
        self.state.mkdir(mode=0o700)
        self.snapshot = f.c.SnapshotRequest(f.settings, self.installed.receipt.storage,
                                           request.approved_sources, ("notes.md",), True, "refs/heads/work")
        self.hook = f.project / ".git/hooks/reference-transaction"
        self.before_git_config = (f.project / ".git/config").read_bytes()
        self.processes = []
        self.denied = []

        def local_process(argv, *args, **kwargs):
            if (args or kwargs.get("shell") or not isinstance(argv, (tuple, list))
                    or str(argv[0]) not in ("/usr/bin/git", "/bin/git", str(Path(sys.executable).resolve()))
                    or not Path(kwargs.get("cwd", f.base)).resolve().is_relative_to(f.base)):
                self.denied.append("process")
                raise AssertionError("Host fixture permits only explicit local Git/Python")
            process = POPEN(argv, **kwargs)
            self.processes.append(process)
            return process

        self.enterContext(patch.object(subprocess, "Popen", side_effect=local_process))
        self.enterContext(patch("socket.socket", side_effect=AssertionError("No network")))

    def clean_fixture(self) -> None:
        self.assertTrue(self.fixture.doCleanups())
        self.assertFalse(self.fixture.base.exists())
        for process in getattr(self, "processes", []):
            self.assertIsNotNone(process.returncode)
            self.assertFalse(Path(f"/proc/{process.pid}").exists())
        self.assertEqual(getattr(self, "denied", []), [])

    def host(self):
        self.assertTrue(MODULE.is_file(), "Missing opt-in checkpoint host connection")
        return importlib.import_module("checkpoint_host")

    def request(self, api):
        return api.HostRequest(self.snapshot, self.state, Path(sys.executable).resolve())

    def test_preview_and_unapproved_install_do_not_mutate(self) -> None:
        api = self.host()
        f = self.fixture
        before = {str(p): p.read_bytes() for p in f.base.rglob("*") if p.is_file()}
        preview = api.preview_connections(self.request(api))
        self.assertEqual(preview.status, "approval_required", preview.blockers)
        self.assertEqual(preview.hook_path, self.hook)
        self.assertTrue(preview.periodic_command)
        self.assertEqual(api.apply_connections(preview).status, "approval_required")
        self.assertEqual({str(p): p.read_bytes() for p in f.base.rglob("*") if p.is_file()}, before)
        self.assertFalse(self.hook.exists())
        self.assertEqual(list(self.state.iterdir()), [])

    def test_real_commit_captures_without_grant_and_owned_uninstall_preserves_snapshot(self) -> None:
        api = self.host()
        f = self.fixture
        installed = api.apply_connections(api.preview_connections(self.request(api)), approval=True)
        self.assertEqual(installed.status, "configured", installed.blockers)
        self.assertEqual(self.hook.stat().st_mode & 0o777, 0o700)
        content = b"# Updated context before an actual local commit\n"
        (f.project / "notes.md").write_bytes(content)
        f.git("add", "--", "notes.md")
        f.git("commit", "-m", "observed manual commit")
        result = f.c.read_json(self.state / "last-result.json")
        self.assertEqual(result["status"], "captured", result)
        self.assertEqual(result["push"]["status"], "denied")
        snapshot = Path(result["snapshot"]["snapshot_path"])
        self.assertEqual(f.s.read_payload(snapshot, "notes.md", f.identity, f.settings.limits), content)
        self.assertEqual(f.s.verify_snapshot(snapshot, f.identity, f.settings.limits).head,
                         f.git("rev-parse", "HEAD"))
        self.assertEqual((f.project / ".git/config").read_bytes(), self.before_git_config)
        self.assertEqual(api.remove_connections(installed.receipt).status, "approval_required")
        removed = api.remove_connections(installed.receipt, approval=True)
        self.assertEqual(removed.status, "removed", removed.blockers)
        self.assertFalse(self.hook.exists())
        self.assertEqual(f.s.read_payload(snapshot, "notes.md", f.identity, f.settings.limits), content)
        self.assertTrue((self.state / "state.json").is_file())
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_foreign_hook_blocks_install_without_replacement(self) -> None:
        api = self.host()
        self.hook.parent.mkdir(exist_ok=True)
        self.hook.write_text("#!/bin/sh\nexit 0\n")
        self.hook.chmod(0o700)
        before = self.hook.read_bytes()
        preview = api.preview_connections(self.request(api))
        self.assertEqual(preview.status, "blocked")
        self.assertIn("foreign_hooks", preview.blockers)
        self.assertEqual(api.apply_connections(preview, approval=True).status, "blocked")
        self.assertEqual(self.hook.read_bytes(), before)
        self.assertEqual(list(self.state.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
