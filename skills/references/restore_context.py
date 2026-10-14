"""Read explicitly selected context as inert data, never restored authority.

Snapshot hashes establish content integrity, not origin trust or permission to
execute saved instructions. Loading never recreates the original checkout.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import NoReturn

import backup_store as S
import continuity_config as C


def _fail(code: str) -> NoReturn:
    details = {
        "context_not_found": "The selected context is not available; no fallback was used.",
        "unsafe_path": "The selected context path cannot be read safely.",
        "read_budget_exceeded": "The selected context exceeds the context byte budget.",
        "invalid_context": "The selected context is not a stable UTF-8 context document.",
        "approval_required": "Export requires current explicit approval before reading or writing.",
        "preview_required": "Export requires the exact manifest SHA-256 from the approved preview.",
        "destination_not_empty": "Export requires a new or empty destination directory.",
        "destination_changed": "The export destination changed; recovery was not completed.",
        "integrity_mismatch": "The selected snapshot changed or failed integrity verification.",
        "export_forbidden": "Snapshot content is excluded from recovery by safety policy.",
        "history_invalid": "Bundled history could not be recovered safely.",
        "export_failed": "Export could not complete safely; no recovery success is claimed.",
        "resource_limit": "Export requires more directory descriptors than are safely available.",
        "invalid_config": "Recovery arguments do not satisfy the command schema.",
    }
    raise C.ContinuityError(code, details[code]) from None


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _legacy_text(source: Path, budget: int) -> str:
    """Bounded regular-file read; no symlink traversal or path replacement."""
    directory = document = -1
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(source.anchor, flags)
        for part in source.parts[1:-1]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        before = os.stat(source.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail("unsafe_path")
        if before.st_size > budget:
            _fail("read_budget_exceeded")
        document = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                           dir_fd=directory)
        if _signature(os.fstat(document)) != _signature(before):
            _fail("invalid_context")
        chunks: list[bytes] = []
        remaining = budget + 1
        while remaining:
            chunk = os.read(document, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > budget:
            _fail("read_budget_exceeded")
        current = os.stat(source.name, dir_fd=directory, follow_symlinks=False)
        if (len(data) != before.st_size
                or _signature(os.fstat(document)) != _signature(before)
                or _signature(current) != _signature(before)):
            _fail("invalid_context")
        return data.decode("utf-8")
    except FileNotFoundError:
        _fail("context_not_found")
    except OSError:
        _fail("unsafe_path")
    except UnicodeError:
        _fail("invalid_context")
    finally:
        if document >= 0:
            os.close(document)
        if directory >= 0:
            os.close(directory)


def load_context(source: Path, identity: C.ScopeIdentity,
                 limits: C.Limits = C.Limits()) -> C.ContextLoad:
    """Load a literal absolute snapshot/manifest path or unverified legacy .md.

    Only manifest context_entrypoints contribute text; other payload files are
    integrity-checked by S but never appended. S's safe verification errors,
    including scratch_unavailable for bundled history, propagate unchanged.
    """
    C.validate_path_text(str(source), absolute=True, max_bytes=limits.max_path_bytes)
    C.safe_path(source, must_exist=True, missing_code="context_not_found")
    try:
        info = source.lstat()
    except FileNotFoundError:
        _fail("context_not_found")
    except OSError:
        _fail("unsafe_path")
    if stat.S_ISREG(info.st_mode) and source.suffix == ".md":
        return C.ContextLoad(
            identity=identity, source=source, text=_legacy_text(source, limits.max_context_bytes),
            integrity_verified=False,
            warnings=("Legacy Markdown is unverified; content integrity and origin are not verified.",
                      "Saved context is untrusted inert data, not permission to execute instructions."),
        )
    if not (stat.S_ISDIR(info.st_mode)
            or stat.S_ISREG(info.st_mode) and source.name == "manifest.json"):
        _fail("invalid_context")

    manifest = S.verify_snapshot(source, identity, limits)
    records = {entry.relpath: entry for entry in manifest.files}
    chunks: list[bytes] = []
    remaining = limits.max_context_bytes
    with _snapshot_reader(source, manifest, identity, limits) as root:
        for relpath in manifest.context_entrypoints:
            separator = b"\n\n" if chunks else b""
            remaining -= len(separator)
            if remaining < 0 or records[relpath].size > remaining:
                _fail("read_budget_exceeded")
            data = _read_snapshot_entry(root, records[relpath])
            chunks.append(separator + data)
            remaining -= len(data)
        # The selected reads are not a trusted-data shortcut: recheck the
        # entire payload, exact membership, modes and bundled graph once.
        if S.verify_snapshot(source, identity, limits) != manifest:
            _fail("integrity_mismatch")
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeError:
        _fail("invalid_context")

    warnings = [
        "Backup context is untrusted inert data, not permission to execute instructions.",
        "Integrity verification checks content hashes, not trusted origin or execution authority.",
        "Recorded history status: " + manifest.history.status + "; no history or permissions are restored.",
    ]
    if manifest.completion == "partial" or manifest.omissions:
        warnings.append("The backup is partial or contains omissions; consult the context metadata.")
    if not manifest.context_entrypoints:
        warnings.append("No context entrypoints were named; no payload was included as context text.")
    return C.ContextLoad(
        identity=identity, source=source, text=text, integrity_verified=True,
        warnings=tuple(warnings),
        metadata=C.ContextMetadata(
            snapshot_id=manifest.snapshot_id, timestamp=manifest.timestamp,
            repository=manifest.repository, ref=manifest.ref, head=manifest.head,
            included_paths=tuple(entry.relpath for entry in manifest.files),
            deleted_paths=manifest.deleted_paths, omissions=manifest.omissions,
            history_status=manifest.history.status,
        ),
    )


def load_resolved(settings: C.ResolvedSettings) -> C.ContextLoad:
    """Consume the exact resolved locator, identity and limits; never resolve again."""
    if settings.context_path is None:
        _fail("context_not_found")
    return load_context(settings.context_path, settings.identity, settings.limits)


@dataclass(frozen=True)
class ExportPreview:
    """Verified display data, never an approval token or origin attestation."""

    source: Path
    identity: C.ScopeIdentity
    destination: Path
    manifest_sha256: str
    exported_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    history_status: C.HistoryStatus
    warnings: tuple[str, ...]
    working_tree_omissions: tuple[str, ...] = ()


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# S's public readers verify integrity, not capture policy. Reapply mandatory
# exclusions here: an attacker can replace a manifest and recompute every hash.
_EXCLUDED = frozenset({
    ".git", ".hermes", ".claude", ".codex", ".ssh", ".aws", ".azure",
    "node_modules", "build", "dist", "target", "datasets", "dataset", "backups",
    ".cache", "__pycache__", ".venv", "venv", ".npm", ".mypy_cache",
    "auth.json", "credentials.json", "credentials", "id_rsa", "id_ed25519",
    "id_ecdsa", "sessions.db", "transcript.jsonl", "transcripts.jsonl",
    "continuity.local.json", "grants.json",
})
_SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
                     rb"AKIA[A-Z0-9]{16}|-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|"
                     rb"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{15,}|"
                     rb"(?i:(?:api[_-]?key|api[_-]?token|access[_-]?token|password|secret)"
                     rb"\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{16,}))")


def _check_export_path(relative: str) -> None:
    if any(part in _EXCLUDED or part == ".env" or part.startswith(".env.")
           or part.endswith((".pem", ".key", ".p12", ".pfx"))
           for part in relative.lower().split("/")):
        _fail("export_forbidden")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Read-only no-follow walk; callers never expand or resolve input paths."""
    fd = os.open(path.anchor, _DIR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_export_file(parent: int, name: str, budget: int, *,
                      expected_mode: int | None = None) -> bytes:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        _fail("unsafe_path")
    if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
        _fail("integrity_mismatch")
    if before.st_size > budget:
        _fail("read_budget_exceeded")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        if _signature(os.fstat(fd)) != _signature(before):
            _fail("integrity_mismatch")
        chunks: list[bytes] = []
        remaining = budget + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(data) != before.st_size
                or _signature(os.fstat(fd)) != _signature(before)
                or _signature(current) != _signature(before)):
            _fail("integrity_mismatch")
        return data
    finally:
        os.close(fd)


