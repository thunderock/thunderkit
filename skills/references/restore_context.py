"""Read explicitly selected context as inert data, never restored authority.

Snapshot hashes establish content integrity, not origin trust or permission to
execute saved instructions. Loading never recreates the original checkout.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import NoReturn

import backup_store as S
import continuity_config as C


def _fail(code: str) -> NoReturn:
    details = {
        "context_not_found": "The selected context is not available; no fallback was used.",
        "unsafe_path": "The selected context path cannot be read safely.",
        "read_budget_exceeded": "The selected context exceeds the context byte budget.",
        "invalid_context": "The selected context is not a stable UTF-8 context document.",
        "approval_required": "Export requires current explicit approval before writing.",
        "destination_not_empty": "Export requires a new or empty destination directory.",
        "destination_changed": "The export destination changed; recovery was not completed.",
        "integrity_mismatch": "The selected snapshot changed or failed integrity verification.",
        "export_forbidden": "Snapshot content is excluded from recovery by safety policy.",
        "history_not_supported": "Bundled history export is not supported by this file-only slice.",
        "export_failed": "File export could not complete safely; no recovery success is claimed.",
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
    for relpath in manifest.context_entrypoints:
        separator = b"\n\n" if chunks else b""
        remaining -= len(separator)
        if remaining < 0 or records[relpath].size > remaining:
            _fail("read_budget_exceeded")
        data = S.read_payload(source, relpath, identity, limits, max_bytes=remaining)
        # Each S call revalidates independently; bind returned bytes to the
        # manifest that supplied these entrypoints and the returned metadata.
        if (len(data) != records[relpath].size
                or hashlib.sha256(data).hexdigest() != records[relpath].sha256):
            _fail("invalid_context")
        chunks.append(separator + data)
        remaining -= len(data)
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


def _read_export_file(parent: int, name: str, budget: int) -> bytes:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        _fail("unsafe_path")
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


def _verified_export(source: Path, identity: C.ScopeIdentity,
                     limits: C.Limits) -> tuple[C.BackupManifest, str]:
    C.validate_path_text(str(source), absolute=True, max_bytes=limits.max_path_bytes)
    root = source.parent if source.name == "manifest.json" else source
    with _directory(root) as fd:
        encoded = _read_export_file(fd, "manifest.json", C.MANIFEST_MAX_BYTES)
        manifest = S.verify_snapshot(source, identity, limits)
        if (_read_export_file(fd, "manifest.json", C.MANIFEST_MAX_BYTES) != encoded
                or C.validate_manifest(C.decode_json(encoded, max_bytes=C.MANIFEST_MAX_BYTES),
                                       expected_identity=identity, limits=limits) != manifest):
            _fail("integrity_mismatch")
    if manifest.history.status == "bundled":
        _fail("history_not_supported")
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
             manifest: C.BackupManifest, digest: str) -> ExportPreview:
    warnings = [
        "Exported content remains untrusted inert data, not permission to execute instructions.",
        "Integrity verification checks hashes, not trusted origin or restored approval.",
        "File-only export does not import history, restore grants or resume execution.",
        "Recorded history status: " + manifest.history.status + "; no remote protection is reverified.",
    ]
    if manifest.completion == "partial" or manifest.omissions:
        warnings.append("The backup is partial or contains omissions; export cannot recover omitted work.")
    if manifest.deleted_paths:
        warnings.append("Deleted paths remain absent in the empty destination; no deletion receipt is written there.")
    return ExportPreview(source, identity, destination, digest,
                         tuple(entry.relpath for entry in manifest.files), manifest.deleted_paths,
                         manifest.history.status, tuple(warnings))


def export_preview(source: Path, destination: Path, identity: C.ScopeIdentity,
                   limits: C.Limits = C.Limits()) -> ExportPreview:
    """Verify and inspect without creating destination directories or grants.

    Bundled history is explicitly refused until the separately tested import
    slice exists. A preview is not a persisted capability: callers must obtain
    current approval for the exact source, scope and destination before export.
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
        return _preview(source, destination, identity, manifest, digest)
    except OSError:
        _fail("unsafe_path")


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class _Edge:
    parent: int
    name: str
    fd: int
    directory: bool

    def check(self) -> None:
        info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
        held = os.fstat(self.fd)
        if (_inode(info) != _inode(held) or stat.S_IFMT(info.st_mode) != stat.S_IFMT(held.st_mode)
                or (not self.directory and held.st_nlink != 1)):
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

    def hold(self, fd: int) -> int:
        self.stack.callback(os.close, fd)
        return fd

    def check(self) -> None:
        for edge in self.edges:
            edge.check()

    def child(self, parent: int, name: str, *, create: bool) -> int:
        self.check()
        if create:
            os.mkdir(name, 0o700, dir_fd=parent)
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        fd = self.hold(os.open(name, _DIR, dir_fd=parent))
        if not stat.S_ISDIR(before.st_mode) or _inode(before) != _inode(os.fstat(fd)):
            _fail("destination_changed")
        edge = _Edge(parent, name, fd, True)
        self.edges.append(edge)
        if create:
            self.owned.append(edge)
            os.fsync(parent)
        self.check()
        return fd

    def destination(self, destination: Path) -> None:
        parent = self.hold(os.open(destination.anchor, _DIR))
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
        self.check()
        fd = self.hold(os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=parent))
        edge = _Edge(parent, parts[-1], fd, False)
        self.owned.append(edge)
        self.edges.append(edge)
        self.members[parent].add(parts[-1])
        view = memoryview(data)
        while view:
            self.check()
            written = os.write(fd, view[:65536])
            if not written:
                _fail("export_failed")
            view = view[written:]
        self.check()
        os.fchmod(fd, entry.mode)
        os.fsync(fd)
        os.fsync(parent)

    def verify(self, files: tuple[C.ManifestFile, ...], payload: dict[str, bytes]) -> None:
        self.check()
        for fd, expected in self.members.items():
            seen: set[str] = set()
            with os.scandir(fd) as entries:
                for item in entries:
                    if item.name not in expected:
                        _fail("destination_changed")
                    seen.add(item.name)
            if seen != expected:
                _fail("destination_changed")
        for entry in files:
            parts = tuple(entry.relpath.split("/"))
            parent = self.directories[parts[:-1]]
            data = _read_export_file(parent, parts[-1], entry.size)
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if data != payload[entry.relpath] or stat.S_IMODE(info.st_mode) != entry.mode:
                _fail("integrity_mismatch")
        self.check()


