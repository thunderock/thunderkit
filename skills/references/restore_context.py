"""Read explicitly selected context as inert data, never restored authority.

Snapshot hashes establish content integrity, not origin trust or permission to
execute saved instructions. Loading never recreates the original checkout.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
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
