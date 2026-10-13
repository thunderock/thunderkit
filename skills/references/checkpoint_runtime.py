"""Bounded event/due capture and exact-tip publication with fresh caller authority.

The in-process deadline requires Linux/POSIX timers on the main thread with no
active ITIMER_REAL timer. Unsupported contexts refuse before writes. As with S,
this requires ordinary responsive local storage; Python signals cannot preempt
an uninterruptible kernel I/O operation. Git uses bounded local subprocesses;
publication uses G's trusted driver without replacing host verification policy.
State is an inert cadence/history observation, never a grant or semantic summary.
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
import checkpoint_push as G
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


class AuthorityProvider(Protocol):
    """Attest current exact-OID ownership/review/tests/branch safety in-process."""

    def __call__(self, request: C.PushRequest) -> C.PushGrant | None: ...


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
    # SnapshotRequest is project scope authority; C enforces the global subset.
    _require(bool(s.approved_sources), "unapproved_sources")
    _require(s.storage.durability in ("software-readback", "operator-qualified"), "storage_unqualified")
    # Captured remote assertions cannot substitute for G's live readback.
    _require(s.verified_remote_oid is None and s.remote_evidence is None, "saved_remote_evidence_refused")
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
        self.request_bytes = _encoded(asdict(request.snapshot))
        self.end = end
        self.state = _Directory(request.state_dir, stack)
        info = self.state.info
        _require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(), "unsafe_state_directory")
        self.store = _Directory(request.snapshot.storage.root, stack)
        project = request.snapshot.settings.project_root
        self.project = _Directory(project, stack) if project else None
        self.git_directory: _Directory | None = None
        self.git: S._Git | None = None
        if self.project is not None:
            try:
                info = os.stat(".git", dir_fd=self.project.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _require(stat.S_ISDIR(info.st_mode), "unsupported_repository")
                self.git_directory = _Directory(self.project.path / ".git", stack)
                self.git = stack.enter_context(S._git_session())
        _require(self.git is not None or (not request.snapshot.include_history
                 and request.snapshot.history_ref is None), "history_repository_missing")
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
        if self.git_directory is not None:
            binding["git_directory"] = _inode(self.git_directory.info)
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
        _require(_encoded(asdict(self.request.snapshot)) == self.request_bytes, "request_changed")
        for directory in (self.state, self.store, *([self.project] if self.project else [])):
            directory.check()
        for parent, source, original in self.sources:
            parent.check()
            now = os.stat(source.name, dir_fd=parent.fd, follow_symlinks=False)
            _require(_inode(now) == _inode(original) and (stat.S_ISDIR(now.st_mode) or now.st_nlink == 1),
                     "source_changed")
        if self.git_directory:
            self.git_directory.check()
        elif self.project:
            try:
                os.stat(".git", dir_fd=self.project.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _Refused("repository_changed")
        current = S.inspect_storage(self.request.snapshot.storage.root)
        _require(current == replace(self.request.snapshot.storage, durability="unqualified")
                 and current.mount_fingerprint != "unavailable-mount", "storage_changed")

    def capture_guard(self, event: str, path: Path | None) -> None:
        self.check()

    def git_line(self, args: list[str], budget: int = 4096) -> str:
        self.check()
        assert self.git is not None and self.project is not None
        data = self.git.run(self.project.path, args, budget)
        _require(data.endswith(b"\n") and data.count(b"\n") == 1, "invalid_git_output")
        return data[:-1].decode("utf-8", errors="strict")

    def git_tip(self) -> tuple[str, str]:
        """Establish the exact attached repository, refusing synthetic graphs."""
        assert self.git_directory is not None and self.project is not None and self.git is not None
        root = self.project.path
        _require(self.git_line(["rev-parse", "--show-toplevel"]) == str(root)
                 and self.git_line(["rev-parse", "--absolute-git-dir"]) == str(self.git_directory.path)
                 and self.git_line(["rev-parse", "--is-bare-repository"]) == "false", "repository_changed")
        for suffix in ("commondir", "info/grafts", "objects/info/alternates", "objects/info/http-alternates", "shallow"):
            path = self.git_directory.path / suffix
            C.safe_path(path)
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise _Refused("synthetic_history_refused")
        budget = min(STATE_MAX_BYTES, self.request.snapshot.settings.limits.max_snapshot_bytes)
        # --no-includes prevents evaluating repository-controlled includes.
        raw = self.git.run(root, ["config", "--null", "--no-includes", "--local", "--list"], budget)
        _require(not raw or raw.endswith(b"\0"), "invalid_git_output")
        for record in raw.split(b"\0"):
            key = record.partition(b"\n")[0].lower()
            _require(not key.startswith((b"include.", b"includeif.", b"extensions."))
                     and key not in (b"core.worktree", b"core.alternaterefscommand")
                     and not key.endswith((b".promisor", b".partialclonefilter")), "synthetic_history_refused")
        replacements = self.git.run(root, ["for-each-ref", "--format=%(refname)", "refs/replace/"], budget)
        _require(not replacements, "synthetic_history_refused")
        ref = C.validate_ref(self.git_line(["symbolic-ref", "--quiet", "HEAD"]))
        head = C.validate_oid(self.git_line(["rev-parse", "--verify", "HEAD"]))
        _require(self.git_line(["cat-file", "-t", head]) == "commit", "invalid_git_head")
        expected = self.request.snapshot.history_ref
        _require(expected is None or ref == expected, "history_ref_mismatch")
        return ref, head

    def reachable(self, tip: tuple[str, str]) -> tuple[str, ...]:
        """Bound both the object listing and total uncompressed object bytes."""
        assert self.git is not None and self.project is not None
        limits = self.request.snapshot.settings.limits
        budget = min(limits.max_files * 65, limits.max_snapshot_bytes, STATE_MAX_BYTES)
        git, root = self.git, self.project.path

        def listing(args: list[str]) -> tuple[str, ...]:
            self.check()
            data = git.run(root, args, budget)
            _require(data.endswith(b"\n"), "invalid_git_output")
            oids = tuple(C.validate_oid(item.decode("ascii")) for item in data[:-1].split(b"\n"))
            _require(len(oids) <= limits.max_files and len(set(oids)) == len(oids)
                     and all(len(oid) == len(tip[1]) for oid in oids), "history_budget_exceeded")
            return oids

        objects = listing(["rev-list", "--objects", "--no-object-names", tip[1], "--"])
        total = 0
        for oid in objects:
            size = self.git_line(["cat-file", "-s", oid], 32)
            _require(size.isascii() and size.isdecimal(), "invalid_git_output")
            total += int(size)
            _require(total <= limits.max_snapshot_bytes, "history_budget_exceeded")
        commits = listing(["rev-list", "--reverse", "--topo-order", tip[1], "--"])
        _require(tip[1] in commits and set(commits) <= set(objects), "invalid_git_output")
        _require(self.git_tip() == tip, "repository_changed")
        return commits

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
            if self.git is not None:
                observation_fields |= {"observed_oids", "ref", "head", "publication_status"}
            _require(set(raw) == set(self.template) | observation_fields, "state_corrupt")
            _require(_encoded({key: raw[key] for key in self.template}) == _encoded(self.template), "state_foreign")
            _number(raw["last_success_monotonic"], nonnegative=True)
            if self.git is not None:
                ref = C.validate_ref(raw["ref"])
                head = C.validate_oid(raw["head"])
                _require(self.request.snapshot.history_ref in (None, ref), "state_foreign")
                seen = raw["observed_oids"]
                _require(isinstance(seen, list), "state_corrupt")
                assert isinstance(seen, list)
                oids = [C.validate_oid(oid) for oid in seen]
                _require(len(oids) <= self.request.snapshot.settings.limits.max_files
                         and len(set(oids)) == len(oids) and head in oids
                         and all(len(oid) == len(head) for oid in oids), "state_corrupt")
                _require(raw["publication_status"] in ("disabled", "denied", "pushed", "already-present", "failed", "unknown"),
                         "state_corrupt")
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

    def write_state(self, snapshot: C.SnapshotResult, observation: _Observation, *,
                    tip: tuple[str, str] | None = None, observed: tuple[str, ...] = (),
                    publication_status: str = "disabled") -> None:
        raw = dict(self.template)
        raw.update(boot_id=observation.boot, last_success_monotonic=observation.monotonic,
                   last_event_id=self.request.event_id, last_snapshot=str(snapshot.snapshot_path))
        if tip is not None:
            raw.update(ref=tip[0], head=tip[1], observed_oids=list(observed), publication_status=publication_status)
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


def checkpoint(request: CheckpointRequest, *, clock: Clock | None = None,
               authority: AuthorityProvider | None = None,
               driver: G.GitDriver = G.production_driver) -> CheckpointResult:
    """Capture once on event or due; only complete/unchanged S advances state.

    A repeated event still observes content through S, which owns deduplication.
    Cadence uses the supplied clock; execution limits always use real monotonic.
    KeyboardInterrupt/SystemExit propagate after owned-lock and S cleanup.
    """
    gaps: tuple[str, ...] = ("periodic_unconfigured", "ref_events_unconfigured", "publication_disabled")
    warnings: list[str] = []
    snapshot: C.SnapshotResult | None = None
    push: C.PushResult | None = None
    observed: tuple[str, ...] = ()
    age: float | None = None

    def result(status: Status, action: str) -> CheckpointResult:
        return CheckpointResult(status, snapshot, push, age, observed, gaps, action, tuple(warnings))

    try:
        # Timer qualification precedes filesystem reads and writes.
        _require(type(request) is CheckpointRequest)
        seconds = _number(request.deadline_seconds)
        _require(0 < seconds <= MAX_DEADLINE_SECONDS, "invalid_deadline")
        with _deadline(seconds) as end:
            request = _validated(request)
            _require(authority is None or callable(authority), "invalid_authority_provider")
            if request.snapshot.settings.git.push_after_commit:
                gaps = ("periodic_unconfigured", "ref_events_unconfigured", "fresh_authority_required")
            active_clock = clock if clock is not None else _SystemClock()
            observation = _observe(active_clock)
            if observation.boot is None:
                warnings.append("boot_identity_unknown")
            with ExitStack() as stack:
                scope = _Scope(request, stack, end)
                if scope.git is not None:
                    gaps += ("reachable_history_only",)
                # Any existing entry is foreign/busy, irrespective of age/type.
                scope.check()
                try:
                    os.mkdir(_LOCK, 0o700, dir_fd=scope.state.fd)
                except FileExistsError:
                    return result("busy", "An existing checkpoint lock must be resolved by its owner.")
                owned = os.stat(_LOCK, dir_fd=scope.state.fd, follow_symlinks=False)
                try:
                    scope.check()
                    tip = scope.git_tip() if scope.git is not None else None
                    reachable = scope.reachable(tip) if tip is not None else ()

                    state, warning = scope.read_state()
                    if warning:
                        warnings.append(warning)
                    age = _context_age(scope, observation.wall)
                    known: tuple[str, ...] = ()
                    if tip is not None and state is not None and state["ref"] == tip[0]:
                        saved = state["observed_oids"]
                        assert isinstance(saved, list)
                        known = tuple(C.validate_oid(oid) for oid in saved)
                    seen = set(known)
                    observed = tuple(oid for oid in reachable if oid not in seen)
                    all_observed = (*known, *observed)
                    _require(len(all_observed) <= request.snapshot.settings.limits.max_files, "state_budget_exceeded")
                    graph_same = tip is None or (state is not None and state["ref"] == tip[0]
                                                and state["head"] == tip[1] and not observed)
                    publication_settled = (tip is None or not request.snapshot.settings.git.push_after_commit
                                           or (state is not None and state["publication_status"] in ("pushed", "already-present")))
                    if (request.trigger == "due" and state is not None and observation.boot is not None
                            and graph_same and publication_settled):
                        last = _number(state["last_success_monotonic"], nonnegative=True)
                        elapsed = observation.monotonic - last
                        if state["boot_id"] == observation.boot and 0 <= elapsed < request.snapshot.settings.backup.interval_minutes * 60:
                            return result("not-due", "Wait for the policy interval; no snapshot or publication was attempted.")
                    scope.check()
                    if tip is not None:
                        _require(scope.git_tip() == tip, "repository_changed")
                    snapshot = S.create_snapshot(request.snapshot, fault=scope.capture_guard)
                    scope.check()
                    if tip is not None:
                        _require(scope.git_tip() == tip, "repository_changed")
                    if snapshot.status == "partial":
                        return result("partial", "Resolve snapshot omissions; last-success cadence was not advanced.")
                    _require(snapshot.status in ("complete", "unchanged") and snapshot.snapshot_path is not None
                             and snapshot.identity == request.snapshot.settings.identity, "invalid_snapshot_result")
                    if tip is not None:
                        assert snapshot.snapshot_path is not None
                        manifest = S.verify_snapshot(snapshot.snapshot_path, snapshot.identity, request.snapshot.settings.limits)
                        _require((manifest.ref, manifest.head) == tip and manifest.completion == "complete"
                                 and manifest.history.status == "bundled", "snapshot_tip_mismatch")
                    completed = _observe(active_clock)
                    # A crash or deadline during G leaves a complete snapshot and
                    # an explicitly unsettled observation, never remote success.
                    scope.write_state(snapshot, completed, tip=tip, observed=all_observed,
                                      publication_status="unknown" if request.snapshot.settings.git.push_after_commit else "disabled")
                    duplicate = (snapshot.status == "unchanged" and request.trigger == "event"
                                 and request.event_id is not None and state is not None
                                 and state["last_event_id"] == request.event_id
                                 and state["last_snapshot"] == str(snapshot.snapshot_path))
                    if tip is not None:
                        assert scope.project is not None
                        publish = C.PushRequest(request.snapshot.settings.identity, scope.project.path,
                                                "origin", tip[0], tip[1], request.snapshot.settings.git)
                        push = C.PushResult("denied", tip[1], tip[0], None,
                                            "Fresh publication authority has not been established.")
                        grant: C.PushGrant | None = None
                        if publish.policy.push_after_commit and authority is not None:
                            _require(scope.git_tip() == tip, "repository_changed")
                            binding = _encoded(asdict(publish))
                            try:
                                grant = authority(publish)
                            except Exception:
                                # Provider diagnostics can contain credentials;
                                # interrupts/deadlines (BaseException) propagate.
                                warnings.append("authority_provider_failed")
                            _require(_encoded(asdict(publish)) == binding, "publication_request_changed")
                        scope.check()
                        _require(scope.git_tip() == tip, "repository_changed")
                        push = C.PushResult("unknown", tip[1], tip[0], None,
                                            "Publication may have started; reconcile the exact ref before retrying.")
                        push = G.checkpoint_push(publish, grant, driver=driver)
                        scope.check()
                        scope.write_state(snapshot, completed, tip=tip, observed=all_observed,
                                          publication_status=push.status)
                        if push.status in ("pushed", "already-present", "disabled"):
                            gaps = tuple(gap for gap in gaps if gap != "fresh_authority_required")
                        elif push.status in ("failed", "unknown"):
                            gaps += ("publication_unconfirmed",)
                    return result("duplicate" if duplicate else "captured",
                                  "Saved artifacts captured; inspect the separate publication outcome and configure periodic/ref coverage.")
                finally:
                    scope.remove_owned(_LOCK, owned, directory=True)
    except _Deadline:
        warnings.append("deadline_exceeded")
        if push is not None and push.status == "unknown":
            gaps += ("publication_unconfirmed",)
        return result("failed", "Deadline expired; retain the last complete snapshot and reconcile any unknown publication before retrying.")
    except _Refused as error:
        warnings.append(str(error))
        return result("failed", "Checkpoint refused; resolve the reported safety condition before retrying.")
    except C.ContinuityError:
        warnings.append("snapshot_or_configuration_refused")
        return result("failed", "Snapshot/configuration checks failed; inspect the approved inputs before retrying.")
    except (OSError, ValueError, TypeError, OverflowError):
        warnings.append("checkpoint_io_or_input_failed")
        return result("failed", "Checkpoint did not complete; inspect approved paths and inputs before retrying.")
