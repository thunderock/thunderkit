"""Bounded, inert snapshots. Software readback is not backing durability.

Only explicit requests authorize writes. Readers never need the source checkout.
The optional fault callback is an in-process testing seam, not deserialized data
or authority. All filesystem access uses anchored no-follow directory handles.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import shutil
import tempfile
import subprocess
import selectors
import time
from typing import cast
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import NoReturn, Protocol, TypeAlias, TypeVar
import uuid

import continuity_config as C

Fault: TypeAlias = Callable[[str, Path | None], None]
_T = TypeVar("_T")


class CleanupGuard(Protocol):
    """Caller-owned cancellation deferral, limited to acquisition and teardown."""

    def __call__(self, *, unwinding: bool) -> AbstractContextManager[None]: ...


_cleanup_guard: ContextVar[CleanupGuard | None] = ContextVar("snapshot_cleanup_guard", default=None)


@contextmanager
def cleanup_scope(guard: CleanupGuard) -> Iterator[None]:
    """Cooperate with an enclosing deadline without owning its timer or budget.

    This in-process hook is not authority. Standalone readers/writers retain
    their normal behavior, and nested scopes restore the previous guard.
    """
    token = None
    failed = False
    try:
        with guard(unwinding=False):
            token = _cleanup_guard.set(guard)
        yield
    except BaseException:
        failed = True
        raise
    finally:
        with guard(unwinding=failed):
            if token is not None:
                _cleanup_guard.reset(token)


def _guard(*, unwinding: bool = False) -> AbstractContextManager[None]:
    guard = _cleanup_guard.get()
    return nullcontext() if guard is None else guard(unwinding=unwinding)


@contextmanager
def _resources() -> Iterator[ExitStack]:
    stack = ExitStack()
    primary: BaseException | None = None
    try:
        yield stack
    except BaseException as error:
        primary = error
        raise
    finally:
        with _guard(unwinding=primary is not None):
            try:
                stack.close()
            except BaseException as cleanup_error:
                if primary is not None:
                    raise primary from cleanup_error
                raise


def _enter(stack: ExitStack, resource: AbstractContextManager[_T]) -> _T:
    # Registration must finish before a deferred deadline can be raised.
    with _guard():
        return stack.enter_context(resource)


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _fail(code: str) -> NoReturn:
    # Neither OS/Git diagnostics nor source contents cross this boundary.
    raise C.ContinuityError(code, "Snapshot operation refused: " + code + ".")


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _directory(path: Path, missing: str = "integrity_mismatch") -> Iterator[int]:
    C.validate_path_text(str(path), absolute=True)
    try:
        with _resources() as stack:
            with _guard():
                fd = os.open(path.anchor, _DIR)
                stack.callback(lambda: os.close(fd))
            for part in path.parts[1:]:
                with _guard():
                    child = os.open(part, _DIR, dir_fd=fd)
                    try:
                        os.close(fd)
                    finally:
                        fd = child
            yield fd
    except FileNotFoundError:
        _fail(missing)
    except OSError as error:
        _fail("unsafe_path" if error.errno in (errno.ELOOP, errno.ENOTDIR) else missing)


@contextmanager
def _child_directory(parent: int, name: str) -> Iterator[int]:
    try:
        with _resources() as stack:
            with _guard():
                fd = os.open(name, _DIR, dir_fd=parent)
                stack.callback(os.close, fd)
            yield fd
    except FileNotFoundError:
        _fail("integrity_mismatch")
    except OSError:
        _fail("unsafe_path")


def _read(parent: int, name: str, budget: int, *,
          changed: str = "integrity_mismatch", fault: Fault | None = None,
          path: Path | None = None) -> tuple[bytes, os.stat_result]:
    with _resources() as stack:
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail("unsafe_path")
            if before.st_size > budget:
                _fail("read_budget_exceeded")
            with _guard():
                fd = os.open(name, _FILE, dir_fd=parent)
                stack.callback(os.close, fd)
            if _signature(os.fstat(fd)) != _signature(before):
                _fail(changed)
            chunks: list[bytes] = []
            remaining = budget + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if fault is not None:
                fault("after_source_read", path)
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _signature(before) != _signature(after) or _signature(before) != _signature(current):
                _fail(changed)
            data = b"".join(chunks)
            if len(data) != before.st_size:
                _fail(changed)
            if changed == "source_changed":
                # Coarse filesystem timestamps can miss same-size rewrites. Compare
                # a bounded second pass on the same FD, then recheck its identity.
                os.lseek(fd, 0, os.SEEK_SET)
                offset = 0
                while offset < len(data):
                    chunk = os.read(fd, min(65536, len(data) - offset))
                    if not chunk or chunk != data[offset:offset + len(chunk)]:
                        _fail(changed)
                    offset += len(chunk)
                if os.read(fd, 1) or _signature(os.fstat(fd)) != _signature(before):
                    _fail(changed)
            return data, before
        except FileNotFoundError:
            _fail(changed)
        except OSError:
            _fail("unsafe_path")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mount_identity(root: Path, device: int) -> tuple[str, str]:
    """Read only the kernel mount table; longest component match, never prefix."""
    try:
        with _resources() as stack:
            with _guard():
                stream = stack.enter_context(open("/proc/self/mountinfo", "rb"))
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            _fail("storage_unqualified")
        matches: list[tuple[int, str, str]] = []
        for line in raw.decode("utf-8", "strict").splitlines():
            left, right = line.split(" - ", 1)
            fields, backing = left.split(), right.split()
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
            mount = Path(point)
            if root.is_relative_to(mount):
                matches.append((len(mount.parts), _hash((str(device) + " " + fields[2] + " " + fields[3] + " " + " ".join(backing[:2])).encode()), _hash(line.encode())))
        if matches:
            _, backing_id, mount_id = max(matches)
            return backing_id, mount_id
    except (OSError, ValueError, IndexError, UnicodeError):
        pass
    # A device number alone cannot attest a portable mount/backing binding.
    return "unavailable-device-" + str(device), "unavailable-mount"


def inspect_storage(root: Path) -> C.StorageIdentity:
    """Zero writes; bind current inode/device and kernel backing/mount identity."""
    with _directory(root, "storage_missing") as fd:
        info = os.fstat(fd)
        backing, mount = _mount_identity(root, info.st_dev)
        return C.StorageIdentity(root, info.st_dev, info.st_ino, backing, mount)


def _check_storage(storage: C.StorageIdentity, *, qualified: bool = True) -> None:
    if qualified and storage.durability not in ("software-readback", "operator-qualified"):
        _fail("storage_unqualified")
    current = inspect_storage(storage.root)
    if replace(storage, durability="unqualified") != current:
        _fail("storage_changed")
    if current.mount_fingerprint == "unavailable-mount":
        _fail("storage_unqualified")
    with _directory(storage.root, "storage_missing") as fd:
        if not os.fstat(fd).st_mode & 0o222 or os.fstatvfs(fd).f_flag & os.ST_RDONLY:
            _fail("storage_readonly")


@dataclass(frozen=True)
class StorageQualification:
    identity: C.StorageIdentity
    readback_verified: bool
    backing_durability_verified: bool


def qualify_storage(storage: C.StorageIdentity, *, approval: bool = False) -> StorageQualification:
    """An approved exclusive fsync/rename/readback probe; never durability proof."""
    if approval is not True:
        _fail("approval_required")
    _check_storage(storage, qualified=False)
    name = ".probe-" + uuid.uuid4().hex
    renamed = name + "-readback"
    owned: dict[str, tuple[int, int]] = {}
    data = os.urandom(32)
    with _directory(storage.root, "storage_missing") as parent:
        try:
            _check_storage(storage, qualified=False)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                owned[name] = (info.st_dev, info.st_ino)
                _check_storage(storage, qualified=False)
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            _check_storage(storage, qualified=False)
            os.rename(name, renamed, src_dir_fd=parent, dst_dir_fd=parent)
            owned[renamed] = owned.pop(name)
            os.fsync(parent)
            if _read(parent, renamed, 32)[0] != data:
                _fail("integrity_mismatch")
            _check_storage(storage, qualified=False)
        except OSError:
            _fail("storage_readonly")
        finally:
            for item, identity in owned.items():
                info = os.stat(item, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == identity:
                    os.unlink(item, dir_fd=parent)
            os.fsync(parent)
    return StorageQualification(replace(storage, durability="software-readback"), True, False)


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _snapshot_path(snapshot: Path) -> Path:
    C.validate_path_text(str(snapshot), absolute=True)
    return snapshot.parent if snapshot.name == "manifest.json" else snapshot


def _layout(manifest: C.BackupManifest) -> dict[str, C.ManifestFile | None]:
    if manifest.history.file not in (None, "history.bundle"):
        _fail("invalid_manifest")
    paths = {entry.relpath for entry in manifest.files}
    for deleted in manifest.deleted_paths:
        if any(p == deleted or p.startswith(deleted + "/") or deleted.startswith(p + "/") for p in paths):
            _fail("invalid_manifest")
    expected: dict[str, C.ManifestFile | None] = {"payload": None}
    for entry in manifest.files:
        name = "payload/" + entry.relpath
        expected[name] = entry
        parts = name.split("/")
        for i in range(1, len(parts)):
            expected.setdefault("/".join(parts[:i]), None)
    return expected


def _verified(snapshot: Path, identity: C.ScopeIdentity, limits: C.Limits,
              select: str | None = None, max_bytes: int | None = None,
              history_requested: bool = False) -> tuple[C.BackupManifest, bytes]:
    path = _snapshot_path(snapshot)
    with _directory(path) as root:
        encoded, _ = _read(root, "manifest.json", C.MANIFEST_MAX_BYTES)
        try:
            raw = C.decode_json(encoded, max_bytes=C.MANIFEST_MAX_BYTES)
            manifest = C.validate_manifest(raw, expected_identity=identity, limits=limits)
        except C.ContinuityError as error:
            if error.code == "invalid_config":
                _fail("invalid_manifest")
            raise
        expected = _layout(manifest)
        root_names = {"manifest.json", "payload"}
        if manifest.history.file is not None:
            root_names.add("history.bundle")
        if set(os.listdir(root)) != root_names:
            _fail("integrity_mismatch")
        selected = b""
        if select is not None:
            entry = next((e for e in manifest.files if e.relpath == select), None)
            if entry is None:
                _fail("payload_not_found")
            bound = limits.max_file_bytes if max_bytes is None else min(limits.max_file_bytes, max_bytes)
            if bound < 0 or entry.size > bound:
                _fail("read_budget_exceeded")
        seen: set[str] = set()

        def walk(fd: int, prefix: str) -> None:
            nonlocal selected
            with _resources() as entries_stack:
                with _guard():
                    entries = entries_stack.enter_context(os.scandir(fd))
                for item in entries:
                    name = prefix + "/" + item.name
                    info = item.stat(follow_symlinks=False)
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        _fail("unsafe_path")
                    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                        _fail("unsafe_path")
                    if name not in expected:
                        _fail("integrity_mismatch")
                    seen.add(name)
                    record = expected[name]
                    if record is None:
                        if not stat.S_ISDIR(info.st_mode):
                            _fail("integrity_mismatch")
                        with _child_directory(fd, item.name) as child:
                            walk(child, name)
                    else:
                        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != record.mode or info.st_size != record.size:
                            _fail("integrity_mismatch")
                        data, _ = _read(fd, item.name, record.size)
                        if _hash(data) != record.sha256:
                            _fail("integrity_mismatch")
                        if record.relpath == select:
                            selected = data
        with _child_directory(root, "payload") as payload:
            seen.add("payload")
            walk(payload, "payload")
        if seen != set(expected):
            _fail("integrity_mismatch")
        if manifest.history.status == "bundled":
            bundle, _ = _read(root, "history.bundle", manifest.history.size)
            if len(bundle) != manifest.history.size or _hash(bundle) != manifest.history.sha256:
                _fail("integrity_mismatch")
            _verify_history(bundle, manifest.history, limits)
            if history_requested:
                selected = bundle
        elif history_requested:
            _fail("history_invalid")
        # Pin the exact manifest and snapshot directory during this read.
        again, _ = _read(root, "manifest.json", C.MANIFEST_MAX_BYTES)
        if again != encoded:
            _fail("integrity_mismatch")
        return manifest, selected


def verify_snapshot(snapshot: Path, identity: C.ScopeIdentity, limits: C.Limits) -> C.BackupManifest:
    """Verify inert metadata, exact membership, bytes/modes and bundled graph."""
    return _verified(snapshot, identity, limits)[0]


def read_payload(snapshot: Path, relpath: str, identity: C.ScopeIdentity, limits: C.Limits,
                 *, max_bytes: int | None = None) -> bytes:
    """Revalidate, then return selected bytes; no prior manifest is a capability."""
    C.validate_relpath(relpath, max_bytes=limits.max_path_bytes)
    return _verified(snapshot, identity, limits, select=relpath, max_bytes=max_bytes)[1]


def read_history(snapshot: Path, identity: C.ScopeIdentity, limits: C.Limits) -> bytes:
    """Return only a revalidated dedicated, self-contained exact-branch bundle."""
    return _verified(snapshot, identity, limits, history_requested=True)[1]


class _Writer:
    def __init__(self, storage: C.StorageIdentity) -> None:
        self.storage = storage

    def check(self) -> None:
        _check_storage(self.storage)

    def mkdir(self, parent: int, name: str, *, exclusive: bool = False) -> None:
        self.check()
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            if exclusive:
                _fail("snapshot_busy")
            with _child_directory(parent, name):
                pass

    def write(self, parent: int, name: str, data: bytes, mode: int = 0o644,
              *, cleanup: ExitStack | None = None) -> None:
        self.check()
        with _resources() as stack:
            with _guard():
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                stack.callback(os.close, fd)
                if cleanup is not None:
                    info = os.fstat(fd)
                    cleanup.callback(_owned_unlink, parent, name, (info.st_dev, info.st_ino))
            view = memoryview(data)
            while view:
                self.check()
                written = os.write(fd, view[:65536])
                if not written:
                    _fail("storage_readonly")
                view = view[written:]
            self.check()
            os.fchmod(fd, mode)
            os.fsync(fd)
        os.fsync(parent)
        readback, info = _read(parent, name, len(data))
        if readback != data or stat.S_IMODE(info.st_mode) != mode:
            _fail("integrity_mismatch")

    def owned_directory(self, parent: int, name: str, stack: ExitStack) -> None:
        self.check()
        with _guard():
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                _fail("snapshot_busy")
            inode = _inode(parent, name)
            stack.callback(_owned_remove, parent, name, inode)
            os.fsync(parent)

    def rename(self, parent: int, name: str, destination: int, target: str) -> None:
        self.check()
        os.rename(name, target, src_dir_fd=parent, dst_dir_fd=destination)
        os.fsync(parent)
        os.fsync(destination)


def _owned_remove(parent: int, name: str, inode: tuple[int, int]) -> None:
    """Cleanup uses the held directory, never a replacement at the root path."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != inode or not stat.S_ISDIR(info.st_mode):
            return
        shutil.rmtree(name, dir_fd=parent)
        os.fsync(parent)
    except FileNotFoundError:
        pass


