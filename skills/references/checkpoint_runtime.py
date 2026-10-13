"""Explicit, bounded event/due capture; no scheduler, publication or authority.

The in-process deadline requires Linux/POSIX timers on the main thread with no
active ITIMER_REAL timer. Unsupported contexts refuse before writes. As with S,
this requires ordinary responsive local storage; Python signals cannot preempt
an uninterruptible kernel I/O operation. No worker/thread or process is started.
State is an inert cadence observation, never a grant or a semantic summary.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import threading
import time
from types import FrameType
from typing import Literal, Protocol, TypeAlias
import uuid

import backup_store as S
import continuity_config as C

Status: TypeAlias = Literal["captured", "not-due", "duplicate", "busy", "partial", "failed"]
STATE_MAX_BYTES = 64 * 1024
MAX_DEADLINE_SECONDS = 60.0
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_LOCK = ".checkpoint.lock"


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def time(self) -> float: ...
    def boot_id(self) -> str: ...


@dataclass(frozen=True)
class CheckpointRequest:
    snapshot: C.SnapshotRequest
    state_dir: Path
    trigger: str = "event"
    event_id: str | None = None
    deadline_seconds: float = 60.0


@dataclass(frozen=True)
class CheckpointResult:
    status: Status
    snapshot: C.SnapshotResult | None
    push: C.PushResult | None
    context_age_seconds: float | None
    observed_oids: tuple[str, ...]
    coverage_gaps: tuple[str, ...]
    next_action: str
    warnings: tuple[str, ...] = ()


class _Refused(Exception):
    """Contains only an internal stable code, never raw caller or OS text."""


class _Deadline(BaseException):
    """Bypass S's recoverable per-file omissions, but execute its finally blocks."""


def _require(ok: bool, code: str = "invalid_request") -> None:
    if not ok:
        raise _Refused(code)


def _number(value: object, *, nonnegative: bool = False) -> float:
    _require(type(value) in (int, float), "invalid_clock_or_deadline")
    assert isinstance(value, (int, float))
    result = float(value)
    _require(math.isfinite(result) and (not nonnegative or result >= 0), "invalid_clock_or_deadline")
    return result


