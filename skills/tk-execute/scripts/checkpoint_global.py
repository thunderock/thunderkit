"""Explicit global-note scheduling; ownership is not saved consent.

Only a caller-qualified scheduler and fresh apply/remove approval change the
host. Ticks use pinned helpers, current explicit policy and cooperative local
I/O budgets; neither Git, subprocesses, models nor publication are involved.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import stat
import sys
import time
from typing import cast

import backup_store as S
import checkpoint_host as Host
import checkpoint_runtime as H
import continuity_config as C

_MODULES = (*Host._MODULES, "checkpoint_global")
_BINDING = "global-binding.json"
_LAUNCHER = "global-tick"
_RECEIPT = "global-receipt.json"
_GUARD = "global-guard.json"
_LOCK = ".global-operation.lock"
_PYTHON_MAX = 64 * 1024 * 1024


@dataclass(frozen=True)
class GlobalScheduleRequest:
    snapshot: C.SnapshotRequest
    state_dir: Path
    python: Path
    global_config: Path


@dataclass(frozen=True)
class GlobalPreview:
    request: GlobalScheduleRequest
    status: Host.Status
    blockers: tuple[str, ...] = ()
    binding: bytes = b""
    launcher: bytes = b""
    previous: bytes = b""
    block: bytes = b""


@dataclass(frozen=True)
class GlobalInstallation:
    state_dir: Path
    identity: C.ScopeIdentity
    periodic_command: str
    files: tuple[Host.OwnedFile, ...]
    pending: tuple[Host.OwnedFile, ...]
    receipt: Host.OwnedFile
    guard: Host.OwnedFile | None
    phase: str
    initial_absent_sha256: str
    absent_sha256: str
    binding: bytes


@dataclass(frozen=True)
class GlobalResult:
    status: Host.Status
    installation: GlobalInstallation | None = None
    blockers: tuple[str, ...] = ()
    retained_paths: tuple[Path, ...] = ()


_require = Host._require
_object = Host._object
_path = Host._path
_text = Host._text
_encoded = Host._encoded


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pin(path: Path, limit: int = C.CONFIG_MAX_BYTES) -> Host.OwnedFile:
    data, info = Host._read(path, limit)
    return Host.OwnedFile(path, info.st_dev, info.st_ino, info.st_mode, _digest(data))


def _file(value: C.JsonValue) -> Host.OwnedFile:
    row = _object(value, {"path", "device", "inode", "mode", "sha256"})
    return Host.OwnedFile(_path(row["path"]), Host._integer(row["device"]),
        Host._integer(row["inode"]), Host._integer(row["mode"]), C.validate_sha256(row["sha256"]))


def _files(value: C.JsonValue) -> tuple[Host.OwnedFile, ...]:
    _require(isinstance(value, list) and len(value) <= len(_MODULES))
    assert isinstance(value, list)
    return tuple(_file(row) for row in value)


def _snapshot(value: C.JsonValue) -> C.SnapshotRequest:
    raw = _object(value, {"settings", "storage", "approved_sources", "context_entrypoints",
        "include_history", "history_ref", "verified_remote_oid", "remote_evidence"})
    cfg = _object(raw["settings"], {"identity", "project_root", "backup_root", "context_path", "context_source",
        "backup", "git", "limits", "approved_sources"})
    backup = _object(cfg["backup"], {"enabled", "interval_minutes", "retention", "prune"})
    git = _object(cfg["git"], {"push_after_commit"})
    limits = _object(cfg["limits"], set(asdict(C.Limits())))
    _require(cfg["project_root"] is None)
    settings = C.ResolvedSettings(C.decode_identity(cfg["identity"]), None, _path(cfg["backup_root"]),
        None if cfg["context_path"] is None else _path(cfg["context_path"]),
        cast(C.ContextSource, cfg["context_source"]),
        C.BackupPolicy(cast(bool, backup["enabled"]), cast(int, backup["interval_minutes"]),
            cast(int, backup["retention"]), cast(bool, backup["prune"])),
        C.GitPolicy(cast(bool, git["push_after_commit"])), C.Limits(**cast(dict[str, int], limits)),
        Host._paths(cfg["approved_sources"]))
    store = _object(raw["storage"], {"root", "device", "inode", "backing_fingerprint", "mount_fingerprint", "durability"})
    storage = C.StorageIdentity(_path(store["root"]), cast(int, store["device"]), cast(int, store["inode"]),
        cast(str | None, store["backing_fingerprint"]), cast(str | None, store["mount_fingerprint"]), _text(store["durability"]))
    entries = raw["context_entrypoints"]
    _require(isinstance(entries, list) and raw["include_history"] is False
        and all(raw[key] is None for key in ("history_ref", "verified_remote_oid", "remote_evidence")))
    assert isinstance(entries, list)
    return C.SnapshotRequest(settings, storage, Host._paths(raw["approved_sources"]),
        tuple(C.validate_relpath(entry) for entry in entries))


def _decode_binding(data: bytes) -> tuple[GlobalScheduleRequest, tuple[Host.OwnedFile, ...],
                                         Host.OwnedFile, tuple[tuple[Path, tuple[int, ...]], ...]]:
    raw = _object(C.decode_json(data), {"schema", "kind", "snapshot", "state_dir", "python", "global_config",
        "runtime", "interpreter", "directories"})
    C._schema(raw, "global-checkpoint-binding")
    request = GlobalScheduleRequest(_snapshot(raw["snapshot"]), _path(raw["state_dir"]),
        _path(raw["python"]), _path(raw["global_config"]))
    _require(request.snapshot.settings.identity.kind == "global")
    runtime, interpreter = _files(raw["runtime"]), _file(raw["interpreter"])
    _require(interpreter.path == request.python and bool(interpreter.mode & 0o111)
        and len(runtime) == len(_MODULES))
    parent = runtime[0].path.parent
    _require(tuple(item.path for item in runtime) == tuple(parent / (name + ".py") for name in _MODULES))
    rows = raw["directories"]
    _require(isinstance(rows, list))
    assert isinstance(rows, list)
    directories = []
    for row in rows:
        _require(isinstance(row, list) and len(row) == 2)
        assert isinstance(row, list)
        _require(isinstance(row[1], list) and len(row[1]) == 5)
        assert isinstance(row[1], list)
        directories.append((_path(row[0]), tuple(Host._integer(v) for v in row[1])))
    expected = {p for root in _roots(request, parent) for p in (root, *root.parents)}
    _require(len(directories) == len(expected) and {p for p, _ in directories} == expected)
    return request, runtime, interpreter, tuple(directories)


def _roots(request: GlobalScheduleRequest, runtime: Path) -> tuple[Path, ...]:
    return (request.state_dir, request.snapshot.storage.root, request.global_config.parent, runtime,
            *(p.parent for p in request.snapshot.approved_sources))


def _command(state: Path) -> str:
    command = shlex.join((str(state / _LAUNCHER), "--tick"))
    _require(not any(c in command for c in "\n\r%"), "unsafe_schedule_command")
    return command


def _block(state: Path) -> bytes:
    marker = ("# thunderkit-global-checkpoint " + _digest(str(state / _LAUNCHER).encode())).encode()
    return marker + b" begin\n* * * * * " + _command(state).encode() + b"\n" + marker + b" end\n"


def _launcher(request: GlobalScheduleRequest, runtime: tuple[Host.OwnedFile, ...],
              interpreter: Host.OwnedFile, digest: str) -> bytes:
    # Every source is read and pinned before any helper executes. Executed bytes
    # are those exact reads, not an import that reopens a replaceable source.
    pins = [(str(p.path), p.device, p.inode, p.mode, p.sha256) for p in (interpreter, *runtime)]
    return (f"#!{request.python} -I\n" + """import sys,time
