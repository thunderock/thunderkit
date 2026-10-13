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
_CLAIM = ".setup-claim.json"
_CLAIM_PENDING = ".setup-claim-pending"
_MAX_CAPTURES = 128


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
class _CaptureRecord:
    path: Path
    device: int
    inode: int
    manifest_sha256: str


@dataclass(frozen=True)
class _Claim:
    scope: OwnedPath
    marker: OwnedPath
    captures: tuple[_CaptureRecord, ...] = ()
    pending: tuple[C.ManifestFile, ...] | None = None
    latest: Path | None = None


@dataclass(frozen=True)
class _CapturedScope:
    claim: _Claim
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
    elif _scope_fingerprints(request, captured.claim) != captured.fingerprints:
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


def _history_preflight(project: Path) -> None:
    """Only an absent local HEAD target proves unborn; never spawn new Git argv."""
    def read(path: Path) -> bytes | None:
        C.safe_path(path)
        try:
            with _directory(path.parent) as fd:
                data, _ = _read_file(fd, path.name, _GIT_BYTES)
            return data.strip()
        except FileNotFoundError:
            return None

    head = read(project / ".git/HEAD")
    if head is None or not head.startswith(b"ref: "):
        _fail("history_approval_required")
    try:
        ref = head[5:].decode("ascii")
        C.validate_ref(ref)
    except (UnicodeError, C.ContinuityError):
        _fail("history_approval_required")
    if read(project / ".git" / ref) is not None:
        _fail("history_approval_required")
    packed = read(project / ".git/packed-refs")
    if packed is not None:
        for line in packed.splitlines():
            if line and not line.startswith((b"#", b"^")):
                fields = line.split(b" ")
                if len(fields) != 2 or fields[1] == ref.encode("ascii"):
                    _fail("history_approval_required")


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
        if not request.include_history:
            assert request.settings.project_root is not None
            _history_preflight(request.settings.project_root)
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


class _ReservationError(C.ContinuityError):
    def __init__(self, code: str, paths: tuple[Path, ...]) -> None:
        super().__init__(code, "Setup reservation interrupted; owned artifacts retained.")
        self.paths = paths


def _reserve_scope(request: SetupRequest) -> OwnedPath:
    # Claim first use exclusively; S's reusable namespace mkdir is not a claim.
    scope = _scope_path(request)
    created: list[Path] = []
    try:
        with _directory(scope.parent.parent) as root:
            _check_directory(scope.parent.parent, os.fstat(root))
            try:
                os.mkdir("projects", 0o700, dir_fd=root)
            except FileExistsError:
                pass
            else:
                created.append(scope.parent)
            os.fsync(root)
        with _directory(scope.parent) as group:
            _check_directory(scope.parent, os.fstat(group))
            try:
                os.mkdir(scope.name, 0o700, dir_fd=group)
            except FileExistsError:
                _fail("scope_exists")
            created.append(scope)
            owned = _check_directory(scope, os.stat(scope.name, dir_fd=group, follow_symlinks=False))
            os.fsync(group)
        return owned
    except (C.ContinuityError, OSError, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, C.ContinuityError) else "setup_interrupted"
        raise _ReservationError(code, tuple(created)) from None


def _claim_document(request: SetupRequest, scope: OwnedPath, marker: OwnedPath,
                    captures: tuple[_CaptureRecord, ...],
                    pending: tuple[C.ManifestFile, ...] | None, latest: Path | None) -> C.JsonObject:
    project = request.settings.project_root
    assert project is not None and request.settings.backup_root is not None
    with _directory(project) as fd:
        root = _check_directory(project, os.fstat(fd))
    storage = S.inspect_storage(request.settings.backup_root)
    return {
        "schema": 1, "kind": "setup-ownership", "identity": {
            "kind": request.settings.identity.kind, "id": request.settings.identity.id},
        "project": {"path": str(project), "device": root.device, "inode": root.inode},
        "scope": {"path": str(scope.path), "device": scope.device, "inode": scope.inode},
        "storage": {"path": str(storage.root), "device": storage.device, "inode": storage.inode,
                    "backing": storage.backing_fingerprint, "mount": storage.mount_fingerprint},
        "marker": {"device": marker.device, "inode": marker.inode},
        "integration": str(request.integration_root),
        "sources": [str(path) for path in request.approved_sources],
        "latest": None if latest is None else str(latest),
        "captures": [{"name": item.path.name, "device": item.device, "inode": item.inode,
                      "sha256": item.manifest_sha256} for item in captures],
        "pending": None if pending is None else [
            {"relpath": item.relpath, "size": item.size, "sha256": item.sha256, "mode": item.mode}
            for item in pending],
    }