def _text(value: object) -> str:
    _require(type(value) is str, "invalid_text")
    assert isinstance(value, str)
    _require(0 < len(value) <= 256 and all(32 <= ord(c) < 127 for c in value), "invalid_text")
    return value


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def boot_id(self) -> str:
        # This single explicit kernel observation is not an ambient config read.
        try:
            fd = os.open("/proc/sys/kernel/random/boot_id", _FILE)
            try:
                data = os.read(fd, 65)
            finally:
                os.close(fd)
            if re.fullmatch(rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n", data):
                return data.decode("ascii").rstrip("\n")
        except OSError:
            pass
        return ""  # Unknown: never a guessed cross-run boot identity.


@dataclass(frozen=True)
class _Observation:
    monotonic: float
    wall: float
    boot: str | None


def _observe(clock: Clock) -> _Observation:
    mono = _number(clock.monotonic(), nonnegative=True)
    wall = _number(clock.time())
    boot = clock.boot_id()
    _require(type(boot) is str, "invalid_clock")
    return _Observation(mono, wall, _text(boot) if boot else None)


@contextmanager
def _deadline(seconds: float) -> Iterator[float]:
    _require(sys.platform.startswith("linux") and threading.current_thread() is threading.main_thread()
             and hasattr(signal, "setitimer"), "deadline_context_unsupported")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    # Leave an existing owner's timer/handler entirely untouched, not postponed.
    _require(previous_timer == (0.0, 0.0), "deadline_timer_in_use")
    previous_handler = signal.getsignal(signal.SIGALRM)
    end = time.monotonic() + seconds

    def expired(signum: int, frame: FrameType | None) -> None:
        raise _Deadline()

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield end
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _budget(end: float) -> None:
    if time.monotonic() >= end:
        raise _Deadline()


def _inode(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (*_inode(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Directory:
    """Keep and recheck every no-follow parent; never reopen a replaced target."""

    def __init__(self, path: Path, stack: ExitStack) -> None:
        C.validate_path_text(str(path), absolute=True)
        self.path = path
        self.links: list[tuple[int, str, int]] = []
        parent = os.open(path.anchor, _DIR)
        stack.callback(os.close, parent)
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=parent)
            stack.callback(os.close, child)
            self.links.append((parent, part, child))
            parent = child
        self.fd = parent
        self.info = os.fstat(parent)

    def check(self) -> None:
        for parent, part, child in self.links:
            _require(_inode(os.stat(part, dir_fd=parent, follow_symlinks=False)) == _inode(os.fstat(child)),
                     "path_changed")
        _require(_inode(os.fstat(self.fd)) == _inode(self.info), "path_changed")


def _validated(request: CheckpointRequest) -> CheckpointRequest:
    _require(type(request) is CheckpointRequest)
    s = request.snapshot
    _require(type(s) is C.SnapshotRequest and type(s.settings) is C.ResolvedSettings
             and type(s.storage) is C.StorageIdentity)
    cfg = s.settings
    _require(type(cfg.identity) is C.ScopeIdentity and type(cfg.backup) is C.BackupPolicy
             and type(cfg.git) is C.GitPolicy and type(cfg.limits) is C.Limits)
    # Frozen dataclasses can still be tampered with in-process; revalidate leaves.
    cfg = replace(cfg, identity=replace(cfg.identity), backup=replace(cfg.backup),
                  git=replace(cfg.git), limits=replace(cfg.limits))
    s = replace(s, settings=cfg, storage=replace(s.storage))
    _require(isinstance(request.state_dir, Path))
    C.validate_path_text(str(request.state_dir), absolute=True)
    _require(request.trigger in ("event", "due") and type(request.trigger) is str)
    if request.event_id is not None:
        _text(request.event_id)
    seconds = _number(request.deadline_seconds)
    _require(0 < seconds <= MAX_DEADLINE_SECONDS, "invalid_deadline")
    _require(cfg.backup.enabled, "backup_disabled")
    _require(cfg.project_root is not None or cfg.identity.kind == "global")
    _require(bool(s.approved_sources) and set(s.approved_sources) <= set(cfg.approved_sources),
             "unapproved_sources")
    _require(s.storage.durability in ("software-readback", "operator-qualified"), "storage_unqualified")
    # These are the initial in-process non-Git bounds, not a Git adapter.
    _require(not s.include_history and all(value is None for value in (
        s.history_ref, s.verified_remote_oid, s.remote_evidence)), "history_unconfigured")
    for source in (*s.approved_sources, s.storage.root, *((cfg.project_root,) if cfg.project_root else ())):
        _require(not request.state_dir.is_relative_to(source) and not source.is_relative_to(request.state_dir),
                 "state_scope_overlap")
    for source in s.approved_sources:
        _require(cfg.project_root is None or source.is_relative_to(cfg.project_root), "unapproved_sources")
        _require(not source.is_relative_to(s.storage.root) and not s.storage.root.is_relative_to(source),
                 "source_store_overlap")
    return replace(request, snapshot=s, deadline_seconds=seconds)


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda obj: str(obj) if isinstance(obj, Path) else obj).encode("utf-8")


class _Scope:
    def __init__(self, request: CheckpointRequest, stack: ExitStack, end: float) -> None:
        self.request = request
        self.end = end
        self.state = _Directory(request.state_dir, stack)
        info = self.state.info
        _require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(), "unsafe_state_directory")
        self.store = _Directory(request.snapshot.storage.root, stack)
        project = request.snapshot.settings.project_root
        self.project = _Directory(project, stack) if project else None
        self.sources: list[tuple[_Directory, Path, os.stat_result]] = []
        for source in request.snapshot.approved_sources:
            parent = _Directory(source.parent, stack)
            info = os.stat(source.name, dir_fd=parent.fd, follow_symlinks=False)
            _require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                     "unsafe_source")
            self.sources.append((parent, source, info))
        self.check()
        binding = {
            "request": asdict(request.snapshot),
            "state_directory": _inode(self.state.info),
            "project": _inode(self.project.info) if self.project else None,
            "sources": [(str(path), _inode(info)) for _, path, info in self.sources],
            "parents": [[(name, _inode(os.fstat(child))) for _, name, child in directory.links]
                        for directory in (self.state, self.store, *([self.project] if self.project else []),
                                          *(parent for parent, _, _ in self.sources))],
        }
        s = request.snapshot
        self.template: C.JsonObject = {
            "schema": 1, "identity": {"kind": s.settings.identity.kind, "id": s.settings.identity.id},
            "project_root": str(project) if project else None, "backup_root": str(s.storage.root),
            "approved_sources": [str(path) for path in s.approved_sources],
            "binding_sha256": hashlib.sha256(_encoded(binding)).hexdigest(),
        }
        self.state_info: os.stat_result | None = None

    def check(self) -> None:
        _budget(self.end)
        for directory in (self.state, self.store, *([self.project] if self.project else [])):
            directory.check()
        for parent, source, original in self.sources:
            parent.check()
            now = os.stat(source.name, dir_fd=parent.fd, follow_symlinks=False)
            _require(_inode(now) == _inode(original) and (stat.S_ISDIR(now.st_mode) or now.st_nlink == 1),
                     "source_changed")
        if self.project:
            try:
                os.stat(".git", dir_fd=self.project.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _Refused("git_runtime_unconfigured")
        current = S.inspect_storage(self.request.snapshot.storage.root)
        _require(current == replace(self.request.snapshot.storage, durability="unqualified")
                 and current.mount_fingerprint != "unavailable-mount", "storage_changed")

    def capture_guard(self, event: str, path: Path | None) -> None:
        self.check()

    def _current_state(self) -> os.stat_result | None:
        try:
            info = os.stat("state.json", dir_fd=self.state.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.geteuid()
                 and info.st_size <= STATE_MAX_BYTES, "unsafe_state_file")
        return info

    def read_state(self) -> tuple[C.JsonObject | None, str | None]:
        self.state_info = self._current_state()
        if self.state_info is None:
            return None, "state_missing"
        fd = os.open("state.json", _FILE, dir_fd=self.state.fd)
        try:
            _require(_signature(os.fstat(fd)) == _signature(self.state_info), "state_changed")
            chunks: list[bytes] = []
            remaining = STATE_MAX_BYTES + 1
            while remaining:
                _budget(self.end)
                data = os.read(fd, remaining)
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            _require(_signature(os.fstat(fd)) == _signature(self.state_info), "state_changed")
        finally:
            os.close(fd)
        self.check_state_unchanged()
        try:
            raw = C.decode_json(b"".join(chunks), max_bytes=STATE_MAX_BYTES)
            observation_fields = {"boot_id", "last_success_monotonic", "last_event_id", "last_snapshot"}
            _require(set(raw) == set(self.template) | observation_fields, "state_corrupt")
            _require(_encoded({key: raw[key] for key in self.template}) == _encoded(self.template), "state_foreign")
            _number(raw["last_success_monotonic"], nonnegative=True)
            if raw["boot_id"] is not None:
                _text(raw["boot_id"])
            if raw["last_event_id"] is not None:
                _text(raw["last_event_id"])
            last = raw["last_snapshot"]
            _require(isinstance(last, str), "state_corrupt")
            assert isinstance(last, str)
            C.validate_path_text(last, absolute=True)
            identity = self.request.snapshot.settings.identity
            scope = self.request.snapshot.storage.root / ("projects" if identity.kind == "project" else "global") / identity.id
            _require(Path(last).parent == scope / "snapshots" and re.fullmatch(r"[0-9a-f]{32}", Path(last).name) is not None,
                     "state_corrupt")
            return raw, None
        except C.ContinuityError:
            return None, "state_corrupt"
        except _Refused as error:
            return None, "state_foreign" if str(error) == "state_foreign" else "state_corrupt"

    def check_state_unchanged(self) -> None:
        current = self._current_state()
        _require((current is None and self.state_info is None) or
                 (current is not None and self.state_info is not None
                  and _signature(current) == _signature(self.state_info)), "state_changed")

    def write_state(self, snapshot: C.SnapshotResult, observation: _Observation) -> None:
        raw = dict(self.template)
        raw.update(boot_id=observation.boot, last_success_monotonic=observation.monotonic,
                   last_event_id=self.request.event_id, last_snapshot=str(snapshot.snapshot_path))
        data = _encoded(raw)
        _require(len(data) <= STATE_MAX_BYTES, "state_budget_exceeded")
        name = ".checkpoint-state-" + uuid.uuid4().hex
        self.check()
        self.check_state_unchanged()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.state.fd)
        owned = os.fstat(fd)
        try:
            view = memoryview(data)
            while view:
                self.check()
                count = os.write(fd, view)
                _require(count > 0, "state_write_failed")
                view = view[count:]
            self.check()
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            self.check()
            self.check_state_unchanged()
            _require(_inode(os.stat(name, dir_fd=self.state.fd, follow_symlinks=False)) == _inode(owned), "state_changed")
            os.replace(name, "state.json", src_dir_fd=self.state.fd, dst_dir_fd=self.state.fd)
            os.fsync(self.state.fd)
            self.state_info = os.fstat(fd)
            self.check_state_unchanged()
        finally:
            os.close(fd)
            self.remove_owned(name, owned, directory=False)

    def remove_owned(self, name: str, owned: os.stat_result, *, directory: bool) -> None:
        # Cleanup only exact owned entries at held descriptors; never recurse.
        try:
            now = os.stat(name, dir_fd=self.state.fd, follow_symlinks=False)
            if _inode(now) == _inode(owned):
                if directory:
                    os.rmdir(name, dir_fd=self.state.fd)
                else:
                    os.unlink(name, dir_fd=self.state.fd)
        except FileNotFoundError:
            pass


