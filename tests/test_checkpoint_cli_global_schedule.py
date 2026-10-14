"""Global schedule CLI: qualified setup evidence, consent and fresh cleanup.

Public CLI paths must reach the actual global installer without synthesizing
storage qualification from configuration or dropping configured allowlists.
The existing global storage control proves valid inputs before parser RED.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import unittest

import test_checkpoint_global_schedule as fixture

CLI = fixture.REFERENCES / "checkpoint_cli.py"


class GlobalScheduleCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = fixture.CheckpointGlobalScheduleTests()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.c = self.case
        self.setup_result = self.c.base / "global-setup-result.json"

    def clean(self) -> None:
        self.assertTrue(self.case.doCleanups())
        self.assertFalse(self.case.base.exists())

    def cli(self, *args: str, transport: bool = False) -> tuple[int, str, str]:
        body = ("\nimport runpy\nsys.argv=sys.argv[2:]\n"
                f"sys.path.insert(0,{str(CLI.parent)!r})\n"
                "runpy.run_path(sys.argv[0],run_name='__main__')\n")
        return self.c.child(body, CLI, *args, allow_transport=transport)

    def capture_args(self, command: str) -> list[str]:
        return [command, "--scope", "global", "--identity", self.c.identity.id,
                "--global-config", str(self.c.config), "--backup-root", str(self.c.backup),
                "--source", str(self.c.notes), "--context-entrypoint", fixture.ENTRY,
                "--state-dir", str(self.c.state)]

    def schedule_args(self, command: str) -> list[str]:
        return [*self.capture_args(command), "--setup-result", str(self.setup_result),
                "--python", str(self.c.python), "--crontab", str(self.c.executable),
                "--service-qualified", "--exclusive-writer"]

    def setup(self) -> dict:
        code, out, err = self.cli(*self.capture_args("global-setup"), "--approve")
        self.assertEqual((code, err), (0, ""))
        value = json.loads(out)
        self.assertEqual(value["status"], "configured", value)
        self.assertEqual(value["storage"]["root"], str(self.c.backup))
        self.assertEqual(value["storage"]["inode"], self.c.backup.stat().st_ino)
        self.assertEqual(value["storage"]["durability"], "software-readback")
        self.c.verify_snapshot(value["snapshot"]["snapshot_path"], fixture.NOTE)
        self.setup_result.write_text(out)
        self.setup_result.chmod(0o600)
        return value

    def test_setup_preview_install_tick_and_remove_use_real_global_scope(self) -> None:
        # Real no-model/no-Git storage control, independent of the new CLI path.
        snapshot = self.c.snapshot_request()
        control = self.c.s.create_snapshot(snapshot)
        self.assertEqual(control.status, "complete")
        self.c.verify_snapshot(control.snapshot_path, fixture.NOTE)
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*self.capture_args("global-setup"))
        self.assertEqual(fixture.tree(self.c.base), before)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.setup()
        before = fixture.tree(self.c.state)
        code, out, err = self.cli(*self.schedule_args("global-preview"), transport=True)
        self.assertEqual((code, err), (0, ""))
        preview = json.loads(out)
        self.assertEqual(preview["status"], "approval_required", preview)
        self.assertNotIn("previous", preview)
        self.assertEqual(fixture.tree(self.c.state), before)
        self.assertEqual(self.c.table.read_bytes(), fixture.UNRELATED)
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*self.schedule_args("global-install"), transport=True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.assertEqual(fixture.tree(self.c.base), before)
        code, out, err = self.cli(*self.schedule_args("global-install"), "--approve", transport=True)
        self.assertEqual((code, err), (0, ""))
        installed = json.loads(out)
        self.assertEqual(installed["status"], "configured", installed)
        command = installed["installation"]["periodic_command"]
        self.assertIn(command.encode(), self.c.table.read_bytes())
        code, out, err = self.c.child(
            "\nimport runpy\nsys.argv=sys.argv[2:]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n",
            *shlex.split(command))
        self.assertEqual((code, err), (0, ""))
        tick = json.loads(out)
        self.assertIn(tick["status"], ("captured", "not-due"), tick)
        self.assertIsNone(tick["push"])
        self.c.table.write_bytes(self.c.table.read_bytes() + fixture.LATER)
        owned = [Path(row["path"]) for row in installed["installation"]["files"]]
        removal = ["global-remove", "--scope", "global", "--identity", self.c.identity.id,
                   "--state-dir", str(self.c.state), "--crontab", str(self.c.executable),
                   "--service-qualified", "--exclusive-writer"]
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*removal, transport=True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.assertEqual(fixture.tree(self.c.base), before)
        code, out, err = self.cli(*removal, "--approve", transport=True)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "removed")
        self.assertEqual(self.c.table.read_bytes(), fixture.UNRELATED + fixture.LATER)
        self.assertTrue(all(not p.exists() for p in owned))
        self.c.verify_snapshot(control.snapshot_path, fixture.NOTE)

    def test_replaced_store_is_not_requalified_by_global_install(self) -> None:
        self.setup()
        self.c.backup.rename(self.c.base / "original-store")
        self.c.backup.mkdir()
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*self.schedule_args("global-install"), "--approve", transport=True)
        self.assertEqual(code, 2)
        self.assertEqual(fixture.tree(self.c.base), before)
        self.assertEqual(json.loads(err)["error"]["code"], "storage_changed")
        self.assertEqual(self.c.calls(), [])

    def test_unqualified_and_foreign_source_global_install_make_zero_writes(self) -> None:
        self.setup()
        args = self.schedule_args("global-install")
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*[a for a in args if a != "--service-qualified"], "--approve", transport=True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "scheduler_unqualified")
        self.assertEqual(fixture.tree(self.c.base), before)
        unapproved = self.c.base / "outside.md"
        unapproved.write_text("Not in the configured allowlist\n")
        args[args.index("--source") + 1] = str(unapproved)
        before = fixture.tree(self.c.base)
        code, out, err = self.cli(*args, "--approve", transport=True)
        self.assertEqual(code, 2)
        self.assertIn(json.loads(err)["error"]["code"], ("identity_mismatch", "setup_scope_changed"))
        self.assertEqual(fixture.tree(self.c.base), before)
        self.assertEqual(self.c.calls(), [])


if __name__ == "__main__":
    unittest.main()
