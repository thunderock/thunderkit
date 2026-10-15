"""Resume interrupted setup through a fresh, model-free CLI invocation.

Protect fresh consent and exact local claim reuse, not serialization of authority.
A parser-only implementation or apply_setup retry could otherwise strand the
verified snapshot. Existing API retries do not prove the user-facing command.
The production seam is checkpoint_cli -> checkpoint_setup.resume_setup.
"""
from __future__ import annotations

from pathlib import Path
import unittest

import test_checkpoint_cli as fixtures


class CheckpointCLIResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = fixtures.CheckpointCLITests()
        self.addCleanup(self.clean)
        self.case.setUp()
        self.fixture = self.case.fixture

    def clean(self) -> None:
        self.assertTrue(self.case.doCleanups())
        self.assertFalse(self.case.fixture.base.exists())

    def test_resume_setup_reuses_partial_capture_with_fresh_approval(self) -> None:
        f, case = self.fixture, self.case
        # Synthetic token-shaped fixture text is never a real credential. Real
        # storage exclusion produces the interrupted state; no API is replaced.
        case.notes.write_bytes(b"API_TOKEN=" + b"ghp_" + b"Q" * 36 + b"\n")
        first = f.api.apply_setup(f.api.preview_setup(case.request), approval=True)
        self.assertEqual(first.status, "blocked", first)
        self.assertIn("first_snapshot_incomplete", first.blockers)
        self.assertFalse(f.private.exists())
        snapshots = f.backup / "projects" / f.identity.id / "snapshots"
        preserved = {path.relative_to(snapshots): path.read_bytes()
                     for path in snapshots.rglob("*") if path.is_file()}
        self.assertTrue(preserved, "Existing API must leave real recoverable progress")
        repaired = b"# Repaired saved context for an explicitly approved retry\n"
        case.notes.write_bytes(repaired)
        argv = case.argv("resume-setup")
        before = case.tree()
        denied = case.run_cli(*argv)
        self.assertEqual(case.tree(), before)
        self.assertEqual(denied.returncode, 2, denied.stdout)
        self.assertEqual(f.c.decode_json(denied.stderr)["error"]["code"], "approval_required")
        value = case.output(case.run_cli(*argv, "--approve"))
        self.assertEqual(value["status"], "configured", value)
        manifest = Path(value["receipt"]["manifest_path"])
        case.verify(manifest, repaired)
        for relative, data in preserved.items():
            self.assertEqual((snapshots / relative).read_bytes(), data)
        self.assertTrue(f.private.is_file())
        self.assertTrue((f.integration / "config.json").is_file())
        self.assertFalse((f.project / ".thunderkit-setup.lock").exists())
        self.assertFalse((f.project / ".thunderkit/config.json").exists())
        self.assertFalse((f.project / ".git/hooks/reference-transaction").exists())

    def test_resume_setup_without_owned_claim_is_read_only(self) -> None:
        f, case = self.fixture, self.case
        control = f.api.resume_setup(case.request, approval=True)
        self.assertEqual(control.status, "blocked")
        self.assertIn("resume_unavailable", control.blockers)
        before = case.tree()
        process = case.run_cli(*case.argv("resume-setup"), "--approve")
        self.assertEqual(case.tree(), before)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertTrue(process.stdout.strip(), "Expose the actual refused setup result")
        value = f.c.decode_json(process.stdout)
        self.assertEqual(value["status"], "blocked")
        self.assertIn("resume_unavailable", value["blockers"])


if __name__ == "__main__":
    unittest.main()