def _context_age(scope: _Scope, wall: float) -> float | None:
    request = scope.request.snapshot
    root = request.settings.project_root
    if root is None or not request.context_entrypoints:
        return None
    ages: list[float] = []
    for entry in request.context_entrypoints:
        path = root / entry
        matches = [(source, info) for _, source, info in scope.sources
                   if path == source or (stat.S_ISDIR(info.st_mode) and path.is_relative_to(source))]
        if len(matches) != 1:
            return None
        try:
            with ExitStack() as stack:
                parent = _Directory(path.parent, stack)
                info = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return None
                parent.check()
                age = wall - info.st_mtime
                if not math.isfinite(age) or age < 0:
                    return None
                ages.append(age)
        except OSError:
            return None
    return max(ages)


def checkpoint(request: CheckpointRequest, *, clock: Clock | None = None) -> CheckpointResult:
    """Capture once on event or due; only complete/unchanged S advances state.

    A repeated event still observes content through S, which owns deduplication.
    Cadence uses the supplied clock; execution limits always use real monotonic.
    KeyboardInterrupt/SystemExit propagate after owned-lock and S cleanup.
    """
    gaps = ("periodic_unconfigured", "ref_events_unconfigured", "publication_disabled")
    warnings: list[str] = []
    snapshot: C.SnapshotResult | None = None
    age: float | None = None

    def result(status: Status, action: str) -> CheckpointResult:
        return CheckpointResult(status, snapshot, None, age, (), gaps, action, tuple(warnings))

    try:
        # Timer qualification precedes filesystem reads and writes.
        _require(type(request) is CheckpointRequest)
        seconds = _number(request.deadline_seconds)
        _require(0 < seconds <= MAX_DEADLINE_SECONDS, "invalid_deadline")
        with _deadline(seconds) as end:
            request = _validated(request)
            if request.snapshot.settings.git.push_after_commit:
                gaps = ("periodic_unconfigured", "ref_events_unconfigured", "fresh_authority_required")
            active_clock = clock if clock is not None else _SystemClock()
            observation = _observe(active_clock)
            if observation.boot is None:
                warnings.append("boot_identity_unknown")
            with ExitStack() as stack:
                scope = _Scope(request, stack, end)
                # Any existing entry is foreign/busy, irrespective of age/type.
                scope.check()
                try:
                    os.mkdir(_LOCK, 0o700, dir_fd=scope.state.fd)
                except FileExistsError:
                    return result("busy", "An existing checkpoint lock must be resolved by its owner.")
                owned = os.stat(_LOCK, dir_fd=scope.state.fd, follow_symlinks=False)
                try:
                    scope.check()
                    state, warning = scope.read_state()
                    if warning:
                        warnings.append(warning)
                    age = _context_age(scope, observation.wall)
                    if request.trigger == "due" and state is not None and observation.boot is not None:
                        last = _number(state["last_success_monotonic"], nonnegative=True)
                        elapsed = observation.monotonic - last
                        if state["boot_id"] == observation.boot and 0 <= elapsed < request.snapshot.settings.backup.interval_minutes * 60:
                            return result("not-due", "Wait for the policy interval; no snapshot or publication was attempted.")
                    scope.check()
                    snapshot = S.create_snapshot(request.snapshot, fault=scope.capture_guard)
                    scope.check()
                    if snapshot.status == "partial":
                        return result("partial", "Resolve snapshot omissions; last-success cadence was not advanced.")
                    _require(snapshot.status in ("complete", "unchanged") and snapshot.snapshot_path is not None
                             and snapshot.identity == request.snapshot.settings.identity, "invalid_snapshot_result")
                    completed = _observe(active_clock)
                    scope.write_state(snapshot, completed)
                    duplicate = (snapshot.status == "unchanged" and request.trigger == "event"
                                 and request.event_id is not None and state is not None
                                 and state["last_event_id"] == request.event_id
                                 and state["last_snapshot"] == str(snapshot.snapshot_path))
                    return result("duplicate" if duplicate else "captured",
                                  "Saved artifacts captured; configure periodic/ref coverage separately. Publication was not attempted.")
                finally:
                    scope.remove_owned(_LOCK, owned, directory=True)
    except _Deadline:
        warnings.append("deadline_exceeded")
        return result("failed", "Deadline expired; inspect the last snapshot before retrying. No publication was attempted.")
    except _Refused as error:
        warnings.append(str(error))
        return result("failed", "Checkpoint refused; resolve the reported safety condition before retrying.")
    except C.ContinuityError:
        warnings.append("snapshot_or_configuration_refused")
        return result("failed", "Snapshot/configuration checks failed; inspect the approved inputs before retrying.")
    except (OSError, ValueError, TypeError, OverflowError):
        warnings.append("checkpoint_io_or_input_failed")
        return result("failed", "Checkpoint did not complete; inspect approved paths and inputs before retrying.")
