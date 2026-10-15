"""Real host CLI install/tick/remove; table transport is fixture-only.

Protect consent, saved setup identity and exact restartable ownership. Existing
API tests cannot detect a CLI that invents storage qualification, ignores scope,
or cannot remove a job after installer exit. These tests execute real helpers;
only crontab's storage/service is represented by an explicit local executable.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_checkpoint_cli as cli_fixture
import test_checkpoint_crontab as crontab_fixture
import test_checkpoint_host_lifecycle as lifecycle

CLI = lifecycle.REFERENCES / "checkpoint_cli.py"
_OFFLINE = """
import atexit,os,runpy,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from test_checkpoint_cli import local_git
base=Path(sys.argv[2]); cron=str(base/'fixture-crontab'); denied=[]
def guard(event,args):
 bad=event.startswith(('socket.','os.exec','os.spawn')) or event in ('os.system','os.posix_spawn','os.fork','os.forkpty')
 if event=='subprocess.Popen':
  executable,argv,cwd,env=args
  host_args={('config','--null','--show-origin','--no-includes','--list'),('rev-parse','--show-toplevel'),('rev-parse','--absolute-git-dir'),('rev-parse','--git-path','hooks')}
  host=(str(executable)=='/usr/bin/git' and tuple(argv[1:]) in host_args and Path(cwd)==base/'project' and env.get('GIT_ALLOW_PROTOCOL')=='')
  scheduler=(str(executable)==cron and list(argv) in ([cron,'-l'],[cron,'-']) and Path(cwd)==base)
  bad=not(host or scheduler or local_git(executable,argv,cwd,env,base))
 if bad:
  denied.append(event);raise RuntimeError('Only exact offline host transports permitted')
def finish():
 if denied:os._exit(97)
