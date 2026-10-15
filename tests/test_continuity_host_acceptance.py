"""Real disposable installed-host acceptance; no service/durability claim."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import time

import test_checkpoint_host_lifecycle as lifecycle


def descriptors():
    """Ignore the transient descriptor used to enumerate /proc itself."""
    result = {}
    for path in Path("/proc/self/fd").iterdir():
        try:
            result[path.name] = os.readlink(path)
        except FileNotFoundError:
            pass
    return result


class ContinuityHostAcceptanceTests(lifecycle.HostLifecycleFixture):
    def setUp(self):
        self.initial_descriptors = descriptors()
        super().setUp()

    def clean(self):
        try:
            super().clean()
        finally:
            restored = descriptors() == self.initial_descriptors
            lifecycle.record({"event": "descriptor_cleanup", "case": self.id(),
                              "restored": restored})
            self.assertTrue(restored, "Installed execution leaked caller descriptors")

    def git(self, *args):
        code, out, err, _ = self.child(["/usr/bin/git", *map(str, args)], timeout=65)
        self.assertEqual(code, 0, (args, out, err))
        return out.decode().strip()

    def tick(self, receipt):
        code, out, err, _ = self.child(shlex.split(receipt.periodic_command), timeout=65)
        self.assertEqual(code, 0, err)
        value = json.loads(out)
        self.assertEqual(self.f.c.read_json(self.state / "last-result.json"), value)
        return value

    def remove(self, receipt):
        saved = lifecycle.tree(self.f.backup)
        result = self.api.remove_connections(receipt, approval=True)
        self.assertEqual(result.status, "removed", result.blockers)
        for path in (self.hook, self.binding, self.ownership):
            self.assertFalse(path.exists())
        self.assertEqual(lifecycle.tree(self.f.backup), saved)
        self.assertEqual((self.f.project / ".git/config").read_bytes(), self.case.before_git_config)
        lifecycle.record({"event": "owned_uninstall", "case": self.id(),
                          "status": result.status, "backup_unchanged": True})

    def assert_capture(self, value, head, graph, content, *, push="denied"):
        """Read real retained objects in a fresh bare repo, not source objects."""
        f = self.f
        self.assertEqual(value["status"], "captured", value)
        self.assertEqual(value["push"]["status"], push)
        self.assertEqual(value["push"]["oid"], head)
        self.assertEqual(value["push"]["destination_ref"], "refs/heads/work")
        self.assertIsNone(value["push"]["remote_oid"])
        self.assertIn("reachable_history_only", value["coverage_gaps"])
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        saved = Path(value["snapshot"]["snapshot_path"])
        manifest = f.s.verify_snapshot(saved, f.identity, f.settings.limits)
        self.assertEqual(manifest.completion, "complete", manifest.omissions)
        self.assertEqual(manifest.head, head)
        self.assertEqual(manifest.ref, "refs/heads/work")
        self.assertEqual({entry.relpath for entry in manifest.files}, {"notes.md"})
        self.assertEqual(f.s.read_payload(saved, "notes.md", f.identity, f.settings.limits), content)
        self.assertEqual(manifest.history.status, "bundled")
        self.assertEqual(manifest.history.head, head)
        bundle = saved / manifest.history.file
        self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), manifest.history.sha256)
        self.assertEqual(hashlib.sha256((saved / "manifest.json").read_bytes()).hexdigest(),
                         value["snapshot"]["manifest_sha256"])
        bare = f.base / ("bundle-check-" + manifest.snapshot_id)
        self.git("init", "--bare", "--initial-branch=work", f"--template={f.template}", bare)
        self.git("-C", bare, "bundle", "verify", bundle)
        self.assertEqual(self.git("-C", bare, "bundle", "unbundle", bundle),
                         head + " refs/heads/work")
        actual = {parts[0]: parts[1:] for line in
                  self.git("-C", bare, "rev-list", "--parents", head).splitlines()
                  if (parts := line.split())}
        self.assertEqual(actual, graph, "Retained branch graph differs from exact expected parents")
        state = f.c.read_json(self.state / "state.json")
        self.assertEqual(state["head"], head)
        self.assertEqual(set(state["observed_oids"]), set(graph))
        pointer = f.c.decode_locator(f.c.read_json(
            f.backup / "projects" / f.identity.id / "latest.json"), pointer=True)
        self.assertEqual(Path(pointer.path), saved / "manifest.json")
        lifecycle.record({"event": "capture_verified", "case": self.id(), "head": head,
                          "graph": actual, "files": [entry.relpath for entry in manifest.files],
                          "payload_sha256": hashlib.sha256(content).hexdigest(),
                          "snapshot": str(saved), "manifest_sha256": value["snapshot"]["manifest_sha256"],
                          "bundle_sha256": manifest.history.sha256, "result": value})
        return saved, bare

    def current(self, head, graph, content):
        return self.assert_capture(self.f.c.read_json(self.state / "last-result.json"),
                                   head, graph, content)

    def test_no_verify_and_amend_capture_exact_rewritten_tip(self):
        initial = self.git("rev-parse", "HEAD")
        receipt = self.install()
        self.assertEqual(self.hook.name, "reference-transaction")
        self.assertFalse((self.hook.parent / "post-commit").exists())
        first = b"# Native commit with ordinary verification bypassed\n"
        (self.f.project / "notes.md").write_bytes(first)
        self.git("add", "--", "notes.md")
        self.git("commit", "--no-verify", "-m", "native no-verify")
        native = self.git("rev-parse", "HEAD")
        saved, bare = self.current(native, {initial: [], native: [initial]}, first)
        self.assertEqual(self.git("-C", bare, "show", f"{native}:notes.md"), first.decode().strip())
        retained = lifecycle.tree(saved)
        amended = b"# Saved amended work\n"
        (self.f.project / "notes.md").write_bytes(amended)
        self.git("add", "--", "notes.md")
        self.git("commit", "--amend", "--no-verify", "-m", "rewritten native tip")
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, native)
        _, bare = self.current(head, {initial: [], head: [initial]}, amended)
        self.assertEqual(self.git("-C", bare, "show", f"{head}:notes.md"), amended.decode().strip())
        self.assertEqual(lifecycle.tree(saved), retained, "Rewrite must preserve the earlier verified snapshot")
        self.assertNotIn(native, self.f.c.read_json(self.state / "state.json")["observed_oids"])
        self.remove(receipt)

    def test_plumbing_fast_forward_and_merge_preserve_current_reachable_graph(self):
        initial = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        receipt = self.install()
        content = (self.f.project / "notes.md").read_bytes()
        before = (lifecycle.tree(self.state), lifecycle.tree(self.f.backup))
        plumbing = self.git("commit-tree", tree, "-p", initial, "-m", "plumbing candidate")
        # Unattached objects do not imply universal transient-commit coverage.
        self.assertEqual((lifecycle.tree(self.state), lifecycle.tree(self.f.backup)), before)
        self.git("update-ref", "refs/heads/work", plumbing, initial)
        graph = {initial: [], plumbing: [initial]}
        self.current(plumbing, graph, content)
        forward = self.git("commit-tree", tree, "-p", plumbing, "-m", "fast-forward candidate")
        self.git("merge", "--ff-only", forward)
        graph[forward] = [plumbing]
        self.current(forward, graph, content)
        side = self.git("commit-tree", tree, "-p", plumbing, "-m", "sibling merge candidate")
        self.git("merge", "--no-ff", "-m", "two-parent merge", side)
        merged = self.git("rev-parse", "HEAD")
        self.assertNotIn(merged, graph)
        graph.update({side: [plumbing], merged: [forward, side]})
        self.current(merged, graph, content)
        self.remove(receipt)

    def test_installed_idle_tick_reads_aged_saved_context_then_is_not_due(self):
        # Default-on publication without authority intentionally forces retries;
        # cadence-only installation explicitly turns that independent feature off.
        settings = replace(self.case.snapshot.settings, git=self.f.c.GitPolicy(push_after_commit=False))
        self.case.snapshot = replace(self.case.snapshot, settings=settings)
        head = self.git("rev-parse", "HEAD")
        content = b"# Last saved context; no model ran while idle\n"
        note = self.f.project / "notes.md"
        note.write_bytes(content)
        saved_at = time.time() - 600
        os.utime(note, (saved_at, saved_at))
        receipt = self.install()
        source = lifecycle.tree(self.f.project)
        self.assertFalse((self.state / "connection-schedule.json").exists(),
                         "Manual installed tick must not masquerade as a configured service")
        lower = time.time() - note.stat().st_mtime
        first = self.tick(receipt)
        upper = time.time() - note.stat().st_mtime
        self.assertGreaterEqual(first["context_age_seconds"], lower)
        self.assertLessEqual(first["context_age_seconds"], upper)
        self.assert_capture(first, head, {head: []}, content, push="disabled")
        backup = lifecycle.tree(self.f.backup)
        state = (self.state / "state.json").read_bytes()
        second = self.tick(receipt)
        self.assertEqual(second["status"], "not-due", second)
        self.assertIsNone(second["snapshot"])
        self.assertIsNone(second["push"])
        self.assertEqual(second["observed_oids"], [])
        self.assertEqual(lifecycle.tree(self.f.backup), backup)
        self.assertEqual((self.state / "state.json").read_bytes(), state)
        self.assertEqual(lifecycle.tree(self.f.project), source)
        self.assertFalse((self.f.project / ".thunderkit/config.json").exists())
        self.assertEqual(list(self.f.home.iterdir()), [])
        lifecycle.record({"event": "idle_due_verified", "case": self.id(), "saved_at": saved_at,
                          "age_bounds": [lower, upper], "first": first, "second": second,
                          "backup_unchanged_on_second_tick": True})
        self.remove(receipt)

    def test_missing_and_replaced_backing_refuse_without_locator_or_backup_writes(self):
        receipt = self.install()
        head = self.git("rev-parse", "HEAD")
        content = (self.f.project / "notes.md").read_bytes()
        saved, _ = self.assert_capture(self.tick(receipt), head, {head: []}, content)
        original = self.f.base / "original-backing"
        self.f.backup.rename(original)
        relocated = original / saved.relative_to(self.f.backup)
        for condition in ("missing", "replaced"):
            if condition == "replaced":
                self.f.backup.mkdir()
                (self.f.backup / "foreign.txt").write_bytes(b"Not the qualified backing\n")
            before = lifecycle.tree(self.f.base)
            code, out, err, _ = self.child(shlex.split(receipt.periodic_command), timeout=65)
            self.assertEqual(lifecycle.tree(self.f.base), before,
                             "Backing refusal wrote state, locator or replacement storage")
            self.assertEqual(code, 2, (condition, out, err))
            self.assertEqual(out, b"")
            error = json.loads(err)
            expected = "unsafe_path" if condition == "missing" else "connection_path_changed"
            self.assertEqual(error["error"]["code"], expected)
            self.assertEqual(self.f.s.verify_snapshot(relocated, self.f.identity,
                                                     self.f.settings.limits).head, head)
            self.assertEqual(self.f.s.read_payload(relocated, "notes.md", self.f.identity,
                                                  self.f.settings.limits), content)
            lifecycle.record({"event": "backing_refusal_verified", "case": self.id(),
                              "condition": condition, "exit": code, "error": error,
                              "tree_unchanged": True, "original_snapshot": str(relocated)})
        # Ownership removal must not qualify/adopt the replacement backup path.
        self.remove(receipt)
        self.assertEqual(self.f.s.verify_snapshot(relocated, self.f.identity,
                                                 self.f.settings.limits).head, head)
