"""Recovery trust boundaries through real S captures and R API/CLI calls.

Snapshots and commands remain inert; every destructive fixture action is local.
This file composes the sibling fixture so it can move unchanged into tests/.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_restore_context as fixtures


# File-only recovery needs no child commands or sockets. Run the real CLI main
# in a child with an audit guard; fail even if a denied attempt gets swallowed.
_OFFLINE_CLI = """
import atexit, os, runpy, sys
attempts = []
def guard(event, args):
    if event.startswith(('socket.', 'os.exec', 'os.spawn')) or event in (
        'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty'):
        attempts.append(event)
        raise RuntimeError('Offline recovery attempted transport or execution')
def finish():
    if attempts:
        os._exit(97)
atexit.register(finish)
sys.addaudithook(guard)
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class RestoreBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.RestoreContextTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.attempts: list[str] = []
        self.addCleanup(self.assert_no_transport)
        original_popen = subprocess.Popen
        self.cli_prefix = [sys.executable, "-c", _OFFLINE_CLI, str(fixtures.RESTORE)]

        def deny(*args, **kwargs):
            self.attempts.append("socket")
            raise AssertionError("Recovery must not use sockets")

        def only_cli(args, *positional, **kwargs):
            if (not isinstance(args, list) or args[:4] != self.cli_prefix
                    or kwargs.get("shell", False)):
                self.attempts.append("subprocess")
                raise AssertionError("File-only recovery must not launch other commands")
            return original_popen(args, *positional, **kwargs)

        for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
            self.enterContext(patch.object(socket, name, side_effect=deny))
        self.enterContext(patch.object(subprocess, "Popen", side_effect=only_cli))
        self.fixture.load_api()
        self.api = importlib.import_module("restore_context")
        self.source, self.manifest = self.fixture.snapshot_without_checkout()
        self.destination = self.fixture.work / "recovered"
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def cleanup_fixture(self) -> None:
        cleaned = self.fixture.doCleanups()
        self.assertFalse(self.fixture.base.exists(), "Fixture root survived doCleanups")
        self.assertTrue(cleaned, "Nested fixture cleanup must not hide errors")

    def assert_no_transport(self) -> None:
        self.assertEqual(self.attempts, [], "A refused transport attempt is not zero transport")

    def owned_tree(self) -> dict[str, tuple]:
        """Observe links themselves with lstat/readlink, never traverse them."""
        result = {}
        for parent, directories, files in os.walk(self.fixture.base, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    data = os.readlink(path)
                elif stat.S_ISREG(info.st_mode):
                    data = path.read_bytes()
                else:
                    self.assertTrue(stat.S_ISDIR(info.st_mode), "Unexpected fixture node")
                    data = None
                result[str(path.relative_to(self.fixture.base))] = (
                    info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_mtime_ns, info.st_ctime_ns, data,
                )
        return result

    @contextmanager
    def unchanged(self):
        before = self.owned_tree()
        try:
            yield
        finally:
            self.assertEqual(self.owned_tree(), before, "Refusal must not mutate owned files")
            self.assertFalse(self.fixture.project.exists(), "Do not recreate the source checkout")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.cli_prefix, *args], cwd=self.fixture.work, env=dict(os.environ),
            capture_output=True, text=True, timeout=20,
        )

    def refused_cli(self, code: str, *args: str, private: tuple[str, ...] = ()) -> None:
        process = self.cli(*args)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(process.stdout, "")
        error = json.loads(process.stderr)
        self.assertEqual(set(error), {"error"})
        self.assertEqual(set(error["error"]), {"code", "detail"})
        self.assertEqual(error["error"]["code"], code)
        self.assertTrue(error["error"]["detail"])
        for value in (str(self.fixture.base), *private):
            self.assertNotIn(value, process.stderr, "Raw private input leaked into diagnostics")
        self.assertNotIn("Traceback", process.stderr)

    def refused_api(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(fixtures.C.ContinuityError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.fixture.base), caught.exception.detail)

    def reject_snapshot(self, source: Path, identity, code: str) -> None:
        f = self.fixture
        with self.unchanged():
            self.refused_api(code, self.api.load_context, source, identity, f.limits)
            for operation in ("preview", "export"):
                function = self.api.export_preview if operation == "preview" else self.api.export_snapshot
                approval = {} if operation == "preview" else {
                    "approval": True, "expected_manifest_sha256": self.digest,
                }
                self.refused_api(code, function, source, self.destination, identity, f.limits, **approval)
            for operation in ("load", "preview", "export"):
                args = [operation, "--context", str(source), "--identity", identity.id,
                        "--scope", identity.kind]
                if operation != "load":
                    args += ["--destination", str(self.destination)]
                if operation == "export":
                    args += ["--approve", "--expect-manifest-sha256", self.digest]
                self.refused_cli(code, *args)
        self.assertFalse(self.destination.exists())

    def test_exact_scope_id_and_kind_are_required_by_load_preview_and_export(self) -> None:
        C, S, f = fixtures.C, fixtures.S, self.fixture
        for identity in (C.ScopeIdentity("project", "other-project"),
                         C.ScopeIdentity("global", f.identity.id)):
            with self.subTest(identity=identity):
                self.reject_snapshot(self.source, identity, "identity_mismatch")
        # A genuine global S capture proves the reverse crossing, not a
        # malformed project manifest with its kind merely rewritten.
        document = f.work / "global-context.md"
        document.write_text("Global synthetic context\n")
        identity = C.ScopeIdentity("global", f.identity.id)
        settings = C.ResolvedSettings(
            identity, None, f.backups, None, "unconfigured",
            backup=C.BackupPolicy(enabled=True), approved_sources=(document,),
        )
        storage = S.qualify_storage(S.inspect_storage(f.backups), approval=True).identity
        captured = S.create_snapshot(C.SnapshotRequest(
            settings, storage, (document,), context_entrypoints=("sources/0/global-context.md",),
        ))
        self.assertEqual(captured.status, "complete")
        self.assertIsNotNone(captured.snapshot_path)
        selected = captured.snapshot_path / "manifest.json"
        manifest = S.verify_snapshot(selected, identity, f.limits)
        self.assertEqual(manifest.identity, identity)
        self.assertEqual(self.api.load_context(selected, identity).text, "Global synthetic context\n")
        document.unlink()
        with self.subTest(identity=f.identity, source_kind="global"):
            self.reject_snapshot(selected, f.identity, "identity_mismatch")

    def test_remembered_pointer_requires_exact_scope_without_explicit_fallback(self) -> None:
        C, f = fixtures.C, self.fixture
        pointer = f.home / "pointer.json"
        for kind, scope_id in (("project", "other-project"), ("global", f.identity.id)):
            with self.subTest(pointer_kind=kind, pointer_id=scope_id):
                pointer.write_text(json.dumps({
                    "schema": 1, "kind": "context-pointer",
                    "identity": {"kind": kind, "id": scope_id}, "path": str(self.source),
                }))
                C.validate_continuity_document(C.read_json(pointer), kind="context-pointer")
                with self.unchanged():
                    self.refused_cli("identity_mismatch", "load", "--identity", f.identity.id,
                                     "--memory-pointer", str(pointer))
        pointer.write_text(json.dumps({
            "schema": 1, "kind": "context-pointer",
            "identity": {"kind": "project", "id": f.identity.id}, "path": str(self.source),
        }))
        C.validate_continuity_document(C.read_json(pointer), kind="context-pointer")
        with self.unchanged():
            loaded = self.cli("load", "--identity", f.identity.id, "--memory-pointer", str(pointer))
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(json.loads(loaded.stdout)["text"], fixtures.CONTEXT)
        malformed = f.work / "explicit-invalid.md"
        malformed.write_bytes(b"\xff invalid UTF-8; do not fall back\n")
        with self.unchanged():
            self.refused_cli("invalid_context", "load", "--identity", f.identity.id,
                             "--memory-pointer", str(pointer), "--context", str(malformed))

    def test_literal_traversal_is_rejected_before_path_normalization(self) -> None:
        f = self.fixture
        # These resolve to a valid source / valid empty destination if normalized.
        source = self.source.parent / "payload" / ".." / "manifest.json"
        destination = f.work / ".." / "recovery-work" / "recovered"
        self.assertIn("..", source.parts)
        self.assertIn("..", destination.parts)
        self.reject_snapshot(source, f.identity, "unsafe_path")
        with self.unchanged():
            for operation in ("preview", "export"):
                function = self.api.export_preview if operation == "preview" else self.api.export_snapshot
                approval = {} if operation == "preview" else {
                    "approval": True, "expected_manifest_sha256": self.digest,
                }
                self.refused_api("unsafe_path", function, self.source, destination,
                                 f.identity, f.limits, **approval)
                args = [operation, "--context", str(self.source), "--identity", f.identity.id,
                        "--destination", str(destination)]
                if operation == "export":
                    args += ["--approve", "--expect-manifest-sha256", self.digest]
                self.refused_cli("unsafe_path", *args)

    def test_linked_legacy_and_snapshot_inputs_are_never_followed(self) -> None:
        f = self.fixture
        legacy = f.work / "plain.md"
        legacy.write_text("Synthetic link target\n")
        alias = f.work / "alias.md"
        ancestor = f.work / "linked-snapshot"
        ancestor.symlink_to(self.source.parent, target_is_directory=True)
        with self.subTest(node="snapshot-ancestor-symlink"):
            self.reject_snapshot(ancestor / "manifest.json", f.identity, "unsafe_path")
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(node="legacy", link_kind=link_kind):
                if link_kind == "symlink":
                    alias.symlink_to(legacy)
                    self.assertTrue(stat.S_ISLNK(alias.lstat().st_mode))
                else:
                    os.link(legacy, alias)
                    self.assertEqual(alias.lstat().st_nlink, 2)
                with self.unchanged():
                    self.refused_api("unsafe_path", self.api.load_context, alias, f.identity, f.limits)
                    self.refused_cli("unsafe_path", "load", "--context", str(alias),
                                     "--identity", f.identity.id)
                alias.unlink()
        # Same payload bytes and a still-valid schema, but an unsafe file node.
        for relative in ("manifest.json", "payload/notes/context.md"):
            for link_kind in ("symlink", "hardlink"):
                with self.subTest(node=relative, link_kind=link_kind):
                    clone = f.work / "linked-copy"
                    shutil.copytree(self.source.parent, clone)
                    leaf = clone / relative
                    target = f.work / "owned-link-target"
                    leaf.rename(target)
                    if link_kind == "symlink":
                        leaf.symlink_to(target)
                        self.assertTrue(stat.S_ISLNK(leaf.lstat().st_mode))
                    else:
                        os.link(target, leaf)
                        self.assertEqual(leaf.lstat().st_nlink, 2)
                    self.reject_snapshot(clone / "manifest.json", f.identity, "unsafe_path")
                    shutil.rmtree(clone)
                    target.unlink()

    def test_linked_destination_components_cannot_redirect_export(self) -> None:
        f = self.fixture
        target = f.work / "owned-target"
        target.mkdir()
        ancestor = f.work / "destination-alias"
        ancestor.symlink_to(target, target_is_directory=True)
        leaf = f.work / "destination-leaf"
        leaf.symlink_to(target, target_is_directory=True)
        # Directories cannot be hardlinked; a hardlinked regular file is also
        # forbidden as destination leaf or parent, without touching its twin.
        twin = f.work / "hardlink-twin"
        twin.write_bytes(b"Must not overwrite\n")
        hardlink = f.work / "hardlink-destination"
        os.link(twin, hardlink)
        self.assertEqual(hardlink.lstat().st_nlink, 2)
        for destination in (leaf, ancestor / "new", hardlink, hardlink / "new"):
            with self.subTest(destination=destination.name), self.unchanged():
                for operation in ("preview", "export"):
                    function = self.api.export_preview if operation == "preview" else self.api.export_snapshot
                    approval = {} if operation == "preview" else {
                        "approval": True, "expected_manifest_sha256": self.digest,
                    }
                    self.refused_api("unsafe_path", function, self.source, destination,
                                     f.identity, f.limits, **approval)
                    args = [operation, "--context", str(self.source), "--destination", str(destination),
                            "--identity", f.identity.id]
                    if operation == "export":
                        args += ["--approve", "--expect-manifest-sha256", self.digest]
                    self.refused_cli("unsafe_path", *args)
        self.assertEqual(tuple(target.iterdir()), ())

    def rehashed_copy(self, relative: str, data: bytes) -> tuple[Path, str]:
        """Change a real S capture, then independently prove schema AND bytes."""
        f = self.fixture
        clone = f.work / "rehashed"
        shutil.copytree(self.source.parent, clone)
        selected = clone / "manifest.json"
        raw = json.loads(selected.read_text())
        old = clone / "payload/app.py"
        old.unlink()
        payload = clone / "payload" / relative
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(data)
        payload.chmod(0o644)
        record = {"relpath": relative, "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "mode": 0o644}
        raw["files"] = sorted([record, raw["files"][1]], key=lambda entry: entry["relpath"])
        selected.write_text(json.dumps(raw))
        schema = fixtures.C.read_manifest(selected, expected_identity=f.identity, limits=f.limits)
        verified = fixtures.S.verify_snapshot(selected, f.identity, f.limits)
        self.assertEqual(verified, schema, "Exclusion test must reach policy, not hash/schema refusal")
        self.assertIn(relative, tuple(entry.relpath for entry in verified.files))
        self.assertEqual(fixtures.S.read_payload(selected, relative, f.identity, f.limits), data)
        return selected, hashlib.sha256(selected.read_bytes()).hexdigest()

    def test_valid_rehashed_private_and_runtime_paths_are_not_export_authority(self) -> None:
        f = self.fixture
        for relative in ("nested/.env.local", ".thunderkit/continuity.local.json",
                         ".hermes/sessions.db", "nested/grants.json", "build/output.txt"):
            with self.subTest(path=relative):
                selected, digest = self.rehashed_copy(relative, b"Synthetic excluded marker\n")
                with self.unchanged():
                    for operation in ("preview", "export"):
                        function = self.api.export_preview if operation == "preview" else self.api.export_snapshot
                        approval = {} if operation == "preview" else {
                            "approval": True, "expected_manifest_sha256": digest,
                        }
                        self.refused_api("export_forbidden", function, selected, self.destination,
                                         f.identity, f.limits, **approval)
                        args = [operation, "--context", str(selected), "--identity", f.identity.id,
                                "--destination", str(self.destination)]
                        if operation == "export":
                            args += ["--approve", "--expect-manifest-sha256", digest]
                        self.refused_cli("export_forbidden", *args, private=(relative,))
                    self.assertFalse(self.destination.exists())
                shutil.rmtree(selected.parent)

    def test_rehashed_secret_shaped_bytes_are_refused_after_valid_preview(self) -> None:
        f = self.fixture
        synthetic = b"password=" + b"SYNTHETIC_ONLY_" * 3 + b"\n"
        selected, digest = self.rehashed_copy("app.py", synthetic)
        with self.unchanged():
            preview = self.api.export_preview(selected, self.destination, f.identity, f.limits)
            self.assertEqual(preview.manifest_sha256, digest)
            self.refused_api("export_forbidden", self.api.export_snapshot,
                             selected, self.destination, f.identity, f.limits,
                             approval=True, expected_manifest_sha256=preview.manifest_sha256)
            self.refused_cli("export_forbidden", "export", "--context", str(selected),
                             "--identity", f.identity.id, "--destination", str(self.destination),
                             "--approve", "--expect-manifest-sha256", digest,
                             private=(synthetic.decode().strip(),))
            self.assertFalse(self.destination.exists())

    def test_malformed_cli_arguments_are_sanitized_and_write_nothing(self) -> None:
        f = self.fixture
        marker = "SYNTHETIC_PRIVATE_ARGUMENT"
        common = ["--context", str(self.source), "--identity", f.identity.id]
        export = ["export", *common, "--destination", str(self.destination), "--approve"]
        cases = (
            ("unknown-command", [marker]),
            ("unknown-option", ["load", *common, "--" + marker]),
            ("missing-value", ["load", "--identity", marker, "--context"]),
            ("invalid-choice", ["load", *common, "--scope", marker]),
            ("invalid-identity", ["load", "--context", str(self.source), "--identity", marker + "/bad"]),
            ("non-decimal-budget", ["load", *common, "--max-files", marker]),
            ("signed-budget", ["load", *common, "--max-files", "+2"]),
            ("unicode-budget", ["load", *common, "--max-files", "２"]),
            ("zero-budget", ["load", *common, "--max-files", "0"]),
            ("bad-digest", [*export, "--expect-manifest-sha256", marker]),
            ("uppercase-digest", [*export, "--expect-manifest-sha256", "A" * 64]),
        )
        for name, args in cases:
            with self.subTest(case=name), self.unchanged():
                self.refused_cli("invalid_config", *args, private=(marker,))
                self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