def _claim_int(value: C.JsonValue) -> int:
    if type(value) is not int or not 0 <= value < 2 ** 64:
        _fail("resume_unavailable")
    return value


def _load_claim(request: SetupRequest) -> _Claim:
    """Strict local binding, not a credential, capability or saved approval."""
    scope = _scope_path(request)
    with _directory(scope) as fd:
        owned = _check_directory(scope, os.fstat(fd))
        data, info = _read_file(fd, _CLAIM, C.CONFIG_MAX_BYTES)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        _fail("resume_unavailable")
    marker = _owned(scope / _CLAIM, info, hashlib.sha256(data).hexdigest())
    raw = C.decode_json(data)
    if not isinstance(raw, dict):
        _fail("resume_unavailable")
    records = raw.get("captures")
    pending_raw = raw.get("pending")
    if not isinstance(records, list) or len(records) > _MAX_CAPTURES:
        _fail("resume_unavailable")
    captures: list[_CaptureRecord] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "device", "inode", "sha256"}:
            _fail("resume_unavailable")
        name = record["name"]
        if not isinstance(name, str) or len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
            _fail("resume_unavailable")
        captures.append(_CaptureRecord(scope / "snapshots" / name, _claim_int(record["device"]),
                                       _claim_int(record["inode"]), C.validate_sha256(record["sha256"])))
    pending: tuple[C.ManifestFile, ...] | None = None
    if pending_raw is not None:
        if not isinstance(pending_raw, list) or len(pending_raw) > request.settings.limits.max_files:
            _fail("resume_unavailable")
        files: list[C.ManifestFile] = []
        for item in pending_raw:
            if not isinstance(item, dict) or set(item) != {"relpath", "size", "sha256", "mode"}:
                _fail("resume_unavailable")
            files.append(C.ManifestFile(C.validate_relpath(item["relpath"]), _claim_int(item["size"]),
                                        C.validate_sha256(item["sha256"]), _claim_int(item["mode"])))
        pending = tuple(files)
    latest_raw = raw.get("latest")
    latest: Path | None = None
    if latest_raw is not None:
        latest = Path(C.validate_path_text(latest_raw, absolute=True))
        if latest.name != "manifest.json" or latest.parent not in {item.path for item in captures}:
            _fail("resume_unavailable")
    expected = _claim_document(request, owned, marker, tuple(captures), pending, latest)
    # Canonical comparison rejects unknown keys and bool/int aliasing as well as
    # same-content replacement at a different inode. Same-user forgery is not
    # cryptographically distinguishable; only a current caller can approve use.
    if _encoded(raw) != _encoded(expected) or len({c.path for c in captures}) != len(captures):
        _fail("resume_unavailable")
    return _Claim(owned, marker, tuple(captures), pending, latest)


