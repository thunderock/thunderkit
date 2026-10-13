"""Model-free private setup, explicitly approved in-process for one preview.

A receipt records ownership, never authority. Setup captures once and leaves
periodic/ref-event activation and publication disconnected. Saved settings cannot
supply approval to either installer or uninstall.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
import errno
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time
from typing import Literal, NoReturn, TypeAlias

import backup_store as S
import continuity_config as C

SetupStatus: TypeAlias = Literal["approval_required", "blocked", "configured", "removed"]
CapabilityName: TypeAlias = Literal["periodic", "ref_events", "auto_push", "full_target"]
CapabilityStatus: TypeAlias = Literal["supported", "disabled", "blocked"]
_GIT_TIMEOUT = 5.0
_GIT_BYTES = 1024 * 1024
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_LOCK = ".thunderkit-setup.lock"


@dataclass(frozen=True)
class SetupRequest:
    settings: C.ResolvedSettings
    approved_sources: tuple[Path, ...]
    integration_root: Path
    scheduler: str = "none"
    per_commit: bool = False
    context_entrypoints: tuple[str, ...] = ()
    include_history: bool = False
    history_ref: str | None = None


@dataclass(frozen=True)
class SetupMutation:
    path: Path
    operation: Literal["create"]
    mode: int
    content_sha256: str


@dataclass(frozen=True)
class Capability:
    name: CapabilityName
    status: CapabilityStatus
    reason: str


@dataclass(frozen=True)
class SetupFingerprint:
    path: Path
    digest: str
    payload: C.ManifestFile | None = None


@dataclass(frozen=True)
class OwnedPath:
    path: Path
    device: int
    inode: int
    uid: int
    mode: int
    content_sha256: str | None = None


@dataclass(frozen=True)
class InstalledState:
    identity: C.ScopeIdentity
    project_root: Path
    integration_root: Path
    storage: C.StorageIdentity
    manifest_path: Path
    manifest_sha256: str
    files: tuple[OwnedPath, ...]
    directories: tuple[OwnedPath, ...]
    parents: tuple[OwnedPath, ...]
    capabilities: tuple[Capability, ...]


@dataclass(frozen=True)
class SetupPreview:
    request: SetupRequest
    storage: C.StorageIdentity | None
    mutations: tuple[SetupMutation, ...]
    capabilities: tuple[Capability, ...]
    missing_approvals: tuple[str, ...]
    blockers: tuple[str, ...]
    status: SetupStatus
    fingerprints: tuple[SetupFingerprint, ...] = ()


@dataclass(frozen=True)
class SetupResult:
    status: SetupStatus
    changed_paths: tuple[Path, ...] = ()
    receipt: InstalledState | None = None
    blockers: tuple[str, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    retained_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _CapturedScope:
    snapshot: Path
    manifest_sha256: str
    fingerprints: tuple[SetupFingerprint, ...]


def _fail(code: str) -> NoReturn:
    raise C.ContinuityError(code, "Setup inspection refused.")


def _capabilities(request: SetupRequest) -> tuple[Capability, ...]:
    return (
        Capability("periodic", "disabled" if request.scheduler == "none" else "blocked",
                   "not_requested" if request.scheduler == "none" else "unsupported_scheduler"),
        Capability("ref_events", "disabled" if request.per_commit is False else "blocked",
                   "not_requested" if request.per_commit is False else "ref_events_unavailable"),
        Capability("auto_push", "blocked", "fresh_authority_required"),
        Capability("full_target", "blocked", "activation_and_coverage_not_established"),
    )


def _overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _git_environment() -> dict[str, str]:
    # Read-only inspection uses no host/global configuration or transport. These
    # child-only flags neither rewrite settings nor qualify G's verification env.
    env = {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "TMPDIR")
           if key in os.environ}
    env.update({
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_COUNT": "0",
        "GIT_ALLOW_PROTOCOL": "file", "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    })
    return env


def _git_read(project: Path, args: tuple[str, ...]) -> tuple[int, bytes]:
    """Bound time and combined output, without scratch files or raw diagnostics."""
    if os.name != "posix":
        _fail("git_inspection_unavailable")
    binary = shutil.which("git", path="/usr/bin:/bin")
    if binary is None:
        _fail("git_inspection_unavailable")
    deadline = time.monotonic() + _GIT_TIMEOUT
    try:
        with subprocess.Popen((binary, "-c", "core.excludesFile=/dev/null", *args),
                              cwd=project, env=_git_environment(),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, shell=False,
                              start_new_session=True) as process:
            assert process.stdout is not None and process.stderr is not None
            output = bytearray()
            total = 0
            had_errors = False
            try:
                with selectors.DefaultSelector() as poller:
                    poller.register(process.stdout, selectors.EVENT_READ)
                    poller.register(process.stderr, selectors.EVENT_READ)
                    while poller.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            _fail("git_inspection_failed")
                        for key, _ in poller.select(remaining):
                            chunk = os.read(key.fd, min(65536, _GIT_BYTES + 1 - total))
                            if not chunk:
                                poller.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > _GIT_BYTES:
                                _fail("git_inspection_failed")
                            if key.fd == process.stdout.fileno():
                                output.extend(chunk)
                            else:
                                had_errors = True
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                if had_errors or code not in (0, 1):
                    _fail("git_inspection_failed")
                return code, bytes(output)
            except BaseException:
                # Cleanup only; cancellation and the original failure propagate.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=_GIT_TIMEOUT)
                raise
    except (OSError, subprocess.TimeoutExpired):
        _fail("git_inspection_failed")


def _private_git_check(project: Path, private: Path, integration: Path) -> None:
    # No implicit parent/linked repository, include expansion, external ignore
    # file, fsmonitor command, or config-driven repository redirection.
    git_dir = C.safe_path(project / ".git", must_exist=True)
    if not git_dir.is_dir():
        _fail("unsupported_repository")
    code, raw = _git_read(project, ("config", "--null", "--show-origin", "--no-includes", "--list"))
    if code:
        _fail("git_inspection_failed")
    fields = raw.split(b"\0")
    if fields[-1] != b"" or len(fields) % 2 != 1:
        _fail("git_inspection_failed")
    for index in range(0, len(fields) - 1, 2):
        origin, record = fields[index:index + 2]
        key, _, value = record.partition(b"\n")
        key = key.lower()
        # Only this invocation's fixed override is allowed. A repository-declared
        # excludesFile remains refused even when overridden on the command line.
        if (origin, key, value) == (b"command line:", b"core.excludesfile", b"/dev/null"):
            continue
        if (not origin.startswith(b"file:") or not key
                or key.startswith((b"include.", b"includeif.", b"extensions."))
                or key in (b"core.fsmonitor", b"core.worktree", b"core.excludesfile")
                or (key == b"core.bare" and value.lower() != b"false")):
            _fail("unsupported_repository")
    for args, expected in (
        (("rev-parse", "--show-toplevel"), project),
        (("rev-parse", "--absolute-git-dir"), git_dir),
    ):
        code, raw = _git_read(project, args)
        if code or raw != str(expected).encode("utf-8") + b"\n":
            _fail("identity_mismatch")
    private_rel = private.relative_to(project).as_posix()
    integration_rel = integration.relative_to(project).as_posix()
    code, tracked = _git_read(project, ("ls-files", "-z", "--", private_rel, integration_rel))
    if code:
        _fail("git_inspection_failed")
    if tracked:
        _fail("private_storage_tracked")
    for path in (private_rel, integration_rel + "/config.json"):
        code, _ = _git_read(project, ("check-ignore", "--quiet", "--", path))
        if code:
            _fail("private_storage_unignored")


def _new_private_path(path: Path, project: Path) -> None:
    C.safe_path(path, root=project)
    if path.exists():
        _fail("private_storage_exists")
    # A future create must use owned, non-shared parents. No mkdir/write probe.
    for parent in path.parents:
        if not parent.is_relative_to(project):
            break
        if parent.exists():
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022):
                _fail("unsafe_path")
            if not info.st_mode & 0o200 or os.statvfs(parent).f_flag & os.ST_RDONLY:
                _fail("private_storage_readonly")


def _encoded(document: C.JsonObject) -> bytes:
    content = json.dumps(document, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    C.decode_json(content, max_bytes=C.CONFIG_MAX_BYTES)
    return content


def _mutation(path: Path, document: C.JsonObject) -> SetupMutation:
    return SetupMutation(path, "create", 0o600, hashlib.sha256(_encoded(document)).hexdigest())


def _documents(request: SetupRequest, manifest: Path | None = None) -> tuple[C.JsonObject, C.JsonObject]:
    settings = request.settings
    document: C.JsonObject = {
        "schema": 1, "kind": "project-settings",
        "identity": {"kind": settings.identity.kind, "id": settings.identity.id},
        "project_root": str(settings.project_root), "backup_root": str(settings.backup_root),
    }
    if manifest is not None:
        document["latest_context"] = {
            "identity": {"kind": settings.identity.kind, "id": settings.identity.id},
            "path": str(manifest),
        }
        settings = replace(settings, context_path=manifest, context_source="project")
    C.validate_continuity_document(document, kind="project-settings")
    # C owns settings validation. The inert integration envelope has no C schema
    # kind of its own; its nested settings are constructed from C's typed API.
    integration_document: C.JsonObject = {
        "schema": 1, "kind": "checkpoint-settings", "settings": C.settings_to_json(settings),
        "approved_sources": [str(path) for path in request.approved_sources],
        "scheduler": request.scheduler, "per_commit": request.per_commit,
        "context_entrypoints": list(request.context_entrypoints),
        "include_history": request.include_history, "history_ref": request.history_ref,
    }
    return document, integration_document


def _scope_path(request: SetupRequest) -> Path:
    assert request.settings.backup_root is not None
    return request.settings.backup_root / "projects" / request.settings.identity.id


def _inspect(request: SetupRequest, captured: _CapturedScope | None = None
             ) -> tuple[C.StorageIdentity, tuple[SetupMutation, ...]]:
    settings = replace(request.settings)
    # Reuse C validation; no resolver re-read of model/global configuration.
    replace(settings.identity)
    replace(settings.backup)
    replace(settings.git)
    replace(settings.limits)
    if type(request.per_commit) is not bool or not isinstance(request.scheduler, str):
        _fail("invalid_config")
    if settings.identity.kind != "project" or settings.project_root is None:
        _fail("unsupported_scope")
    if settings.backup_root is None:
        _fail("storage_missing")
    if not settings.backup.enabled:
        _fail("backup_disabled")
    project = C.safe_path(settings.project_root, must_exist=True)
    if not project.is_dir():
        _fail("unsafe_path")
    if not isinstance(request.integration_root, Path):
        _fail("unsafe_path")
    C.validate_path_text(str(request.integration_root), absolute=True,
                         max_bytes=settings.limits.max_path_bytes)
    integration = C.safe_path(request.integration_root, root=project / ".thunderkit")
    # Git's allowed read argv has no literal-pathspec switch. Refuse wildcard
    # syntax rather than treating a pattern match as proof for one private path.
    if any(char in integration.relative_to(project).as_posix() for char in "*?["):
        _fail("unsafe_path")
    private = project / ".thunderkit/continuity.local.json"
    reserved = (private, project / ".thunderkit/config.json", project / ".thunderkit/continuity.json")
    if any(_overlap(integration, path) for path in reserved):
        _fail("unsafe_path")
    backup = C.safe_path(settings.backup_root, must_exist=True, missing_code="storage_missing")
    if _overlap(backup, project) or _overlap(backup, integration):
        _fail("unsafe_path")
    storage = S.inspect_storage(backup)
    scope = C.safe_path(_scope_path(request))
    if captured is None:
        if scope.exists():
            _fail("scope_exists")
    elif _scope_fingerprints(request, captured.snapshot, captured.manifest_sha256) != captured.fingerprints:
        _fail("scope_changed")
    C.SnapshotRequest(settings, storage, request.approved_sources, request.context_entrypoints,
                      request.include_history, request.history_ref)
    if not request.approved_sources:
        _fail("sources_required")
    for source in request.approved_sources:
        C.validate_path_text(str(source), absolute=True, max_bytes=settings.limits.max_path_bytes)
        C.safe_path(source, root=project, must_exist=True)
        # Ancestor approval is not approval of excluded descendants. S owns the
        # mandatory payload policy; explicit private/runtime sources still fail.
        if (_source_excluded(request, source)
                or _overlap(source, backup)):
            _fail("unsafe_path")
        info = source.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            _fail("unsafe_path")
    info = backup.lstat()
    if not info.st_mode & 0o222 or os.statvfs(backup).f_flag & os.ST_RDONLY:
        _fail("storage_readonly")
    for path in (private, integration / "config.json"):
        _new_private_path(path, project)
    if integration.exists():
        with _directory(integration) as fd:
            with os.scandir(fd) as entries:
                if next(entries, None) is not None:
                    _fail("private_storage_exists")
    _private_git_check(project, private, integration)
    if S.inspect_storage(backup) != storage:
        _fail("storage_changed")
    document, integration_document = _documents(request)
    return storage, (_mutation(private, document),
                     _mutation(integration / "config.json", integration_document))


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    C.validate_path_text(str(path), absolute=True)
    fd = os.open(path.anchor, _DIR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(parent: int, name: str, budget: int) -> tuple[bytes, os.stat_result]:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > budget:
        _fail("unsafe_path")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        if _signature(os.fstat(fd)) != _signature(before):
            _fail("scope_changed")
        chunks: list[bytes] = []
        remaining = budget + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if (len(data) != before.st_size or _signature(os.fstat(fd)) != _signature(before)
                or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(before)):
            _fail("scope_changed")
        return data, before
    finally:
        os.close(fd)


def _owned(path: Path, info: os.stat_result, digest: str | None = None) -> OwnedPath:
    return OwnedPath(path, info.st_dev, info.st_ino, info.st_uid, info.st_mode, digest)


def _source_excluded(request: SetupRequest, path: Path) -> bool:
    project = request.settings.project_root
    assert project is not None
    return (path.is_relative_to(request.integration_root)
            or path.is_relative_to(project / _LOCK)
            or S._forbidden(path.relative_to(project).as_posix()))


def _fingerprints(request: SetupRequest) -> tuple[SetupFingerprint, ...]:
    """Bound source bytes and config/ignore/hooks state, not only path strings."""
    project = request.settings.project_root
    assert project is not None
    limits = request.settings.limits
    remaining = limits.max_snapshot_bytes + 4 * _GIT_BYTES
    count = 0
    result: list[SetupFingerprint] = []

    def visit(path: Path, recursive: bool = False, source: bool = False) -> None:
        nonlocal remaining, count
        if source and _source_excluded(request, path):
            return
        count += 1
        if count > limits.max_files + limits.max_sources + 100:
            _fail("inspection_budget_exceeded")
        C.validate_path_text(str(path), absolute=True, max_bytes=limits.max_path_bytes)
        C.safe_path(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            result.append(SetupFingerprint(path, "missing"))
            return
        if stat.S_ISDIR(info.st_mode):
            # Only the project root is affected by our own setup lock. Nested
            # membership changes must remain detectable even after deletion.
            identity: tuple[int, ...] = (info.st_dev, info.st_ino, info.st_uid, info.st_mode)
            if path != project:
                identity += (info.st_mtime_ns, info.st_ctime_ns)
            data = repr(identity).encode()
            result.append(SetupFingerprint(path, hashlib.sha256(data).hexdigest()))
            if recursive:
                with _directory(path) as fd:
                    with os.scandir(fd) as entries:
                        names: list[str] = []
                        for entry in entries:
                            names.append(entry.name)
                            if len(names) + count > limits.max_files + limits.max_sources + 100:
                                _fail("inspection_budget_exceeded")
                    for name in sorted(names):
                        visit(path / name, True, source)
            return
        with _directory(path.parent) as fd:
            content, info = _read_file(fd, path.name, min(remaining, max(limits.max_file_bytes, _GIT_BYTES)))
        remaining -= len(content)
        payload = None if not source else C.ManifestFile(
            path.relative_to(project).as_posix(), len(content), hashlib.sha256(content).hexdigest(),
            0o755 if info.st_mode & 0o111 else 0o644)
        result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(info)).encode() + content).hexdigest(),
                                       payload))

    paths = {project, project / ".git"}
    for target in (project / ".thunderkit/continuity.local.json", request.integration_root / "config.json"):
        paths.add(target)
        paths.update(parent for parent in target.parents if parent.is_relative_to(project))
    for path in sorted(paths):
        visit(path)
    for relative in (".gitignore", ".thunderkit/config.json", ".thunderkit/continuity.json",
                     ".git/config", ".git/info/exclude", ".git/index", ".git/HEAD", ".git/packed-refs"):
        visit(project / relative)
    visit(project / ".git/refs", True)
    hooks = project / ".git/hooks"
    code, raw = _git_read(project, ("config", "--null", "--show-origin", "--no-includes", "--list"))
    if code:
        _fail("git_inspection_failed")
    for record in raw.split(b"\0")[1::2]:
        key, _, value = record.partition(b"\n")
        if key.lower() == b"core.hookspath":
            try:
                hooks = C.literal_path(value.decode("utf-8"), base=project)
            except UnicodeError:
                _fail("unsupported_repository")
            if not hooks.is_relative_to(project):
                _fail("unsupported_repository")
    visit(hooks, True)
    for source in request.approved_sources:
        visit(source, True, True)
    return tuple(result)


def preview_setup(request: SetupRequest) -> SetupPreview:
    """Inspect literal scope and private targets; never capture, probe or install."""
    return _preview_setup(request)


def _preview_setup(request: SetupRequest, captured: _CapturedScope | None = None) -> SetupPreview:
    capabilities = _capabilities(request)
    blockers: list[str] = []
    if request.scheduler != "none":
        blockers.append("unsupported_scheduler")
    if request.per_commit is not False:
        blockers.append("ref_events_unavailable")
    storage: C.StorageIdentity | None = None
    mutations: tuple[SetupMutation, ...] = ()
    fingerprints: tuple[SetupFingerprint, ...] = ()
    try:
        storage, mutations = _inspect(request, captured)
        fingerprints = _fingerprints(request)
    except C.ContinuityError as error:
        blockers.append(error.code)
    except OSError:
        blockers.append("inspection_failed")
    if blockers:
        # Never offer even a subset of writes from an unsafe/unsupported plan.
        return SetupPreview(request, storage, (), capabilities, (),
                            tuple(dict.fromkeys(blockers)), "blocked")
    return SetupPreview(request, storage, mutations, capabilities,
                        ("setup_write",), (), "approval_required", fingerprints)


def _check_directory(path: Path, info: os.stat_result) -> OwnedPath:
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        _fail("unsafe_path")
    return _owned(path, info)


@contextmanager
def _setup_lock(project: Path) -> Iterator[None]:
    with _directory(project) as parent:
        _check_directory(project, os.fstat(parent))
        try:
            os.mkdir(_LOCK, 0o700, dir_fd=parent)
        except FileExistsError:
            _fail("setup_locked")
        owned = _owned(project / _LOCK, os.stat(_LOCK, dir_fd=parent, follow_symlinks=False))
        try:
            os.fsync(parent)
            yield
        finally:
            try:
                info = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
                if _owned(project / _LOCK, info) != owned:
                    _fail("cleanup_unresolved")
                os.rmdir(_LOCK, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                _fail("cleanup_unresolved")


def _prepare_parent(path: Path, project: Path, directories: list[OwnedPath],
                    parents: list[OwnedPath]) -> None:
    for current in reversed((path, *tuple(p for p in path.parents if p.is_relative_to(project)))):
        if current != project:
            with _directory(current.parent) as fd:
                try:
                    os.mkdir(current.name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    directories.append(_owned(current, os.stat(current.name, dir_fd=fd, follow_symlinks=False)))
                    os.fsync(fd)
        with _directory(current) as fd:
            record = _check_directory(current, os.fstat(fd))
        if record not in parents:
            parents.append(record)


def _matches(record: OwnedPath) -> bool:
    try:
        with _directory(record.path.parent) as parent:
            if record.content_sha256 is None:
                info = os.stat(record.path.name, dir_fd=parent, follow_symlinks=False)
                return stat.S_ISDIR(info.st_mode) and _owned(record.path, info) == record
            data, info = _read_file(parent, record.path.name, C.CONFIG_MAX_BYTES)
            return _owned(record.path, info, hashlib.sha256(data).hexdigest()) == record
    except (C.ContinuityError, OSError):
        return False


def _write_new(path: Path, data: bytes, files: list[OwnedPath]) -> None:
    with _directory(path.parent) as parent:
        _check_directory(path.parent, os.fstat(parent))
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        position = len(files)
        digest = hashlib.sha256()
        files.append(_owned(path, os.fstat(fd), digest.hexdigest()))
        try:
            os.fchmod(fd, 0o600)
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                if count <= 0:
                    _fail("private_write_failed")
                digest.update(data[offset:offset + count])
                offset += count
                files[position] = _owned(path, os.fstat(fd), digest.hexdigest())
            os.fsync(fd)
            files[position] = _owned(path, os.fstat(fd), digest.hexdigest())
        finally:
            os.close(fd)
        os.fsync(parent)
    if not _matches(files[position]):
        _fail("private_write_failed")


def _remove_owned(files: tuple[OwnedPath, ...], directories: tuple[OwnedPath, ...],
                  parents: tuple[OwnedPath, ...]) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    # Compare the entire removable set before deleting even its first member.
    if not all(_matches(item) for item in (*parents, *directories, *files)):
        _fail("owned_file_changed")
    removed: list[Path] = []
    retained: list[Path] = []
    for item in files:
        with _directory(item.path.parent) as parent:
            if not _matches(item):
                _fail("cleanup_unresolved")
            os.unlink(item.path.name, dir_fd=parent)
            removed.append(item.path)
            os.fsync(parent)
    for item in reversed(directories):
        with _directory(item.path.parent) as parent:
            if not _matches(item):
                _fail("cleanup_unresolved")
            try:
                os.rmdir(item.path.name, dir_fd=parent)
            except OSError as error:
                if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    raise
                retained.append(item.path)
            os.fsync(parent)
    return tuple(removed), tuple(retained)


def _reserve_scope(request: SetupRequest) -> OwnedPath:
    # Claim first use exclusively; S's reusable namespace mkdir is not a claim.
    scope = _scope_path(request)
    with _directory(scope.parent.parent) as root:
        _check_directory(scope.parent.parent, os.fstat(root))
        try:
            os.mkdir("projects", 0o700, dir_fd=root)
        except FileExistsError:
            pass
        os.fsync(root)
    with _directory(scope.parent) as group:
        _check_directory(scope.parent, os.fstat(group))
        try:
            os.mkdir(scope.name, 0o700, dir_fd=group)
        except FileExistsError:
            _fail("scope_exists")
        owned = _check_directory(scope, os.stat(scope.name, dir_fd=group, follow_symlinks=False))
        os.fsync(group)
    return owned


def _scope_fingerprints(request: SetupRequest, snapshot: Path,
                        manifest_sha256: str) -> tuple[SetupFingerprint, ...]:
    """Accept only the single first capture and its exact complete locator."""
    scope = _scope_path(request)
    if snapshot.parent != scope / "snapshots":
        _fail("scope_changed")
    result: list[SetupFingerprint] = []
    for path, names in ((scope, {"snapshots", ".staging", "latest.json"}),
                        (scope / "snapshots", {snapshot.name}), (scope / ".staging", set())):
        with _directory(path) as fd:
            before = os.fstat(fd)
            with os.scandir(fd) as entries:
                remaining = set(names)
                for entry in entries:
                    if entry.name not in remaining:
                        _fail("scope_changed")
                    remaining.remove(entry.name)
            if remaining or _signature(os.fstat(fd)) != _signature(before):
                _fail("scope_changed")
            result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(before)).encode()).hexdigest()))
    manifest_path = snapshot / "manifest.json"
    for path, bound in ((manifest_path, C.MANIFEST_MAX_BYTES), (scope / "latest.json", C.CONFIG_MAX_BYTES)):
        with _directory(path.parent) as fd:
            data, info = _read_file(fd, path.name, bound)
        if path == manifest_path:
            if hashlib.sha256(data).hexdigest() != manifest_sha256:
                _fail("integrity_mismatch")
        else:
            locator = C.decode_locator(C.decode_json(data), pointer=True)
            if locator.identity != request.settings.identity or locator.path != str(manifest_path):
                _fail("scope_changed")
        result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(info)).encode() + data).hexdigest()))
    manifest = S.verify_snapshot(manifest_path, request.settings.identity, request.settings.limits)
    if manifest.completion != "complete" or manifest.snapshot_id != snapshot.name:
        _fail("first_snapshot_incomplete")
    return tuple(result)


def _fresh(preview: SetupPreview, captured: _CapturedScope | None = None) -> None:
    current = _preview_setup(preview.request, captured)
    if current.blockers or current != preview or not current.fingerprints:
        _fail("stale_preview")


def _retained(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    # Existence, not matching ownership: changed/replaced data must be disclosed
    # too. Never follow a replacement parent or remove anything during reporting.
    retained: list[Path] = []
    for path in dict.fromkeys(paths):
        try:
            with _directory(path.parent) as parent:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except (OSError, C.ContinuityError):
            pass  # Uninspectable is unresolved, not evidence of removal.
        retained.append(path)
    return tuple(retained)


def apply_setup(preview: SetupPreview, *, approval: bool = False) -> SetupResult:
    """Fresh caller approval for exactly this re-inspected, unconnected scope."""
    if approval is not True:
        return SetupResult("approval_required", capabilities=preview.capabilities)
    capabilities = _capabilities(preview.request)
    files: list[OwnedPath] = []
    directories: list[OwnedPath] = []
    parents: list[OwnedPath] = []
    receipt: InstalledState | None = None
    known_backup_paths: tuple[Path, ...] = ()
    lock_path: tuple[Path, ...] = ()

    def blocked(code: str) -> SetupResult:
        state = None if receipt is None else replace(
            receipt, files=tuple(files), directories=tuple(directories), parents=tuple(parents))
        changed = tuple(item.path for item in (*directories, *files))
        return SetupResult("blocked", changed, state, (code,), capabilities,
                           _retained((*changed, *known_backup_paths, *lock_path)))

    try:
        _fresh(preview)
        project = preview.request.settings.project_root
        storage = preview.storage
        assert project is not None and storage is not None
        with _setup_lock(project):
            lock_path = (project / _LOCK,)
            try:
                _fresh(preview)
                qualified = S.qualify_storage(storage, approval=True)
                if not qualified.readback_verified:
                    _fail("storage_unqualified")
                # First-use configuration never authorizes pruning prior backups.
                capture_settings = replace(preview.request.settings,
                                           backup=replace(preview.request.settings.backup, prune=False))
                request = C.SnapshotRequest(capture_settings, qualified.identity,
                                            preview.request.approved_sources, preview.request.context_entrypoints,
                                            preview.request.include_history, preview.request.history_ref)
                _fresh(preview)
                known_backup_paths = (_scope_path(preview.request).parent, _scope_path(preview.request))
                scope_owned = _reserve_scope(preview.request)
                snapshot = S.create_snapshot(request)
                known_backup_paths += tuple(path for path in (snapshot.snapshot_path, snapshot.last_locator)
                                            if path is not None)
                if (snapshot.status != "complete" or snapshot.snapshot_path is None
                        or snapshot.last_verified_snapshot is not None or not _matches(scope_owned)):
                    _fail("first_snapshot_incomplete")
                manifest_path = snapshot.snapshot_path / "manifest.json"
                manifest = S.verify_snapshot(manifest_path, request.settings.identity, request.settings.limits)
                if manifest.completion != "complete":
                    _fail("first_snapshot_incomplete")
                # Root timestamps cannot detect transient membership across our
                # own lock. Every captured file must match previewed bytes/mode.
                expected = {item.payload.relpath: item.payload for item in preview.fingerprints
                            if item.payload is not None}
                if (any(expected.get(item.relpath) != item for item in manifest.files)
                        or manifest.context_entrypoints != request.context_entrypoints):
                    _fail("stale_preview")
                with _directory(manifest_path.parent) as fd:
                    manifest_bytes, _ = _read_file(fd, manifest_path.name, C.MANIFEST_MAX_BYTES)
                manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
                if manifest_hash != snapshot.manifest_sha256:
                    _fail("integrity_mismatch")
                captured = _CapturedScope(snapshot.snapshot_path, manifest_hash,
                                          _scope_fingerprints(preview.request, snapshot.snapshot_path, manifest_hash))
                _fresh(preview, captured)
                documents = _documents(preview.request, manifest_path)
                # Establish recovery metadata before the first private write.
                # Failure paths replace its ownership lists with actual progress.
                receipt = InstalledState(request.settings.identity, project, preview.request.integration_root,
                                         qualified.identity, manifest_path, manifest_hash, (), (), (), capabilities)
                for mutation, document in zip(preview.mutations, documents, strict=True):
                    _prepare_parent(mutation.path.parent, project, directories, parents)
                    _write_new(mutation.path, _encoded(document), files)
                if (not all(_matches(item) for item in (*parents, *files))
                        or S.inspect_storage(storage.root) != storage
                        or not _matches(scope_owned)
                        or _scope_fingerprints(preview.request, captured.snapshot, captured.manifest_sha256)
                        != captured.fingerprints):
                    _fail("scope_changed")
                receipt = InstalledState(request.settings.identity, project, preview.request.integration_root,
                                       qualified.identity, manifest_path, manifest_hash, tuple(files),
                                       tuple(directories), tuple(parents), capabilities)
            except BaseException:
                # Never remove backups, foreign paths or changed owned content.
                # Interrupted cleanup is a blocker rather than configured state.
                try:
                    _, retained = _remove_owned(tuple(files), tuple(directories), tuple(parents))
                    if retained:
                        _fail("cleanup_unresolved")
                except (C.ContinuityError, OSError):
                    _fail("cleanup_unresolved")
                raise
        return SetupResult("configured", tuple(item.path for item in files), receipt,
                           capabilities=capabilities)
    except C.ContinuityError as error:
        return blocked(error.code)
    except (OSError, KeyboardInterrupt):
        return blocked("setup_interrupted")


def _validate_receipt(receipt: InstalledState) -> None:
    """Validate removal scope, not stored permission; backup need not be online."""
    replace(receipt.identity)
    project = C.safe_path(receipt.project_root, must_exist=True)
    integration = C.safe_path(receipt.integration_root, root=project / ".thunderkit", must_exist=True)
    private = project / ".thunderkit/continuity.local.json"
    reserved = (private, project / ".thunderkit/config.json", project / ".thunderkit/continuity.json")
    if (receipt.identity.kind != "project" or any(_overlap(integration, path) for path in reserved)
            or _overlap(receipt.storage.root, project)):
        _fail("owned_file_changed")
    expected = {private, integration / "config.json"}
    if len(receipt.files) != 2 or {item.path for item in receipt.files} != expected:
        _fail("owned_file_changed")
    necessary = {parent for path in expected for parent in path.parents if parent.is_relative_to(project)}
    if ({item.path for item in receipt.parents} != necessary
            or len(receipt.parents) != len(necessary)
            or len({item.path for item in receipt.directories}) != len(receipt.directories)):
        _fail("owned_file_changed")
    for item in (*receipt.files, *receipt.directories, *receipt.parents):
        if item.uid != os.geteuid():
            _fail("owned_file_changed")
    for item in receipt.files:
        if item.content_sha256 is None or item.mode != stat.S_IFREG | 0o600:
            _fail("owned_file_changed")
        C.validate_sha256(item.content_sha256)
    for item in receipt.directories:
        if item.path == project or item.path not in necessary or item not in receipt.parents:
            _fail("owned_file_changed")
    if not all(_matches(item) for item in (*receipt.parents, *receipt.directories, *receipt.files)):
        _fail("owned_file_changed")


def uninstall_setup(receipt: InstalledState, *, approval: bool = False) -> SetupResult:
    """Remove exact owned private artifacts only, with a fresh explicit approval."""
    if approval is not True:
        return SetupResult("approval_required")
    try:
        _validate_receipt(receipt)
    except (C.ContinuityError, OSError):
        return SetupResult("blocked", blockers=("owned_file_changed",))
    try:
        with _setup_lock(receipt.project_root):
            _validate_receipt(receipt)
            removed, retained = _remove_owned(receipt.files, receipt.directories, receipt.parents)
        return SetupResult("removed", removed, capabilities=receipt.capabilities, retained_paths=retained)
    except C.ContinuityError as error:
        return SetupResult("blocked", blockers=(error.code,), capabilities=receipt.capabilities)
    except (OSError, KeyboardInterrupt):
        return SetupResult("blocked", blockers=("cleanup_unresolved",), capabilities=receipt.capabilities)