end=time.monotonic()+60.0
if sys.argv[1:]!=['--tick']: raise SystemExit(2)
import hashlib,importlib.util,json,os,stat
from pathlib import Path
""" + f"pins={pins!r}\n" + """try:
 sources=[]
 for index,(path,device,inode,mode,digest) in enumerate(pins):
  limit=67108864 if index==0 else 1048576
  p=Path(path); fd=os.open(p.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:
   for part in p.parts[1:-1]:
    if time.monotonic()>=end: raise ValueError()
    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
    os.close(fd); fd=child
   source=os.open(p.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
   try:
    info=os.fstat(source)
    if (info.st_dev,info.st_ino,info.st_mode)!=(device,inode,mode) or not stat.S_ISREG(mode) or info.st_nlink!=1 or mode&0o022 or info.st_uid not in (0,os.geteuid()) or info.st_size>limit: raise ValueError()
    parts=[]; size=0
    while size<=limit:
     if time.monotonic()>=end: raise ValueError()
     chunk=os.read(source,min(65536,limit+1-size))
     if not chunk: break
     parts.append(chunk); size+=len(chunk)
    data=b''.join(parts)
    if info!=os.fstat(source) or size!=info.st_size or hashlib.sha256(data).hexdigest()!=digest: raise ValueError()
   finally: os.close(source)
  finally: os.close(fd)
  if index: sources.append((path,data))
  elif sys.executable!=path: raise ValueError()
 sys.dont_write_bytecode=True
 for path,data in sources:
  if time.monotonic()>=end: raise ValueError()
  name=Path(path).stem; spec=importlib.util.spec_from_file_location(name,path)
  module=importlib.util.module_from_spec(spec); sys.modules[name]=module
  exec(compile(data,path,'exec'),module.__dict__)
except Exception:
 print(json.dumps({'error':{'code':'global_runtime_changed','detail':'Pinned runtime unavailable.'}}),file=sys.stderr)
 raise SystemExit(2)
""" + f"args=['run','--state-dir',{str(request.state_dir)!r},'--identity',{request.snapshot.settings.identity.id!r},'--expect',{digest!r}]\n"
        + "raise SystemExit(sys.modules['checkpoint_global'].main(args,end=end))\n").encode()


def _policy(request: GlobalScheduleRequest) -> None:
    current = C.resolve_continuity(global_config=request.global_config, scope="global",
        expected_identity=request.snapshot.settings.identity, environment={})
    _require(current.backup.enabled, "backup_disabled")
    _require(current == request.snapshot.settings, "global_policy_changed")


def _validate(request: GlobalScheduleRequest) -> None:
    _require(type(request) is GlobalScheduleRequest and sys.platform.startswith("linux"), "unsupported_host")
    for path in (request.state_dir, request.global_config, request.python):
        _path(str(path))
        C.safe_path(path, must_exist=True)
    try:
        H._validated(H.CheckpointRequest(request.snapshot, request.state_dir))
    except H._Refused:
        Host._fail("invalid_global_request")
    _require(request.snapshot.settings.identity.kind == "global", "identity_mismatch")
    _require(stat.S_IMODE(request.state_dir.lstat().st_mode) == 0o700, "unsafe_state_directory")
    _require(not any(c.isspace() for c in str(request.python)), "unsafe_interpreter")
    _policy(request)
    S._preflight(request.snapshot)
    # Reject rather than silently capture installation metadata or execution
    # inputs through an encompassing selected directory.
    excluded = (request.state_dir, request.global_config, request.python, Path(__file__).absolute().parent)
    for source in request.snapshot.approved_sources:
        _require(all(not source.is_relative_to(p) and not p.is_relative_to(source) for p in excluded), "global_source_overlap")


def _state_capacity(request: GlobalScheduleRequest) -> None:
    # H has a separate 64KiB cadence envelope; refuse before staging.
    identity = request.snapshot.settings.identity
    envelope = {"schema": 1, "identity": asdict(identity), "project_root": None,
        "backup_root": str(request.snapshot.storage.root),
        "approved_sources": list(map(str, request.snapshot.approved_sources)),
        "binding_sha256": "f" * 64, "boot_id": "b" * 256,
        "last_success_monotonic": 1.7976931348623157e308, "last_event_id": None,
        "last_snapshot": str(request.snapshot.storage.root / "global" / identity.id / "snapshots" / ("f" * 32))}
    _require(len(_encoded(envelope)) <= H.STATE_MAX_BYTES, "global_state_budget_exceeded")


def _metadata_capacity(request: GlobalScheduleRequest, binding: bytes, launcher: bytes) -> None:
    # Worst-width identity integers bound both ownership envelopes before any
    # exclusive writes; only real observed identities are ever persisted.
    maximum = 2**128 - 1
    state = request.state_dir
    files = tuple(Host.OwnedFile(state / name, maximum, maximum, stat.S_IFREG | mode, _digest(data))
        for name, data, mode in ((_BINDING, binding, 0o600), (_LAUNCHER, launcher, 0o700)))
    pending = tuple(replace(item, path=state / name)
        for item, name in zip(files, (".global-binding.stage", ".global-tick.stage")))
    receipt = Host.OwnedFile(state / _RECEIPT, maximum, maximum, stat.S_IFREG | 0o600, "f" * 64)
    candidate = GlobalInstallation(state, request.snapshot.settings.identity, _command(state),
        files, pending, receipt, None, "removing", "f" * 64, "f" * 64, binding)
    _receipt_bytes(candidate)
    _guard_bytes(candidate)


def preview_global(request: GlobalScheduleRequest, driver: Host.ScheduleDriver, *,
                   service_qualified: bool = False, exclusive_writer: bool = False) -> GlobalPreview:
    """Read-only admission; qualification flags are external evidence, not proof."""
    try:
        _require(service_qualified is True and exclusive_writer is True, "scheduler_unqualified")
        with Host._operation(H.MAX_DEADLINE_SECONDS) as budget, S.work_scope(budget):
            _validate(request)
            _state_capacity(request)
            _require(not any(os.path.lexists(request.state_dir / name) for name in
                (_BINDING, _LAUNCHER, _RECEIPT, _GUARD, ".global-binding.stage", ".global-tick.stage")), "global_installation_exists")
            runtime = tuple(_pin(Path(__file__).absolute().parent / (name + ".py")) for name in _MODULES)
            interpreter = _pin(request.python, _PYTHON_MAX)
            _require(bool(interpreter.mode & 0o111), "unsafe_interpreter")
            directories = Host._directories(_roots(request, runtime[0].path.parent))
            binding = _encoded({"schema": 1, "kind": "global-checkpoint-binding", "snapshot": asdict(request.snapshot),
                "state_dir": request.state_dir, "python": request.python, "global_config": request.global_config,
                "runtime": [asdict(p) for p in runtime], "interpreter": asdict(interpreter), "directories": directories})
            launcher = _launcher(request, runtime, interpreter, _digest(binding))
            _metadata_capacity(request, binding, launcher)
            scope = request.snapshot.storage.root / "global" / request.snapshot.settings.identity.id
            path, manifest, _ = S._prior(scope, request.snapshot.settings.identity)
            _require(path is not None and manifest is not None and manifest.completion == "complete"
                and manifest.history.status == "not-applicable", "first_snapshot_required")
            previous = Host._table(driver.read())
            block = _block(request.state_dir)
            _require(block.split(b" begin\n")[0] not in previous, "schedule_already_present")
            Host._table(previous + block)
            return GlobalPreview(request, "approval_required", binding=binding, launcher=launcher, previous=previous, block=block)
    except (C.ContinuityError, OSError, ValueError, TypeError) as error:
        return GlobalPreview(request, "blocked", (error.code if isinstance(error, C.ContinuityError) else "global_inspection_failed",))


def _receipt_bytes(installation: GlobalInstallation) -> bytes:
    return _encoded({"schema": 1, "kind": "global-checkpoint-installation", "state_dir": installation.state_dir,
        "identity": asdict(installation.identity), "periodic_command": installation.periodic_command,
        "files": [asdict(p) for p in installation.files], "pending": [asdict(p) for p in installation.pending],
        "initial_absent_sha256": installation.initial_absent_sha256, "binding": installation.binding.decode()})


def _guard_bytes(installation: GlobalInstallation) -> bytes:
    return _encoded({"schema": 1, "kind": "global-checkpoint-guard", "receipt": asdict(installation.receipt),
        "installation": _receipt_bytes(installation).decode(), "phase": installation.phase,
        "absent_sha256": installation.absent_sha256})


def _load(state: Path, identity: C.ScopeIdentity) -> GlobalInstallation:
    _path(str(state))
    _require(type(identity) is C.ScopeIdentity and identity.kind == "global", "identity_mismatch")
    guard: Host.OwnedFile | None = None
    phase, absent = "staged", _digest(b"")
    if os.path.lexists(state / _GUARD):
        guard = Host._record(state / _GUARD)
        raw_guard = _object(C.decode_json(Host._private(guard.path)),
            {"schema", "kind", "receipt", "installation", "phase", "absent_sha256"})
        C._schema(raw_guard, "global-checkpoint-guard")
        receipt = _file(raw_guard["receipt"])
        _require(receipt.path == state / _RECEIPT and receipt.mode == stat.S_IFREG | 0o600)
        data = _text(raw_guard["installation"]).encode()
        phase = _text(raw_guard["phase"])
        _require(phase in ("staged", "active", "removing"))
        absent = C.validate_sha256(raw_guard["absent_sha256"])
        _require(_digest(data) == receipt.sha256)
        if os.path.lexists(receipt.path):
            _require(Host._record(receipt.path) == receipt and Host._private(receipt.path) == data, "owned_global_changed")
        else:
            _require(phase == "removing", "owned_global_changed")
    else:
        receipt = Host._record(state / _RECEIPT)
        data = Host._private(receipt.path)
    raw = _object(C.decode_json(data), {"schema", "kind", "state_dir", "identity", "periodic_command", "files", "pending", "binding", "initial_absent_sha256"})
    C._schema(raw, "global-checkpoint-installation")
    initial_absent = C.validate_sha256(raw["initial_absent_sha256"])
    if guard is None:
        absent = initial_absent
    _require(raw["state_dir"] == str(state) and C.decode_identity(raw["identity"]) == identity, "identity_mismatch")
    binding = _text(raw["binding"]).encode()
    request, runtime, interpreter, directories = _decode_binding(binding)
    _require(request.state_dir == state and request.snapshot.settings.identity == identity, "identity_mismatch")
    Host._check_directories(directories)
    S._check_storage(request.snapshot.storage)
    command = _text(raw["periodic_command"])
    _require(command == _command(state))
    files, pending = _files(raw["files"]), _files(raw["pending"])
    expected = ((state / _BINDING, binding, 0o600, state / ".global-binding.stage"),
                (state / _LAUNCHER, _launcher(request, runtime, interpreter, _digest(binding)), 0o700, state / ".global-tick.stage"))
    _require(len(files) == len(pending) == 2)
    for item, stage, (path, content, mode, temporary) in zip(files, pending, expected):
        _require(item.path == path and item.mode == stat.S_IFREG | mode and item.sha256 == _digest(content))
        _require(stage == replace(item, path=temporary))
        present = []
        for candidate in (item, stage):
            if os.path.lexists(candidate.path):
                _require(Host._record(candidate.path) == candidate, "owned_global_changed")
                present.append(candidate.path)
        _require(len(present) <= 1 and (bool(present) or phase == "removing"), "owned_global_changed")
        if phase == "active":
            _require(present == [item.path], "owned_global_changed")
        if guard is None:
            _require(present == [stage.path], "owned_global_changed")
    return GlobalInstallation(state, identity, command, files, pending, receipt, guard, phase, initial_absent, absent, binding)


def load_global(state_dir: Path, *, identity: C.ScopeIdentity) -> GlobalInstallation:
    """Exact durable ownership, including staged/uncertain/partial removal states."""
    try:
        with Host._operation(H.MAX_DEADLINE_SECONDS) as budget, S.work_scope(budget):
            return _load(state_dir, identity)
    except (OSError, ValueError, TypeError, KeyError):
        Host._fail("invalid_global_installation")


def _retained(installation: GlobalInstallation | None) -> tuple[Path, ...]:
    if installation is None:
        return ()
    paths = {p.path for p in (*installation.files, *installation.pending, installation.receipt)}
    paths.add(installation.state_dir / _GUARD)
    return tuple(sorted(p for p in paths if os.path.lexists(p)))


def _update(installation: GlobalInstallation, *, phase: str, absent: str) -> GlobalInstallation:
    updated = replace(installation, phase=phase, absent_sha256=absent)
    if installation.guard is None:
        Host._write_new(installation.state_dir / _GUARD, _guard_bytes(updated), 0o600)
    else:
        _require(Host._record(installation.guard.path) == installation.guard, "owned_global_changed")
        Host._replace_owned(installation.guard.path, _guard_bytes(updated))
    return replace(updated, guard=Host._record(installation.state_dir / _GUARD))


def apply_global(preview: GlobalPreview, driver: Host.ScheduleDriver, *, approval: bool = False) -> GlobalResult:
    if approval is not True:
        return GlobalResult("approval_required")
    if preview.status != "approval_required":
        return GlobalResult("blocked", blockers=preview.blockers)
    installation = None
    staged: list[Host.OwnedFile] = []
    receipt_created = False
    try:
        with Host._operation(H.MAX_DEADLINE_SECONDS) as budget, S.work_scope(budget):
            state = preview.request.state_dir
            with Host._lock(state, _LOCK):
                _require(preview_global(preview.request, driver, service_qualified=True, exclusive_writer=True) == preview,
                         "stale_global_preview")
                for name, data, mode in ((".global-binding.stage", preview.binding, 0o600),
                                          (".global-tick.stage", preview.launcher, 0o700)):
                    staged.append(Host._write_new(state / name, data, mode))
                files = tuple(replace(item, path=state / name) for item, name in zip(staged, (_BINDING, _LAUNCHER)))
                placeholder = Host.OwnedFile(state / _RECEIPT, 0, 0, stat.S_IFREG | 0o600, "0" * 64)
                installation = GlobalInstallation(state, preview.request.snapshot.settings.identity, _command(state),
                    files, tuple(staged), placeholder, None, "staged", _digest(preview.previous), _digest(preview.previous), preview.binding)
                try:
                    receipt = Host._write_new(state / _RECEIPT, _receipt_bytes(installation), 0o600)
                except FileExistsError:
                    # Exclusive-create collision never confers ownership.
                    raise
                except (C.ContinuityError, OSError, ValueError, TypeError):
                    # A completed exclusive write can fail during directory
                    # fsync/readback. Preserve staging for fresh exact validation;
                    # existence alone is not a usable ownership record.
                    receipt_created = os.path.lexists(state / _RECEIPT)
                    raise
                receipt_created = True
                installation = replace(installation, receipt=receipt)
                installation = _update(installation, phase="staged", absent=_digest(preview.previous))
                _require(_load(state, installation.identity) == installation, "global_receipt_readback_failed")
                for stage, item in zip(staged, files):
                    _require(Host._record(stage.path) == stage and not os.path.lexists(item.path), "owned_global_changed")
                    with S._directory(state) as fd:
                        os.rename(stage.path.name, item.path.name, src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                installation = _update(installation, phase="active", absent=_digest(preview.previous))
                _require(_load(state, installation.identity) == installation, "global_receipt_readback_failed")
                _require(driver.replace(preview.previous, preview.previous + preview.block), "schedule_conflict")
                _require(driver.read() == preview.previous + preview.block, "schedule_readback_failed")
        return GlobalResult("configured", installation)
    except (C.ContinuityError, OSError, ValueError, TypeError) as error:
        if not receipt_created:
            for item in reversed(staged):
                if os.path.lexists(item.path):
                    Host._unlink(item)
            installation = None
        return GlobalResult("blocked", installation,
            (error.code if isinstance(error, C.ContinuityError) else "global_install_failed",), _retained(installation))


def remove_global(installation: GlobalInstallation, driver: Host.ScheduleDriver, *, approval: bool = False) -> GlobalResult:
    if approval is not True:
        return GlobalResult("approval_required")
    try:
        with Host._operation(H.MAX_DEADLINE_SECONDS) as budget, S.work_scope(budget):
            # The complete exact set is checked before any lock, guard update,
            # transport write or deletion. Receipt replacement cannot adopt files.
            _require(load_global(installation.state_dir, identity=installation.identity) == installation, "owned_global_changed")
            with Host._lock(installation.state_dir, _LOCK):
                current = Host._table(driver.read())
                block = _block(installation.state_dir)
                marker = block.split(b" begin\n")[0]
                if current.count(block) == 1 and current.count(marker) == 2:
                    desired = current.replace(block, b"", 1)
                    installation = _update(installation, phase=installation.phase, absent=_digest(desired))
                    _require(driver.replace(current, desired), "schedule_conflict")
                else:
                    _require(marker not in current and _digest(current) == installation.absent_sha256, "owned_schedule_changed")
                    desired = current
                _require(driver.read() == desired, "schedule_readback_failed")
                installation = _update(installation, phase="removing", absent=_digest(desired))
                # Read back durable intent before allowing absent original members.
                _require(_load(installation.state_dir, installation.identity) == installation, "global_receipt_readback_failed")
                for item in (*reversed(installation.files), *installation.pending, installation.receipt):
                    if os.path.lexists(item.path):
                        Host._unlink(item)
                assert installation.guard is not None
                Host._unlink(installation.guard)
        return GlobalResult("removed")
    except (C.ContinuityError, OSError, ValueError, TypeError) as error:
        return GlobalResult("blocked", installation,
            (error.code if isinstance(error, C.ContinuityError) else "global_remove_failed",), _retained(installation))


def _age(request: C.SnapshotRequest, saved: Path | None) -> float | None:
    if saved is None or not request.context_entrypoints:
        return None
    manifest = S.verify_snapshot(saved, request.settings.identity, request.settings.limits)
    if manifest.completion != "complete" or manifest.context_entrypoints != request.context_entrypoints:
        return None
    files = {entry.relpath: entry for entry in manifest.files}
    ages = []
    for entry in manifest.context_entrypoints:
        matches = []
        for index, source in enumerate(request.approved_sources):
            prefix = f"sources/{index}/{source.name}"
            if entry == prefix:
                matches.append(source)
            elif entry.startswith(prefix + "/"):
                matches.append(source / entry[len(prefix) + 1:])
        if len(matches) != 1:
            return None
        path = matches[0]
        try:
            with S._directory(path.parent) as fd:
                data, info = S._read(fd, path.name, request.settings.limits.max_file_bytes)
            if _digest(data) != files[entry].sha256 or len(data) != files[entry].size:
                return None
            age = time.time() - info.st_mtime
            if not math.isfinite(age) or age < 0:
                return None
            ages.append(age)
        except (C.ContinuityError, OSError):
            return None
    return max(ages) if ages else None


def _run(state: Path, identity: C.ScopeIdentity, expect: str, budget: Host._Budget) -> C.JsonObject:
    installation = load_global(state, identity=identity)
    _require(installation.phase == "active", "global_installation_inactive")
    _require(_digest(installation.binding) == C.validate_sha256(expect), "global_binding_changed")
    request, runtime, interpreter, _ = _decode_binding(installation.binding)
    _policy(request)
    _require(_pin(request.python, _PYTHON_MAX) == interpreter and all(_pin(p.path) == p for p in runtime), "global_runtime_changed")
    with Host._lock(state, _LOCK), Host._lock(state, H._LOCK):
        # Reuse H's global scope, validated cadence codec and exact state writer,
        # but not its signal-owning deadline wrapper. No module patching occurs.
        timer = H._Timer(budget.remaining(H.MAX_DEADLINE_SECONDS))
        capture = H._validated(H.CheckpointRequest(request.snapshot, state, trigger="due", deadline_seconds=timer.remaining(H.MAX_DEADLINE_SECONDS)))
        with H._resources(timer) as stack:
            scope = H._Scope(capture, stack, timer)
            observation = H._observe(H._SystemClock(timer))
            previous, warning = scope.read_state()
            status: H.Status = "captured"
            snapshot = None
            if previous is not None and observation.boot is not None:
                elapsed = observation.monotonic - H._number(previous["last_success_monotonic"], nonnegative=True)
                if previous["boot_id"] == observation.boot and 0 <= elapsed < request.snapshot.settings.backup.interval_minutes * 60:
                    status = "not-due"
            if status == "captured":
                snapshot = S.create_snapshot(request.snapshot, fault=scope.capture_guard)
                scope.check()
                if snapshot.status == "partial":
                    status = "partial"
                else:
                    planned = scope.plan_state(snapshot, H._observe(H._SystemClock(timer)), tip=None, reachable=())
                    scope.write_state(planned, "disabled")
            saved = snapshot.snapshot_path if snapshot is not None else (
                _path(previous["last_snapshot"]) if previous is not None else None)
            age = _age(request.snapshot, saved)
            scope.check()
            result = H.CheckpointResult(status, snapshot, None, age, (), ("service_not_proven",),
                "Wait for the approved interval." if status == "not-due" else "Inspect the saved global snapshot.",
                (warning,) if warning else ())
            return C.decode_json(_encoded(asdict(result)))


def main(argv: list[str] | None = None, *, end: float | None = None) -> int:
    """Internal pinned-launcher entrypoint; external setup CLI is separate."""
    try:
        budget = Host._Budget(min(time.monotonic() + H.MAX_DEADLINE_SECONDS, end) if end is not None
                              else time.monotonic() + H.MAX_DEADLINE_SECONDS, "global_deadline")
        with Host._operation(budget.remaining(H.MAX_DEADLINE_SECONDS)), S.work_scope(budget):
            parser = Host._Parser(allow_abbrev=False)
            parser.add_argument("command", choices=("run",))
            parser.add_argument("--state-dir", required=True)
            parser.add_argument("--identity", required=True)
            parser.add_argument("--expect", required=True)
            args = parser.parse_args(argv)
            result = _run(_path(args.state_dir), C.ScopeIdentity("global", args.identity), args.expect, budget)
            budget.check()
        print(json.dumps(result, allow_nan=False))
        return 0 if result["status"] in ("captured", "not-due") else 2
    except (C.ContinuityError, OSError, ValueError, TypeError, KeyError, H._Refused, H._Deadline) as error:
        code = error.code if isinstance(error, C.ContinuityError) else "global_checkpoint_failed"
        print(json.dumps({"error": {"code": code, "detail": "Global checkpoint did not complete."}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
