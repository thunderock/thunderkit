"""H's first setup slice; no production code or host activation.

The passing C/S control validates untouched disposable inputs independently.
Initial RED witnesses missing checkpoint_setup, not the behavioral tails of
preview/refusal tests. Parent checkpoints this file before minimal GREEN.
API proposal and later adapter/authority decisions: continuity/H-design.md.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import importlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
MODULE = REFERENCES / "checkpoint_setup.py"
MISSING = "Missing safe setup-preview entrypoint: skills/references/checkpoint_setup.py"
POPEN = subprocess.Popen


class CheckpointSetupTests(unittest.TestCase):
    def setUp(self):
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        self.temp = tempfile.TemporaryDirectory(prefix="thunderkit-H-red-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.backups = self.base / "approved-store"
        self.home = self.base / "empty-home"
        self.template = self.base / "empty-template"
        for directory in (self.project, self.backups, self.home, self.template):
            directory.mkdir()
        git = shutil.which("git", path="/usr/bin:/bin")
        self.assertIsNotNone(git, "Offline fixture Git is required, not skipped")
        assert git is not None
        self.git = git
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home), "TMPDIR": str(self.base),
            "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_COUNT": "0",
            "GIT_ALLOW_PROTOCOL": "file", "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ATTR_NOSYSTEM": "1",
        }
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, self.env, clear=True))
        previous = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)
        stack.enter_context(patch.object(sys, "path", [str(REFERENCES), *sys.path]))
        stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        stack.enter_context(patch.dict(sys.modules))
        self.c = importlib.import_module("continuity_config")
        self.s = importlib.import_module("backup_store")
        self.raw("init", "--initial-branch=work", f"--template={self.template}", str(self.project))
        self.hooks = self.project / ".git/hooks"
        self.hooks.mkdir(exist_ok=True)
        self.raw("config", "core.hooksPath", str(self.hooks))
        self.raw("config", "credential.helper", "")
        self.private = self.project / ".thunderkit/continuity.local.json"
        self.integration = self.project / ".thunderkit/checkpoint"
        self.private.parent.mkdir()
        (self.project / ".gitignore").write_text(".thunderkit/*\n", encoding="utf-8")
        self.notes = self.project / "notes.md"
        self.notes.write_text("# Saved fixture context\nNo execution instructions.\n", encoding="utf-8")
        self.identity = self.c.ScopeIdentity("project", "h-fixture")
        # No private settings or model configuration exists at first use.
        unresolved = self.c.resolve_continuity(
            self.project, expected_identity=self.identity, environment=self.env,
        )
        self.settings = replace(unresolved, backup_root=self.backups,
                                backup=self.c.BackupPolicy(enabled=True))
        self.storage = self.s.inspect_storage(self.backups)
        self.baseline = self.tree()
        self.addCleanup(self.assert_untouched)
        # Future preview implementations may make only these real inert Git reads.
        # No child process may execute a scheduler, model client, hook or transport.
        stack.enter_context(patch("subprocess.Popen", side_effect=self.read_process))
        stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("No setup network")))
        stack.enter_context(patch("socket.socket", side_effect=AssertionError("No setup network")))

    def raw(self, *args):
        process = POPEN([self.git, *args], cwd=self.project, env=self.env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("Offline fixture Git timed out")
        self.assertEqual(process.returncode, 0, "Offline fixture Git failed")
        return output.strip()

    def read_process(self, argv, *args, **kwargs):
        self.assertFalse(args, "Use explicit bounded Git subprocess options")
        self.assertIsInstance(argv, (tuple, list), "No shell commands in preview")
        self.assertIn(str(argv[0]), (self.git, "git"))
        self.assertEqual(Path(kwargs.get("cwd", Path.cwd())), self.project)
        self.assertFalse(kwargs.get("shell", False))
        allowed = {
            ("rev-parse", "--show-toplevel"),
            ("rev-parse", "--absolute-git-dir"),
            ("rev-parse", "--git-path", "hooks"),
            ("symbolic-ref", "--quiet", "HEAD"),
            ("config", "--null", "--show-origin", "--no-includes", "--list"),
            ("check-ignore", "--quiet", "--", ".thunderkit/continuity.local.json"),
            ("check-ignore", "--quiet", "--", ".thunderkit/checkpoint/config.json"),
            ("ls-files", "-z", "--", ".thunderkit/continuity.local.json", ".thunderkit/checkpoint"),
        }
        self.assertIn(tuple(argv[1:]), allowed, "Preview attempted a non-read Git command")
        self.assertEqual(dict(kwargs.get("env", os.environ)), self.env)
        return POPEN([self.git, *argv[1:]], **kwargs)

    def tree(self):
        result = {}
        for path in (self.base, *sorted(self.base.rglob("*"))):
            info = path.lstat()
            self.assertFalse(stat.S_ISLNK(info.st_mode), "Unexpected fixture symlink")
            data = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
            result[str(path.relative_to(self.base))] = (
                info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_mtime_ns, info.st_ctime_ns, data,
            )
        return result

    def assert_untouched(self):
        # Includes missing-file membership, bytes, mode, inode and timestamps;
        # reads may alter atime, which deliberately is not compared.
        self.assertEqual(self.tree(), self.baseline, "Preview/refusal mutated the fixture")

    def setup_api(self):
        self.assertTrue(MODULE.is_file(), MISSING)
        api = importlib.import_module("checkpoint_setup")
        self.assertTrue(callable(getattr(api, "preview_setup", None)), MISSING)
        return api

    def request(self, api, *, scheduler="none"):
        return api.SetupRequest(
            settings=self.settings, approved_sources=(self.notes,),
            integration_root=self.integration, scheduler=scheduler, per_commit=False,
        )

    def preview(self, api, request):
        with patch.object(self.s, "qualify_storage", side_effect=AssertionError("Preview must not probe storage")), \
                patch.object(self.s, "create_snapshot", side_effect=AssertionError("Preview must not capture")):
            return api.preview_setup(request)

    def test_00_C_S_inputs_are_valid_and_no_approval_probe_is_read_only(self):
        self.assertEqual(self.raw("symbolic-ref", "HEAD"), "refs/heads/work")
        self.assertEqual(self.raw("config", "--get", "core.hooksPath"), str(self.hooks))
        self.assertEqual(self.raw("check-ignore", "--", ".thunderkit/continuity.local.json"),
                         ".thunderkit/continuity.local.json")
        self.assertEqual(self.raw("check-ignore", "--", ".thunderkit/checkpoint/config.json"),
                         ".thunderkit/checkpoint/config.json")
        self.assertEqual(self.raw("ls-files"), "")
        snapshot = self.c.SnapshotRequest(self.settings, self.storage, (self.notes,))
        self.assertEqual(snapshot.storage.root, self.backups)
        self.assertEqual(snapshot.settings.identity, self.identity)
        self.assertEqual(self.storage.durability, "unqualified")
        self.assertEqual(self.storage.inode, self.backups.stat().st_ino)
        with self.assertRaises(self.c.ContinuityError) as caught:
            self.s.qualify_storage(self.storage, approval=False)
        self.assertEqual(caught.exception.code, "approval_required")
        self.assertFalse(self.private.exists())
        self.assertFalse((self.project / ".thunderkit/config.json").exists())
        self.assertEqual(tuple(self.home.iterdir()), ())
        self.assertEqual(tuple(self.hooks.iterdir()), ())
        self.assertEqual(dict(os.environ), self.env)
        self.assert_untouched()

    def test_preview_reports_unconnected_scope_without_writes(self):
        api = self.setup_api()
        preview = self.preview(api, self.request(api))
        self.assertEqual(preview.status, "approval_required")
        self.assertEqual(preview.storage, self.storage)
        self.assertIn("setup_write", preview.missing_approvals)
        self.assertNotEqual(preview.mutations, ())
        allowed = {self.private, self.integration / "config.json"}
        self.assertTrue(all(m.path in allowed and m.operation == "create"
                            for m in preview.mutations))
        capabilities = {cap.name: cap.status for cap in preview.capabilities}
        self.assertEqual(capabilities["periodic"], "disabled")
        self.assertEqual(capabilities["ref_events"], "disabled")
        self.assertEqual(capabilities["auto_push"], "blocked")
        self.assertEqual(capabilities["full_target"], "blocked")
        self.assert_untouched()

    def test_apply_without_approval_returns_refusal_without_writes(self):
        api = self.setup_api()
        preview = self.preview(api, self.request(api))
        with patch.object(self.s, "qualify_storage", side_effect=AssertionError("No approved probe")), \
                patch.object(self.s, "create_snapshot", side_effect=AssertionError("No approved capture")):
            result = api.apply_setup(preview)  # The default must refuse; no consent supplied.
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(result.changed_paths, ())
        self.assertIsNone(result.receipt)
        self.assert_untouched()

    def test_unknown_scheduler_blocks_full_target_without_writes(self):
        api = self.setup_api()
        preview = self.preview(api, self.request(api, scheduler="unknown-fixture-adapter"))
        self.assertEqual(preview.status, "blocked")
        self.assertIn("unsupported_scheduler", preview.blockers)
        capabilities = {cap.name: cap.status for cap in preview.capabilities}
        self.assertEqual(capabilities["periodic"], "blocked")
        self.assertEqual(capabilities["full_target"], "blocked")
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