@contextmanager
def _snapshot_reader(source: Path, manifest: C.BackupManifest,
                     identity: C.ScopeIdentity, limits: C.Limits,
                     digest: str | None = None) -> Iterator[int]:
    """Pin a batch to one snapshot inode and exact manifest, not prior trust."""
    path = source.parent if source.name == "manifest.json" else source
    try:
        with _directory(path) as root:
            held = _inode(os.fstat(root))
            encoded = _read_export_file(root, "manifest.json", C.MANIFEST_MAX_BYTES)
            if (C.validate_manifest(C.decode_json(encoded, max_bytes=C.MANIFEST_MAX_BYTES),
                                    expected_identity=identity, limits=limits) != manifest
                    or digest is not None and hashlib.sha256(encoded).hexdigest() != digest):
                _fail("integrity_mismatch")
            with _directory(path) as current:
                if _inode(os.fstat(current)) != held:
                    _fail("integrity_mismatch")
            yield root
            if _read_export_file(root, "manifest.json", C.MANIFEST_MAX_BYTES) != encoded:
                _fail("integrity_mismatch")
            with _directory(path) as current:
                if _inode(os.fstat(current)) != held:
                    _fail("integrity_mismatch")
    except OSError:
        _fail("integrity_mismatch")


def _read_snapshot_entry(root: int, entry: C.ManifestFile) -> bytes:
    # Only a root, a walking parent and one child/leaf FD are live, regardless
    # of payload count or depth. Final full verification detects source drift.
    parent = os.dup(root)
    try:
        for part in ("payload", *entry.relpath.split("/")[:-1]):
            child = os.open(part, _DIR, dir_fd=parent)
            os.close(parent)
            parent = child
        data = _read_export_file(parent, entry.relpath.split("/")[-1], entry.size,
                                 expected_mode=entry.mode)
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            _fail("integrity_mismatch")
        return data
    finally:
        os.close(parent)


