"""Runtime Git capture/reconciliation uses separate, fresh caller authority."""
from __future__ import annotations

from dataclasses import replace
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_checkpoint_push as push_fixtures
import test_checkpoint_runtime as runtime_fixtures

REFERENCES = Path(__file__).resolve().parents[1] / "skills/references"


class GitCheckpointRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Composition reuses the real local-only transport firewall without
        # inheriting and duplicating its test suite in discovery.
        self.fixture = push_fixtures.CheckpointPushTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.enterContext(patch("subprocess.Popen", push_fixtures.POPEN))
        self.c = self.fixture.c
        # Bind these modules to this fixture's C module, not an older test import.
        for name in ("backup_store", "checkpoint_push", "checkpoint_runtime"):
            spec = importlib.util.spec_from_file_location(name, REFERENCES / (name + ".py"))
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        self.s = importlib.import_module("backup_store")
        self.api = importlib.import_module("checkpoint_runtime")
        self.backups = self.fixture.base / "approved-store"
        self.state = self.fixture.base / "runtime-state"
        self.backups.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.notes = self.fixture.repo / "notes.md"
        self.notes.write_text("# Saved uncommitted context\n")
        self.settings = self.c.ResolvedSettings(
            identity=self.fixture.identity, project_root=self.fixture.repo,
            backup_root=self.backups, context_path=None, context_source="unconfigured",
            backup=self.c.BackupPolicy(enabled=True), git=self.c.GitPolicy(True),
            approved_sources=(self.notes,),
        )
        storage = self.s.qualify_storage(self.s.inspect_storage(self.backups), approval=True).identity
        self.snapshot_request = self.c.SnapshotRequest(
            self.settings, storage, (self.notes,), context_entrypoints=("notes.md",),
            include_history=True, history_ref=self.fixture.ref,
        )
        self.clock = runtime_fixtures.FakeClock()

    def request(self):
        return self.api.CheckpointRequest(self.snapshot_request, self.state, trigger="event")

    def with_authority(self, authority):
        try:
            return self.api.checkpoint(self.request(), clock=self.clock,
                                       authority=authority, driver=self.fixture.driver)
        except TypeError:
            self.fail("Runtime lacks separate fresh authority and Git driver inputs")

    def assert_history_saved(self, result) -> None:
        self.assertEqual(result.status, "captured", result.warnings)
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.history_status, "bundled")
        manifest = self.s.verify_snapshot(result.snapshot.snapshot_path, self.fixture.identity, self.settings.limits)
        self.assertEqual((manifest.head, manifest.ref), (self.fixture.oid, self.fixture.ref))
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(self.s.read_payload(result.snapshot.snapshot_path, "notes.md",
                                             self.fixture.identity, self.settings.limits), self.notes.read_bytes())

    def test_git_event_bundles_history_without_saved_or_implicit_push_authority(self) -> None:
        before = self.fixture.remote_refs()
        result = self.api.checkpoint(self.request(), clock=self.clock)
        self.assert_history_saved(result)
        self.assertEqual(set(result.observed_oids), {self.fixture.old, self.fixture.oid})
        self.assertIsNotNone(result.push)
        self.assertEqual(result.push.status, "denied")
        self.assertIn("fresh_authority_required", result.coverage_gaps)
        self.assertIn("reachable_history_only", result.coverage_gaps)
        self.assertEqual(self.fixture.remote_refs(), before)
        self.assertEqual(self.fixture.driver.calls, [])
        state = json.loads((self.state / "state.json").read_text())
        self.assertFalse({"grant", "authority", "environment", "commands"} & state.keys())

    def test_current_tip_push_requires_callback_and_reconciles_bypassed_events(self) -> None:
        issued = []

        def authority(request):
            self.assertEqual((request.identity, request.repository, request.oid, request.destination_ref),
                             (self.fixture.identity, self.fixture.repo, self.fixture.oid, self.fixture.ref))
            issued.append(request.oid)
            return self.fixture.grant

        first = self.with_authority(authority)
        self.assert_history_saved(first)
        self.assertEqual(first.push.status, "pushed")
        self.assertEqual(self.fixture.remote_oid(), self.fixture.oid)
        self.assertEqual(self.fixture.driver.writes, 1)
        old = self.fixture.oid
        # No hook is installed: reconciliation must discover this manual commit.
        self.fixture.raw("commit", "--allow-empty", "-m", "manual bypassed event")
        self.fixture.oid = self.fixture.head()
        self.fixture.known_oids.add(self.fixture.oid)
        self.fixture.fresh_driver()
        self.clock.mono += 10
        self.notes.write_text("# New saved context after manual commit\n")
        second = self.with_authority(authority)
        self.assert_history_saved(second)
        self.assertIn(self.fixture.oid, second.observed_oids)
        self.assertNotIn(old, second.observed_oids, "Previously observed OIDs must not be reissued")
        self.assertEqual(second.push.status, "pushed")
        self.assertEqual(self.fixture.remote_oid(), self.fixture.oid)
        self.assertEqual(issued, [old, self.fixture.oid])
        self.assertEqual(self.fixture.driver.writes, 1)
        self.assertEqual(self.fixture.remote_oid("refs/heads/main"), self.fixture.old)
        self.assertIn("reachable_history_only", second.coverage_gaps)

    def test_partial_snapshot_does_not_call_publication_authority(self) -> None:
        self.notes.write_bytes(b"API_TOKEN=" + b"ghp_" + b"Z" * 36 + b"\n")
        calls = []

        def authority(request):
            calls.append(request)
            return self.fixture.grant

        result = self.with_authority(authority)
        self.assertEqual(result.status, "partial", result.warnings)
        self.assertEqual(result.snapshot.status, "partial")
        self.assertIsNone(result.push)
        self.assertEqual(calls, [])
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertEqual(self.fixture.remote_oid(), self.fixture.old)
        self.assertFalse((self.state / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