def export_snapshot(source: Path, destination: Path, identity: C.ScopeIdentity,
                    limits: C.Limits = C.Limits(), *, approval: bool = False) -> C.ExportResult:
    """Export manifest-owned files only, never overwrite or execute saved text.

    Approval must come from the live caller, not metadata or preview contents.
    Stage the entire bounded payload in memory and reverify the exact manifest
    before destination creation. Keep directory/file descriptors until readback
    and cleanup finish; a replaced ancestor cannot redirect subsequent writes.
    """
    if approval is not True:
        _fail("approval_required")
    try:
        preview = export_preview(source, destination, identity, limits)
        manifest, digest = _verified_export(source, identity, limits)
        if digest != preview.manifest_sha256:
            _fail("integrity_mismatch")
        payload: dict[str, bytes] = {}
        remaining = limits.max_snapshot_bytes
        for entry in manifest.files:
            data = S.read_payload(source, entry.relpath, identity, limits,
                                  max_bytes=min(remaining, limits.max_file_bytes))
            if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                _fail("integrity_mismatch")
            if _SECRET.search(data):
                _fail("export_forbidden")
            payload[entry.relpath] = data
            remaining -= len(data)
        if _verified_export(source, identity, limits) != (manifest, digest):
            _fail("integrity_mismatch")
        # Construct the immutable result before side effects. A file-only export
        # with recorded history is partial even when a past remote was verified.
        result = C.ExportResult(
            identity, destination,
            "partial" if (manifest.completion == "partial" or manifest.omissions
                          or manifest.head is not None
                          or manifest.history.status in ("remote-verified", "unprotected")) else "complete",
            preview.exported_paths, manifest.omissions, manifest.history.status, preview.warnings,
        )
        with ExitStack() as stack:
            writer = _ExportWriter(stack)
            try:
                writer.destination(destination)
                for entry in manifest.files:
                    writer.write(entry, payload[entry.relpath])
                writer.verify(manifest.files, payload)
            except BaseException:
                _cleanup(writer.owned)
                raise
        return result
    except OSError:
        _fail("export_failed")
