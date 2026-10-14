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
        self.addCleanup(self.cleanup_fixture)
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

    def cleanup_fixture(self) -> None:
        self.assertTrue(self.fixture.doCleanups(), "Composed fixture cleanup must not hide an error")
        self.assertFalse(self.fixture.base.exists(), "Every owned runtime fixture must be removed")

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

    def test_large_object_listing_does_not_prevent_file_only_capture(self) -> None:
        # Prevent fixture commits from launching background repository maintenance.
        self.fixture.config("gc.auto", "0")
        self.fixture.config("maintenance.auto", "false")
        documents = self.fixture.repo / "tracked-documents"
        documents.mkdir()
        for number in range(1600):
            (documents / f"entry-{number:04d}.txt").write_text(f"Safe record {number}\n")
        self.fixture.raw("add", "--all")
        self.fixture.raw("commit", "-m", "large ordinary object inventory")
        self.fixture.oid = self.fixture.head()
        objects = self.fixture.raw("rev-list", "--objects", "--no-object-names", "HEAD", "--").stdout
        self.assertGreater(len(objects.encode("ascii")), self.api.STATE_MAX_BYTES)
        self.assertLess(len(objects.splitlines()), self.settings.limits.max_files)
        self.snapshot_request = replace(self.snapshot_request, include_history=False, history_ref=None)
        control = self.s.create_snapshot(self.snapshot_request)
        self.assertEqual((control.status, control.history_status), ("partial", "unprotected"))
        self.assertEqual(self.s.read_payload(control.snapshot_path, "notes.md", self.fixture.identity,
                                            self.settings.limits), self.notes.read_bytes())
        calls = []

        def authority(request):
            calls.append(request)
            return self.fixture.grant

        result = self.with_authority(authority)
        self.assertIsNotNone(result.snapshot,
                             "H8-F1: graph listing limits must not discard a valid file-only snapshot")
        self.assertEqual((result.status, result.snapshot.history_status), ("partial", "unprotected"))
        self.assertEqual(self.s.read_payload(result.snapshot.snapshot_path, "notes.md", self.fixture.identity,
                                            self.settings.limits), self.notes.read_bytes())
        self.assertEqual(calls, [])
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertIsNone(result.push)
        self.assertFalse((self.state / ".checkpoint.lock").exists())

    def test_history_count_limit_preserves_files_without_publication(self) -> None:
        self.settings = replace(self.settings, limits=self.c.Limits(max_files=2))
        self.snapshot_request = replace(self.snapshot_request, settings=self.settings)
        control = self.s.create_snapshot(self.snapshot_request)
        self.assertEqual((control.status, control.history_status), ("partial", "unprotected"))
        self.assertEqual(self.s.read_payload(control.snapshot_path, "notes.md", self.fixture.identity,
                                            self.settings.limits), self.notes.read_bytes())
        calls = []

        def authority(request):
            calls.append(request)
            return self.fixture.grant

        result = self.with_authority(authority)
        self.assertIsNotNone(result.snapshot,
                             "H8-F1: history capacity failure must retain independently valid current files")
        self.assertEqual((result.status, result.snapshot.history_status), ("partial", "unprotected"))
        self.assertEqual(self.s.read_payload(result.snapshot.snapshot_path, "notes.md", self.fixture.identity,
                                            self.settings.limits), self.notes.read_bytes())
        self.assertTrue(result.snapshot.omissions)
        self.assertEqual(calls, [])
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertIsNone(result.push)
        self.assertFalse((self.state / "state.json").exists())
        self.assertFalse((self.state / ".checkpoint.lock").exists())

    def test_unborn_attached_repository_captures_context_without_publication(self) -> None:
        project = self.fixture.base / "unborn-project"
        self.fixture.raw("init", "--initial-branch=work", f"--template={self.fixture.template}",
                         str(project), cwd=self.fixture.base)
        notes = project / "notes.md"
        notes.write_text("# Context before the first commit\n")
        self.settings = replace(self.settings, project_root=project, approved_sources=(notes,))
        self.snapshot_request = replace(self.snapshot_request, settings=self.settings,
                                        approved_sources=(notes,), include_history=False, history_ref=None)
        control = self.s.create_snapshot(self.snapshot_request)
        self.assertEqual((control.status, control.history_status), ("complete", "not-applicable"))
        calls = []

        def authority(request):
            calls.append(request)
            return self.fixture.grant

        result = self.with_authority(authority)
        self.assertIsNotNone(result.snapshot,
                             "H8-F3B: an unborn Git repository must preserve context before its first commit")
        self.assertEqual(result.status, "captured", result.warnings)
        self.assertEqual(result.snapshot.history_status, "not-applicable")
        self.assertEqual(self.s.read_payload(result.snapshot.snapshot_path, "notes.md", self.fixture.identity,
                                            self.settings.limits), notes.read_bytes())
        self.assertEqual(result.observed_oids, ())
        self.assertEqual(calls, [])
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertIsNone(result.push)
        self.assertFalse((self.state / ".checkpoint.lock").exists())

    def test_unborn_whole_project_omits_ignored_files_except_named_context(self) -> None:
        project = self.fixture.base / "unborn-whole-project"
        self.fixture.raw("init", "--initial-branch=work", f"--template={self.fixture.template}",
                         str(project), cwd=self.fixture.base)
        (project / ".gitignore").write_text("scratch-notes.txt\nselected-context.md\n")
        (project / "notes.md").write_text("# Visible pre-commit context\n")
        (project / "scratch-notes.txt").write_text("Ignored ordinary content\n")
        (project / "selected-context.md").write_text("# Explicitly named ignored context\n")
        ignored = self.fixture.raw("check-ignore", "--", "scratch-notes.txt", "selected-context.md",
                                   cwd=project)
        self.assertEqual(ignored.stdout.splitlines(), ["scratch-notes.txt", "selected-context.md"])
        self.assertEqual(self.fixture.raw("rev-list", "--all", cwd=project).stdout, "")
        self.settings = replace(self.settings, project_root=project, approved_sources=(project,))
        self.snapshot_request = replace(self.snapshot_request, settings=self.settings,
                                        approved_sources=(project,), context_entrypoints=("notes.md",),
                                        include_history=False, history_ref=None)
        calls = []

        def authority(request):
            calls.append(request)
            return self.fixture.grant

        result = self.with_authority(authority)
        self.assertEqual(result.status, "captured", result.warnings)
        self.assertEqual(result.snapshot.history_status, "not-applicable")
        manifest = self.s.verify_snapshot(result.snapshot.snapshot_path, self.fixture.identity,
                                           self.settings.limits)
        self.assertEqual({entry.relpath for entry in manifest.files}, {".gitignore", "notes.md"},
                         "H13-ignore: unborn whole-project capture must honor Git ignore rules")
        self.assertEqual(manifest.deleted_paths, ())
        self.assertEqual(calls, [])
        self.assertIsNone(result.push)
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertFalse((self.state / ".checkpoint.lock").exists())
        # Ignored context is allowed only when explicitly selected by this caller.
        self.snapshot_request = replace(self.snapshot_request,
            context_entrypoints=("notes.md", "selected-context.md"))
        selected = self.with_authority(authority)
        self.assertEqual(selected.status, "captured", selected.warnings)
        manifest = self.s.verify_snapshot(selected.snapshot.snapshot_path, self.fixture.identity,
                                           self.settings.limits)
        self.assertEqual({entry.relpath for entry in manifest.files},
                         {".gitignore", "notes.md", "selected-context.md"})
        self.assertEqual(self.s.read_payload(selected.snapshot.snapshot_path, "selected-context.md",
                                             self.fixture.identity, self.settings.limits),
                         b"# Explicitly named ignored context\n")
        self.assertEqual(manifest.context_entrypoints, ("notes.md", "selected-context.md"))
        self.assertEqual(manifest.deleted_paths, ())
        self.assertEqual(calls, [])
        self.assertIsNone(selected.push)
        self.assertEqual(self.fixture.driver.calls, [])
        self.assertFalse((self.state / ".checkpoint.lock").exists())

    def test_rewritten_tips_do_not_accumulate_unreachable_observation_state(self) -> None:
        self.settings = replace(self.settings, limits=self.c.Limits(max_files=3))
        self.snapshot_request = replace(self.snapshot_request, settings=self.settings)
        self.fixture.config("gc.auto", "0")
        self.fixture.config("maintenance.auto", "false")
        first = self.api.checkpoint(self.request(), clock=self.clock)
        self.assert_history_saved(first)
        superseded = set()
        for number in range(3):
            superseded.add(self.fixture.oid)
            self.fixture.raw("commit", "--amend", "--allow-empty", "-m", f"revised draft {number}")
            self.fixture.oid = self.fixture.head()
            self.fixture.known_oids.add(self.fixture.oid)
            self.clock.mono += 1
            result = self.api.checkpoint(self.request(), clock=self.clock)
            self.assertEqual(result.status, "captured",
                             "H10-F2: rewritten observations must not exhaust the current graph budget")
            self.assert_history_saved(result)
            current = set(self.fixture.raw("rev-list", "HEAD", "--").stdout.splitlines())
            state = json.loads((self.state / "state.json").read_text())
            self.assertEqual(set(state["observed_oids"]), current,
                             "H10-F2: unreachable rewritten tips must not accumulate in saved state")
            self.assertTrue(superseded.isdisjoint(state["observed_oids"]))
        self.fixture.fresh_driver()
        published = self.with_authority(lambda request: self.fixture.grant)
        self.assertEqual(published.push.status, "pushed")
        self.assertEqual(self.fixture.remote_oid(), self.fixture.oid)
        self.assertEqual(self.fixture.driver.writes, 1)
        self.assertFalse((self.state / ".checkpoint.lock").exists())

    def test_observation_envelope_capacity_does_not_block_captured_tip_publication(self) -> None:
        first = self.api.checkpoint(self.request(), clock=self.clock)
        self.assert_history_saved(first)
        state_path = self.state / "state.json"
        # Tighten this private process's real state cap around a valid envelope,
        # avoiding thousands of fixture subprocesses while exercising admission.
        state_bound = state_path.stat().st_size + 16
        self.assertLess(state_bound, self.api.STATE_MAX_BYTES)
        self.fixture.raw("commit", "--allow-empty", "-m", "new tip at state capacity")
        self.fixture.oid = self.fixture.head()
        self.fixture.known_oids.add(self.fixture.oid)
        self.fixture.fresh_driver()
        self.clock.mono += 1
        issued = []

        def authority(request):
            issued.append(request.oid)
            return self.fixture.grant

        with patch.object(self.api, "STATE_MAX_BYTES", state_bound):
            result = self.with_authority(authority)
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.status, "complete")
        self.assertEqual(result.status, "captured",
                         "H10-F2-envelope: bounded observation metadata must not prevent exact-tip publication")
        self.assertEqual(result.push.status, "pushed")
        self.assertEqual(issued, [self.fixture.oid])
        self.assertEqual(self.fixture.remote_oid(), self.fixture.oid)
        self.assertEqual(self.fixture.driver.writes, 1)
        self.assertLessEqual(state_path.stat().st_size, state_bound)
        state = json.loads(state_path.read_text())
        reachable = set(self.fixture.raw("rev-list", "HEAD", "--").stdout.splitlines())
        self.assertIn(self.fixture.oid, state["observed_oids"])
        self.assertTrue(set(state["observed_oids"]) <= reachable)
        self.assertIn("observation_history_truncated", result.coverage_gaps,
                      "A bounded observation window must disclose that earlier observations may repeat")
        self.assertFalse((self.state / ".checkpoint.lock").exists())

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