def _verified_export(source: Path, identity: C.ScopeIdentity,
                     limits: C.Limits) -> tuple[C.BackupManifest, str]:
    C.validate_path_text(str(source), absolute=True, max_bytes=limits.max_path_bytes)
    root = source.parent if source.name == "manifest.json" else source
    with _directory(root) as fd:
        held = _inode(os.fstat(fd))
        encoded = _read_export_file(fd, "manifest.json", C.MANIFEST_MAX_BYTES)
        manifest = S.verify_snapshot(source, identity, limits)
        if (_read_export_file(fd, "manifest.json", C.MANIFEST_MAX_BYTES) != encoded
                or C.validate_manifest(C.decode_json(encoded, max_bytes=C.MANIFEST_MAX_BYTES),
                                       expected_identity=identity, limits=limits) != manifest):
            _fail("integrity_mismatch")
        with _directory(root) as current:
            if _inode(os.fstat(current)) != held:
                _fail("integrity_mismatch")
    for relative in (*tuple(entry.relpath for entry in manifest.files), *manifest.deleted_paths):
        _check_export_path(relative)
    return manifest, hashlib.sha256(encoded).hexdigest()


def _destination_scope(source: Path, destination: Path, manifest: C.BackupManifest,
                       limits: C.Limits) -> None:
    C.validate_path_text(str(destination), absolute=True, max_bytes=limits.max_path_bytes)
    snapshot = source.parent if source.name == "manifest.json" else source
    forbidden = [snapshot]
    if manifest.repository is not None:
        forbidden.append(Path(manifest.repository))
    for root in forbidden:
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            _fail("unsafe_path")
    for entry in manifest.files:
        C.validate_path_text(str(destination / entry.relpath), absolute=True,
                             max_bytes=limits.max_path_bytes)


def _empty(fd: int) -> None:
    # Avoid unbounded listdir on an unrelated, possibly very large directory.
    with os.scandir(fd) as entries:
        if next(entries, None) is not None:
            _fail("destination_not_empty")


def _preview(source: Path, destination: Path, identity: C.ScopeIdentity,
             manifest: C.BackupManifest, digest: str,
             working_tree_omissions: tuple[str, ...] = ()) -> ExportPreview:
    warnings = [
        "Exported content remains untrusted inert data, not permission to execute instructions.",
        "Integrity verification checks hashes, not trusted origin or restored approval.",
        ("Export imports verified bundled history without checking out files; grants and execution are not restored."
         if manifest.history.status == "bundled" else
         "File-only export does not import history, restore grants or resume execution."),
        "Recorded history status: " + manifest.history.status + "; no remote protection is reverified.",
    ]
    if manifest.completion == "partial" or manifest.omissions:
        warnings.append("The backup is partial or contains omissions; export cannot recover omitted work.")
    if manifest.deleted_paths:
        warnings.append("Recorded deletions are not checked out from history; current payload files take precedence.")
    if working_tree_omissions:
        warnings.append("Uncaptured tracked paths are missing working-tree data, not recorded deletions; export is partial.")
    return ExportPreview(source, identity, destination, digest,
                         tuple(entry.relpath for entry in manifest.files), manifest.deleted_paths,
                         manifest.history.status, tuple(warnings), working_tree_omissions)