def _publish_claim(request: SetupRequest, scope: OwnedPath, captures: tuple[_CaptureRecord, ...],
                   pending: tuple[C.ManifestFile, ...] | None, previous: _Claim | None = None,
                   *, latest: Path | None = None) -> _Claim:
    if len(captures) > _MAX_CAPTURES or not _matches(scope):
        _fail("scope_changed")
    with _directory(scope.path) as parent:
        if previous is not None and not _matches(previous.marker):
            _fail("scope_changed")
        name = _CLAIM if previous is None else _CLAIM_PENDING
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            os.fchmod(fd, 0o600)
            marker = _owned(scope.path / _CLAIM, os.fstat(fd))
            data = _encoded(_claim_document(request, scope, marker, captures, pending, latest))
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                if count <= 0:
                    _fail("private_write_failed")
                offset += count
            os.fsync(fd)
            marker = _owned(scope.path / _CLAIM, os.fstat(fd), hashlib.sha256(data).hexdigest())
        finally:
            os.close(fd)
        if previous is not None:
            if not _matches(previous.marker) or not _matches(scope):
                _fail("scope_changed")
            os.replace(name, _CLAIM, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    claim = _load_claim(request)
    if claim != _Claim(scope, marker, captures, pending, latest):
        _fail("scope_changed")
    return claim


def _scope_state(request: SetupRequest, claim: _Claim
                 ) -> tuple[tuple[SetupFingerprint, ...], tuple[_CaptureRecord, ...]]:
    """Exact bounded prior set, at most one pending capture, empty staging only."""
    if _load_claim(request) != claim or not _matches(claim.scope):
        _fail("scope_changed")
    scope = claim.scope.path
    project = request.settings.project_root
    assert project is not None
    result: list[SetupFingerprint] = []

    def directory(path: Path, allowed: set[str] | None, required: set[str]) -> set[str]:
        with _directory(path) as fd:
            before = os.fstat(fd)
            _check_directory(path, before)
            names: set[str] = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    if len(names) >= _MAX_CAPTURES or (allowed is not None and entry.name not in allowed):
                        _fail("scope_changed")
                    names.add(entry.name)
            if not required <= names or _signature(os.fstat(fd)) != _signature(before):
                _fail("scope_changed")
            result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(before)).encode()).hexdigest()))
        return names

    def file(path: Path, bound: int) -> bytes:
        with _directory(path.parent) as fd:
            data, info = _read_file(fd, path.name, bound)
        result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(info)).encode() + data).hexdigest()))
        return data

    names = directory(scope, {_CLAIM, "snapshots", ".staging", "latest.json"}, {_CLAIM})
    file(scope / _CLAIM, C.CONFIG_MAX_BYTES)
    if ".staging" in names:
        directory(scope / ".staging", set(), set())
    previous = {item.path.name: item for item in claim.captures}
    snapshots = directory(scope / "snapshots", None, set(previous)) if "snapshots" in names else set()
    if set(previous) - snapshots or len(snapshots - set(previous)) > (1 if claim.pending is not None else 0):
        _fail("scope_changed")
    complete: set[Path] = set()
    records: list[_CaptureRecord] = []
    total_bytes = 0
    for name in sorted(snapshots):
        if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
            _fail("scope_changed")
        path = scope / "snapshots" / name
        with _directory(path) as fd:
            info = os.fstat(fd)
            _check_directory(path, info)
        data = file(path / "manifest.json", C.MANIFEST_MAX_BYTES)
        manifest = C.validate_manifest(C.decode_json(data, max_bytes=C.MANIFEST_MAX_BYTES),
                                       expected_identity=request.settings.identity, limits=request.settings.limits)
        total_bytes += sum(item.size for item in manifest.files) + manifest.history.size
        if total_bytes > request.settings.limits.max_snapshot_bytes:
            _fail("inspection_budget_exceeded")
        if (manifest.snapshot_id != name or manifest.repository not in (None, str(project))
                or any(not any((project / rel).is_relative_to(source) for source in request.approved_sources)
                       or _source_excluded(request, project / rel)
                       for rel in (*tuple(item.relpath for item in manifest.files), *manifest.deleted_paths))):
            _fail("scope_changed")
        record = _CaptureRecord(path, info.st_dev, info.st_ino, hashlib.sha256(data).hexdigest())
        if name in previous:
            if record != previous[name]:
                _fail("scope_changed")
        else:
            expected = {item.relpath: item for item in claim.pending or ()}
            if any(expected.get(item.relpath) != item for item in manifest.files):
                _fail("scope_changed")
        if S.verify_snapshot(path, request.settings.identity, request.settings.limits) != manifest:
            _fail("scope_changed")
        if manifest.completion == "complete":
            complete.add(path / "manifest.json")
        result.append(SetupFingerprint(path, hashlib.sha256(repr(_signature(info)).encode()).hexdigest()))
        records.append(record)
    if "latest.json" in names:
        locator = C.decode_locator(C.decode_json(file(scope / "latest.json", C.CONFIG_MAX_BYTES)), pointer=True)
        if locator.identity != request.settings.identity or Path(locator.path) not in complete:
            _fail("scope_changed")
        if claim.pending is None and Path(locator.path) != claim.latest:
            _fail("scope_changed")
    elif claim.latest is not None:
        _fail("scope_changed")
    # An interrupted locator publication may leave a complete owned snapshot
    # without latest.json; a new capture can publish its own locator safely.
    return tuple(result), tuple(records)


def _scope_fingerprints(request: SetupRequest, claim: _Claim) -> tuple[SetupFingerprint, ...]:
    return _scope_state(request, claim)[0]


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
    return _apply_setup(preview)


