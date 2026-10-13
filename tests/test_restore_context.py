"""R's first load slice; C/S APIs are consumed unchanged.

The initial RED proves only a missing callable, not the behavior assertions
below it. Real S qualification/capture/readback must succeed before that check.
Export, CLI and adversarial recovery cases belong to later vertical slices.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
import importlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import cast
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
RESTORE = REFERENCES / "restore_context.py"
sys.path.insert(0, str(REFERENCES))
try:
    import continuity_config as C  # pyright: ignore[reportMissingImports]
    import backup_store as S  # pyright: ignore[reportMissingImports]
finally:
    sys.path.pop(0)

MISSING = "Missing R load_context entrypoint: skills/references/restore_context.py"
LoadContext = Callable[[Path, C.ScopeIdentity, C.Limits], C.ContextLoad]
CONTEXT = "# Saved context\nPending: review the café notes.\n"


class RestoreContextTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        temporary = tempfile.TemporaryDirectory(prefix="thunderkit-R-red-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / "source"
        self.backups = self.base / "approved-backups"
        self.home = self.base / "empty-home"
        self.work = self.base / "recovery-work"
        self.scratch = self.base / "scratch"
        for path in (self.project, self.backups, self.home, self.work, self.scratch):
            path.mkdir()
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home),
            "THUNDERKIT_HOME": str(self.home / "thunderkit"),
            "TMPDIR": str(self.scratch), "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file", "GIT_PROTOCOL_FROM_USER": "0",
        }
        replaced = patch.dict(os.environ, environment, clear=True)
        replaced.start()
        self.addCleanup(replaced.stop)
        previous = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, previous)
        self.identity = C.ScopeIdentity("project", "restore-fixture")
        self.limits = C.Limits()

    def load_api(self) -> LoadContext:
        # Deliberate assertion, not a discovery/import error or a skip. Call only
        # after the case's fixture assertions so invalid setup cannot fake RED.
        self.assertTrue(RESTORE.is_file(), MISSING)
        sys.path.insert(0, str(REFERENCES))
        try:
            module = importlib.import_module("restore_context")
        finally:
            sys.path.pop(0)
        load = getattr(module, "load_context", None)
        self.assertTrue(callable(load), MISSING)
        return cast(LoadContext, load)

    def tree(self) -> dict[str, tuple[int, int, int, bytes | None]]:
        """Observe only this test's owned tree; ignore read-induced atime."""
        result: dict[str, tuple[int, int, int, bytes | None]] = {}
        for parent, directories, files in os.walk(self.base, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                info = path.lstat()
                self.assertFalse(stat.S_ISLNK(info.st_mode), "fixture contains a link")
                data = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
                result[str(path.relative_to(self.base))] = (
                    info.st_mode, info.st_mtime_ns, info.st_ctime_ns, data,
                )
        return result

    def snapshot_without_checkout(self) -> tuple[Path, C.BackupManifest]:
        """Real S fixture, also callable independently of the missing R API."""
        notes = self.project / "notes"
        notes.mkdir()
        (notes / "context.md").write_text(CONTEXT, encoding="utf-8")
        (self.project / "app.py").write_text('print("NOT CONTEXT")\n', encoding="utf-8")
        self.assertFalse((self.project / ".git").exists())
        self.assertEqual(tuple(self.home.iterdir()), ())
        self.assertNotIn("SSH_AUTH_SOCK", os.environ)
        qualification = S.qualify_storage(S.inspect_storage(self.backups), approval=True)
        self.assertTrue(qualification.readback_verified)
        self.assertFalse(qualification.backing_durability_verified)
        settings = C.ResolvedSettings(
            identity=self.identity, project_root=self.project, backup_root=self.backups,
            context_path=None, context_source="unconfigured",
            backup=C.BackupPolicy(enabled=True), limits=self.limits,
        )
        source_before = {
            str(path.relative_to(self.project)): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        result = S.create_snapshot(C.SnapshotRequest(
            settings=settings, storage=qualification.identity,
            approved_sources=(self.project,),
            context_entrypoints=("notes/context.md",), include_history=False,
        ))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.omissions, ())
        self.assertEqual(result.history_status, "not-applicable")
        self.assertIsNotNone(result.snapshot_path)
        assert result.snapshot_path is not None
        selected = result.snapshot_path / "manifest.json"
        manifest = S.verify_snapshot(selected, self.identity, self.limits)
        self.assertEqual(manifest.identity, self.identity)
        self.assertEqual(manifest.context_entrypoints, ("notes/context.md",))
        self.assertEqual(tuple(item.relpath for item in manifest.files), ("app.py", "notes/context.md"))
        self.assertEqual(manifest.deleted_paths, ())
        self.assertEqual(manifest.history.status, "not-applicable")
        self.assertEqual(manifest.completion, "complete")
        self.assertEqual(manifest.omissions, ())
        self.assertEqual(S.read_payload(
            selected, "notes/context.md", self.identity, self.limits,
            max_bytes=self.limits.max_context_bytes,
        ), CONTEXT.encode("utf-8"))
        self.assertEqual({
            str(path.relative_to(self.project)): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }, source_before)
        shutil.rmtree(self.project)  # Only the disposable source, never this repo.
        self.assertFalse(self.project.exists())
        self.assertEqual(S.verify_snapshot(selected, self.identity, self.limits), manifest)
        self.assertEqual(tuple(self.scratch.iterdir()), ())
        return selected, manifest

    def test_selected_snapshot_context_loads_after_original_checkout_loss(self) -> None:
        selected, manifest = self.snapshot_without_checkout()
        before = self.tree()
        load = self.load_api()
        result = load(selected, self.identity, self.limits)
        self.assertIsInstance(result, C.ContextLoad)
        self.assertEqual(result.source, selected)
        self.assertEqual(result.identity, self.identity)
        self.assertEqual(result.text, CONTEXT)
        self.assertTrue(result.integrity_verified)
        self.assertEqual(result.metadata, C.ContextMetadata(
            snapshot_id=manifest.snapshot_id, timestamp=manifest.timestamp,
            repository=None, ref=None, head=None,
            included_paths=("app.py", "notes/context.md"), deleted_paths=(),
            omissions=(), history_status="not-applicable",
        ))
        self.assertIsInstance(result.warnings, tuple)
        with self.assertRaises(FrozenInstanceError):
            setattr(result, "integrity_verified", False)
        with self.assertRaises(FrozenInstanceError):
            setattr(result.metadata, "included_paths", ())
        with self.assertRaises(FrozenInstanceError):
            setattr(result.identity, "id", "different-scope")
        self.assertFalse(self.project.exists(), "loading must not recreate the checkout")
        self.assertEqual(self.tree(), before, "loading must not write pointers, grants or recovery state")

    def test_bounded_legacy_markdown_is_unverified_inert_text(self) -> None:
        text = "# Historical handoff\nRésumé: saved instructions are data, not current approval.\n"
        legacy = self.work / "legacy.md"
        legacy.write_text(text, encoding="utf-8")
        limits = C.Limits(max_context_bytes=len(text.encode("utf-8")))
        self.assertEqual(legacy.stat().st_size, limits.max_context_bytes)
        before = self.tree()
        load = self.load_api()
        result = load(legacy, self.identity, limits)
        self.assertIsInstance(result, C.ContextLoad)
        self.assertEqual(result.source, legacy)
        self.assertEqual(result.identity, self.identity)
        self.assertEqual(result.text, text)
        self.assertFalse(result.integrity_verified)
        self.assertEqual(result.metadata, C.ContextMetadata())
        self.assertIsInstance(result.warnings, tuple)
        warnings = " ".join(result.warnings).lower()
        self.assertRegex(warnings, r"unverified|not (?:integrity[- ]?)?verified")
        self.assertRegex(warnings, r"inert|not execut(?:ed|able)|never execut")
        self.assertEqual(self.tree(), before)

    def test_missing_explicit_path_errors_instead_of_using_nearby_context(self) -> None:
        selected = self.work / "missing.md"
        (self.work / "HANDOFF.md").write_text("Must not load nearby handoff.\n", encoding="utf-8")
        (self.home / "context.md").write_text("Must not scan home context.\n", encoding="utf-8")
        self.assertFalse(selected.exists())
        before = self.tree()
        load = self.load_api()
        with self.assertRaises(C.ContinuityError) as caught:
            load(selected, self.identity, self.limits)
        self.assertEqual(caught.exception.code, "context_not_found")
        self.assertEqual(self.tree(), before, "missing explicit context must not trigger fallback writes")

    def test_export_preview_and_approved_files_roundtrip_without_source_checkout(self) -> None:
        selected, manifest = self.snapshot_without_checkout()
        self.load_api()
        module = importlib.import_module("restore_context")
        preview_fn = getattr(module, "export_preview", None)
        export_fn = getattr(module, "export_snapshot", None)
        self.assertTrue(callable(preview_fn), "Missing read-only export preview")
        self.assertTrue(callable(export_fn), "Missing separately approved snapshot export")
        assert callable(preview_fn) and callable(export_fn)
        destination = self.work / "new-recovery"
        before = self.tree()
        preview = preview_fn(selected, destination, self.identity, self.limits)
        self.assertEqual(preview.identity, self.identity)
        self.assertEqual(preview.source, selected)
        self.assertEqual(preview.destination, destination)
        self.assertEqual(preview.exported_paths, ("app.py", "notes/context.md"))
        self.assertEqual(preview.deleted_paths, ())
        self.assertEqual(self.tree(), before, "Preview is read-only")
        with self.assertRaises(C.ContinuityError) as caught:
            export_fn(selected, destination, self.identity, self.limits)
        self.assertEqual(caught.exception.code, "approval_required")
        self.assertFalse(destination.exists())
        result = export_fn(selected, destination, self.identity, self.limits, approval=True)
        self.assertIsInstance(result, C.ExportResult)
        self.assertEqual((result.status, result.destination), ("complete", destination))
        self.assertEqual(result.exported_paths, ("app.py", "notes/context.md"))
        self.assertEqual((destination / "notes/context.md").read_text(encoding="utf-8"), CONTEXT)
        self.assertEqual((destination / "app.py").read_bytes(), b'print("NOT CONTEXT")\n')
        self.assertEqual({str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()},
                         {"app.py", "notes/context.md"})
        self.assertFalse((destination / ".git").exists())
        self.assertEqual(S.verify_snapshot(selected, self.identity, self.limits), manifest)
        self.assertFalse(self.project.exists())
        before = self.tree()
        with self.assertRaises(C.ContinuityError) as caught:
            export_fn(selected, destination, self.identity, self.limits, approval=True)
        self.assertEqual(caught.exception.code, "destination_not_empty")
        self.assertEqual(self.tree(), before, "Existing recovery must not be overwritten")

    def test_export_revalidates_corruption_before_creating_destination(self) -> None:
        selected, _ = self.snapshot_without_checkout()
        self.load_api()
        module = importlib.import_module("restore_context")
        preview_fn = getattr(module, "export_preview", None)
        export_fn = getattr(module, "export_snapshot", None)
        self.assertTrue(callable(preview_fn), "Missing read-only export preview")
        self.assertTrue(callable(export_fn), "Missing separately approved snapshot export")
        assert callable(preview_fn) and callable(export_fn)
        destination = self.work / "new-recovery"
        preview_fn(selected, destination, self.identity, self.limits)
        (selected.parent / "payload/app.py").write_bytes(b"corrupt\n")
        with self.assertRaises(C.ContinuityError) as caught:
            export_fn(selected, destination, self.identity, self.limits, approval=True)
        self.assertEqual(caught.exception.code, "integrity_mismatch")
        self.assertFalse(destination.exists(), "Invalid snapshot cannot create recovery output")
    def test_export_recovers_exact_branch_and_dirty_files_after_checkout_loss(self) -> None:
        template = self.scratch / "empty-template"
        hooks = self.scratch / "empty-hooks"
        template.mkdir()
        hooks.mkdir()
        environment = dict(os.environ)
        environment.update(GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                           GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid")

        def git(*args: str, cwd: Path | None = None) -> str:
            process = subprocess.run(["/usr/bin/git", *args], cwd=cwd or self.project,
                                     env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(process.returncode, 0, "Isolated recovery Git operation failed")
            return process.stdout.strip()

        git("init", "--initial-branch=work", f"--template={template}")
        git("config", "core.hooksPath", str(hooks))
        (self.project / "notes.md").write_text("Committed context\n")
        (self.project / "deleted.txt").write_text("Deleted unfinished file\n")
        (self.project / "run.sh").write_text("#!/bin/sh\nprintf fixture\n")
        git("add", "--all")
        git("commit", "-m", "initial fixture")
        (self.project / "tracked.txt").write_text("Second commit\n")
        git("add", "--all")
        git("commit", "-m", "second fixture")
        head = git("rev-parse", "HEAD")
        graph = git("rev-list", "--parents", "HEAD")
        (self.project / "notes.md").write_text(CONTEXT, encoding="utf-8")
        (self.project / "deleted.txt").unlink()
        (self.project / "run.sh").chmod(0o755)
        (self.project / "untracked.md").write_text("Uncommitted work\n")
        settings = C.ResolvedSettings(self.identity, self.project, self.backups, None, "unconfigured",
                                      backup=C.BackupPolicy(enabled=True))
        storage = S.qualify_storage(S.inspect_storage(self.backups), approval=True).identity
        captured = S.create_snapshot(C.SnapshotRequest(settings, storage, (self.project,),
                                      context_entrypoints=("notes.md",), include_history=True,
                                      history_ref="refs/heads/work"))
        self.assertEqual((captured.status, captured.history_status), ("complete", "bundled"))
        assert captured.snapshot_path is not None
        source = captured.snapshot_path
        manifest = S.verify_snapshot(source, self.identity, self.limits)
        self.assertEqual(manifest.deleted_paths, ("deleted.txt",))
        shutil.rmtree(self.project)
        self.load_api()
        api = importlib.import_module("restore_context")
        destination = self.work / "recovered-history"
        try:
            preview = api.export_preview(source, destination, self.identity, self.limits)
            self.assertEqual(preview.history_status, "bundled")
            result = api.export_snapshot(source, destination, self.identity, self.limits, approval=True)
        except C.ContinuityError as error:
            self.fail("Verified branch history must be recoverable: " + error.code)
        self.assertEqual((result.status, result.history_status), ("complete", "bundled"))
        self.assertEqual(git("rev-parse", "HEAD", cwd=destination), head)
        self.assertEqual(git("symbolic-ref", "HEAD", cwd=destination), "refs/heads/work")
        self.assertEqual(git("rev-list", "--parents", "HEAD", cwd=destination), graph)
        self.assertEqual((destination / "notes.md").read_text(encoding="utf-8"), CONTEXT)
        self.assertFalse((destination / "deleted.txt").exists())
        self.assertEqual((destination / "run.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual((destination / "untracked.md").read_text(), "Uncommitted work\n")
        status = git("status", "--porcelain=v1", cwd=destination)
        self.assertIn("M notes.md", status)
        self.assertIn("D deleted.txt", status)
        self.assertIn("?? untracked.md", status)
        self.assertEqual(git("remote", cwd=destination), "")
        self.assertFalse(self.project.exists())
        self.assertEqual(S.verify_snapshot(source, self.identity, self.limits), manifest)


if __name__ == "__main__":
    unittest.main()