def export_preview(source: Path, destination: Path, identity: C.ScopeIdentity,
                   limits: C.Limits = C.Limits()) -> ExportPreview:
    """Verify and inspect without creating destination directories or grants.

    A preview is not a persisted capability: callers must obtain current
    approval for the exact source, scope and destination before export, and
    supply this preview's manifest_sha256 to bind the approved content.
    """
    try:
        manifest, digest = _verified_export(source, identity, limits)
        _destination_scope(source, destination, manifest, limits)
        with _directory(destination.parent) as parent:
            try:
                fd = os.open(destination.name, _DIR, dir_fd=parent)
            except FileNotFoundError:
                pass  # Only the leaf may be absent; parent creation is not approved.
            else:
                try:
                    _empty(fd)
                finally:
                    os.close(fd)
        omissions: tuple[str, ...] = ()
        if manifest.history.status == "bundled":
            bundle = S.read_history(source, identity, limits)
            with _import_history(bundle, manifest.history) as (git, repository):
                omissions = _working_tree_omissions(manifest, _history_paths(git, repository, limits))
            # Inventory belongs to this exact manifest/history, not a prior
            # verification or a replacement with the same scope identity.
            if _verified_export(source, identity, limits) != (manifest, digest):
                _fail("integrity_mismatch")
        return _preview(source, destination, identity, manifest, digest, omissions)
    except OSError:
        _fail("unsafe_path")


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


@dataclass
class _Edge:
    parent: int
    name: str
    fd: int
    directory: bool
    recorded: os.stat_result

    def check(self) -> None:
        info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
        held = os.fstat(self.fd) if self.fd >= 0 else self.recorded
        if (_inode(info) != _inode(self.recorded) or _inode(held) != _inode(self.recorded)
                or stat.S_IFMT(info.st_mode) != stat.S_IFMT(self.recorded.st_mode)
                or (not self.directory and (info.st_nlink != 1 or held.st_nlink != 1
                    or self.fd < 0 and _signature(info) != _signature(self.recorded)))):
            _fail("destination_changed")


def _cleanup(owned: list[_Edge]) -> None:
    """Remove only recorded inodes; rmdir never consumes unrelated additions."""
    for edge in reversed(owned):
        try:
            edge.check()
            if edge.directory:
                os.rmdir(edge.name, dir_fd=edge.parent)
            else:
                os.unlink(edge.name, dir_fd=edge.parent)
        except (OSError, C.ContinuityError):
            # Replaced paths and nonempty directories belong to the caller.
            # Failure is already reported; cleanup must not broaden its scope.
            continue