def resume_setup(request: SetupRequest, *, approval: bool = False) -> SetupResult:
    """Re-preview and resume only an exact local claim with new caller approval."""
    capabilities = _capabilities(request)
    if approval is not True:
        return SetupResult("approval_required", capabilities=capabilities)
    try:
        if request.settings.backup_root is None or request.settings.project_root is None:
            _fail("resume_unavailable")
        claim = _load_claim(request)
        captured = _CapturedScope(claim, _scope_fingerprints(request, claim))
    except (C.ContinuityError, OSError):
        return SetupResult("blocked", blockers=("resume_unavailable",), capabilities=capabilities)
    preview = _preview_setup(request, captured)
    if preview.blockers:
        return SetupResult("blocked", blockers=preview.blockers, capabilities=capabilities)
    return _apply_setup(preview, captured)


def _apply_setup(preview: SetupPreview, captured: _CapturedScope | None = None) -> SetupResult:
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
        _fresh(preview, captured)
        project = preview.request.settings.project_root
        storage = preview.storage
        assert project is not None and storage is not None
        with _setup_lock(project):
            lock_path = (project / _LOCK,)
            try:
                _fresh(preview, captured)
                qualified = S.qualify_storage(storage, approval=True)
                if not qualified.readback_verified:
                    _fail("storage_unqualified")
                # First-use configuration never authorizes pruning prior backups.
                capture_settings = replace(preview.request.settings,
                                           backup=replace(preview.request.settings.backup, prune=False))
                request = C.SnapshotRequest(capture_settings, qualified.identity,
                                            preview.request.approved_sources, preview.request.context_entrypoints,
                                            preview.request.include_history, preview.request.history_ref)
                _fresh(preview, captured)
                if captured is None:
                    try:
                        scope_owned = _reserve_scope(preview.request)
                    except _ReservationError as error:
                        known_backup_paths = error.paths
                        raise
                    known_backup_paths = (scope_owned.path,)
                    claim = _publish_claim(preview.request, scope_owned, (), None)
                else:
                    claim = captured.claim
                    scope_owned = claim.scope
                    known_backup_paths = (scope_owned.path, claim.marker.path, *tuple(c.path for c in claim.captures))
                known_backup_paths += (claim.marker.path, scope_owned.path / _CLAIM_PENDING)
                fingerprints, previous_records = _scope_state(preview.request, claim)
                if captured is not None and fingerprints != captured.fingerprints:
                    _fail("scope_changed")
                pending_files = tuple(sorted({item.payload.relpath: item.payload for item in preview.fingerprints
                                              if item.payload is not None}.values(), key=lambda item: item.relpath))
                latest = claim.latest
                with _directory(scope_owned.path) as fd:
                    try:
                        locator_data, _ = _read_file(fd, "latest.json", C.CONFIG_MAX_BYTES)
                    except FileNotFoundError:
                        pass
                    else:
                        latest = Path(C.decode_locator(C.decode_json(locator_data), pointer=True).path)
                claim = _publish_claim(preview.request, scope_owned, previous_records, pending_files, claim,
                                       latest=latest)
                captured = _CapturedScope(claim, _scope_fingerprints(preview.request, claim))
                _fresh(preview, captured)
                snapshot = S.create_snapshot(request)
                known_backup_paths += tuple(path for path in (snapshot.snapshot_path, snapshot.last_locator)
                                            if path is not None)
                if (snapshot.status not in ("complete", "unchanged") or snapshot.snapshot_path is None
                        or not _matches(scope_owned)):
                    # A returned partial is known progress, not an unbounded
                    # pending candidate on the next resume. Seal its exact bytes.
                    if snapshot.status == "partial" and snapshot.snapshot_path is not None:
                        _, records = _scope_state(preview.request, claim)
                        claim = _publish_claim(preview.request, scope_owned, records, None, claim,
                                               latest=claim.latest)
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
                _, records = _scope_state(preview.request, claim)
                with _directory(scope_owned.path) as fd:
                    locator_data, _ = _read_file(fd, "latest.json", C.CONFIG_MAX_BYTES)
                locator = C.decode_locator(C.decode_json(locator_data), pointer=True)
                if locator.identity != request.settings.identity or locator.path != str(manifest_path):
                    _fail("scope_changed")
                claim = _publish_claim(preview.request, scope_owned, records, None, claim,
                                       latest=manifest_path)
                captured = _CapturedScope(claim, _scope_fingerprints(preview.request, claim))
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
                        or _scope_fingerprints(preview.request, captured.claim)
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
