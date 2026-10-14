"""Explicitly installed local Git checkpoint connection; no saved push grants.

The hook and periodic command share the bounded checkpoint runtime. Installation
requires a fresh approval and an empty owned repository-local hooks directory.
A generated periodic command is not proof of a running or configured scheduler.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
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
from typing import Literal, NoReturn, cast
import uuid

import backup_store as S
import checkpoint_runtime as H
import continuity_config as C

Status = Literal["approval_required", "blocked", "configured", "removed"]
_BINDING = "connection.json"
_HOOK = "reference-transaction"
_MODULES = ("continuity_config", "backup_store", "checkpoint_push", "checkpoint_runtime", "checkpoint_host")


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


def _git(project: Path, args: tuple[str, ...]) -> bytes:
    command = ("/usr/bin/git", *args)
    chunks: list[bytes] = []
    count = 0
    end = time.monotonic() + 5
    with subprocess.Popen(command, cwd=project, env=_git_environment(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
        assert process.stdout is not None and process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    _require(remaining > 0, "git_inspection_timeout")
                    for key, _ in selector.select(remaining):
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                        else:
                            count += len(data)
                            _require(count <= C.CONFIG_MAX_BYTES, "git_inspection_too_large")
                            if key.data:
                                chunks.append(data)
            code = process.wait(timeout=max(0.001, end - time.monotonic()))
            _require(code == 0, "git_inspection_failed")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    return b"".join(chunks)


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
    # Hash and execute the SAME bytes; never hash a module then reopen it to import.
    text = (f"#!{python} -I\n" + "import hashlib,importlib.util,sys\nfrom pathlib import Path\n"
            + "if len(sys.argv)!=2: raise SystemExit(2)\n"
            + "phase=sys.argv[1]\n"
            + "if phase in ('preparing','prepared','aborted'): raise SystemExit(0)\n"
            + "if phase not in ('committed','--tick'): raise SystemExit(2)\n"
            + f"expected={files!r}\n"
            + "sources={}\nfor path,digest in expected.items():\n"
            + " data=Path(path).read_bytes()\n"
            + " if hashlib.sha256(data).hexdigest()!=digest: raise SystemExit(2)\n"
            + " sources[path]=data\n"
            + "sys.dont_write_bytecode=True\n"
            + "for path,data in sources.items():\n"
            + " name=Path(path).stem\n spec=importlib.util.spec_from_file_location(name,path)\n"
            + " module=importlib.util.module_from_spec(spec)\n sys.modules[name]=module\n"
            + " exec(compile(data,path,'exec'),module.__dict__)\n"
            + f"args=['run','--binding',{str(binding)!r},'--expect',{digest!r}]\n"
            + "args+=['--trigger','due'] if phase=='--tick' else ['--trigger','ref','--phase',phase]\n"
            + "raise SystemExit(sys.modules['checkpoint_host'].main(args))\n")
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
        _require(not any((request.state_dir / name).exists() for name in (_BINDING, "connection-receipt.json")),
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
    files: list[OwnedFile] = []
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
            files.append(_write_new(preview.request.state_dir / _BINDING, preview.binding, 0o600))
            # Hook is the activation point and is installed last.
            files.append(_write_new(preview.hook_path, preview.hook, 0o700))
            _check_directories(preview.directories)
            return HostResult("configured", InstalledConnection(preview.request.state_dir, preview.hook_path,
                              tuple(files), tuple(sorted(set((*preview.directories,
                                  (preview.hook_path.parent, hook_identity))))), preview.periodic_command, created_hooks))
    except (C.ContinuityError, OSError) as error:
        retained = []
        for item in reversed(files):
            try:
                _unlink(item)
            except (C.ContinuityError, OSError):
                retained.append(item.path)
        if created_hooks and preview.hook_path is not None:
            try:
                _require(_identity(preview.hook_path.parent) == hook_identity, "connection_path_changed")
                preview.hook_path.parent.rmdir()
            except (C.ContinuityError, OSError):
                retained.append(preview.hook_path.parent)
        return HostResult("blocked", blockers=(error.code if isinstance(error, C.ContinuityError)
                                                else "connection_install_failed",), retained_paths=tuple(retained))


def _unlink(item: OwnedFile) -> None:
    _require(_record(item.path) == item, "owned_connection_changed")
    with S._directory(item.path.parent) as parent:
        info = os.stat(item.path.name, dir_fd=parent, follow_symlinks=False)
        _require((info.st_dev, info.st_ino, info.st_mode) == (item.device, item.inode, item.mode), "owned_connection_changed")
        os.unlink(item.path.name, dir_fd=parent)
        os.fsync(parent)


def remove_connections(receipt: InstalledConnection, *, approval: bool = False) -> HostResult:
    if approval is not True:
        return HostResult("approval_required")
    try:
        _require(type(receipt) is InstalledConnection and len(receipt.files) == 2)
        _require({item.path for item in receipt.files} == {receipt.hook_path, receipt.state_dir / _BINDING})
        _require(receipt.hook_path.name == _HOOK and receipt.hook_path.parent.name == "hooks"
                 and receipt.hook_path.parent.parent.name == ".git")
        with _lock(receipt.state_dir, ".connection-install.lock"):
            _check_directories(receipt.directories)
            _require(all(_record(item.path) == item for item in receipt.files), "owned_connection_changed")
            for item in sorted(receipt.files, key=lambda row: row.path != receipt.hook_path):
                _unlink(item)
            if receipt.created_hooks:
                receipt.hook_path.parent.rmdir()
        return HostResult("removed")
    except (C.ContinuityError, OSError) as error:
        return HostResult("blocked", blockers=(error.code if isinstance(error, C.ContinuityError)
                                                else "connection_remove_failed",))


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


def run_connection(path: Path, digest: str, *, trigger: str = "due", phase: str = "committed",
                   events: bytes = b"") -> C.JsonObject:
    started = time.monotonic()
    _require(trigger in ("due", "ref"))
    if trigger == "ref" and phase in ("preparing", "prepared", "aborted"):
        return {"status": "ignored", "reason": "uncommitted_transaction"}
    request, _ = _load_binding(path, digest)
    ref = request.snapshot.history_ref
    assert ref is not None
    if trigger == "ref" and not _relevant(events, phase, ref):
        return {"status": "ignored", "reason": "unrelated_ref"}
    with _lock(request.state_dir, ".connection-run.lock"):
        remaining = H.MAX_DEADLINE_SECONDS - (time.monotonic() - started)
        _require(remaining > 0, "connection_deadline")
        result = H.checkpoint(H.CheckpointRequest(request.snapshot, request.state_dir,
            trigger="due" if trigger == "due" else "event", deadline_seconds=remaining,
            event_id=hashlib.sha256(events).hexdigest() if trigger == "ref" else None))
        value = C.decode_json(_encoded(asdict(result)))
        _save_result(request.state_dir, value)
        return value


def _stdin() -> bytes:
    parts: list[bytes] = []
    total = 0
    end = time.monotonic() + 5
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
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("--binding", required=True)
    run.add_argument("--expect", required=True)
    run.add_argument("--trigger", choices=("due", "ref"), default="due")
    run.add_argument("--phase", default="committed")
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