class _ExportWriter:
    def __init__(self, stack: ExitStack) -> None:
        self.stack = stack
        self.edges: list[_Edge] = []
        self.owned: list[_Edge] = []
        self.directories: dict[tuple[str, ...], int] = {}
        self.members: dict[int, set[str]] = {}
        self.ancestors: dict[int, _Edge] = {}
        self.reserved: list[int] = []
        self.stack.callback(self.release_reserved)

    def release_reserved(self) -> None:
        while self.reserved:
            os.close(self.reserved.pop())

    def capacity(self, destination: Path, files: tuple[C.ManifestFile, ...]) -> None:
        """Reserve every directory slot plus transient headroom before writes.

        Keep ancestor descriptors pinned, but never discover EMFILE midway
        through creating a deep/wide output tree. No process limits change.
        """
        prefixes = {tuple(entry.relpath.split("/")[:i])
                    for entry in files for i in range(1, len(entry.relpath.split("/")))}
        needed = len(destination.parts) + len(prefixes)
        try:
            for _ in range(needed + 8):
                self.reserved.append(os.open(destination.anchor, _DIR))
        except OSError:
            self.release_reserved()
            _fail("resource_limit")
        for _ in range(8):
            os.close(self.reserved.pop())

    def directory_fd(self, name: str, parent: int | None = None) -> int:
        os.close(self.reserved.pop())
        return self.hold(os.open(name, _DIR, dir_fd=parent))

    def hold(self, fd: int) -> int:
        self.stack.callback(os.close, fd)
        return fd

    def check(self, parent: int | None = None) -> None:
        if parent is None:
            for edge in self.edges:
                edge.check()
        else:
            while parent in self.ancestors:
                edge = self.ancestors[parent]
                edge.check()
                parent = edge.parent

    def child(self, parent: int, name: str, *, create: bool) -> int:
        self.check(parent)
        if create:
            os.mkdir(name, 0o700, dir_fd=parent)
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        edge = _Edge(parent, name, -1, True, before)
        if create:
            self.owned.append(edge)
        fd = self.directory_fd(name, parent)
        edge.fd = fd
        if not stat.S_ISDIR(before.st_mode) or _inode(before) != _inode(os.fstat(fd)):
            _fail("destination_changed")
        self.edges.append(edge)
        self.ancestors[fd] = edge
        if create:
            os.fsync(parent)
        self.check(fd)
        return fd

    def destination(self, destination: Path) -> None:
        parent = self.directory_fd(destination.anchor)
        for part in destination.parts[1:-1]:
            parent = self.child(parent, part, create=False)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            root = self.child(parent, destination.name, create=True)
        else:
            root = self.child(parent, destination.name, create=False)
        _empty(root)
        self.directories[()] = root
        self.members[root] = set()

    def write(self, entry: C.ManifestFile, data: bytes) -> None:
        parts = tuple(entry.relpath.split("/"))
        parent = self.directories[()]
        for i, part in enumerate(parts[:-1], 1):
            prefix = parts[:i]
            if prefix not in self.directories:
                child = self.child(parent, part, create=True)
                self.members[parent].add(part)
                self.directories[prefix] = child
                self.members[child] = set()
            parent = self.directories[prefix]
        self.check(parent)
        fd = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        edge: _Edge | None = None
        try:
            edge = _Edge(parent, parts[-1], fd, False, os.fstat(fd))
            self.owned.append(edge)
            self.edges.append(edge)
            self.members[parent].add(parts[-1])
            view = memoryview(data)
            while view:
                self.check(parent)
                edge.check()
                written = os.write(fd, view[:65536])
                if not written:
                    _fail("export_failed")
                view = view[written:]
            self.check(parent)
            edge.check()
            os.fchmod(fd, entry.mode)
            os.fsync(fd)
            os.fsync(parent)
            self.check(parent)
            edge.check()
        finally:
            try:
                if edge is not None:
                    edge.recorded = os.fstat(fd)
                    edge.fd = -1
            finally:
                os.close(fd)

    def membership(self) -> None:
        for fd, expected in self.members.items():
            seen: set[str] = set()
            with os.scandir(fd) as entries:
                for item in entries:
                    if item.name not in expected:
                        _fail("destination_changed")
                    seen.add(item.name)
            if seen != expected:
                _fail("destination_changed")

    def verify(self, files: tuple[C.ManifestFile, ...], payload: dict[str, bytes]) -> None:
        self.check()
        self.membership()
        for entry in files:
            parts = tuple(entry.relpath.split("/"))
            parent = self.directories[parts[:-1]]
            data = _read_export_file(parent, parts[-1], entry.size, expected_mode=entry.mode)
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if data != payload[entry.relpath] or stat.S_IMODE(info.st_mode) != entry.mode:
                _fail("integrity_mismatch")
        self.check()
        self.membership()


class _HistoryGit(S._Git):
    """Reuse S's private sandbox configuration, not its subprocess lifecycle.

    Encapsulation dependency: S._Git supplies binary/env/options and
    S._git_session supplies owned scratch. Neither is a public S contract.
    This runner additionally kills the entire subprocess group on every exit.
    """

    def run(self, cwd: Path, args: list[str], budget: int, *, allow_failure: bool = False) -> bytes:
        if sum(len(arg.encode()) + 1 for arg in args) > 65536:
            _fail("read_budget_exceeded")
        output = bytearray()
        errors = 0
        deadline = time.monotonic() + 30
        try:
            with subprocess.Popen([self.binary, *self.options, *args], cwd=cwd, env=self.env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, start_new_session=True) as process:
                assert process.stdout is not None and process.stderr is not None
                try:
                    with selectors.DefaultSelector() as poller:
                        poller.register(process.stdout, selectors.EVENT_READ, True)
                        poller.register(process.stderr, selectors.EVENT_READ, False)
                        while poller.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                _fail("history_invalid")
                            for key, _ in poller.select(min(remaining, 0.2)):
                                chunk = os.read(key.fd, 65536)
                                if not chunk:
                                    poller.unregister(key.fileobj)
                                elif key.data:
                                    if len(output) + len(chunk) > budget:
                                        _fail("read_budget_exceeded")
                                    output.extend(chunk)
                                else:
                                    errors += len(chunk)
                                    if errors > 65536:
                                        _fail("history_invalid")
                    code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
                    if code and not allow_failure:
                        _fail("history_invalid")
                    return bytes(output) if code == 0 else b""
                finally:
                    # A parent can exit while a child still holds the pipes.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
        except (OSError, subprocess.TimeoutExpired):
            _fail("history_invalid")


