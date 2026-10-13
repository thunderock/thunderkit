"""S tests-only contract; C types are consumed unchanged.

The initial RED witnesses only the missing entrypoint, not snapshot semantics.
Behavioral cases must reach their own assertions during incremental implementation.
No production implementation or conditional skips live in this file.

Frozen layout: <root>/{projects|global}/<id>/snapshots/<snapshot-id>/
{manifest.json,payload/<relpath>,history.bundle?}; scope/latest.json is C's
context-pointer to the immutable absolute manifest path. Scope-owned transient
names: .snapshot.lock (exclusive directory), .staging/<unique-exclusive-id>/.

Fault hooks receive (event, source_or_scope_path): after_source_read,
after_scope_lock, before_manifest_publish, before_locator_publish. They grant no
authority and are only used against disposable test-owned files.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Protocol, cast
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
STORE = REFERENCES / "backup_store.py"
# Only the existing C module is imported during the initial red checkpoint.
sys.path.insert(0, str(REFERENCES))
try:
    import continuity_config as C  # pyright: ignore[reportMissingImports]
finally:
    sys.path.pop(0)

MISSING = "Missing crash-safe snapshot capture and verified payload reads: skills/references/backup_store.py is not implemented"
Fault = Callable[[str, Path | None], None]


class Qualification(Protocol):
    identity: C.StorageIdentity
    readback_verified: bool
    backing_durability_verified: bool


class StoreAPI(Protocol):
    def inspect_storage(self, root: Path) -> C.StorageIdentity: ...
    def qualify_storage(self, storage: C.StorageIdentity, *, approval: bool = False) -> Qualification: ...
    def create_snapshot(self, request: C.SnapshotRequest, *, fault: Fault | None = None) -> C.SnapshotResult: ...
    def verify_snapshot(self, snapshot: Path, identity: C.ScopeIdentity, limits: C.Limits) -> C.BackupManifest: ...
    def read_payload(self, snapshot: Path, relpath: str, identity: C.ScopeIdentity, limits: C.Limits, *, max_bytes: int | None = None) -> bytes: ...
    def read_history(self, snapshot: Path, identity: C.ScopeIdentity, limits: C.Limits) -> bytes: ...


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        self.temp = tempfile.TemporaryDirectory(prefix="thunderkit-S-red-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture_parent = Path(cast(str, scratch))
        self.project = self.base / "project"
        self.backups = self.base / "approved-backups"
        self.home = self.base / "empty-home"
        self.hooks = self.base / "empty-hooks"
        self.template = self.base / "empty-template"
        for path in (self.project, self.backups, self.home, self.hooks, self.template):
            path.mkdir()
        git = shutil.which("git")
        self.assertIsNotNone(git, "local Git is required for offline fixtures")
        self.git_binary = str(git)
        self.environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home),
            "TMPDIR": str(self.base), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file", "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_CONFIG_COUNT": "4", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(self.hooks),
            "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
            "GIT_CONFIG_KEY_2": "commit.gpgsign", "GIT_CONFIG_VALUE_2": "false",
            "GIT_CONFIG_KEY_3": "tag.gpgsign", "GIT_CONFIG_VALUE_3": "false",
            "GIT_AUTHOR_NAME": "Offline Fixture", "GIT_COMMITTER_NAME": "Offline Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        environment = patch.dict(os.environ, self.environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        previous = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)
        self.identity = C.ScopeIdentity("project", "project-a")
        self.settings = C.ResolvedSettings(
            identity=self.identity, project_root=self.project, backup_root=self.backups,
            context_path=None, context_source="unconfigured", backup=C.BackupPolicy(enabled=True),
        )
        self.scope = self.backups / "projects/project-a"
        self.locator = self.scope / "latest.json"
        self.write("notes.md", b"# Saved context\nPending fixture work.\n")

    def write(self, name: str, data: bytes, *, root: Path | None = None) -> Path:
        path = (root or self.project) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            [self.git_binary, "-c", f"core.hooksPath={self.hooks}", "-c", "credential.helper=",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            cwd=cwd or self.project, env=self.environment, capture_output=True, text=True,
            timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, f"Offline fixture Git failed: {args!r}: {result.stderr}")
        return result.stdout.strip()

    def init_git(self) -> str:
        self.git("init", "--initial-branch=work", f"--template={self.template}")
        self.write(".gitignore", b".context/\nignored/\n.thunderkit/continuity.local.json\n")
        self.write("tracked.txt", b"committed\n")
        self.write("deleted.txt", b"remove later\n")
        self.write("run.sh", b"#!/bin/sh\nprintf fixture\n")
        self.commit("initial fixture")
        return self.git("rev-parse", "HEAD")

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tree(self, root: Path) -> dict[str, tuple[int, int, bytes | str | None]]:
        result: dict[str, tuple[int, int, bytes | str | None]] = {}
        if not root.exists():
            return result
        for parent, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                info = path.lstat()
                data: bytes | str | None = None
                if stat.S_ISLNK(info.st_mode):
                    data = os.readlink(path)
                elif stat.S_ISREG(info.st_mode):
                    data = path.read_bytes()
                # Lock/probe creation may change directory timestamps; unchanged
                # snapshot files and locator timestamps must still stay exact.
                mtime = 0 if stat.S_ISDIR(info.st_mode) else info.st_mtime_ns
                result[str(path.relative_to(root))] = (info.st_mode, mtime, data)
        return result

    def error(self, code: str, action: Callable[[], object]) -> C.ContinuityError:
        with self.assertRaises(C.ContinuityError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class SnapshotEntrypointTests(unittest.TestCase):
    def test_snapshot_behavior_entrypoint_exists(self) -> None:
        self.assertTrue(STORE.is_file(), MISSING)


class SnapshotFixtureTests(Fixture):
    def test_existing_C_contract_and_isolated_git_fixture_are_usable(self) -> None:
        head = self.init_git()
        info = self.backups.stat()
        request = C.SnapshotRequest(
            settings=self.settings, storage=C.StorageIdentity(self.backups, info.st_dev, info.st_ino),
            approved_sources=(self.project,), include_history=True, history_ref="refs/heads/work",
        )
        self.assertEqual(request.settings.identity, self.identity)
        self.assertEqual(self.git("symbolic-ref", "HEAD"), request.history_ref)
        self.assertEqual(C.validate_oid(head), head)
        self.assertEqual(self.git("config", "--get", "core.hooksPath"), str(self.hooks))
        self.assertEqual(tuple(self.home.iterdir()), ())
        self.assertEqual(tuple(self.hooks.iterdir()), ())
        self.assertEqual(self.git("remote"), "")
        self.assertEqual(self.tree(self.backups), {})
        self.assertNotIn("SSH_AUTH_SOCK", os.environ)
        self.assertEqual(self.base.parent, self.fixture_parent)
        self.assertEqual(Path(os.environ["TMPDIR"]), self.base)


class BackupStoreTests(Fixture):
    def setUp(self) -> None:
        self.assertTrue(STORE.is_file(), MISSING)
        super().setUp()
        sys.path.insert(0, str(REFERENCES))
        try:
            self.api = cast(StoreAPI, importlib.import_module("backup_store"))
        finally:
            sys.path.pop(0)
        self.storage = self.api.qualify_storage(self.api.inspect_storage(self.backups), approval=True).identity

    def request(self, *, sources: tuple[Path, ...] | None = None, history: bool = False,
                settings: C.ResolvedSettings | None = None, context: tuple[str, ...] = ()) -> C.SnapshotRequest:
        return C.SnapshotRequest(
            settings=settings or self.settings, storage=self.storage,
            approved_sources=sources if sources is not None else (self.project,),
            context_entrypoints=context, include_history=history,
            history_ref="refs/heads/work" if history else None,
        )

    def capture(self, request: C.SnapshotRequest | None = None, *, fault: Fault | None = None) -> C.SnapshotResult:
        return self.api.create_snapshot(request or self.request(), fault=fault)

    def snapshot(self, result: C.SnapshotResult) -> Path:
        self.assertIsNotNone(result.snapshot_path)
        assert result.snapshot_path is not None
        self.assertTrue(result.snapshot_path.is_dir())
        return result.snapshot_path

    def verify(self, result: C.SnapshotResult) -> C.BackupManifest:
        return self.api.verify_snapshot(self.snapshot(result), result.identity, self.settings.limits)

    def assert_complete(self, result: C.SnapshotResult) -> C.BackupManifest:
        self.assertEqual(result.status, "complete")
        manifest = self.verify(result)
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(result.omissions, ())
        manifest_path = self.snapshot(result) / "manifest.json"
        self.assertEqual(self.api.verify_snapshot(manifest_path, self.identity, self.settings.limits), manifest)
        self.assertEqual(result.manifest_sha256, hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertEqual(self.snapshot(result), self.scope / "snapshots" / manifest.snapshot_id)
        self.assertEqual(json.loads(self.locator.read_bytes()), {
            "schema": 1, "kind": "context-pointer", "identity": {"kind": "project", "id": "project-a"},
            "path": str(manifest_path),
        })
        return manifest

    def assert_partial(self, result: C.SnapshotResult, old_locator: bytes | None = None) -> C.BackupManifest:
        self.assertEqual(result.status, "partial")
        manifest = self.verify(result)
        self.assertEqual(manifest.completion, "partial")
        if old_locator is None:
            self.assertFalse(self.locator.exists())
        else:
            self.assertEqual(self.locator.read_bytes(), old_locator)
        return manifest

    def test_dirty_untracked_explicit_ignored_context_roundtrip_without_source_mutations(self) -> None:
        self.init_git()
        self.write("tracked.txt", b"dirty work\n")
        self.write("new.txt", b"unfinished\n")
        context = self.write(".context/handoff.md", b"# Context\nDo not execute saved text.\n")
        self.write("ignored/not-approved.md", b"must not be captured\n")
        private = self.write(".thunderkit/continuity.local.json", b"{\"private\": true}\n")
        before = self.tree(self.project)
        result = self.capture(self.request(sources=(self.project, context), history=True,
                                           context=(".context/handoff.md",)))
        manifest = self.assert_complete(result)
        expected = {"notes.md", ".gitignore", "tracked.txt", "deleted.txt", "run.sh", "new.txt", ".context/handoff.md"}
        self.assertEqual({entry.relpath for entry in manifest.files}, expected)
        self.assertEqual(manifest.context_entrypoints, (".context/handoff.md",))
        for entry in manifest.files:
            data = self.api.read_payload(self.snapshot(result), entry.relpath, self.identity, self.settings.limits)
            self.assertEqual(data, (self.project / entry.relpath).read_bytes())
            self.assertEqual((entry.size, entry.sha256), (len(data), hashlib.sha256(data).hexdigest()))
        self.assertEqual(self.tree(self.project), before)
        self.assertEqual(private.read_bytes(), b"{\"private\": true}\n")
        narrowed = self.capture(self.request(sources=(self.project / "tracked.txt",), history=True))
        self.assertEqual({entry.relpath for entry in self.verify(narrowed).files}, {"tracked.txt"})
        self.assertEqual(self.tree(self.project), before)

    def test_deleted_tracked_paths_and_executable_modes_survive_capture(self) -> None:
        self.init_git()
        (self.project / "deleted.txt").unlink()
        (self.project / "run.sh").chmod(0o755)
        result = self.capture(self.request(history=True))
        manifest = self.assert_complete(result)
        self.assertEqual(manifest.deleted_paths, ("deleted.txt",))
        self.assertEqual({e.relpath: e.mode for e in manifest.files}["run.sh"], 0o755)
        self.assertFalse((self.snapshot(result) / "payload/deleted.txt").exists())
        self.assertEqual(stat.S_IMODE((self.snapshot(result) / "payload/run.sh").stat().st_mode), 0o755)

    def test_global_explicit_sources_map_deterministically_without_cross_scope_writes(self) -> None:
        project_result = self.capture()
        before = self.tree(self.scope)
        notes = self.write("notes/a.md", b"global note\n", root=self.base)
        config = self.write("selected/settings.json", b"{\"theme\": \"dark\"}\n", root=self.base)
        other = self.write("other-profile/private.md", b"not approved\n", root=self.base)
        identity = C.ScopeIdentity("global", "profile-a")
        sources = (notes.parent, config)
        settings = replace(self.settings, identity=identity, project_root=None, approved_sources=sources)
        result = self.capture(self.request(sources=sources, settings=settings))
        manifest = self.api.verify_snapshot(self.snapshot(result), identity, settings.limits)
        self.assertEqual(result.status, "complete")
        self.assertEqual({f.relpath for f in manifest.files}, {"sources/0/notes/a.md", "sources/1/settings.json"})
        self.assertEqual(self.snapshot(result).parent, self.backups / "global/profile-a/snapshots")
        self.assertEqual(manifest.history.status, "not-applicable")
        global_pointer = json.loads((self.backups / "global/profile-a/latest.json").read_bytes())
        self.assertEqual(global_pointer, {
            "schema": 1, "kind": "context-pointer", "identity": {"kind": "global", "id": "profile-a"},
            "path": str(self.snapshot(result) / "manifest.json"),
        })
        self.assertEqual(self.tree(self.scope), before)
        self.assertEqual(other.read_bytes(), b"not approved\n")
        self.error("identity_mismatch", lambda: self.api.verify_snapshot(self.snapshot(project_result), identity, settings.limits))

    def test_self_contained_exact_branch_bundle_recovers_graph_after_checkout_loss(self) -> None:
        self.init_git()
        self.git("checkout", "-b", "side")
        self.write("side.txt", b"side graph\n")
        self.commit("side change")
        self.git("checkout", "work")
        self.write("work.txt", b"work graph\n")
        self.commit("work change")
        self.git("merge", "--no-ff", "side", "-m", "fixture merge")
        self.git("branch", "unrelated-ref")
        self.git("tag", "unrequested-tag")
        expected = self.git("rev-list", "--parents", "HEAD")
        head = self.git("rev-parse", "HEAD")
        result = self.capture(self.request(history=True))
        manifest = self.assert_complete(result)
        self.assertEqual((manifest.history.status, manifest.history.file, manifest.history.head), ("bundled", "history.bundle", head))
        bundle = self.snapshot(result) / "history.bundle"
        data = self.api.read_history(self.snapshot(result), self.identity, self.settings.limits)
        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (manifest.history.size, manifest.history.sha256))
        self.assertEqual(data, bundle.read_bytes())
        refs = dict(line.split(" ", 1)[::-1] for line in self.git("bundle", "list-heads", str(bundle)).splitlines())
        self.assertEqual(refs.get("refs/heads/work"), head)
        self.assertLessEqual(set(refs), {"refs/heads/work", "HEAD"})
        if "HEAD" in refs:
            self.assertEqual(refs["HEAD"], head)
        shutil.rmtree(self.project)  # Only this test-owned disposable checkout.
        recovered = self.base / "recovered"
        recovered.mkdir()
        self.git("init", "--initial-branch=work", f"--template={self.template}", cwd=recovered)
        self.git("bundle", "verify", str(bundle), cwd=recovered)
        self.git("fetch", "--no-tags", str(bundle), "refs/heads/work:refs/heads/work", cwd=recovered)
        self.assertEqual(self.git("rev-list", "--parents", "refs/heads/work", cwd=recovered), expected)
        self.assertEqual(self.api.verify_snapshot(bundle.parent, self.identity, self.settings.limits), manifest)

    def test_historical_blob_path_or_commit_secret_makes_history_unprotected(self) -> None:
        self.init_git()
        previous = self.capture(self.request(history=True))
        self.assert_complete(previous)
        old_locator = self.locator.read_bytes()
        initial = self.git("rev-parse", "HEAD")
        synthetic = b"ghp_" + b"A" * 36  # Deliberately inert, generated test marker.
        for kind in ("blob", "path", "commit"):
            with self.subTest(kind=kind):
                self.git("reset", "--hard", initial)
                path = ".env" if kind == "path" else "old.txt"
                self.write(path, b"API_TOKEN=" + synthetic + b"\n" if kind == "blob" else b"old public data\n")
                message = "history " + synthetic.decode() if kind == "commit" else "historical fixture"
                self.commit(message)
                (self.project / path).unlink()
                self.commit("remove old material")
                result = self.capture(self.request(history=True))
                manifest = self.assert_partial(result, old_locator)
                self.assertEqual(manifest.history.status, "unprotected")
                self.assertFalse((self.snapshot(result) / "history.bundle").exists())
                self.assertTrue(manifest.files)
                self.assertTrue(manifest.omissions)
                self.assertNotIn(synthetic, (self.snapshot(result) / "manifest.json").read_bytes())

    def test_history_scan_bytes_and_object_count_are_bounded_not_just_worktree(self) -> None:
        self.init_git()
        self.write("large-history.bin", bytes(range(256)) * 128)
        self.commit("large historical blob")
        (self.project / "large-history.bin").unlink()
        self.commit("remove large blob")
        for limits in (C.Limits(max_file_bytes=1024, max_snapshot_bytes=4096), C.Limits(max_files=1)):
            with self.subTest(limits=limits):
                settings = replace(self.settings, limits=limits)
                result = self.capture(self.request(sources=(self.project / "notes.md",), history=True, settings=settings))
                manifest = self.assert_partial(result)
                self.assertEqual(manifest.history.status, "unprotected")
                self.assertEqual({f.relpath for f in manifest.files}, {"notes.md"})
                self.assertFalse((self.snapshot(result) / "history.bundle").exists())

    def test_only_attached_working_branch_can_be_bundled(self) -> None:
        self.init_git()
        self.git("branch", "other")
        before = self.tree(self.backups)
        wrong = replace(self.request(history=True), history_ref="refs/heads/other")
        self.error("history_ref_mismatch", lambda: self.capture(wrong))
        self.git("checkout", "--detach")
        self.error("history_ref_mismatch", lambda: self.capture(self.request(history=True)))
        self.assertEqual(self.tree(self.backups), before)

    def test_caller_remote_evidence_is_not_proof_and_partial_preserves_locator(self) -> None:
        head = self.init_git()
        self.assert_complete(self.capture(self.request(history=True)))
        previous = self.locator.read_bytes()
        for evidence in (False, True):
            with self.subTest(evidence=evidence):
                request = self.request()
                if evidence:
                    request = replace(request, verified_remote_oid=head, remote_evidence="caller claims remote success")
                manifest = self.assert_partial(self.capture(request), previous)
                self.assertEqual(manifest.history.status, "unprotected")

    def test_explicit_forbidden_sources_and_secret_content_are_omitted_without_values(self) -> None:
        self.init_git()
        synthetic = b"ghp_" + b"B" * 36
        names = (".env", "auth.json", "id_rsa", ".thunderkit/continuity.local.json",
                 ".thunderkit/grants.json", ".hermes/sessions.db", "transcript.jsonl",
                 ".git/inert-fixture", "node_modules/dependency.js", "build/output.bin", "datasets/rows.csv")
        for name in names:
            with self.subTest(name=name):
                forbidden = self.write(name, b"excluded fixture data\n")
                result = self.capture(self.request(sources=(self.project / "notes.md", forbidden)))
                manifest = self.assert_partial(result)
                self.assertEqual({f.relpath for f in manifest.files}, {"notes.md"})
                self.assertTrue(manifest.omissions)
        secret = self.write("credentials.py", b"API_TOKEN='" + synthetic + b"'\n")
        code = self.write("parser.py", b"def token_count(tokens):\n    return len(tokens)\n")
        result = self.capture(self.request(sources=(secret, code)))
        manifest = self.assert_partial(result)
        self.assertEqual({f.relpath for f in manifest.files}, {"parser.py"})
        encoded = (self.snapshot(result) / "manifest.json").read_bytes()
        self.assertNotIn(synthetic, encoded)
        self.assertTrue(all(len(item.encode()) <= 4096 and "\n" not in item for item in manifest.omissions))

    def test_source_links_special_files_traversal_outside_and_destination_overlap_fail_closed(self) -> None:
        outside = self.write("outside.md", b"outside scope\n", root=self.base)
        link = self.project / "link.md"
        link.symlink_to(outside)
        ancestor = self.project / "linked-dir"
        ancestor.symlink_to(self.base, target_is_directory=True)
        hardlink = self.project / "hard.md"
        os.link(outside, hardlink)
        fifo = self.project / "pipe"
        os.mkfifo(fifo)
        before = self.tree(self.backups)
        for source in (link, ancestor / "outside.md", hardlink, fifo, outside,
                       self.project / "../outside.md", self.backups, self.base):
            with self.subTest(source=source.name):
                self.error("unsafe_path", lambda: self.capture(self.request(sources=(source,))))
                self.assertEqual(self.tree(self.backups), before)
        inner = self.project / "destination"
        inner.mkdir()
        storage = self.api.qualify_storage(self.api.inspect_storage(inner), approval=True).identity
        request = C.SnapshotRequest(settings=replace(self.settings, backup_root=inner), storage=storage,
                                    approved_sources=(self.project,))
        self.error("unsafe_path", lambda: self.capture(request))
        self.assertEqual(self.tree(inner), {})

    def test_file_aggregate_count_and_path_budget_omissions_retain_complete_locator(self) -> None:
        self.assert_complete(self.capture())
        previous = self.locator.read_bytes()
        self.write("a.txt", b"a" * 40)
        self.write("b.txt", b"b" * 40)
        long_name = "x" * 245
        self.write(long_name, b"long path\n")
        cases = (
            ((self.project / "a.txt",), C.Limits(max_file_bytes=32, max_snapshot_bytes=64)),
            ((self.project / "a.txt", self.project / "b.txt"), C.Limits(max_file_bytes=64, max_snapshot_bytes=64)),
            ((self.project / "a.txt", self.project / "b.txt"), C.Limits(max_files=1)),
            ((self.project / long_name,), C.Limits(max_path_bytes=240)),
        )
        for sources, limits in cases:
            with self.subTest(limits=limits):
                result = self.capture(self.request(sources=sources, settings=replace(self.settings, limits=limits)))
                manifest = self.assert_partial(result, previous)
                self.assertTrue(manifest.omissions)
                self.assertLessEqual(sum(f.size for f in manifest.files), limits.max_snapshot_bytes)
                self.assertLessEqual(len(manifest.files), limits.max_files)

    def test_verifier_checks_actual_bytes_modes_extras_and_no_follow_files(self) -> None:
        result = self.capture()
        self.assert_complete(result)
        snapshot = self.snapshot(result)
        payload = snapshot / "payload/notes.md"
        original = payload.read_bytes()
        attacks = ("bytes", "missing", "mode", "extra-payload", "extra-root", "link", "hardlink", "fifo")
        for attack in attacks:
            with self.subTest(attack=attack):
                extra = snapshot / ("payload/extra" if attack == "extra-payload" else "extra")
                try:
                    if attack == "bytes":
                        payload.write_bytes(b"!" * len(original))
                    elif attack == "mode":
                        payload.chmod(0o755)
                    elif attack.startswith("extra"):
                        extra.write_bytes(b"undeclared\n")
                    else:
                        payload.unlink()
                        if attack == "link":
                            payload.symlink_to(self.project / "notes.md")
                        elif attack == "hardlink":
                            os.link(self.project / "notes.md", payload)
                        elif attack == "fifo":
                            os.mkfifo(payload)
                    code = "unsafe_path" if attack in ("link", "hardlink", "fifo") else "integrity_mismatch"
                    self.error(code, lambda: self.api.verify_snapshot(snapshot, self.identity, self.settings.limits))
                finally:
                    if extra.exists():
                        extra.unlink()
                    if payload.exists() or payload.is_symlink():
                        payload.unlink()
                    payload.write_bytes(original)
                    payload.chmod(0o644)

    def test_manifest_path_attacks_and_overlaps_are_rejected_before_payload_access(self) -> None:
        result = self.capture()
        snapshot = self.snapshot(result)
        path = snapshot / "manifest.json"
        original = path.read_bytes()
        for attack in ("traversal", "absolute", "duplicate", "ancestor", "deleted-overlap", "history-overlap"):
            with self.subTest(attack=attack):
                raw = json.loads(original)
                code = "invalid_manifest"
                if attack in ("traversal", "absolute"):
                    raw["files"][0]["relpath"] = "../outside" if attack == "traversal" else str(self.project / "notes.md")
                    code = "unsafe_path"
                elif attack == "duplicate":
                    raw["files"].append(dict(raw["files"][0]))
                elif attack == "ancestor":
                    raw["files"].append({**raw["files"][0], "relpath": "notes.md/child"})
                elif attack == "deleted-overlap":
                    raw["deleted_paths"] = ["notes.md/child"]
                else:
                    raw["history"] = {"status": "bundled", "file": "payload/notes.md", "size": 1,
                                      "sha256": hashlib.sha256(b"x").hexdigest(), "ref": "refs/heads/work", "head": "a" * 40}
                    raw.update(repository=str(self.project), ref="refs/heads/work", head="a" * 40)
                path.write_text(json.dumps(raw), encoding="utf-8")
                self.error(code, lambda: self.api.verify_snapshot(snapshot, self.identity, self.settings.limits))
                path.write_bytes(original)

    def test_bounded_reader_rechecks_after_verify_and_rejects_ancestor_links(self) -> None:
        result = self.capture()
        snapshot = self.snapshot(result)
        self.verify(result)
        self.error("read_budget_exceeded", lambda: self.api.read_payload(snapshot, "notes.md", self.identity, self.settings.limits, max_bytes=1))
        self.error("unsafe_path", lambda: self.api.read_payload(snapshot, "../manifest.json", self.identity, self.settings.limits))
        self.error("payload_not_found", lambda: self.api.read_payload(snapshot, "missing.md", self.identity, self.settings.limits))
        (snapshot / "payload/notes.md").write_bytes(b"changed after verify\n")
        self.error("integrity_mismatch", lambda: self.api.read_payload(snapshot, "notes.md", self.identity, self.settings.limits))
        (snapshot / "payload").rename(self.base / "original-payload")
        (snapshot / "payload").symlink_to(self.project, target_is_directory=True)
        self.error("unsafe_path", lambda: self.api.read_payload(snapshot, "notes.md", self.identity, self.settings.limits))

    def test_bundle_corruption_and_rehashed_wrong_branch_bundle_fail_verification(self) -> None:
        self.init_git()
        result = self.capture(self.request(history=True))
        snapshot = self.snapshot(result)
        bundle = snapshot / "history.bundle"
        original = bundle.read_bytes()
        bundle.write_bytes(b"!" + original[1:])
        self.error("integrity_mismatch", lambda: self.api.read_history(snapshot, self.identity, self.settings.limits))
        self.git("branch", "wrong")
        bundle.unlink()
        self.git("bundle", "create", str(bundle), "refs/heads/wrong")
        raw = json.loads((snapshot / "manifest.json").read_bytes())
        raw["history"].update(size=bundle.stat().st_size, sha256=hashlib.sha256(bundle.read_bytes()).hexdigest())
        (snapshot / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
        self.error("history_invalid", lambda: self.api.verify_snapshot(snapshot, self.identity, self.settings.limits))

    def test_concurrent_source_mutation_retries_at_most_once_and_keeps_locator(self) -> None:
        self.assert_complete(self.capture())
        previous = self.locator.read_bytes()
        reads: list[Path] = []
        target = self.project / "notes.md"
        target.write_bytes(b"new capture input\n")
        def mutate(event: str, path: Path | None) -> None:
            if event == "after_source_read" and path is not None and path == target:
                reads.append(path)
                path.write_bytes(f"mutation {len(reads)}\n".encode())
        self.error("source_changed", lambda: self.capture(fault=mutate))
        self.assertGreaterEqual(len(reads), 1)
        self.assertLessEqual(len(reads), 2)
        self.assertEqual(self.locator.read_bytes(), previous)
        self.assertFalse((self.scope / ".snapshot.lock").exists())

    def test_interruption_before_manifest_or_locator_never_prunes_previous_snapshot(self) -> None:
        previous_result = self.capture()
        self.assert_complete(previous_result)
        previous = self.locator.read_bytes()
        settings = replace(self.settings, backup=C.BackupPolicy(enabled=True, prune=True, retention=1))
        class InjectedStop(RuntimeError):
            pass
        for point in ("before_manifest_publish", "before_locator_publish"):
            with self.subTest(point=point):
                self.write("notes.md", point.encode())
                seen: list[str] = []
                def stop(event: str, path: Path | None) -> None:
                    if event == point:
                        seen.append(event)
                        raise InjectedStop(point)
                with self.assertRaisesRegex(InjectedStop, "^" + point + "$"):
                    self.capture(self.request(settings=settings), fault=stop)
                self.assertEqual(seen, [point])
                self.assertEqual(self.locator.read_bytes(), previous)
                self.verify(previous_result)
                self.assertFalse((self.scope / ".snapshot.lock").exists())
                staging = self.scope / ".staging"
                self.assertFalse(staging.exists() and any(staging.iterdir()))

    def test_scope_lock_excludes_concurrent_capture_and_unknown_lock_is_not_stale_guessed(self) -> None:
        entered: list[str] = []
        def contend(event: str, path: Path | None) -> None:
            if event == "after_scope_lock":
                entered.append(event)
                self.error("snapshot_busy", lambda: self.capture())
        self.assert_complete(self.capture(fault=contend))
        self.assertEqual(entered, ["after_scope_lock"])
        lock = self.scope / ".snapshot.lock"
        lock.mkdir()
        marker = lock / "unknown-owner"
        marker.write_bytes(b"do not steal\n")
        os.utime(lock, (1, 1))
        before = self.tree(self.scope)
        self.error("snapshot_busy", lambda: self.capture())
        self.assertEqual(self.tree(self.scope), before)

    def test_unqualified_lost_replaced_or_readonly_backing_fails_without_fallback(self) -> None:
        before = self.tree(self.backups)
        for storage in (replace(self.storage, durability="unqualified"),
                        replace(self.storage, backing_fingerprint="wrong-backing"),
                        replace(self.storage, mount_fingerprint="wrong-mount")):
            with self.subTest(storage=storage):
                code = "storage_unqualified" if storage.durability == "unqualified" else "storage_changed"
                self.error(code, lambda: self.capture(replace(self.request(), storage=storage)))
                self.assertEqual(self.tree(self.backups), before)
        moved = self.base / "detached-backing"
        self.backups.rename(moved)
        self.error("storage_missing", lambda: self.capture())
        self.assertFalse(self.backups.exists())
        self.backups.mkdir()
        self.error("storage_changed", lambda: self.capture())
        self.assertEqual(self.tree(self.backups), {})
        self.backups.rmdir()
        moved.rename(self.backups)
        self.backups.chmod(0o500)
        try:
            self.error("storage_readonly", lambda: self.capture())
        finally:
            self.backups.chmod(0o700)
        self.assertEqual(self.tree(self.backups), before)
        self.assert_complete(self.capture())
        previous = self.locator.read_bytes()
        swaps: list[str] = []
        def replace_backing(event: str, path: Path | None) -> None:
            if event == "after_scope_lock":
                swaps.append(event)
                self.backups.rename(moved)
                self.backups.mkdir()
        self.write("notes.md", b"new content must not reach fallback\n")
        self.error("storage_changed", lambda: self.capture(fault=replace_backing))
        self.assertEqual(swaps, ["after_scope_lock"])
        self.assertEqual(self.tree(self.backups), {})
        self.assertEqual((moved / "projects/project-a/latest.json").read_bytes(), previous)

    def test_dedupe_ignores_timestamps_but_not_modes_deletions_or_git_identity(self) -> None:
        self.init_git()
        request = self.request(history=True)
        first = self.capture(request)
        self.assert_complete(first)
        before = self.tree(self.backups)
        path = self.project / "notes.md"
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        same = self.capture(request)
        self.assertEqual((same.status, same.snapshot_path), ("unchanged", first.snapshot_path))
        self.assertEqual(self.tree(self.backups), before)
        path.chmod(0o755)
        mode = self.capture(request)
        self.assertEqual(mode.status, "complete")
        self.assertNotEqual(mode.snapshot_path, first.snapshot_path)
        path.unlink()
        deleted = self.capture(request)
        self.assertNotEqual(deleted.snapshot_path, mode.snapshot_path)
        self.assertIn("notes.md", self.verify(deleted).deleted_paths)
        self.git("commit", "--allow-empty", "-m", "same files new HEAD")
        changed_head = self.capture(request)
        self.assertNotEqual(changed_head.snapshot_path, deleted.snapshot_path)
        self.assertNotEqual(self.verify(changed_head).head, self.verify(deleted).head)
        partial = self.capture(self.request(history=False))
        self.assertEqual(partial.status, "partial")
        self.assertEqual(partial.history_status, "unprotected")

    def test_retention_defaults_off_and_prunes_only_owned_valid_snapshots_after_complete(self) -> None:
        first = self.capture()
        self.write("notes.md", b"second\n")
        second = self.capture()
        self.assertTrue(self.snapshot(first).exists())
        unknown = self.scope / "snapshots/unowned"
        unknown.mkdir()
        (unknown / "manifest.json").write_bytes(b"not a manifest")
        outside = self.write("other-scope/keep.md", b"keep\n", root=self.backups)
        foreign = self.scope / "snapshots/foreign"
        foreign.symlink_to(outside.parent, target_is_directory=True)
        retained = self.scope / ".staging/unknown-stage"
        retained.mkdir(parents=True)
        (retained / "owner").write_bytes(b"not ours\n")
        settings = replace(self.settings, backup=C.BackupPolicy(enabled=True, prune=True, retention=1))
        self.write("notes.md", b"third\n")
        third = self.capture(self.request(settings=settings))
        self.assert_complete(third)
        self.assertFalse(cast(Path, first.snapshot_path).exists())
        self.assertFalse(cast(Path, second.snapshot_path).exists())
        self.assertTrue(self.snapshot(third).exists())
        self.assertEqual((unknown / "manifest.json").read_bytes(), b"not a manifest")
        self.assertTrue(foreign.is_symlink())
        self.assertEqual(outside.read_bytes(), b"keep\n")
        self.assertEqual((retained / "owner").read_bytes(), b"not ours\n")

    def test_storage_inspection_is_readonly_and_probe_needs_explicit_approval_not_durability_claim(self) -> None:
        before = self.tree(self.backups)
        inspected = self.api.inspect_storage(self.backups)
        self.assertEqual(inspected.durability, "unqualified")
        self.assertEqual((inspected.device, inspected.inode), (self.backups.stat().st_dev, self.backups.stat().st_ino))
        self.assertTrue(inspected.backing_fingerprint)
        self.assertTrue(inspected.mount_fingerprint)
        self.assertEqual(self.tree(self.backups), before)
        self.error("approval_required", lambda: self.api.qualify_storage(inspected))
        self.assertEqual(self.tree(self.backups), before)
        qualification = self.api.qualify_storage(inspected, approval=True)
        self.assertTrue(qualification.readback_verified)
        self.assertFalse(qualification.backing_durability_verified)
        self.assertEqual(qualification.identity.durability, "software-readback")
        self.assertEqual(self.tree(self.backups), before)
        missing = self.base / "never-create"
        self.error("storage_missing", lambda: self.api.inspect_storage(missing))
        self.assertFalse(missing.exists())

    def test_corrupt_locator_and_disabled_policy_do_not_publish_or_rewrite_settings(self) -> None:
        first = self.capture()
        self.assert_complete(first)
        self.locator.write_bytes(b"{broken locator")
        before = self.tree(self.backups)
        self.error("invalid_locator", lambda: self.capture())
        self.assertEqual(self.tree(self.backups), before)
        settings = replace(self.settings, backup=C.BackupPolicy(enabled=False))
        self.error("backup_disabled", lambda: self.capture(self.request(settings=settings)))
        self.assertEqual(self.tree(self.backups), before)
        self.assertFalse((self.project / ".thunderkit/continuity.local.json").exists())


if __name__ == "__main__":
    unittest.main()
