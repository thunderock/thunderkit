"""Explicitly installed local Git checkpoint connection; no saved push grants.

The hook and periodic command share the bounded checkpoint runtime. Installation
requires a fresh approval and an empty owned repository-local hooks directory.
A generated periodic command is not proof of a running or configured scheduler.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
from typing import Literal, NoReturn, Protocol, cast
import uuid

import backup_store as S
import checkpoint_runtime as H
import continuity_config as C

Status = Literal["approval_required", "blocked", "configured", "removed"]
_BINDING = "connection.json"
_RECEIPT = "connection-receipt.json"
_SCHEDULE = "connection-schedule.json"
_HOOK = "reference-transaction"
_MODULES = ("continuity_config", "backup_store", "checkpoint_push", "checkpoint_runtime", "checkpoint_host")


@dataclass(frozen=True)
class _Budget:
    end: float
    code: str = "connection_deadline"

    def check(self) -> None:
        _require(time.monotonic() < self.end, self.code)

    def remaining(self, maximum: float) -> float:
        value = min(maximum, self.end - time.monotonic())
        _require(value > 0, self.code)
        return value


_budget: ContextVar[_Budget | None] = ContextVar("host_budget", default=None)


@contextmanager
def _operation(seconds: float) -> Iterator[_Budget]:
    existing = _budget.get()
    budget = existing or _Budget(time.monotonic() + seconds)
    token = _budget.set(budget)
    try:
        budget.check()
        yield budget
        budget.check()
    finally:
        _budget.reset(token)


def _end(seconds: float) -> float:
    budget = _budget.get()
    return time.monotonic() + (seconds if budget is None else budget.remaining(seconds))


@dataclass(frozen=True)
class HostRequest:
    snapshot: C.SnapshotRequest
    state_dir: Path
    python: Path


@dataclass(frozen=True)
class OwnedFile:
    path: Path
    device: int
    inode: int
    mode: int
    sha256: str


@dataclass(frozen=True)
class InstalledConnection:
    state_dir: Path
    hook_path: Path
    files: tuple[OwnedFile, ...]
    directories: tuple[tuple[Path, tuple[int, ...]], ...]
    periodic_command: str
    created_hooks: bool = False
    removing: bool = False
    pending: tuple[OwnedFile, ...] = ()


@dataclass(frozen=True)
class HostPreview:
    request: HostRequest
    status: Status
    hook_path: Path | None = None
    periodic_command: str = ""
    blockers: tuple[str, ...] = ()
    binding: bytes = b""
    hook: bytes = b""
    directories: tuple[tuple[Path, tuple[int, ...]], ...] = ()


@dataclass(frozen=True)
class HostResult:
    status: Status
    receipt: InstalledConnection | None = None
    blockers: tuple[str, ...] = ()
    retained_paths: tuple[Path, ...] = ()


def _fail(code: str) -> NoReturn:
    raise C.ContinuityError(code, "Checkpoint connection refused; inspect the approved installation.")


def _require(condition: bool, code: str = "invalid_connection") -> None:
    if not condition:
        _fail(code)


def _encoded(value: object) -> bytes:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda item: str(item) if isinstance(item, Path) else item).encode() + b"\n"
    _require(len(data) <= C.CONFIG_MAX_BYTES)
    return data


def _identity(path: Path) -> tuple[int, ...]:
    C.safe_path(path, must_exist=True)
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode), "unsafe_connection_parent")
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _directories(paths: tuple[Path, ...]) -> tuple[tuple[Path, tuple[int, ...]], ...]:
    # Bind all ancestors, but permission-gate the installation roots themselves.
    # Shared mount ancestors are not ours to chmod; replacements fail identity checks.
    for path in paths:
        identity = _identity(path)
        _require(identity[3] == os.geteuid() and not identity[2] & 0o022, "unsafe_connection_parent")
    all_paths = {parent for path in paths for parent in (path, *path.parents)}
    return tuple((path, _identity(path)) for path in sorted(all_paths))


def _check_directories(rows: tuple[tuple[Path, tuple[int, ...]], ...]) -> None:
    for path, identity in rows:
        _require(_identity(path) == identity, "connection_path_changed")


def _read(path: Path, limit: int = C.CONFIG_MAX_BYTES) -> tuple[bytes, os.stat_result]:
    with S._directory(path.parent) as parent:
        data, info = S._read(parent, path.name, limit)
    _require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022, "unsafe_connection_file")
    return data, info


def _record(path: Path) -> OwnedFile:
    data, info = _read(path)
    return OwnedFile(path, info.st_dev, info.st_ino, info.st_mode, hashlib.sha256(data).hexdigest())


def _git_environment() -> dict[str, str]:
    _require(os.environ.get("GIT_CONFIG_COUNT", "0") == "0"
             and not os.environ.get("GIT_CONFIG_PARAMETERS"), "unqualified_git_overrides")
    keys = ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update(PATH="/usr/bin:/bin", LC_ALL="C", GIT_CONFIG_COUNT="0", GIT_TERMINAL_PROMPT="0",
               GIT_ALLOW_PROTOCOL="", GIT_OPTIONAL_LOCKS="0")
    return env


def _transport(command: tuple[str, ...], cwd: Path, env: dict[str, str], end: float,
               data: bytes | None, *, timeout_code: str, failure_code: str, size_code: str) -> bytes:
    budget = _Budget(end, timeout_code)
    budget.check()
    output: list[bytes] = []
    count = 0
    offset = 0
    process = subprocess.Popen(command, cwd=cwd, env=env,
        stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            for stream, keep in ((process.stdout, True), (process.stderr, False)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, keep)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                if data:
                    selector.register(process.stdin, selectors.EVENT_WRITE, None)
                else:
                    process.stdin.close()
            while selector.get_map():
                for key, event in selector.select(budget.remaining(5)):
                    budget.check()
                    if event & selectors.EVENT_WRITE:
                        assert data is not None and process.stdin is not None
                        try:
                            offset += os.write(key.fd, data[offset:offset + 4096])
                        except BrokenPipeError:
                            _fail(failure_code)
                        if offset == len(data):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            count += len(chunk)
                            _require(count <= C.CONFIG_MAX_BYTES, size_code)
                            if key.data:
                                output.append(chunk)
            try:
                code = process.wait(timeout=budget.remaining(5))
            except subprocess.TimeoutExpired:
                _fail(timeout_code)
            budget.check()
            _require(code == 0, failure_code)
    finally:
        # The group is ours even if its leader already exited. Never leave a
        # descendant holding pipes; cleanup does not spend/reset work budget.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        finally:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
    return b"".join(output)


def _git(project: Path, args: tuple[str, ...]) -> bytes:
    return _transport(("/usr/bin/git", *args), project, _git_environment(), _end(5), None,
        timeout_code="git_inspection_timeout", failure_code="git_inspection_failed",
        size_code="git_inspection_too_large")


def _hooks(project: Path) -> tuple[Path, str]:
    raw = _git(project, ("config", "--null", "--show-origin", "--no-includes", "--list"))
    fields = raw.split(b"\0")
    _require(not raw or (fields[-1] == b"" and len(fields) % 2 == 1), "invalid_git_config")
    for record in fields[1::2]:
        key = record.partition(b"\n")[0].lower()
        _require(not key.startswith((b"include.", b"includeif.")), "unqualified_git_includes")
    for args, expected in ((("rev-parse", "--show-toplevel"), project),
                           (("rev-parse", "--absolute-git-dir"), project / ".git")):
        _require(_git(project, args) == str(expected).encode() + b"\n", "unsupported_repository")
    selected = _git(project, ("rev-parse", "--git-path", "hooks")).decode().rstrip("\n")
    path = C.literal_path(selected, base=project)
    _require(path == project / ".git/hooks", "external_hook_manager")
    if path.exists():
        _identity(path)
    else:
        C.safe_path(path)
    return path, hashlib.sha256(raw).hexdigest()


def _runtime_files(python: Path) -> tuple[dict[str, str], tuple[int, ...]]:
    _require(python.is_absolute() and not any(c.isspace() or c in "%\\" for c in str(python)), "unsafe_interpreter")
    C.safe_path(python, must_exist=True)
    info = python.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o111 != 0
             and not info.st_mode & 0o022 and info.st_uid in (0, os.geteuid()), "unsafe_interpreter")
    files = {}
    for name in _MODULES:
        path = Path(__file__).absolute().parent / (name + ".py")
        data, _ = _read(path)
        files[str(path)] = hashlib.sha256(data).hexdigest()
    return files, (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns)


def _hook_bytes(python: Path, binding: Path, digest: str, files: dict[str, str]) -> bytes:
    # Verify every bounded, no-follow source before executing any helper bytes.
    text = f"#!{python} -I\n" + """import sys