@contextmanager
def _import_history(bundle: bytes, history: C.HistoryRecord) -> Iterator[tuple[_HistoryGit, Path]]:
    """Import exact S-verified bytes for both inventory and metadata export."""
    if (history.ref is None or history.head is None or len(bundle) != history.size
            or hashlib.sha256(bundle).hexdigest() != history.sha256):
        _fail("integrity_mismatch")
    with S._git_session() as sandbox:
        git = _HistoryGit(sandbox.scratch)
        repository = git.scratch / "export.git"
        repository.mkdir(mode=0o700)
        path = git.scratch / "history.bundle"
        with path.open("xb") as stream:
            stream.write(bundle)
            stream.flush()
            os.fsync(stream.fileno())
        with _directory(git.scratch) as scratch_fd:
            if _read_export_file(scratch_fd, path.name, history.size) != bundle:
                _fail("integrity_mismatch")
        object_format = "sha256" if len(history.head) == 64 else "sha1"
        git.run(repository, ["init", "--bare", "--template=", "--object-format=" + object_format], 4096)
        git.run(repository, ["bundle", "verify", str(path)], 65536)
        git.run(repository, ["fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
                             str(path), history.ref + ":" + history.ref], 65536)
        refs = git.run(repository, ["for-each-ref", "--format=%(objectname) %(refname)"], 65536)
        if refs != (history.head + " " + history.ref + "\n").encode("utf-8"):
            _fail("history_invalid")
        if git.run(repository, ["rev-parse", "--verify", history.ref + "^{commit}"], 128).strip() != history.head.encode("ascii"):
            _fail("history_invalid")
        git.run(repository, ["fsck", "--strict", "--no-reflogs", history.head], 65536)
        git.run(repository, ["symbolic-ref", "HEAD", history.ref], 4096)
        yield git, repository


def _history_paths(git: _HistoryGit, repository: Path, limits: C.Limits) -> tuple[str, ...]:
    """One bounded NUL-delimited HEAD inventory; never consult a checkout."""
    raw = git.run(repository, ["ls-tree", "-r", "-z", "--name-only", "HEAD"],
                  limits.max_files * (limits.max_path_bytes + 1))
    if raw and not raw.endswith(b"\0"):
        _fail("history_invalid")
    paths: set[str] = set()
    for item in raw.split(b"\0")[:-1]:
        if len(paths) >= limits.max_files:
            _fail("read_budget_exceeded")
        try:
            path = C.validate_relpath(item.decode("utf-8"), max_bytes=limits.max_path_bytes)
        except UnicodeError:
            _fail("history_invalid")
        _check_export_path(path)
        if path in paths or _SECRET.search(item):
            _fail("history_invalid")
        paths.add(path)
    return tuple(sorted(paths))


def _working_tree_omissions(manifest: C.BackupManifest, tracked: tuple[str, ...]) -> tuple[str, ...]:
    # Literal leaf membership only: a payload/tombstone for `a` or `a/b`
    # cannot explain the loss of a different HEAD leaf during a transition.
    captured = {entry.relpath for entry in manifest.files}
    deleted = set(manifest.deleted_paths)
    return tuple(path for path in tracked if path not in captured and path not in deleted)


