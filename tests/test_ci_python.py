"""CI uses a private Python copy, not a writable shared-cache executable."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
STEP = "Prepare private Python runtime"


def private_python_step(workflow: Path) -> str:
    lines = workflow.read_text().splitlines()
    marker = next((i for i, line in enumerate(lines) if line.strip().removeprefix("- ") == f"name: {STEP}"), None)
    if marker is None:
        raise AssertionError(f"{workflow.name} must prepare a private checkpoint interpreter")
    run = marker + 1
    if lines[run].strip() != "run: |":
        raise AssertionError("Private interpreter setup must be one literal script")
    indentation = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indentation:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)).strip()


class CIPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ci-python-", dir=os.environ["TMPDIR"])
        self.root = Path(self.temporary.name)
        self.addCleanup(self.cleanup)
        self.environment = {
            "PATH": os.environ["PATH"], "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root),
            "TMPDIR": str(self.root), "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8",
            "RUNNER_TEMP": str(self.root), "GITHUB_PATH": str(self.root / "github-path"),
        }
        self.source = self.root / "shared-cache"
        # Resolve the launcher first: a PATH shim need not live beside its stdlib.
        self.run_process([str(Path(sys.executable).resolve()), "-I", "-m", "venv", "--copies",
                          "--without-pip", str(self.source)])
        self.shared_python = self.source / "bin/python3"
        self.shared_python.chmod(0o777)
        self.environment["PATH"] = str(self.source / "bin") + os.pathsep + os.environ["PATH"]

    def cleanup(self) -> None:
        self.temporary.cleanup()
        self.assertFalse(self.root.exists())

    def run_process(self, command: list[str], *, expected: int = 0) -> str:
        process = subprocess.Popen(command, cwd=ROOT, env=self.environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            self.fail("Owned CI interpreter fixture exceeded its deadline")
        self.assertEqual(process.returncode, expected, stderr)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)
        return stdout

    def test_workflows_use_private_copy_for_real_project_and_global_connections(self) -> None:
        scripts = [private_python_step(ROOT / ".github/workflows" / name)
                   for name in ("ci.yml", "release-please.yml")]
        self.assertEqual(scripts[0], scripts[1], "Both test gates need the same interpreter isolation")
        original = self.shared_python.stat()
        digest = hashlib.sha256(self.shared_python.read_bytes()).hexdigest()
        self.run_process(["/bin/bash", "--noprofile", "--norc", "-eu", "-c", scripts[0]])
        paths = (self.root / "github-path").read_text().splitlines()
        self.assertEqual(len(paths), 1)
        binary_dir = Path(paths[0])
        self.assertTrue(binary_dir.is_relative_to(self.root))
        self.assertFalse(binary_dir.is_relative_to(self.source))
        private = binary_dir / "python3"
        self.assertFalse(private.is_symlink())
        self.assertNotEqual(private.stat().st_ino, original.st_ino)
        self.assertEqual(private.stat().st_uid, os.geteuid())
        self.assertEqual(private.stat().st_mode & 0o022, 0)
        self.assertTrue(private.stat().st_mode & 0o111)
        self.assertEqual(hashlib.sha256(self.shared_python.read_bytes()).hexdigest(), digest)
        current = self.shared_python.stat()
        self.assertEqual((current.st_dev, current.st_ino, current.st_mode, current.st_uid, current.st_mtime_ns),
                         (original.st_dev, original.st_ino, original.st_mode, original.st_uid, original.st_mtime_ns))
        self.environment["PATH"] = str(binary_dir) + os.pathsep + self.environment["PATH"]
        body = """
import sys, unittest
sys.path.insert(0, sys.argv[1])
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames([
    'test_checkpoint_host.CheckpointHostTests.test_real_commit_captures_without_grant_and_owned_uninstall_preserves_snapshot',
    'test_checkpoint_global_schedule.CheckpointGlobalScheduleTests.test_00_real_global_control_then_installed_tick_revocation_and_fresh_removal',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.testsRun == 2 and result.wasSuccessful() else 1)
"""
        self.run_process([str(private), "-I", "-B", "-c", body, str(ROOT / "tests")])

    def test_writable_shared_interpreter_is_still_refused(self) -> None:
        body = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import checkpoint_host as H, checkpoint_global as G, continuity_config as C
path = Path(sys.argv[2]); result = {}
for name, operation in [('host', lambda: H._runtime_files(path)),
                        ('global', lambda: G._pin(path, G._PYTHON_MAX))]:
    try:
        operation()
    except C.ContinuityError as error:
        result[name] = error.code
    else:
        result[name] = 'accepted'
print(json.dumps(result))
"""
        value = json.loads(self.run_process([str(Path(sys.executable).resolve()), "-I", "-B", "-c", body,
                                            str(ROOT / "skills/references"), str(self.shared_python)]))
        self.assertEqual(value, {"host": "unsafe_interpreter", "global": "unsafe_connection_file"})
        self.assertEqual(self.shared_python.stat().st_mode & 0o777, 0o777)


if __name__ == "__main__":
    unittest.main()