if len(sys.argv)!=2: raise SystemExit(2)
phase=sys.argv[1]
if phase in ('preparing','prepared','aborted'): raise SystemExit(0)
if phase not in ('committed','--tick'): raise SystemExit(2)
import hashlib,importlib.util,json,os,stat
from pathlib import Path
""" + f"expected={files!r}\nlimit={C.CONFIG_MAX_BYTES}\n" + """try:
 sources={}
 for path,digest in expected.items():
  p=Path(path); fd=os.open(p.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:
   for part in p.parts[1:-1]:
    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
    os.close(fd); fd=child
   source=os.open(p.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
   try:
    before=os.fstat(source)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit or before.st_mode&0o022 or before.st_uid not in (0,os.geteuid()): raise ValueError()
    data=b''
    while len(data)<=limit:
     chunk=os.read(source,min(65536,limit+1-len(data)))
     if not chunk: break
     data+=chunk
    if before!=os.fstat(source) or len(data)!=before.st_size: raise ValueError()
   finally: os.close(source)
  finally: os.close(fd)
  if hashlib.sha256(data).hexdigest()!=digest: raise ValueError()
  sources[path]=data
 sys.dont_write_bytecode=True
 for path,data in sources.items():
  name=Path(path).stem
  spec=importlib.util.spec_from_file_location(name,path)
  module=importlib.util.module_from_spec(spec); sys.modules[name]=module
  exec(compile(data,path,'exec'),module.__dict__)
except Exception:
 print(json.dumps({'error':{'code':'connection_runtime_changed','detail':'Pinned runtime unavailable.'}}),file=sys.stderr)
 raise SystemExit(2)
""" + f"args=['run','--binding',{str(binding)!r},'--expect',{digest!r}]\n" + """args+=['--trigger','due'] if phase=='--tick' else ['--trigger','ref','--phase',phase]
raise SystemExit(sys.modules['checkpoint_host'].main(args))
"""
    return text.encode()


def preview_connections(request: HostRequest) -> HostPreview:
    """Read-only preflight of an explicit, already-qualified capture scope."""
    try:
        _require(type(request) is HostRequest and sys.platform.startswith("linux"), "unsupported_host")
        H._validated(H.CheckpointRequest(request.snapshot, request.state_dir))
        snapshot = request.snapshot
        project = snapshot.settings.project_root
        _require(project is not None and snapshot.include_history and snapshot.history_ref is not None,
                 "project_history_required")
        assert project is not None
        _require(stat.S_IMODE(request.state_dir.lstat().st_mode) == 0o700, "unsafe_state_directory")
        hooks, config_hash = _hooks(project)
        _require(not hooks.exists() or not list(hooks.iterdir()), "foreign_hooks")
        _require(not any(os.path.lexists(request.state_dir / name) for name in (_BINDING, _RECEIPT, _SCHEDULE)),
                 "connection_exists")
        S._check_storage(snapshot.storage)
        pointer = C.decode_locator(C.read_json(snapshot.storage.root / "projects" / snapshot.settings.identity.id / "latest.json"), pointer=True)
        _require(pointer.identity == snapshot.settings.identity, "identity_mismatch")
        path = C.safe_path(pointer.path, root=snapshot.storage.root, must_exist=True)
        manifest = S.verify_snapshot(path, snapshot.settings.identity, snapshot.settings.limits)
        _require(manifest.completion == "complete", "first_snapshot_required")
        directories = _directories((project, project / ".git", *((hooks,) if hooks.exists() else ()), request.state_dir, snapshot.storage.root,
                                    *(p.parent for p in snapshot.approved_sources)))
        files, interpreter = _runtime_files(request.python)
        data = {"schema": 1, "kind": "checkpoint-connection", "snapshot": asdict(snapshot),
                "state_dir": str(request.state_dir), "directories": directories,
                "git_config_sha256": config_hash, "hook_path": str(hooks / _HOOK),
                "python": str(request.python), "python_identity": interpreter, "runtime_files": files}
        binding = _encoded(data)
        digest = hashlib.sha256(binding).hexdigest()
        hook = _hook_bytes(request.python, request.state_dir / _BINDING, digest, files)
        _require(not any(c in str(hooks / _HOOK) for c in "\n\r%"), "unsafe_scheduler_path")
        return HostPreview(request, "approval_required", hooks / _HOOK,
                           shlex.join((str(hooks / _HOOK), "--tick")), (), binding, hook, directories)
    except C.ContinuityError as error:
        return HostPreview(request, "blocked", blockers=(error.code,))
    except (OSError, ValueError, TypeError):
        return HostPreview(request, "blocked", blockers=("connection_inspection_failed",))


def _write_new(path: Path, data: bytes, mode: int) -> OwnedFile:
    with S._directory(path.parent) as parent:
        fd = os.open(path.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode, dir_fd=parent)
        info = os.fstat(fd)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                _require(n > 0, "connection_write_failed")
                view = view[n:]
            os.fchmod(fd, mode)
            os.fsync(fd)
        except BaseException:
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                os.unlink(path.name, dir_fd=parent)
            raise
        finally:
            os.close(fd)
        os.fsync(parent)
    return _record(path)


@contextmanager
def _lock(state: Path, name: str) -> Iterator[None]:
    with S._directory(state) as parent:
        os.mkdir(name, 0o700, dir_fd=parent)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        try:
            yield
        finally:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "connection_lock_changed")
            os.rmdir(name, dir_fd=parent)


def apply_connections(preview: HostPreview, *, approval: bool = False) -> HostResult:
    if approval is not True:
        return HostResult("approval_required")
    if preview.status != "approval_required":
        return HostResult("blocked", blockers=preview.blockers)
    staged: list[OwnedFile] = []
    receipt: InstalledConnection | None = None
    created_hooks = False
    hook_identity: tuple[int, ...] | None = None
    try:
        with _lock(preview.request.state_dir, ".connection-install.lock"):
            _require(preview_connections(preview.request) == preview, "stale_connection_preview")
            assert preview.hook_path is not None
            if not preview.hook_path.parent.exists():
                with S._directory(preview.hook_path.parent.parent) as parent:
                    os.mkdir("hooks", 0o700, dir_fd=parent)
                    os.fsync(parent)
                created_hooks = True
            hook_identity = _identity(preview.hook_path.parent)
            targets = (preview.request.state_dir / _BINDING, preview.hook_path)
            for target, data, mode in ((targets[0], preview.binding, 0o600), (targets[1], preview.hook, 0o700)):
                staged.append(_write_new(target.parent / (".connection-" + uuid.uuid4().hex), data, mode))
            project = preview.hook_path.parents[2]
            receipt = InstalledConnection(preview.request.state_dir, preview.hook_path,
                tuple(replace(item, path=target) for item, target in zip(staged, targets)),
                _directories((project, project / ".git", preview.hook_path.parent, preview.request.state_dir)),
                preview.periodic_command, created_hooks, removing=True, pending=tuple(staged))
            # Recoverable intent precedes activation. Rename preserves the pinned
            # inodes, and missing final members remain safe on a fresh-process retry.
            _write_new(receipt.state_dir / _RECEIPT, _installation_bytes(receipt), 0o600)
            _require(load_connections(receipt.state_dir, project_root=project) == receipt,
                     "connection_receipt_readback_failed")
            _check_directories(preview.directories)
            for item, target in zip(staged, targets):
                _require(not os.path.lexists(target), "owned_connection_changed")
                _require(_record(item.path) == item, "owned_connection_changed")
                with S._directory(target.parent) as parent:
                    os.rename(item.path.name, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
            complete = replace(receipt, removing=False, pending=())
            _replace_owned(receipt.state_dir / _RECEIPT, _installation_bytes(complete))
            _require(load_connections(receipt.state_dir, project_root=project) == complete,
                     "connection_receipt_readback_failed")
            receipt = complete
        return HostResult("configured", receipt)
    except (C.ContinuityError, OSError) as error:
        retained: list[Path] = []
        for item in reversed(staged):
            if os.path.lexists(item.path):
                try:
                    _unlink(item)
                except (C.ContinuityError, OSError):
                    retained.append(item.path)
        if receipt is not None and os.path.lexists(receipt.state_dir / _RECEIPT):
            retained.extend(item.path for item in receipt.files if os.path.lexists(item.path))
        elif created_hooks and preview.hook_path is not None:
            try:
                _require(_identity(preview.hook_path.parent) == hook_identity, "connection_path_changed")
                preview.hook_path.parent.rmdir()
            except (C.ContinuityError, OSError):
                retained.append(preview.hook_path.parent)
        return HostResult("blocked", receipt,
            (error.code if isinstance(error, C.ContinuityError) else "connection_install_failed",), tuple(retained))


def _unlink(item: OwnedFile) -> None:
    _require(_record(item.path) == item, "owned_connection_changed")
    with S._directory(item.path.parent) as parent:
        info = os.stat(item.path.name, dir_fd=parent, follow_symlinks=False)
        _require((info.st_dev, info.st_ino, info.st_mode) == (item.device, item.inode, item.mode), "owned_connection_changed")
        os.unlink(item.path.name, dir_fd=parent)
        os.fsync(parent)


def _installation_bytes(receipt: InstalledConnection) -> bytes:
    return _encoded({"schema": 1, "kind": "checkpoint-installation", **asdict(receipt)})


def _private(path: Path) -> bytes:
    data, info = _read(path)
    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
    return data


def _replace_owned(path: Path, data: bytes) -> None:
    previous = _record(path)
    temporary = path.parent / (".connection-" + uuid.uuid4().hex)
    owned = _write_new(temporary, data, 0o600)
    try:
        _require(_record(path) == previous, "owned_connection_changed")
        with S._directory(path.parent) as parent:
            os.replace(temporary.name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        _require(_private(path) == data, "connection_receipt_readback_failed")
    finally:
        if os.path.lexists(temporary):
            _unlink(owned)


def _path(value: C.JsonValue) -> Path:
    text = C.validate_path_text(value, absolute=True)
    path = Path(text)
    _require(str(path) == text)
    return path


def _integer(value: C.JsonValue) -> int:
    _require(type(value) is int and 0 <= value < 2**128)
    return cast(int, value)


def _installation_shape(receipt: InstalledConnection) -> None:
    _require(type(receipt) is InstalledConnection)
    project = receipt.hook_path.parent.parent.parent
    _require(receipt.hook_path == project / ".git/hooks" / _HOOK)
    _require(type(receipt.created_hooks) is bool and type(receipt.removing) is bool)
    _require(len(receipt.files) == 2 and {item.path for item in receipt.files}
             == {receipt.hook_path, receipt.state_dir / _BINDING})
    _require(not receipt.pending or receipt.removing)
    _require(len(receipt.pending) <= 2 and len({p.path for p in receipt.pending}) == len(receipt.pending))
    for item in receipt.pending:
        _require(item.path.name.startswith(".connection-") and len(item.path.name) == 44)
        _require(all(c in "0123456789abcdef" for c in item.path.name[len(".connection-"):]))
        _require(any(replace(item, path=target.path) == target and item.path.parent == target.path.parent
                     for target in receipt.files))
    for item in receipt.files:
        _require(type(item) is OwnedFile)
        _path(str(item.path))
        _integer(item.device)
        _integer(item.inode)
        _require(type(item.mode) is int and item.mode == stat.S_IFREG | (
            0o700 if item.path == receipt.hook_path else 0o600))
        C.validate_sha256(item.sha256)
    roots = (project, project / ".git", receipt.hook_path.parent, receipt.state_dir)
    expected = {p for root in roots for p in (root, *root.parents)}
    _require(len(receipt.directories) == len(expected)
             and {p for p, _ in receipt.directories} == expected)
    for path, identity in receipt.directories:
        _path(str(path))
        _require(len(identity) == 5)
        for number in identity:
            _integer(number)
        _require(stat.S_ISDIR(identity[2]))
        if path in roots:
            _require(identity[3] == os.geteuid() and not identity[2] & 0o022)
    _require(receipt.periodic_command == shlex.join((str(receipt.hook_path), "--tick")))


def _installation_matches(receipt: InstalledConnection) -> None:
    _installation_shape(receipt)
    for path, identity in receipt.directories:
        if (receipt.removing and receipt.created_hooks and path == receipt.hook_path.parent
                and not os.path.lexists(path)):
            continue
        _require(_identity(path) == identity, "connection_path_changed")
    for item in (*receipt.files, *receipt.pending):
        if receipt.removing and not os.path.lexists(item.path):
            continue
        _require(_record(item.path) == item, "owned_connection_changed")


def load_connections(state_dir: Path, *, project_root: Path) -> InstalledConnection:
    """Load exact local ownership, never execution authority or saved consent.

    A durable removing intent permits only absent original members on retry;
    present members must still match their original identity and content.
    """
    try:
        _path(str(state_dir))
        _path(str(project_root))
        raw = _object(C.decode_json(_private(state_dir / _RECEIPT)), {
            "schema", "kind", "state_dir", "hook_path", "files", "directories",
            "periodic_command", "created_hooks", "removing", "pending"})
        _require(type(raw["schema"]) is int and raw["schema"] == 1
                 and raw["kind"] == "checkpoint-installation")
        _require(raw["state_dir"] == str(state_dir)
                 and raw["hook_path"] == str(project_root / ".git/hooks" / _HOOK))
        _require(type(raw["created_hooks"]) is bool and type(raw["removing"]) is bool)
        decoded: dict[str, tuple[OwnedFile, ...]] = {}
        for key in ("files", "pending"):
            files: list[OwnedFile] = []
            _require(isinstance(raw[key], list))
            for value in cast(list[C.JsonValue], raw[key]):
                row = _object(value, {"path", "device", "inode", "mode", "sha256"})
                files.append(OwnedFile(_path(row["path"]), _integer(row["device"]),
                             _integer(row["inode"]), _integer(row["mode"]), C.validate_sha256(row["sha256"])))
            decoded[key] = tuple(files)
        directories: list[tuple[Path, tuple[int, ...]]] = []
        _require(isinstance(raw["directories"], list))
        for value in cast(list[C.JsonValue], raw["directories"]):
            _require(isinstance(value, list) and len(value) == 2)
            assert isinstance(value, list)
            _require(isinstance(value[1], list))
            directories.append((_path(value[0]), tuple(_integer(v) for v in cast(list[C.JsonValue], value[1]))))
        receipt = InstalledConnection(state_dir, project_root / ".git/hooks" / _HOOK,
            decoded["files"], tuple(directories), _text(raw["periodic_command"]),
            cast(bool, raw["created_hooks"]), cast(bool, raw["removing"]), decoded["pending"])
        _installation_matches(receipt)
        return receipt
    except (OSError, ValueError, TypeError, KeyError):
        _fail("invalid_connection")


def remove_connections(receipt: InstalledConnection, *, approval: bool = False) -> HostResult:
    if approval is not True:
        return HostResult("approval_required")
    retained: tuple[Path, ...] = ()
    try:
        _installation_shape(receipt)
        # Before even acquiring a lock, refuse replacement roots/changed members.
        _check_directories(tuple(row for row in receipt.directories
                                 if not (receipt.removing and receipt.created_hooks
                                         and row[0] == receipt.hook_path.parent and not os.path.lexists(row[0]))))
        with _lock(receipt.state_dir, ".connection-install.lock"):
            current = load_connections(receipt.state_dir, project_root=receipt.hook_path.parents[2])
            _require(replace(current, removing=receipt.removing) == receipt, "owned_connection_changed")
            _require(not os.path.lexists(receipt.state_dir / _SCHEDULE), "owned_schedule_present")
            # Persist intent BEFORE the first unlink. Even failure of later metadata
            # writes leaves a restartable exact set, not an adoption of new paths.
            receipt = replace(current, removing=True)
            if not current.removing:
                _replace_owned(receipt.state_dir / _RECEIPT, _installation_bytes(receipt))
            for item in sorted((*receipt.files, *receipt.pending), key=lambda row: row.path != receipt.hook_path):
                if os.path.lexists(item.path):
                    _unlink(item)
            if receipt.created_hooks and os.path.lexists(receipt.hook_path.parent):
                _require(_identity(receipt.hook_path.parent) == dict(receipt.directories)[receipt.hook_path.parent],
                         "connection_path_changed")
                # A later unrelated entry is not ours to remove, nor a cleanup failure.
                if not any(receipt.hook_path.parent.iterdir()):
                    receipt.hook_path.parent.rmdir()
            _unlink(_record(receipt.state_dir / _RECEIPT))
        return HostResult("removed")
    except (C.ContinuityError, OSError) as error:
        retained = tuple(item.path for item in (*receipt.files, *receipt.pending) if os.path.lexists(item.path))
        return HostResult("blocked", receipt, (error.code if isinstance(error, C.ContinuityError)
                                                else "connection_remove_failed",), retained)


def _object(value: C.JsonValue, names: set[str]) -> C.JsonObject:
    _require(isinstance(value, dict) and set(value) == names)
    return cast(C.JsonObject, value)


def _text(value: C.JsonValue) -> str:
    _require(isinstance(value, str))
    return cast(str, value)


def _paths(value: C.JsonValue) -> tuple[Path, ...]:
    _require(isinstance(value, list))
    return tuple(Path(C.validate_path_text(v, absolute=True)) for v in cast(list[C.JsonValue], value))


def _snapshot(value: C.JsonValue) -> C.SnapshotRequest:
    raw = _object(value, {"settings", "storage", "approved_sources", "context_entrypoints", "include_history",
                          "history_ref", "verified_remote_oid", "remote_evidence"})
    cfg = _object(raw["settings"], {"identity", "project_root", "backup_root", "context_path", "context_source",
                                  "backup", "git", "limits", "approved_sources"})
    identity = C.decode_identity(cfg["identity"])
    policy = _object(cfg["backup"], {"enabled", "interval_minutes", "retention", "prune"})
    git = _object(cfg["git"], {"push_after_commit"})
    limits = _object(cfg["limits"], set(asdict(C.Limits())))
    backup = C.BackupPolicy(cast(bool, policy["enabled"]), cast(int, policy["interval_minutes"]),
                            cast(int, policy["retention"]), cast(bool, policy["prune"]))
    settings = C.ResolvedSettings(identity, Path(C.validate_path_text(cfg["project_root"], absolute=True)),
        Path(C.validate_path_text(cfg["backup_root"], absolute=True)),
        None if cfg["context_path"] is None else Path(C.validate_path_text(cfg["context_path"], absolute=True)),
        cast(C.ContextSource, cfg["context_source"]), backup, C.GitPolicy(cast(bool, git["push_after_commit"])),
        C.Limits(**cast(dict[str, int], limits)), _paths(cfg["approved_sources"]))
    store = _object(raw["storage"], {"root", "device", "inode", "backing_fingerprint", "mount_fingerprint", "durability"})
    storage = C.StorageIdentity(Path(C.validate_path_text(store["root"], absolute=True)), cast(int, store["device"]),
        cast(int, store["inode"]), cast(str | None, store["backing_fingerprint"]),
        cast(str | None, store["mount_fingerprint"]), _text(store["durability"]))
    entries = raw["context_entrypoints"]
    _require(isinstance(entries, list))
    return C.SnapshotRequest(settings, storage, _paths(raw["approved_sources"]),
        tuple(C.validate_relpath(v) for v in cast(list[C.JsonValue], entries)), cast(bool, raw["include_history"]),
        cast(str | None, raw["history_ref"]), cast(str | None, raw["verified_remote_oid"]), cast(str | None, raw["remote_evidence"]))


def _load_binding(path: Path, digest: str) -> tuple[HostRequest, C.JsonObject]:
    C.validate_sha256(digest)
    _require(path.name == _BINDING)
    data, info = _read(path)
    _require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.geteuid())
    _require(hashlib.sha256(data).hexdigest() == digest, "connection_binding_changed")
    raw = _object(C.decode_json(data), {"schema", "kind", "snapshot", "state_dir", "directories",
         "git_config_sha256", "hook_path", "python", "python_identity", "runtime_files"})
    _require(type(raw["schema"]) is int and raw["schema"] == 1 and raw["kind"] == "checkpoint-connection")
    request = HostRequest(_snapshot(raw["snapshot"]), Path(C.validate_path_text(raw["state_dir"], absolute=True)),
                          Path(C.validate_path_text(raw["python"], absolute=True)))
    _require(path.parent == request.state_dir)
    H._validated(H.CheckpointRequest(request.snapshot, request.state_dir))
    directories = raw["directories"]
    _require(isinstance(directories, list))
    for row in cast(list[C.JsonValue], directories):
        _require(isinstance(row, list) and len(row) == 2)
        assert isinstance(row, list)
        _require(list(_identity(Path(C.validate_path_text(row[0], absolute=True)))) == row[1], "connection_path_changed")
    files, interpreter = _runtime_files(request.python)
    _require(raw["runtime_files"] == files and raw["python_identity"] == list(interpreter), "connection_runtime_changed")
    project = request.snapshot.settings.project_root
    assert project is not None
    hooks, config_hash = _hooks(project)
    _require(raw["hook_path"] == str(hooks / _HOOK) and raw["git_config_sha256"] == config_hash,
             "connection_hooks_changed")
    _require(set(p.name for p in hooks.iterdir()) == {_HOOK}, "foreign_hooks")
    hook, hook_info = _read(hooks / _HOOK)
    _require(stat.S_IMODE(hook_info.st_mode) == 0o700 and hook == _hook_bytes(request.python, path, digest, files),
             "connection_hook_changed")
    S._check_storage(request.snapshot.storage)
    return request, raw


def _relevant(data: bytes, phase: str, ref: str) -> bool:
    _require(phase in ("preparing", "prepared", "committed", "aborted"), "invalid_ref_phase")
    if phase != "committed":
        return False
    _require(len(data) <= 65536 and data.endswith(b"\n"), "invalid_ref_events")
    relevant = False
    for line in data.splitlines():
        fields = line.decode("ascii").split(" ")
        _require(len(fields) == 3, "invalid_ref_events")
        old, new, name = fields
        C.validate_oid(old)
        C.validate_oid(new)
        _require(len(old) == len(new), "invalid_ref_events")
        if name == ref and new != "0" * len(new):
            relevant = True
    return relevant


def _save_result(state: Path, value: object) -> None:
    path = state / "last-result.json"
    previous = _record(path) if path.exists() else None
    temporary = state / (".result-" + uuid.uuid4().hex)
    owned = _write_new(temporary, _encoded(value), 0o600)
    try:
        _require((_record(path) if path.exists() else None) == previous, "connection_result_changed")
        with S._directory(state) as fd:
            os.replace(temporary.name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
    finally:
        if temporary.exists():
            _unlink(owned)


def _check_current_policy(project: Path) -> None:
    """Observe revocation at the host boundary, without restoring new authority."""
    path = C.safe_path(project / ".thunderkit/continuity.json")
    try:
        path.lstat()
    except FileNotFoundError:
        return
    policy = C.validate_policy(C.decode_json(_read(path)[0]))
    backup = cast(C.JsonObject, policy.get("backup", {}))
    _require(backup.get("enabled", True) is True, "backup_disabled")


def _run_connection(path: Path, digest: str, *, trigger: str = "due", phase: str = "committed",
                   events: bytes = b"") -> C.JsonObject:
    _require(trigger in ("due", "ref"))
    if trigger == "ref" and phase in ("preparing", "prepared", "aborted"):
        return {"status": "ignored", "reason": "uncommitted_transaction"}
    budget = _budget.get()
    assert budget is not None
    with S.work_scope(budget):
        request, _ = _load_binding(path, digest)
        project = request.snapshot.settings.project_root
        assert project is not None
        _check_current_policy(project)
    ref = request.snapshot.history_ref
    assert ref is not None
    if trigger == "ref" and not _relevant(events, phase, ref):
        return {"status": "ignored", "reason": "unrelated_ref"}
    with _lock(request.state_dir, ".connection-run.lock"):
        remaining = budget.remaining(H.MAX_DEADLINE_SECONDS)
        result = H.checkpoint(H.CheckpointRequest(request.snapshot, request.state_dir,
            trigger="due" if trigger == "due" else "event", deadline_seconds=remaining,
            event_id=hashlib.sha256(events).hexdigest() if trigger == "ref" else None))
        budget.check()
        value = C.decode_json(_encoded(asdict(result)))
        _save_result(request.state_dir, value)
        return value


def run_connection(path: Path, digest: str, *, trigger: str = "due", phase: str = "committed",
                   events: bytes = b"") -> C.JsonObject:
    with _operation(H.MAX_DEADLINE_SECONDS):
        return _run_connection(path, digest, trigger=trigger, phase=phase, events=events)


def _stdin() -> bytes:
    parts: list[bytes] = []
    total = 0
    end = _end(5)
    import select
    while True:
        remaining = end - time.monotonic()
        _require(remaining > 0 and bool(select.select([sys.stdin.fileno()], [], [], remaining)[0]), "ref_input_timeout")
        data = os.read(sys.stdin.fileno(), min(4096, 65537 - total))
        if not data:
            return b"".join(parts)
        parts.append(data)
        total += len(data)
        _require(total <= 65536, "invalid_ref_events")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _fail("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    try:
        with _operation(H.MAX_DEADLINE_SECONDS):
            parser = _Parser(description=__doc__, allow_abbrev=False)
            commands = parser.add_subparsers(dest="command", required=True)
            run = commands.add_parser("run", allow_abbrev=False)
            run.add_argument("--binding", required=True)
            run.add_argument("--expect", required=True)
            run.add_argument("--trigger", choices=("due", "ref"), default="due")
            run.add_argument("--phase", default="committed")
            args = parser.parse_args(argv)
            path = Path(C.validate_path_text(args.binding, absolute=True))
            events = _stdin() if args.trigger == "ref" and args.phase == "committed" else b""
            result = run_connection(path, args.expect, trigger=args.trigger, phase=args.phase, events=events)
        print(json.dumps(result))
        return 0 if result["status"] in ("captured", "duplicate", "not-due", "ignored") else 2
    except C.ContinuityError as error:
        print(json.dumps({"error": {"code": error.code, "detail": error.detail}}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.TimeoutExpired):
        print(json.dumps({"error": {"code": "connection_failed", "detail": "Checkpoint connection did not complete."}}), file=sys.stderr)
        return 2


class ScheduleDriver(Protocol):
    """Trusted existing host manager; replacement is compare-and-swap."""
    def read(self) -> bytes: ...
    def replace(self, expected: bytes, content: bytes) -> bool: ...


@dataclass(frozen=True)
class SchedulePreview:
    connection: InstalledConnection
    status: Status
    previous: bytes = b""
    block: bytes = b""
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstalledSchedule:
    connection: InstalledConnection
    block: bytes


@dataclass(frozen=True)
class ScheduleResult:
    status: Status
    installation: InstalledSchedule | None = None
    blockers: tuple[str, ...] = ()


def _connection_matches(connection: InstalledConnection) -> None:
    _installation_matches(connection)
    _require(not connection.removing, "owned_connection_changed")
    _require(load_connections(connection.state_dir, project_root=connection.hook_path.parents[2]) == connection,
             "owned_connection_changed")
    _require(connection.periodic_command == shlex.join((str(connection.hook_path), "--tick")), "invalid_schedule_command")
    binding = next((item for item in connection.files if item.path == connection.state_dir / _BINDING), None)
    _require(binding is not None and connection.hook_path in {item.path for item in connection.files})
    assert binding is not None
    _load_binding(binding.path, binding.sha256)


def _table(data: bytes) -> bytes:
    _require(type(data) is bytes and len(data) <= C.CONFIG_MAX_BYTES, "invalid_crontab")
    _require(not data or data.endswith(b"\n"), "invalid_crontab")
    _require(b"\0" not in data and b"\r" not in data, "invalid_crontab")
    data.decode("utf-8", "strict")
    return data


def preview_schedule(connection: InstalledConnection, driver: ScheduleDriver, *,
                     service_qualified: bool = False, exclusive_writer: bool = False) -> SchedulePreview:
    """A binary on PATH is not service qualification or exclusive-writer proof."""
    try:
        _require(service_qualified is True and exclusive_writer is True, "scheduler_unqualified")
        _connection_matches(connection)
        _require(not os.path.lexists(connection.state_dir / _SCHEDULE), "owned_schedule_present")
        previous = _table(driver.read())
        block = _schedule_block(connection)
        marker = block.split(b" begin\n", 1)[0]
        _require(marker not in previous, "schedule_already_present")
        _table(previous + block)
        return SchedulePreview(connection, "approval_required", previous, block)
    except (C.ContinuityError, OSError, UnicodeError) as error:
        return SchedulePreview(connection, "blocked", blockers=(error.code if isinstance(error, C.ContinuityError)
                                                                 else "schedule_inspection_failed",))


def _schedule_data(installation: InstalledSchedule, absent: bytes) -> bytes:
    return _encoded({"schema": 1, "kind": "checkpoint-schedule",
        "connection_sha256": hashlib.sha256(_installation_bytes(installation.connection)).hexdigest(),
        "block": installation.block.decode("utf-8"), "absent_sha256": hashlib.sha256(absent).hexdigest()})


def _schedule_block(connection: InstalledConnection) -> bytes:
    token = hashlib.sha256(str(connection.hook_path).encode()).hexdigest()
    marker = f"# thunderkit-checkpoint {token}".encode()
    command = connection.periodic_command.encode()
    _require(not any(value in command for value in (b"\n", b"\r", b"%")), "unsafe_schedule_command")
    return marker + b" begin\n* * * * * " + command + b"\n" + marker + b" end\n"


def _schedule_guard(connection: InstalledConnection) -> tuple[InstalledSchedule, str]:
    raw = _object(C.decode_json(_private(connection.state_dir / _SCHEDULE)),
                  {"schema", "kind", "connection_sha256", "block", "absent_sha256"})
    _require(type(raw["schema"]) is int and raw["schema"] == 1 and raw["kind"] == "checkpoint-schedule")
    _require(raw["connection_sha256"] == hashlib.sha256(_installation_bytes(connection)).hexdigest())
    block = _text(raw["block"]).encode("utf-8")
    _require(block == _schedule_block(connection), "owned_schedule_changed")
    return InstalledSchedule(connection, block), C.validate_sha256(raw["absent_sha256"])


def load_schedule(state_dir: Path, *, project_root: Path) -> InstalledSchedule:
    """Read durable exact schedule ownership, including unconfirmed publication."""
    connection = load_connections(state_dir, project_root=project_root)
    _require(not connection.removing, "owned_connection_changed")
    return _schedule_guard(connection)[0]


def apply_schedule(preview: SchedulePreview, driver: ScheduleDriver, *, approval: bool = False) -> ScheduleResult:
    if approval is not True:
        return ScheduleResult("approval_required")
    if preview.status != "approval_required":
        return ScheduleResult("blocked", blockers=preview.blockers)
    installation: InstalledSchedule | None = None
    published = False
    try:
        with _lock(preview.connection.state_dir, ".connection-install.lock"):
            _require(preview_schedule(preview.connection, driver, service_qualified=True, exclusive_writer=True)
                     == preview, "stale_schedule_preview")
            installation = InstalledSchedule(preview.connection, preview.block)
            guard = preview.connection.state_dir / _SCHEDULE
            _write_new(guard, _schedule_data(installation, preview.previous), 0o600)
            _require(_schedule_guard(preview.connection)[0] == installation, "schedule_readback_failed")
            desired = preview.previous + preview.block
            # A transport exception may follow publication. The prewritten guard
            # survives both failures and the process exiting before readback.
            _require(driver.replace(preview.previous, desired), "schedule_conflict")
            published = True
            try:
                _require(driver.read() == desired, "schedule_readback_failed")
            except (C.ContinuityError, OSError, UnicodeError):
                _fail("schedule_readback_failed")
        return ScheduleResult("configured", installation)
    except (C.ContinuityError, OSError, UnicodeError) as error:
        code = error.code if isinstance(error, C.ContinuityError) else "schedule_install_failed"
        return ScheduleResult("blocked", installation,
                              ("schedule_readback_failed" if published else code,))


def remove_schedule(installation: InstalledSchedule, driver: ScheduleDriver, *, approval: bool = False) -> ScheduleResult:
    if approval is not True:
        return ScheduleResult("approval_required")
    try:
        _require(type(installation) is InstalledSchedule)
        connection = installation.connection
        _installation_matches(connection)
        with _lock(connection.state_dir, ".connection-install.lock"):
            _require(load_connections(connection.state_dir, project_root=connection.hook_path.parents[2]) == connection,
                     "owned_connection_changed")
            owned, absent_digest = _schedule_guard(connection)
            _require(owned == installation, "owned_schedule_changed")
            current = _table(driver.read())
            if current.count(installation.block) == 1:
                desired = current.replace(installation.block, b"", 1)
                # Record the exact prospective absence BEFORE replacement, so a
                # failed readback can be reconciled in a fresh approved process.
                _replace_owned(connection.state_dir / _SCHEDULE, _schedule_data(installation, desired))
                _require(driver.replace(current, desired), "schedule_conflict")
            else:
                _require(hashlib.sha256(current).hexdigest() == absent_digest, "owned_schedule_changed")
                desired = current
            try:
                _require(driver.read() == desired, "schedule_readback_failed")
            except (C.ContinuityError, OSError, UnicodeError):
                _fail("schedule_readback_failed")
            _unlink(_record(connection.state_dir / _SCHEDULE))
        return ScheduleResult("removed")
    except (C.ContinuityError, OSError, UnicodeError) as error:
        return ScheduleResult("blocked", installation, (error.code if isinstance(error, C.ContinuityError)
                                                        else "schedule_remove_failed",))


class CrontabDriver:
    """Pinned local transport, NOT global CAS or proof of a running service.

    Independent service and exclusive-writer attestations are mandatory. Each
    call bounds compare, input/output, confirmation and owned-child reaping.
    """

    def __init__(self, executable: Path, *, service_qualified: bool = False,
                 exclusive_writer: bool = False, timeout_seconds: float = 5.0) -> None:
        _require(service_qualified is True and exclusive_writer is True, "scheduler_unqualified")
        _require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 5, "invalid_scheduler_timeout")
        self.executable = _path(str(executable))
        self.timeout_seconds = timeout_seconds
        try:
            self.pin = _record(self.executable)
            _require(bool(self.pin.mode & 0o111), "scheduler_executable_changed")
            parent = _identity(self.executable.parent)
            _require(parent[3] in (0, os.geteuid()) and not parent[2] & 0o022,
                     "scheduler_executable_changed")
            self.parents = tuple((path, _identity(path)) for path in self.executable.parents)
        except (C.ContinuityError, OSError):
            _fail("scheduler_executable_changed")

    def _run(self, arg: str, end: float, data: bytes | None = None) -> bytes:
        _Budget(end, "scheduler_timeout").check()
        try:
            _check_directories(self.parents)
            _require(_record(self.executable) == self.pin, "scheduler_executable_changed")
        except (C.ContinuityError, OSError):
            _fail("scheduler_executable_changed")
        return _transport((str(self.executable), arg), self.executable.parent,
            {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, end, data,
            timeout_code="scheduler_timeout",
            failure_code="scheduler_read_failed" if arg == "-l" else "scheduler_write_failed",
            size_code="invalid_crontab")

    def read(self) -> bytes:
        return _table(self._run("-l", _end(self.timeout_seconds)))

    def replace(self, expected: bytes, content: bytes) -> bool:
        end = _end(self.timeout_seconds)
        _table(expected)
        _table(content)
        if _table(self._run("-l", end)) != expected:
            return False
        self._run("-", end, content)
        try:
            _require(_table(self._run("-l", end)) == content, "schedule_readback_failed")
        except (C.ContinuityError, OSError, UnicodeError):
            _fail("schedule_readback_failed")
        return True


if __name__ == "__main__":
    raise SystemExit(main())