def _history_export(bundle: bytes, history: C.HistoryRecord,
                    limits: C.Limits) -> tuple[tuple[C.ManifestFile, ...], dict[str, bytes], tuple[str, ...]]:
    """Stage fresh Git metadata, not a checkout, from S-verified bounded history.

    No Git process opens the destination: the fd-bound writer copies verified
    metadata, including the index, just like the manifest-approved payload.
    """
    files: list[C.ManifestFile] = []
    payload: dict[str, bytes] = {}
    # Pack data plus bounded index/ref/config overhead, not archived payload.
    remaining = limits.max_snapshot_bytes * 2 + limits.max_files * (limits.max_path_bytes + 256) + 65536
    with _import_history(bundle, history) as (git, repository):
        tracked = _history_paths(git, repository, limits)
        assert history.head is not None  # Checked by the shared import path.
        # Populate only the index in this bare staging repository. No -u,
        # checkout, reset or restore can overwrite dirty files/tombstones.
        git.run(repository, ["read-tree", history.head], 4096)
        if (git.run(repository, ["write-tree"], 128)
                != git.run(repository, ["rev-parse", "HEAD^{tree}"], 128)):
            _fail("history_invalid")
        git.run(repository, ["config", "core.bare", "false"], 4096)

        def collect(parent: int, prefix: str, depth: int = 0) -> None:
            nonlocal remaining
            if depth > 128:
                _fail("read_budget_exceeded")
            with os.scandir(parent) as entries:
                for item in entries:
                    relative = prefix + "/" + item.name
                    C.validate_relpath(relative, max_bytes=limits.max_path_bytes)
                    info = item.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(item.name, _DIR, dir_fd=parent)
                        try:
                            if _inode(info) != _inode(os.fstat(child)):
                                _fail("integrity_mismatch")
                            collect(child, relative, depth + 1)
                        finally:
                            os.close(child)
                    else:
                        if len(files) >= limits.max_files + 32:
                            _fail("read_budget_exceeded")
                        data = _read_export_file(parent, item.name, remaining)
                        remaining -= len(data)
                        files.append(C.ManifestFile(relative, len(data), hashlib.sha256(data).hexdigest(), 0o644))
                        payload[relative] = data

        with _directory(repository) as root:
            collect(root, ".git")
    return tuple(files), payload, tracked


