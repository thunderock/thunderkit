"""Read-only, model-free first-use setup preview; no installation authority.

Only an unconnected project preview is currently available. Proposed private
writes are not performed, storage is not qualified, and no receipt or setting
can grant setup or publication permission. Activation needs a later API slice.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
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

SetupStatus: TypeAlias = Literal["approval_required", "blocked"]
CapabilityName: TypeAlias = Literal["periodic", "ref_events", "auto_push", "full_target"]
CapabilityStatus: TypeAlias = Literal["supported", "disabled", "blocked"]
_GIT_TIMEOUT = 5.0
_GIT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SetupRequest:
    settings: C.ResolvedSettings
    approved_sources: tuple[Path, ...]
    integration_root: Path
    scheduler: str = "none"
    per_commit: bool = False


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
class SetupPreview:
    request: SetupRequest
    storage: C.StorageIdentity | None
    mutations: tuple[SetupMutation, ...]
    capabilities: tuple[Capability, ...]
    missing_approvals: tuple[str, ...]
    blockers: tuple[str, ...]
    status: SetupStatus


@dataclass(frozen=True)
class SetupResult:
    status: SetupStatus
    changed_paths: tuple[Path, ...] = ()
    # This slice cannot issue an installation receipt, even with approval=True.
    receipt: None = None
    blockers: tuple[str, ...] = ()
    capabilities: tuple[Capability, ...] = ()


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
        with subprocess.Popen((binary, *args), cwd=project, env=_git_environment(),
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


def _mutation(path: Path, document: C.JsonObject) -> SetupMutation:
    content = json.dumps(document, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    if len(content) > C.CONFIG_MAX_BYTES:
        _fail("invalid_config")
    return SetupMutation(path, "create", 0o600, hashlib.sha256(content).hexdigest())


def _inspect(request: SetupRequest) -> tuple[C.StorageIdentity, tuple[SetupMutation, ...]]:
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
    C.SnapshotRequest(settings, storage, request.approved_sources)
    if not request.approved_sources:
        _fail("sources_required")
    for source in request.approved_sources:
        C.validate_path_text(str(source), absolute=True, max_bytes=settings.limits.max_path_bytes)
        C.safe_path(source, root=project, must_exist=True)
        if any(_overlap(source, path) for path in (backup, integration, private, project / ".git")):
            _fail("unsafe_path")
        info = source.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            _fail("unsafe_path")
    info = backup.lstat()
    if not info.st_mode & 0o222 or os.statvfs(backup).f_flag & os.ST_RDONLY:
        _fail("storage_readonly")
    for path in (private, integration / "config.json"):
        _new_private_path(path, project)
    _private_git_check(project, private, integration)
    if S.inspect_storage(backup) != storage:
        _fail("storage_changed")
    document: C.JsonObject = {
        "schema": 1, "kind": "project-settings",
        "identity": {"kind": settings.identity.kind, "id": settings.identity.id},
        "project_root": str(project), "backup_root": str(backup),
    }
    C.validate_continuity_document(document, kind="project-settings")
    integration_document: C.JsonObject = {
        "schema": 1, "kind": "checkpoint-settings", "settings": C.settings_to_json(settings),
        "approved_sources": [str(path) for path in request.approved_sources],
        "scheduler": request.scheduler, "per_commit": request.per_commit,
    }
    return storage, (_mutation(private, document),
                     _mutation(integration / "config.json", integration_document))


def preview_setup(request: SetupRequest) -> SetupPreview:
    """Inspect literal scope and private targets; never capture, probe or install."""
    capabilities = _capabilities(request)
    blockers: list[str] = []
    if request.scheduler != "none":
        blockers.append("unsupported_scheduler")
    if request.per_commit is not False:
        blockers.append("ref_events_unavailable")
    storage: C.StorageIdentity | None = None
    mutations: tuple[SetupMutation, ...] = ()
    try:
        storage, mutations = _inspect(request)
    except C.ContinuityError as error:
        blockers.append(error.code)
    except OSError:
        blockers.append("inspection_failed")
    if blockers:
        # Never offer even a subset of writes from an unsafe/unsupported plan.
        return SetupPreview(request, storage, (), capabilities, (),
                            tuple(dict.fromkeys(blockers)), "blocked")
    return SetupPreview(request, storage, mutations, capabilities,
                        ("setup_write",), (), "approval_required")


def apply_setup(preview: SetupPreview, *, approval: bool = False) -> SetupResult:
    """Default deny before all I/O; a boolean cannot activate this first slice."""
    if approval is not True:
        return SetupResult("approval_required", capabilities=preview.capabilities)
    return SetupResult("blocked", blockers=("setup_apply_unavailable",),
                       capabilities=preview.capabilities)