atexit.register(finish);sys.addaudithook(guard)
sys.argv=sys.argv[3:];sys.path.insert(0,str(Path(sys.argv[0]).parent));runpy.run_path(sys.argv[0],run_name='__main__')
"""


class CheckpointCLIHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = crontab_fixture.CheckpointCrontabTests()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.f = self.case.f
        self.state = self.case.state
        self.hook = self.case.hook
        self.setup_result = self.f.base / "setup-result.json"
        self.setup_result.write_text(json.dumps(asdict(self.case.case.installed), default=str))
        self.setup_result.chmod(0o600)

    def clean(self) -> None:
        self.assertTrue(self.case.doCleanups())
        self.assertFalse(self.case.f.base.exists())

    def run_cli(self, *argv: str) -> tuple[int, bytes, bytes]:
        code, out, err, _ = self.case.child(
            [str(Path(sys.executable).resolve()), "-I", "-B", "-c", _OFFLINE,
             str(Path(__file__).parent), str(self.f.base), str(CLI), *argv], timeout=75)
        self.assertNotEqual(code, 97, "CLI escaped its offline transport firewall")
        return code, out, err

    def success(self, argv: list[str]) -> dict:
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        value = json.loads(out)
        self.assertIsInstance(value, dict)
        return value

    def install_args(self, command: str) -> list[str]:
        return [command, "--project-root", str(self.f.project), "--scope", "project",
                "--identity", self.f.identity.id, "--backup-root", str(self.f.backup),
                "--source", str(self.f.project / "notes.md"), "--context-entrypoint", "notes.md",
                "--include-history", "--history-ref", "refs/heads/work",
                "--state-dir", str(self.state), "--python", str(Path(sys.executable).resolve()),
                "--setup-result", str(self.setup_result)]

    def removal_args(self, command: str) -> list[str]:
        return [command, "--project-root", str(self.f.project), "--state-dir", str(self.state)]

    def schedule_args(self, command: str) -> list[str]:
        return [*self.removal_args(command), "--crontab", str(self.case.executable),
                "--service-qualified", "--exclusive-writer"]

    def test_host_preview_install_tick_and_fresh_remove(self) -> None:
        before = lifecycle.tree(self.f.base)
        preview = self.success(self.install_args("host-preview"))
        self.assertEqual(preview["status"], "approval_required")
        self.assertEqual(preview["hook_path"], str(self.hook))
        self.assertEqual(lifecycle.tree(self.f.base), before)
        code, out, err = self.run_cli(*self.install_args("host-install"))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.assertEqual(lifecycle.tree(self.f.base), before)
        installed = self.success([*self.install_args("host-install"), "--approve"])
        self.assertEqual(installed["status"], "configured", installed)
        self.assertTrue((self.state / "connection-receipt.json").is_file())
        content = b"# Latest saved context while the agent is idle\n"
        (self.f.project / "notes.md").write_bytes(content)
        code, out, err, _ = self.case.child([str(self.hook), "--tick"], timeout=65)
        self.assertEqual(code, 0, err)
        value = json.loads(out)
        self.assertEqual(value["status"], "captured", value)
        self.assertEqual(value["push"]["status"], "denied")
        saved = Path(value["snapshot"]["snapshot_path"])
        self.assertEqual(self.f.s.read_payload(saved, "notes.md", self.f.identity, self.f.settings.limits), content)
        before = lifecycle.tree(self.f.base)
        code, out, err = self.run_cli(*self.removal_args("host-remove"))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.assertEqual(lifecycle.tree(self.f.base), before)
        removed = self.success([*self.removal_args("host-remove"), "--approve"])
        self.assertEqual(removed["status"], "removed", removed)
        self.assertFalse(self.hook.exists())
        self.assertFalse((self.state / "connection.json").exists())
        self.assertFalse((self.state / "connection-receipt.json").exists())
        self.assertEqual(self.f.s.read_payload(saved, "notes.md", self.f.identity, self.f.settings.limits), content)

    def test_replaced_setup_store_refuses_without_qualification_writes(self) -> None:
        self.f.backup.rename(self.f.base / "original-store")
        self.f.backup.mkdir()
        before = lifecycle.tree(self.f.base)
        code, out, err = self.run_cli(*self.install_args("host-install"), "--approve")
        self.assertEqual(code, 2)
        self.assertEqual(lifecycle.tree(self.f.base), before)
        self.assertEqual(json.loads(err)["error"]["code"], "storage_changed")
        self.assertEqual(list(self.f.backup.iterdir()), [])
        self.assertFalse(self.hook.exists())

    def test_setup_result_unknown_keys_or_boolean_identity_never_adopt_paths(self) -> None:
        original = json.loads(self.setup_result.read_text())
        cases = [dict(original, unexpected=True)]
        invalid = json.loads(self.setup_result.read_text())
        invalid["receipt"]["files"][0]["device"] = True
        cases.append(invalid)
        for value in cases:
            with self.subTest(value=value is cases[0]):
                self.setup_result.write_text(json.dumps(value))
                before = lifecycle.tree(self.f.base)
                code, out, err = self.run_cli(*self.install_args("host-install"), "--approve")
                self.assertEqual(code, 2)
                self.assertEqual(lifecycle.tree(self.f.base), before)
                self.assertEqual(json.loads(err)["error"]["code"], "invalid_setup_receipt")
        self.assertFalse(self.hook.exists())

    def test_schedule_cli_roundtrip_requires_qualification_and_removal_order(self) -> None:
        self.case.install()
        args = self.schedule_args("schedule-preview")
        before = lifecycle.tree(self.f.base)
        code, out, err = self.run_cli(*[arg for arg in args if arg != "--service-qualified"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "scheduler_unqualified")
        self.assertEqual(lifecycle.tree(self.f.base), before)
        preview = self.success(args)
        self.assertEqual(preview["status"], "approval_required", preview)
        self.assertNotIn("previous", preview, "Do not print unrelated private crontab contents")
        before = lifecycle.tree(self.f.base)
        code, out, err = self.run_cli(*self.schedule_args("schedule-install"))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "approval_required")
        self.assertEqual(lifecycle.tree(self.f.base), before)
        installed = self.success([*self.schedule_args("schedule-install"), "--approve"])
        self.assertEqual(installed["status"], "configured", installed)
        table = self.case.table.read_bytes()
        code, out, err = self.run_cli(*self.removal_args("host-remove"), "--approve")
        self.assertEqual(code, 2)
        self.assertIn("owned_schedule_present", json.loads(out)["blockers"])
        self.assertEqual(self.case.table.read_bytes(), table)
        self.assertTrue(self.hook.is_file())
        extra = b"# later unrelated task\n15 2 * * * /usr/bin/true\n"
        self.case.table.write_bytes(table + extra)
        removed = self.success([*self.schedule_args("schedule-remove"), "--approve"])
        self.assertEqual(removed["status"], "removed", removed)
        self.assertEqual(self.case.table.read_bytes(), self.case.original + extra)
        removed = self.success([*self.removal_args("host-remove"), "--approve"])
        self.assertEqual(removed["status"], "removed")
        self.assertFalse(self.hook.exists())


if __name__ == "__main__":
    unittest.main()