def export_snapshot(source: Path, destination: Path, identity: C.ScopeIdentity,
                    limits: C.Limits = C.Limits(), *, approval: bool = False,
                    expected_manifest_sha256: str | None = None) -> C.ExportResult:
    """Export manifest-owned files and bundled history without executing text.

    Approval must come from the live caller, not metadata or preview contents.
    The caller supplies the exact prior preview digest as a content binding,
    never as approval or origin trust; do not normalize or infer that digest.
    Stage the entire bounded payload in memory and reverify the exact manifest
    before destination creation. Keep capacity-reserved directory descriptors
    until readback and cleanup finish; regular-file descriptors close promptly.
    A replaced ancestor cannot redirect subsequent writes.
    """
    if approval is not True:
        _fail("approval_required")
    if expected_manifest_sha256 is None:
        _fail("preview_required")
    if (not isinstance(expected_manifest_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None):
        _fail("invalid_config")
    try:
        preview = export_preview(source, destination, identity, limits)
        if preview.manifest_sha256 != expected_manifest_sha256:
            _fail("integrity_mismatch")
        manifest, digest = _verified_export(source, identity, limits)
        if digest != expected_manifest_sha256:
            _fail("integrity_mismatch")
        payload: dict[str, bytes] = {}
        remaining = limits.max_snapshot_bytes
        with _snapshot_reader(source, manifest, identity, limits, digest) as root:
            for entry in manifest.files:
                if entry.size > min(remaining, limits.max_file_bytes):
                    _fail("read_budget_exceeded")
                data = _read_snapshot_entry(root, entry)
                if _SECRET.search(data):
                    _fail("export_forbidden")
                payload[entry.relpath] = data
                remaining -= len(data)
        git_files: tuple[C.ManifestFile, ...] = ()
        git_payload: dict[str, bytes] = {}
        if manifest.history.status == "bundled":
            bundle = S.read_history(source, identity, limits)
            git_files, git_payload, tracked = _history_export(bundle, manifest.history, limits)
            if _working_tree_omissions(manifest, tracked) != preview.working_tree_omissions:
                _fail("integrity_mismatch")
        final_manifest, final_digest = _verified_export(source, identity, limits)
        if final_manifest != manifest or final_digest != expected_manifest_sha256:
            _fail("integrity_mismatch")
        omissions = tuple(dict.fromkeys((*manifest.omissions, *preview.working_tree_omissions)))
        if preview.working_tree_omissions and (len(omissions) > limits.max_files
                or sum(len(item.encode("utf-8")) + 1 for item in omissions)
                > limits.max_files * (limits.max_path_bytes + 1)):
            _fail("read_budget_exceeded")
        # Bundled history preserves the branch, not uncaptured working files.
        result = C.ExportResult(
            identity, destination,
            "partial" if (manifest.completion == "partial" or omissions
                          or manifest.head is not None and manifest.history.status != "bundled"
                          or manifest.history.status in ("remote-verified", "unprotected")) else "complete",
            preview.exported_paths, omissions, manifest.history.status, preview.warnings,
        )
        with ExitStack() as stack:
            writer = _ExportWriter(stack)
            try:
                writer.capacity(destination, (*manifest.files, *git_files))
                writer.destination(destination)
                for entry in git_files:
                    writer.write(entry, git_payload[entry.relpath])
                for entry in manifest.files:
                    writer.write(entry, payload[entry.relpath])
                writer.verify((*manifest.files, *git_files), {**payload, **git_payload})
            except BaseException:
                _cleanup(writer.owned)
                raise
        return result
    except OSError:
        _fail("export_failed")


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse diagnostics contain raw arguments, which may be private.
        _fail("invalid_config")


def _cli_path(value: str) -> Path:
    # Validate the original token before Path can normalize it. No expansion,
    # cwd inference or remembered-path update is part of this command surface.
    return C.literal_path(value, absolute=True)


def _context_budget(value: str) -> int:
    # Reject floats, booleans, signs and oversized decimal strings before int().
    if (len(value) > len(str(C.MAXIMUMS["max_context_bytes"]))
            or re.fullmatch(r"[0-9]+", value) is None):
        _fail("invalid_config")
    return C.Limits(max_context_bytes=int(value)).max_context_bytes


def _json_path(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError("Unsupported recovery result value.")


def main(argv: list[str] | None = None) -> int:
    """Model-free load/preview/export; saved data never activates or executes work."""
    parser = _SafeParser(
        prog="restore_context.py", allow_abbrev=False,
        description="Model-free recovery of inert context and verified files; no activation or execution.",
        epilog="The default command is load. All paths must be literal absolute paths; no shell or home expansion.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser(
        "load", allow_abbrev=False, help="Read explicit or exact-scope remembered context without writes",
        description="Read inert context without activation or execution. All paths must be literal absolute paths.",
    )
    preview = commands.add_parser(
        "preview", allow_abbrev=False, help="Verify and preview export without writing",
        description="Read-only export preview, not approval. All paths must be literal absolute paths.",
    )
    export = commands.add_parser(
        "export", allow_abbrev=False, help="Export only with fresh --approve and the exact preview digest; never execute recovered files",
        description="Export verified files without activation or execution. All paths must be literal absolute paths.",
    )
    for command in (load, preview, export):
        command.add_argument("--context", type=_cli_path, required=command is not load)
        command.add_argument("--identity", required=command is not load,
                             help="Exact scope ID; load may instead use a bound private identity")
        command.add_argument("--scope", choices=("project", "global"), default="project")
    load.add_argument("--project-root", type=_cli_path, help="Explicit project binding; never inferred from cwd")
    load.add_argument("--global-config", type=_cli_path)
    load.add_argument("--memory-pointer", type=_cli_path, help="Already available matching context-pointer JSON")
    load.add_argument("--max-context-bytes", type=_context_budget,
                      help="Positive decimal read limit within Limits bounds; other policy limits remain unchanged")
    for command in (preview, export):
        command.add_argument("--destination", type=_cli_path, required=True)
    export.add_argument("--approve", action="store_true", default=False,
                        help="Fresh approval for this exact source, scope and new/empty destination only")
    export.add_argument("--expect-manifest-sha256",
                        help="Exact lowercase 64-hex manifest_sha256 from the approved preview; content binding, not approval or origin trust")
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or (arguments[0].startswith("-") and arguments[0] not in ("-h", "--help")):
        arguments.insert(0, "load")
    try:
        args = parser.parse_args(arguments)
        identity = C.ScopeIdentity(args.scope, args.identity) if args.identity is not None else None
        result: C.ContextLoad | ExportPreview | C.ExportResult
        if args.command == "load":
            settings = C.resolve_continuity(
                project_root=args.project_root, global_config=args.global_config,
                explicit_backup_path=args.context, scope=args.scope,
                expected_identity=identity, memory_pointer=args.memory_pointer,
            )
            if args.max_context_bytes is not None:
                settings = replace(settings, limits=replace(settings.limits, max_context_bytes=args.max_context_bytes))
            result = load_resolved(settings)
        else:
            # Export never consults remembered paths, settings, grants or cwd.
            assert identity is not None  # Required by the preview/export parser.
            if args.command == "preview":
                result = export_preview(args.context, args.destination, identity)
            else:
                result = export_snapshot(args.context, args.destination, identity, approval=args.approve,
                                         expected_manifest_sha256=args.expect_manifest_sha256)
        encoded = json.dumps(asdict(result), default=_json_path, ensure_ascii=True, allow_nan=False)
    except C.ContinuityError as error:
        print(json.dumps({"error": {"code": error.code, "detail": error.detail}}, ensure_ascii=True), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"error": {"code": "read_failed", "detail": "Recovery could not access its inputs or destination safely."}}), file=sys.stderr)
        return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