def _owned_unlink(parent: int, name: str, inode: tuple[int, int]) -> None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == inode and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
    except FileNotFoundError:
        pass


def _inode(parent: int, name: str) -> tuple[int, int]:
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return info.st_dev, info.st_ino


def _event(fault: Fault | None, event: str, path: Path, writer: _Writer) -> None:
    if fault is not None:
        fault(event, path)
    writer.check()


def _preflight(request: C.SnapshotRequest) -> None:
    if not request.settings.backup.enabled:
        _fail("backup_disabled")
    root = request.settings.project_root
    if request.settings.identity.kind == "project" and root is None:
        _fail("unsafe_path")
    for source in request.approved_sources:
        C.safe_path(source, must_exist=True)
        if root is not None and not source.is_relative_to(root):
            _fail("unsafe_path")
        if source.is_relative_to(request.storage.root) or request.storage.root.is_relative_to(source):
            _fail("unsafe_path")
        with _directory(source.parent, "unsafe_path") as parent:
            info = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                _fail("unsafe_path")
    _check_storage(request.storage)


def _prior(scope: Path, identity: C.ScopeIdentity) -> tuple[Path | None, C.BackupManifest | None, str | None]:
    try:
        with _directory(scope) as fd:
            try:
                os.stat("latest.json", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return None, None, None
            raw, _ = _read(fd, "latest.json", C.CONFIG_MAX_BYTES)
            pointer = C.decode_locator(C.decode_json(raw), pointer=True)
            if pointer.identity != identity:
                _fail("invalid_locator")
            path = Path(pointer.path)
            if path.name != "manifest.json" or path.parent.parent != scope / "snapshots":
                _fail("invalid_locator")
            # A new smaller capture budget must not retroactively corrupt a prior
            # valid snapshot. Prior integrity uses bounded schema ceilings.
            limits = C.Limits(max_file_bytes=C.MAXIMUMS["max_file_bytes"],
                              max_snapshot_bytes=C.MAXIMUMS["max_snapshot_bytes"],
                              max_files=C.MAXIMUMS["max_files"])
            manifest = verify_snapshot(path, identity, limits)
            if manifest.completion != "complete" or manifest.snapshot_id != path.parent.name:
                _fail("invalid_locator")
            with _directory(path.parent) as snapshot_fd:
                encoded, _ = _read(snapshot_fd, "manifest.json", C.MANIFEST_MAX_BYTES)
            return path.parent, manifest, _hash(encoded)
    except C.ContinuityError as error:
        if error.code == "scratch_unavailable":
            raise
        _fail("invalid_locator")


_PRIVATE_DIRS = frozenset({".git", ".hermes", ".claude", ".codex", ".ssh", ".aws", ".azure"})
_EXCLUDED = _PRIVATE_DIRS | frozenset({
    "node_modules", "build", "dist", "target", "datasets", "dataset", "backups",
    ".cache", "__pycache__", ".venv", "venv", ".npm", ".mypy_cache",
})
_PRIVATE = frozenset({"auth.json", "credentials.json", "credentials", "id_rsa", "id_ed25519",
                      "id_ecdsa", "sessions.db", "transcript.jsonl", "transcripts.jsonl",
                      "continuity.local.json", "grants.json"})
_SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
                     rb"AKIA[A-Z0-9]{16}|-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|"
                     rb"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{15,}|"
                     rb"(?i:(?:api[_-]?key|api[_-]?token|access[_-]?token|password|secret)"
                     rb"\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{16,}))")


def _private(path: str) -> bool:
    return any(p in _PRIVATE_DIRS or p in _PRIVATE or p == ".env" or p.startswith(".env.")
               or p.endswith((".pem", ".key", ".p12", ".pfx"))
               for p in path.lower().split("/"))


def _forbidden(path: str) -> bool:
    return _private(path) or any(p in _EXCLUDED for p in path.lower().split("/"))


def _omit(omissions: list[str], reason: str, limits: C.Limits) -> None:
    # Fixed deduplicated reasons cannot leak secret-bearing filenames/values.
    if reason not in omissions and len(omissions) < limits.max_files:
        omissions.append(reason)


class _Git:
    """Bounded literal argv only; empty home/hooks/config and no lazy fetch."""
    def __init__(self, scratch: Path) -> None:
        binary = shutil.which("git")
        if binary is None:
            _fail("history_invalid")
        self.binary = binary
        self.scratch = scratch
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(scratch),
            "XDG_CONFIG_HOME": str(scratch), "TMPDIR": str(scratch), "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file", "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ATTR_NOSYSTEM": "1", "GIT_CONFIG_COUNT": "0",
        }
        self.options = ["-c", "core.hooksPath=" + os.devnull, "-c", "credential.helper=",
                        "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                        "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        "-c", "pack.threads=1", "-c", "pack.window=0",
                        "-c", "pack.depth=0", "-c", "core.attributesFile=" + os.devnull]

    def run(self, cwd: Path, args: list[str], budget: int, *, allow_failure: bool = False) -> bytes:
        if sum(len(a.encode()) + 1 for a in args) > 65536:
            _fail("read_budget_exceeded")
        output = bytearray()
        errors = 0
        deadline = time.monotonic() + 30
        try:
            with _resources() as stack:
                with _guard():
                    process = stack.enter_context(subprocess.Popen(
                        [self.binary, *self.options, *args], cwd=cwd, env=self.env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                    stack.callback(_stop_git, process)
                assert process.stdout is not None and process.stderr is not None
                with _guard():
                    poller = stack.enter_context(selectors.DefaultSelector())
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
        except (OSError, subprocess.TimeoutExpired):
            _fail("history_invalid")


def _stop_git(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
        process.wait()


@contextmanager
def _git_session() -> Iterator[_Git]:
    # Scheduler environments often omit TMPDIR. Its absence is not corruption.
    # An explicitly configured unavailable directory must not silently fall back.
    with _resources() as stack:
        try:
            scratch = os.environ.get("TMPDIR") or tempfile.gettempdir()
            C.safe_path(Path(scratch), must_exist=True)
            with _guard():
                temporary = tempfile.TemporaryDirectory(prefix="thunderkit-S-git-", dir=scratch)
                stack.callback(temporary.cleanup)
        except (OSError, C.ContinuityError):
            _fail("scratch_unavailable")
        yield _Git(Path(temporary.name))


def _has_repository(root: Path | None) -> bool:
    if root is None:
        return False
    # No implicit parent repository discovery, no HOME/session search.
    try:
        info = (root / ".git").lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        _fail("unsafe_path")
    return True


def _git_state(request: C.SnapshotRequest, git: _Git | None) -> tuple[str | None, str | None]:
    root = request.settings.project_root
    if not _has_repository(root):
        return None, None
    if git is None or root is None:
        _fail("source_changed")
    head = git.run(root, ["rev-parse", "--verify", "HEAD"], 128, allow_failure=True).strip().decode("ascii")
    if not head:
        return None, None
    C.validate_oid(head)
    ref = git.run(root, ["symbolic-ref", "-q", "HEAD"], 2048, allow_failure=True).strip().decode("utf-8")
    if ref:
        C.validate_ref(ref)
    if request.include_history and (not ref or ref != request.history_ref):
        _fail("history_ref_mismatch")
    return ref or None, head


def _git_paths(git: _Git, root: Path, args: list[str], limits: C.Limits) -> set[str]:
    raw = git.run(root, args, limits.max_files * (limits.max_path_bytes + 1))
    paths: set[str] = set()
    for item in raw.split(b"\0"):
        if item:
            try:
                path = item.decode("utf-8")
                C.validate_relpath(path, max_bytes=limits.max_path_bytes)
            except UnicodeError:
                _fail("unsafe_path")
            paths.add(path)
            if len(paths) > limits.max_files:
                _fail("read_budget_exceeded")
    return paths


def _capture_files(request: C.SnapshotRequest, fault: Fault | None, git: _Git | None,
                   head: str | None) -> tuple[dict[str, bytes], tuple[C.ManifestFile, ...], tuple[str, ...], tuple[str, ...]]:
    root = request.settings.project_root
    limits = request.settings.limits
    payload: dict[str, bytes] = {}
    records: list[C.ManifestFile] = []
    omissions: list[str] = []
    total = 0
    visited = 0
    allowed: set[str] | None = None
    deleted: set[str] = set()
    if root is not None and head is not None:
        if git is None:
            _fail("source_changed")
        try:
            allowed = _git_paths(git, root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], limits)
            deleted = _git_paths(git, root, ["ls-files", "-z", "--deleted"], limits)
            # Index-only enumeration misses paths removed by git rm/mv. Compare
            # the literal working tree to recorded HEAD without filters/hooks.
            deleted |= _git_paths(git, root, [
                "diff-index", "-z", "--name-only", "--no-renames", "--no-ext-diff",
                "--no-textconv", "--diff-filter=D", head, "--",
            ], limits)
        except C.ContinuityError:
            # A bounded scoped walk remains possible, but incomplete enumeration
            # cannot silently claim protection or include ignored files.
            allowed = set()
            _omit(omissions, "Git file inventory exceeds safe bounds.", limits)
    candidates: dict[str, Path] = {}

    def policy_path(path: Path) -> str:
        # Project ancestors are not part of its approved content namespace.
        # Global explicit sources still refuse private/runtime ancestor paths.
        return str(path.relative_to(root)) if root is not None else str(path)

    def note_exclusion(path: Path, relative: str, explicit: bool) -> None:
        name = policy_path(path)
        if explicit:
            _omit(omissions, "Explicit source is excluded by policy.", limits)
        elif not _private(name) and (allowed is None or any(
            p == relative or p.startswith(relative + "/") for p in allowed
        )):
            _omit(omissions, "Approved work was omitted by exclusion policy.", limits)

    def add(path: Path, relative: str, explicit: bool) -> None:
        nonlocal visited
        visited += 1
        if visited > limits.max_files + limits.max_sources:
            _omit(omissions, "File inventory exceeds count budget.", limits)
            return
        try:
            C.validate_relpath(relative, max_bytes=limits.max_path_bytes)
        except C.ContinuityError:
            _omit(omissions, "A path exceeds safe bounds.", limits)
            return
        if _forbidden(policy_path(path)):
            note_exclusion(path, relative, explicit)
            return
        if allowed is not None and not explicit and relative not in allowed and relative not in request.context_entrypoints:
            return
        candidates[relative] = path

    for index, source in enumerate(request.approved_sources):
        prefix = str(source.relative_to(root)) if root is not None else f"sources/{index}/{source.name}"
        explicit = source != root
        if _forbidden(policy_path(source)):
            _omit(omissions, "Explicit source is excluded by policy.", limits)
            continue
        with _directory(source.parent, "unsafe_path") as source_parent:
            info = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                add(source, prefix, True)
                continue
            def walk(fd: int, path: Path, relative: str) -> None:
                nonlocal visited
                with _resources() as entries_stack:
                    with _guard():
                        entries = entries_stack.enter_context(os.scandir(fd))
                    for item in entries:
                        if visited > limits.max_files + limits.max_sources:
                            _omit(omissions, "File inventory exceeds count budget.", limits)
                            break
                        child_path = path / item.name
                        child_relative = str(child_path.relative_to(root)) if root is not None else relative + "/" + item.name
                        item_info = item.stat(follow_symlinks=False)
                        if _forbidden(policy_path(child_path)):
                            note_exclusion(child_path, child_relative, explicit)
                            continue
                        if stat.S_ISDIR(item_info.st_mode):
                            visited += 1
                            if len(child_relative.encode("utf-8", "surrogateescape")) > limits.max_path_bytes:
                                _omit(omissions, "A path exceeds safe bounds.", limits)
                                continue
                            with _child_directory(fd, item.name) as child_fd:
                                walk(child_fd, child_path, child_relative)
                        elif stat.S_ISREG(item_info.st_mode) and item_info.st_nlink == 1:
                            add(child_path, child_relative, explicit)
                        else:
                            _omit(omissions, "Source contains an unsafe node.", limits)
            with _child_directory(source_parent, source.name) as source_fd:
                walk(source_fd, source, prefix)
    for relative, path in sorted(candidates.items()):
        if len(records) >= limits.max_files:
            _omit(omissions, "File inventory exceeds count budget.", limits)
            break
        try:
            with _directory(path.parent, "unsafe_path") as parent:
                for attempt in range(2):
                    try:
                        data, info = _read(parent, path.name, min(limits.max_file_bytes, limits.max_snapshot_bytes - total),
                                           fault=fault, path=path, changed="source_changed")
                        break
                    except C.ContinuityError as error:
                        if error.code != "source_changed" or attempt:
                            raise
                else:
                    _fail("source_changed")
        except C.ContinuityError as error:
            if error.code == "source_changed":
                raise
            _omit(omissions, "File omitted by safety or byte budget.", limits)
            continue
        if _SECRET.search(data):
            _omit(omissions, "Credential-like content was omitted.", limits)
            continue
        total += len(data)
        mode = 0o755 if info.st_mode & 0o111 else 0o644
        payload[relative] = data
        records.append(C.ManifestFile(relative, len(data), _hash(data), mode))
    scoped_deleted: list[str] = []
    if root is not None:
        with _directory(root, "source_changed") as root_fd:
            for relative in sorted(deleted):
                if _forbidden(relative) or not any((root / relative).is_relative_to(s) for s in request.approved_sources):
                    continue
                # An index removal is not necessarily a deleted working file.
                # Recreated bytes and file/directory transitions win; never emit
                # contradictory tombstones beside captured paths.
                if any(relative == p or relative.startswith(p + "/") or p.startswith(relative + "/") for p in payload):
                    continue
                if _absent_worktree_path(root_fd, relative):
                    scoped_deleted.append(relative)
    return payload, tuple(records), tuple(omissions), tuple(scoped_deleted)


def _absent_worktree_path(root: int, relative: str) -> bool:
    """Check presence without following a link, including any replaced ancestor."""
    parts = relative.split("/")
    with _resources() as stack:
        parent = root
        for index, part in enumerate(parts):
            try:
                info = os.stat(part, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return True
            if index == len(parts) - 1 or not stat.S_ISDIR(info.st_mode):
                return False
            parent = _enter(stack, _child_directory(parent, part))
    return False


def _scan_graph(git: _Git, repository: Path, head: str, limits: C.Limits) -> None:
    """Scan objects once for bytes, trees at EVERY path, and all parent commits."""
    objects: dict[str, tuple[str, bytes]] = {}
    total = 0
    path_count = 0
    commits: set[str] = set()
    oid_bytes = len(head) // 2

    def obj(oid: str, expected: str) -> bytes:
        nonlocal total
        C.validate_oid(oid)
        if oid not in objects:
            if len(objects) >= limits.max_files:
                _fail("read_budget_exceeded")
            kind = git.run(repository, ["cat-file", "-t", oid], 32).strip().decode("ascii")
            if kind not in ("commit", "tree", "blob"):
                _fail("history_invalid")
            size_raw = git.run(repository, ["cat-file", "-s", oid], 32).strip()
            if not size_raw.isdigit():
                _fail("history_invalid")
            size = int(size_raw)
            bound = limits.max_file_bytes if kind == "blob" else limits.max_snapshot_bytes
            if size > bound or total + size > limits.max_snapshot_bytes:
                _fail("read_budget_exceeded")
            data = git.run(repository, ["cat-file", kind, oid], size)
            if len(data) != size or _SECRET.search(data):
                _fail("history_invalid")
            objects[oid] = (kind, data)
            total += size
        kind, data = objects[oid]
        if kind != expected:
            _fail("history_invalid")
        return data

    def tree(oid: str, prefix: str, depth: int = 0) -> None:
        nonlocal path_count
        if depth > 128:
            _fail("read_budget_exceeded")
        data = obj(oid, "tree")
        offset = 0
        while offset < len(data):
            end = data.find(b"\0", offset)
            if end < 0 or end + 1 + oid_bytes > len(data):
                _fail("history_invalid")
            metadata = data[offset:end].split(b" ", 1)
            if len(metadata) != 2:
                _fail("history_invalid")
            mode, raw_name = metadata
            try:
                name = raw_name.decode("utf-8")
            except UnicodeError:
                _fail("history_invalid")
            if "/" in name:
                _fail("history_invalid")
            path = prefix + name
            C.validate_relpath(path, max_bytes=limits.max_path_bytes)
            path_count += 1
            if path_count > limits.max_files * 32 or _forbidden(path):
                _fail("history_invalid")
            child = data[end + 1:end + 1 + oid_bytes].hex()
            offset = end + 1 + oid_bytes
            if mode == b"40000":
                tree(child, path + "/", depth + 1)
            elif mode in (b"100644", b"100755"):
                obj(child, "blob")
            else:
                # Submodules and symlink blobs cannot confer file protection.
                _fail("history_invalid")

    pending = [head]
    while pending:
        oid = pending.pop()
        if oid in commits:
            continue
        commits.add(oid)
        data = obj(oid, "commit")
        headers = data.split(b"\n\n", 1)[0].splitlines()
        trees: list[str] = []
        for line in headers:
            if line.startswith((b"tree ", b"parent ")):
                try:
                    target = line.split(b" ", 1)[1].decode("ascii")
                except UnicodeError:
                    _fail("history_invalid")
                C.validate_oid(target)
                if line.startswith(b"tree "):
                    trees.append(target)
                else:
                    pending.append(target)
        if len(trees) != 1:
            _fail("history_invalid")
        tree(trees[0], "")


def _pack_bounds(bundle: bytes, history: C.HistoryRecord, limits: C.Limits) -> None:
    """Bound advertised refs, object count and inflation BEFORE Git imports data."""
    import zlib
    end = bundle.find(b"\n\n", 0, min(len(bundle), 65536))
    if end < 0:
        _fail("history_invalid")
    header = bundle[:end].splitlines()
    if not header or header[0] not in (b"# v2 git bundle", b"# v3 git bundle"):
        _fail("history_invalid")
    advertised: dict[str, str] = {}
    for line in header[1:]:
        if line.startswith(b"@"):
            if line not in (b"@object-format=sha1", b"@object-format=sha256"):
                _fail("history_invalid")
            continue
        if line.startswith(b"-"):
            _fail("history_invalid")
        try:
            oid, ref = line.decode("ascii").split(" ", 1)
        except (ValueError, UnicodeError):
            _fail("history_invalid")
        if ref in advertised or ref not in (history.ref, "HEAD") or oid != history.head:
            _fail("history_invalid")
        advertised[ref] = oid
    if history.ref not in advertised:
        _fail("history_invalid")
    pack = bundle[end + 2:]
    hash_size = 32 if history.head is not None and len(history.head) == 64 else 20
    if len(pack) < 12 + hash_size or pack[:4] != b"PACK" or int.from_bytes(pack[4:8], "big") not in (2, 3):
        _fail("history_invalid")
    count = int.from_bytes(pack[8:12], "big")
    if count > limits.max_files:
        _fail("read_budget_exceeded")
    offset = 12
    inflated = 0
    for _ in range(count):
        if offset >= len(pack) - hash_size:
            _fail("history_invalid")
        byte = pack[offset]
        offset += 1
        kind = (byte >> 4) & 7
        size = byte & 15
        shift = 4
        while byte & 128:
            if offset >= len(pack) - hash_size or shift > 63:
                _fail("history_invalid")
            byte = pack[offset]
            offset += 1
            size |= (byte & 127) << shift
            shift += 7
        if kind not in (1, 2, 3, 4, 6, 7):
            _fail("history_invalid")
        if size > limits.max_snapshot_bytes - inflated or kind == 3 and size > limits.max_file_bytes:
            _fail("read_budget_exceeded")
        if kind == 6:
            # Offset delta base reference; Git verifies graph/offset correctness.
            for i in range(10):
                if offset >= len(pack) - hash_size:
                    _fail("history_invalid")
                byte = pack[offset]
                offset += 1
                if not byte & 128:
                    break
            else:
                _fail("history_invalid")
        elif kind == 7:
            offset += hash_size
        inflater = zlib.decompressobj()
        output_size = 0
        delta_header = bytearray()
        try:
            while not inflater.eof:
                if offset >= len(pack) - hash_size:
                    _fail("history_invalid")
                chunk = pack[offset:min(offset + 65536, len(pack) - hash_size)]
                output = inflater.decompress(chunk, min(65536, size - output_size + 1))
                consumed = len(chunk) - len(inflater.unconsumed_tail) - len(inflater.unused_data)
                offset += consumed
                output_size += len(output)
                if kind in (6, 7) and len(delta_header) < 32:
                    delta_header.extend(output[:32 - len(delta_header)])
                if output_size > size or not consumed and not output:
                    _fail("history_invalid")
        except zlib.error:
            _fail("history_invalid")
        if output_size != size:
            _fail("history_invalid")
        inflated += size
        if kind in (6, 7):
            pos = 0
            result_size = 0
            for field in range(2):
                shift = 0
                value = 0
                while True:
                    if pos >= len(delta_header) or shift > 63:
                        _fail("history_invalid")
                    byte = delta_header[pos]
                    pos += 1
                    value |= (byte & 127) << shift
                    shift += 7
                    if not byte & 128:
                        break
                if field:
                    result_size = value
            inflated += result_size
            if inflated > limits.max_snapshot_bytes:
                _fail("read_budget_exceeded")
    if offset != len(pack) - hash_size:
        _fail("history_invalid")
    digest = hashlib.sha256(pack[:-hash_size]).digest() if hash_size == 32 else hashlib.sha1(pack[:-hash_size]).digest()
    if digest != pack[-hash_size:]:
        _fail("history_invalid")


def _verify_history(bundle: bytes, history: C.HistoryRecord, limits: C.Limits) -> None:
    if history.ref is None or history.head is None:
        _fail("history_invalid")
    try:
        _pack_bounds(bundle, history, limits)
        with _git_session() as git:
            repository = git.scratch / "verify.git"
            repository.mkdir(mode=0o700)
            path = git.scratch / "history.bundle"
            with _resources() as streams:
                with _guard():
                    output = streams.enter_context(path.open("xb"))
                output.write(bundle)
            git.run(repository, ["init", "--bare", "--template=", "--object-format=" + ("sha256" if len(history.head) == 64 else "sha1")], 4096)
            git.run(repository, ["bundle", "verify", str(path)], 65536)
            git.run(repository, ["fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", str(path), history.ref + ":" + history.ref], 65536)
            actual = git.run(repository, ["rev-parse", "--verify", history.ref], 128).strip().decode("ascii")
            if actual != history.head:
                _fail("history_invalid")
            git.run(repository, ["fsck", "--strict", "--no-reflogs", history.head], 65536)
            _scan_graph(git, repository, history.head, limits)
    except C.ContinuityError as error:
        if error.code == "scratch_unavailable":
            raise
        _fail("history_invalid")


def _capture_history(request: C.SnapshotRequest, git: _Git | None, ref: str | None,
                     head: str | None, file_bytes: int) -> tuple[C.HistoryRecord, bytes | None]:
    unprotected = C.HistoryRecord("unprotected" if head else "not-applicable", ref=ref, head=head if ref else None)
    root = request.settings.project_root
    if not request.include_history or root is None or head is None or ref is None:
        return unprotected, None
    if git is None:
        _fail("source_changed")
    limits = request.settings.limits
    try:
        _scan_graph(git, root, head, limits)
        if _git_state(request, git) != (ref, head):
            _fail("source_changed")
        bundle = git.run(root, ["bundle", "create", "-", ref], limits.max_snapshot_bytes - file_bytes)
        if _git_state(request, git) != (ref, head):
            _fail("source_changed")
        history = C.HistoryRecord("bundled", "history.bundle", len(bundle), _hash(bundle), ref, head)
        _verify_history(bundle, history, limits)
        return history, bundle
    except C.ContinuityError as error:
        if error.code in ("source_changed", "history_ref_mismatch"):
            raise
        return unprotected, None


def _payload_write(writer: _Writer, stage: int, payload: dict[str, bytes], files: tuple[C.ManifestFile, ...]) -> None:
    writer.mkdir(stage, "payload")
    with _child_directory(stage, "payload") as payload_fd:
        for entry in files:
            with _resources() as stack:
                parent = payload_fd
                parts = entry.relpath.split("/")
                for part in parts[:-1]:
                    writer.mkdir(parent, part)
                    parent = _enter(stack, _child_directory(parent, part))
                writer.write(parent, parts[-1], payload[entry.relpath], entry.mode)


def _prune(writer: _Writer, snapshots: int, scope: Path, identity: C.ScopeIdentity,
           latest: Path, retention: int) -> None:
    limits = C.Limits(max_file_bytes=C.MAXIMUMS["max_file_bytes"],
                      max_snapshot_bytes=C.MAXIMUMS["max_snapshot_bytes"],
                      max_files=C.MAXIMUMS["max_files"])
    owned: list[tuple[str, str, tuple[int, int]]] = []
    with _resources() as entries_stack:
        with _guard():
            entries = entries_stack.enter_context(os.scandir(snapshots))
        for index, entry in enumerate(entries):
            if index >= C.MAXIMUMS["max_files"]:
                break
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                continue
            path = scope / "snapshots" / entry.name
            try:
                manifest = verify_snapshot(path, identity, limits)
            except C.ContinuityError:
                continue  # Unknown/corrupt entries are never ours to remove.
            if manifest.snapshot_id == entry.name and manifest.completion == "complete":
                owned.append((manifest.timestamp, entry.name, (info.st_dev, info.st_ino)))
    remaining = sorted((item for item in owned if item[1] != latest.name), reverse=True)
    for _, name, inode in remaining[max(0, retention - 1):]:
        writer.check()
        # Revalidate immediately before removing this previously identified tree.
        try:
            manifest = verify_snapshot(scope / "snapshots" / name, identity, limits)
        except C.ContinuityError:
            continue
        if manifest.snapshot_id == name:
            _owned_remove(snapshots, name, inode)


def create_snapshot(request: C.SnapshotRequest, *, fault: Fault | None = None) -> C.SnapshotResult:
    """Capture approved literal bytes; publish a complete locator last."""
    _preflight(request)
    identity = request.settings.identity
    scope = request.storage.root / ("projects" if identity.kind == "project" else "global") / identity.id
    writer = _Writer(request.storage)
    with _resources() as stack:
        git = _enter(stack, _git_session()) if _has_repository(request.settings.project_root) else None
        ref, head = _git_state(request, git)
        root_fd = _enter(stack, _directory(request.storage.root, "storage_missing"))
        group = "projects" if identity.kind == "project" else "global"
        writer.mkdir(root_fd, group)
        group_fd = _enter(stack, _child_directory(root_fd, group))
        writer.mkdir(group_fd, identity.id)
        scope_fd = _enter(stack, _child_directory(group_fd, identity.id))
        previous, previous_manifest, previous_hash = _prior(scope, identity)
        writer.owned_directory(scope_fd, ".snapshot.lock", stack)
        stage_name = uuid.uuid4().hex
        _event(fault, "after_scope_lock", scope, writer)
        payload, files, omissions, deleted = _capture_files(request, fault, git, head)
        history, bundle = _capture_history(request, git, ref, head, sum(f.size for f in files))
        if history.status == "unprotected":
            extra = list(omissions)
            _omit(extra, "Branch history is unprotected.", request.settings.limits)
            omissions = tuple(extra)
        context = tuple(p for p in request.context_entrypoints if p in payload)
        if context != request.context_entrypoints:
            extra = list(omissions)
            _omit(extra, "Requested context was not captured.", request.settings.limits)
            omissions = tuple(extra)
        manifest = C.BackupManifest(1, identity, stage_name, datetime.now(timezone.utc).isoformat(),
                                    str(request.settings.project_root) if head else None, ref, head, head,
                                    files, deleted, context, history, omissions,
                                    "partial" if omissions or not files or history.status == "unprotected" else "complete")
        if previous_manifest is not None and manifest.completion == "complete":
            comparable = replace(manifest, snapshot_id=previous_manifest.snapshot_id, timestamp=previous_manifest.timestamp)
            if comparable == previous_manifest:
                if _git_state(request, git) != (ref, head):
                    _fail("source_changed")
                writer.check()
                return C.SnapshotResult(identity, previous, "unchanged", (), history.status,
                                        previous_hash, scope / "latest.json", previous)
        encoded = _encoded(asdict(manifest))
        C.validate_manifest(C.decode_json(encoded, max_bytes=C.MANIFEST_MAX_BYTES), expected_identity=identity, limits=request.settings.limits)
        writer.mkdir(scope_fd, "snapshots")
        snapshots_fd = _enter(stack, _child_directory(scope_fd, "snapshots"))
        writer.mkdir(scope_fd, ".staging")
        stages_fd = _enter(stack, _child_directory(scope_fd, ".staging"))
        writer.owned_directory(stages_fd, stage_name, stack)
        with _child_directory(stages_fd, stage_name) as stage:
            _payload_write(writer, stage, payload, files)
            if bundle is not None:
                writer.write(stage, "history.bundle", bundle)
            writer.write(stage, "manifest.json", encoded)
        verify_snapshot(scope / ".staging" / stage_name, identity, request.settings.limits)
        _event(fault, "before_manifest_publish", scope, writer)
        if _git_state(request, git) != (ref, head):
            _fail("source_changed")
        writer.rename(stages_fd, stage_name, snapshots_fd, stage_name)
        final = scope / "snapshots" / stage_name
        verify_snapshot(final, identity, request.settings.limits)
        if manifest.completion == "complete":
            _event(fault, "before_locator_publish", scope, writer)
            if _git_state(request, git) != (ref, head):
                _fail("source_changed")
            pointer = {"schema": 1, "kind": "context-pointer", "identity": asdict(identity), "path": str(final / "manifest.json")}
            temporary = ".locator-" + uuid.uuid4().hex
            with _resources() as locator_cleanup:
                writer.write(scope_fd, temporary, _encoded(pointer), cleanup=locator_cleanup)
                writer.rename(scope_fd, temporary, scope_fd, "latest.json")
            observed, _ = _read(scope_fd, "latest.json", C.CONFIG_MAX_BYTES)
            if observed != _encoded(pointer):
                _fail("integrity_mismatch")
            if request.settings.backup.prune:
                _prune(writer, snapshots_fd, scope, identity, final, request.settings.backup.retention)
        return C.SnapshotResult(identity, final, manifest.completion, omissions, manifest.history.status,
                                _hash(encoded), scope / "latest.json" if previous is not None or manifest.completion == "complete" else None,
                                previous)
