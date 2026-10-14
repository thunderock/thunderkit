"""Exact-ref checkpoint publication with independent, current caller authority.

The driver is a trusted execution seam, not serialized authority. Production
uses installed Git and its configured credential helpers/verification hooks;
opaque overrides are denied, never replaced. Branch safety is current caller
attestation, not a claim that server-side protection was queried.

Preflight fingerprints are point-in-time observations, not an atomic lock on
external Git/SSH/config/hook changes. A late outside change or a configured hook
can still act after inspection; readback establishes only the observed exact
ref. Host activation, per-commit observation and hook qualification belong to H.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
from typing import BinaryIO, Protocol, cast

from continuity_config import (
    ContinuityError, GitPolicy, PushGrant, PushRequest, PushResult, ScopeIdentity,
    safe_path, validate_oid,
)

TIMEOUT = 30.0
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_HOOKS = 256
MAX_HOOK_BYTES = 1024 * 1024
RAW_CONFIG = ("config", "--null", "--show-origin", "--no-includes", "--list")
ENDPOINT = re.compile(
    r"(?:https://github\.com/|git@github\.com:)"
    r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\.git"
)


class _Denied(Exception):
    """Internal fail-closed signal; never contains raw input or diagnostics."""


def _require(condition: bool) -> None:
    if not condition:
        raise _Denied("Checkpoint preflight could not establish safe publication.")


class WorkBudget(Protocol):
    """Trusted in-process deadline; check only work, never cleanup transitions."""

    def check(self) -> None: ...
    def remaining(self, maximum: float) -> float: ...


_work_budgets: ContextVar[tuple[WorkBudget, ...]] = ContextVar("work_budgets", default=())


@contextmanager
def work_scope(budget: WorkBudget) -> Iterator[None]:
    """Share the caller's absolute deadline; nested scopes cannot extend it."""
    token = _work_budgets.set((*_work_budgets.get(), budget))
    try:
        yield
    finally:
        _work_budgets.reset(token)


def _work() -> None:
    for budget in _work_budgets.get():
        budget.check()


def _remaining(maximum: float) -> float:
    for budget in _work_budgets.get():
        maximum = min(maximum, budget.remaining(maximum))
    return maximum


class GitDriver(Protocol):
    def __call__(self, argv: tuple[str, ...], *, cwd: Path,
                 timeout: float) -> subprocess.CompletedProcess[str]: ...


def production_driver(argv: tuple[str, ...], *, cwd: Path,
                      timeout: float) -> subprocess.CompletedProcess[str]:
    """Bound output and wall time; retain host hooks/configuration, never raw errors.

    A separate process group bounds stalled hook/transport children on POSIX.
    There is deliberately no filesystem-transport switch or test environment.
    """
    _require(os.name == "posix" and 0 < timeout <= 60)
    try:
        code, output, had_stderr = _run_git(argv, cwd, timeout)
        text = output.decode("utf-8", errors="strict")
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired("git", timeout) from None
    except (OSError, UnicodeError, _Denied):
        return subprocess.CompletedProcess(argv, 128, "", "Git command failed; raw diagnostics withheld.")
    # stderr is never needed for successful parsing, but its presence prevents
    # treating an exit-2 error as definite absence.
    diagnostic = "Git command failed; raw diagnostics withheld." if code not in (0, 2) or had_stderr else ""
    return subprocess.CompletedProcess(argv, code, text if code in (0, 2) else "", diagnostic)


def _run_git(argv: tuple[str, ...], cwd: Path, timeout: float) -> tuple[int, bytes, bool]:
    deadline = time.monotonic() + _remaining(timeout)
    with subprocess.Popen(("git", *argv), cwd=cwd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          shell=False, start_new_session=True) as process:
        assert process.stdout is not None and process.stderr is not None
        output = bytearray()
        total = 0
        had_stderr = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    _work()
                    remaining = _remaining(deadline - time.monotonic())
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired("git", timeout)
                    for key, _events in selector.select(remaining):
                        _work()
                        stream = cast(BinaryIO, key.fileobj)
                        chunk = os.read(stream.fileno(), min(65536, MAX_OUTPUT_BYTES + 1 - total))
                        if not chunk:
                            selector.unregister(stream)
                            continue
                        total += len(chunk)
                        _require(total <= MAX_OUTPUT_BYTES)
                        if stream is process.stdout:
                            output.extend(chunk)
                        else:
                            had_stderr = True
            code = process.wait(timeout=_remaining(max(0.0, deadline - time.monotonic())))
            _work()
        except BaseException:
            # Cleanup only; the original failure is re-raised, not swallowed.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                process.kill()
            process.wait()
            raise
    return code, bytes(output), had_stderr


def _authority(request: PushRequest, grant: PushGrant) -> tuple[PushRequest, PushGrant]:
    # Reconstruct nested values: frozen objects are not an authority transport.
    _require(type(request) is PushRequest and type(grant) is PushGrant)
    _require(type(request.identity) is ScopeIdentity and type(grant.identity) is ScopeIdentity
             and type(request.policy) is GitPolicy)
    captured = PushRequest(ScopeIdentity(request.identity.kind, request.identity.id),
                           request.repository, request.remote, request.destination_ref,
                           request.oid, GitPolicy(request.policy.push_after_commit))
    approved = PushGrant(ScopeIdentity(grant.identity.kind, grant.identity.id),
                         grant.repository, grant.remote_url, grant.destination_ref,
                         grant.default_branch, grant.protected_refs, grant.branch_safety_verified)
    _require(captured.policy.push_after_commit and approved.branch_safety_verified)
    _require(captured.identity == approved.identity and captured.repository == approved.repository
             and captured.destination_ref == approved.destination_ref)
    _require(captured.destination_ref not in (
        "refs/heads/main", "refs/heads/master", approved.default_branch, *approved.protected_refs))
    _require(ENDPOINT.fullmatch(approved.remote_url) is not None
             and ".." not in approved.remote_url)
    return captured, approved


def _call_timeout(on_attempt: Callable[[], None] | None) -> float:
    timeout = _remaining(TIMEOUT)
    # Notification is an allocation-free state transition, not work. Once it
    # returns, enter the driver without another cancellation point that could
    # falsely report an attempt which never reached the driver.
    if on_attempt is not None:
        on_attempt()
    return timeout


def _call(driver: GitDriver, repository: Path, argv: tuple[str, ...], *,
          on_attempt: Callable[[], None] | None = None) -> subprocess.CompletedProcess[str]:
    result = driver(argv, cwd=repository, timeout=_call_timeout(on_attempt))
    _work()
    _require(isinstance(result.stdout, str) and isinstance(result.stderr, str))
    _require(len(result.stdout.encode("utf-8")) <= MAX_OUTPUT_BYTES
             and len(result.stderr.encode("utf-8")) <= MAX_OUTPUT_BYTES)
    return result


def _read(driver: GitDriver, repository: Path, argv: tuple[str, ...]) -> str:
    result = _call(driver, repository, argv)
    _require(result.returncode == 0)
    return result.stdout


def _line(text: str) -> str:
    _require(text.endswith("\n") and text.count("\n") == 1 and "\r" not in text and "\0" not in text)
    return text[:-1]


def _hooks(path: Path) -> str:
    """Fingerprint bounded regular hook files; preserve every byte and mode."""
    safe_path(path)
    digest = hashlib.sha256()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent"
    _require(stat.S_ISDIR(info.st_mode))
    digest.update(repr(_file_identity(info)).encode("utf-8"))
    count = 0
    total = 0
    entries: list[Path] = []
    with os.scandir(path) as scan:
        for entry in scan:
            _work()
            count += 1
            _require(count <= MAX_HOOKS)
            entries.append(Path(entry.path))
    for child in sorted(entries):
        _work()
        safe_path(child, must_exist=True)
        fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
            _require(before.st_size <= MAX_HOOK_BYTES - total)
            data = bytearray()
            while len(data) <= before.st_size:
                _work()
                chunk = os.read(fd, min(65536, before.st_size + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            _require(_file_identity(before) == _file_identity(after) and len(data) == before.st_size)
            total += len(data)
            digest.update(child.name.encode("utf-8"))
            digest.update(repr(_file_identity(before)).encode("utf-8"))
            digest.update(data)
        finally:
            os.close(fd)
    return digest.hexdigest()


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    # atime can change because this module reads; it is not a content mutation.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid)


@dataclass(frozen=True)
class _State:
    config: str
    hooks_path: Path
    hooks_digest: str
    git_dir: Path


def _environment() -> tuple[tuple[str, str], ...]:
    """Refuse opaque transport overrides instead of changing caller settings."""
    forbidden = {
        "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "GIT_PROXY_COMMAND",
        "GIT_CONFIG_PARAMETERS", "GIT_CONFIG", "GIT_DIR", "GIT_WORK_TREE",
        "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_REPLACE_REF_BASE", "GIT_EXEC_PATH", "GIT_SSL_NO_VERIFY",
        "GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_SSL_VERSION", "GIT_SSL_CIPHER_LIST",
        "GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "GIT_NAMESPACE",
        "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
        "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    }
    _require(not forbidden.intersection(os.environ))
    _require(os.environ.get("GIT_CONFIG_COUNT", "0") == "0")
    _require(not any(key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) for key in os.environ))
    # Fingerprint, but never display, routing and executable-selection inputs.
    return tuple(sorted((key, value) for key, value in os.environ.items()
                        if key.startswith(("GIT_", "SSH_", "LC_")) or key in {
                            "HOME", "XDG_CONFIG_HOME", "PATH", "LANG", "SSL_CERT_FILE",
                            "SSL_CERT_DIR", "CURL_CA_BUNDLE", "no_proxy", "NO_PROXY"}))


def _config(raw: str, request: PushRequest, grant: PushGrant) -> None:
    """Parse NUL-delimited origin/key-value pairs before resolving includes."""
    parts = raw.split("\0")
    _require(parts[-1] == "" and len(parts) % 2 == 1 and len(parts) <= 8193)
    values: dict[str, list[str]] = {}
    for index in range(0, len(parts) - 1, 2):
        _work()
        origin, record = parts[index:index + 2]
        key, separator, value = record.partition("\n")
        _require(origin.startswith("file:") and bool(origin[5:]))
        _require(bool(key) and all(ord(c) >= 32 and ord(c) != 127 for c in key))
        key = key.lower()
        _require(not key.startswith(("include.", "includeif.", "url.")))
        _require(key not in {"core.sshcommand", "core.gitproxy", "ssh.variant", "core.fsmonitor",
                             "core.alternaterefscommand", "core.worktree"})
        # HTTP overrides may redirect or weaken TLS, route through a proxy, or
        # supply credentials/headers to an unintended server. Never rewrite them.
        _require(not key.startswith(("http.", "https.", "protocol.", "extensions.")))
        if key.startswith("remote."):
            field = key.rsplit(".", 1)[-1]
            _require(field not in {"push", "mirror", "receivepack", "uploadpack", "proxy",
                                   "proxyauthmethod", "vcs"})
        if key.startswith("credential."):
            field = key.rsplit(".", 1)[-1]
            _require(field not in {"interactive", "credentialstore"})
            if field == "helper":
                # Git invokes helpers through a shell, even without a leading !.
                # Preserve simple named helpers; opaque commands need host review.
                _require(value == "" or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is not None)
        _require(key != "core.askpass")
        _require(key not in {"push.gpgsign", "gpg.program", "gpg.ssh.program", "gpg.x509.program"})
        values.setdefault(key, []).append(value if separator else "true")
    # Repeated identical URLs are still multiple authority candidates.
    for key, candidates in values.items():
        _work()
        if key.startswith("remote.") and key.endswith((".url", ".pushurl")):
            _require(len(candidates) == 1 and ENDPOINT.fullmatch(candidates[0]) is not None
                     and ".." not in candidates[0])
    selected = "remote." + request.remote.lower()
    _require(values.get(selected + ".url") == [grant.remote_url])
    _require(values.get(selected + ".pushurl", [grant.remote_url]) == [grant.remote_url])
    _require(len(values.get("core.hookspath", [])) <= 1)


def _absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _graph(git_dir: Path) -> None:
    # Linked/shared repositories and alternate/replacement graphs require a
    # separate qualification; never let a synthetic graph prove ancestry.
    for suffix in ("commondir", "info/grafts", "objects/info/alternates", "shallow"):
        _work()
        _require(_absent(git_dir / suffix))
    replacements = git_dir / "refs/replace"
    if not _absent(replacements):
        safe_path(replacements, must_exist=True)
        with os.scandir(replacements) as entries:
            _require(next(entries, None) is None)
    packed = git_dir / "packed-refs"
    if not _absent(packed):
        safe_path(packed, must_exist=True)
        fd = os.open(packed, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_OUTPUT_BYTES)
            data = bytearray()
            while len(data) <= MAX_OUTPUT_BYTES:
                _work()
                chunk = os.read(fd, min(65536, MAX_OUTPUT_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            _require(len(data) <= MAX_OUTPUT_BYTES and b" refs/replace/" not in data)
            _require(_file_identity(info) == _file_identity(os.fstat(fd)))
        finally:
            os.close(fd)


def _ssh_configuration(grant: PushGrant) -> None:
    if not grant.remote_url.startswith("git@"):
        return
    home_text = os.environ.get("HOME", "")
    _require(home_text.startswith("/"))
    # Never evaluate Match exec, Include, aliases, or verification overrides.
    # Any user configuration is opaque and needs host qualification.
    user_config = Path(home_text) / ".ssh/config"
    _require(_absent(user_config))
    # System SSH policy is not silently bypassed. A production host with a
    # system policy must use a separately qualified driver (H), not -F /dev/null.
    # Only the production driver checks this OS location; injected drivers own
    # their isolated transport's host policy, just as they own endpoint routing.


def _preflight(request: PushRequest, grant: PushGrant, driver: GitDriver) -> _State:
    _environment()
    _ssh_configuration(grant)
    if driver is production_driver and grant.remote_url.startswith("git@"):
        _require(_absent(Path("/etc/ssh/ssh_config")))
    raw = _read(driver, request.repository, RAW_CONFIG)
    _config(raw, request, grant)
    top = _line(_read(driver, request.repository, ("rev-parse", "--show-toplevel")))
    _require(top == str(request.repository))
    safe_path(request.repository, must_exist=True)
    git_dir = Path(_line(_read(driver, request.repository, ("rev-parse", "--absolute-git-dir"))))
    safe_path(git_dir, must_exist=True)
    _graph(git_dir)
    _require(_line(_read(driver, request.repository, ("rev-parse", "--verify", "HEAD"))) == request.oid)
    _require(_line(_read(driver, request.repository, ("symbolic-ref", "--quiet", "HEAD"))) == request.destination_ref)
    url = _line(_read(driver, request.repository, ("remote", "get-url", "--push", "--all", request.remote)))
    _require(url == grant.remote_url)
    hooks = Path(_line(_read(driver, request.repository, ("rev-parse", "--git-path", "hooks"))))
    if not hooks.is_absolute():
        hooks = request.repository / hooks
    return _State(raw, hooks, _hooks(hooks), git_dir)


def _remote(request: PushRequest, grant: PushGrant, driver: GitDriver) -> str | None:
    result = _call(driver, request.repository,
                   ("ls-remote", "--exit-code", "--refs", "--", grant.remote_url, request.destination_ref))
    if result.returncode == 2 and result.stdout == "" and result.stderr == "":
        return None
    _require(result.returncode == 0)
    line = _line(result.stdout)
    oid, separator, ref = line.partition("\t")
    _require(separator == "\t" and ref == request.destination_ref)
    return validate_oid(oid)


def checkpoint_push(request: PushRequest, grant: PushGrant | None = None, *,
                    driver: GitDriver = production_driver,
                    on_attempt: Callable[[], None] | None = None) -> PushResult:
    """Publish at most once; the optional observer is in-process, never authority."""
    if request.policy.push_after_commit is False:
        return PushResult("disabled", request.oid, request.destination_ref, None,
                          "Checkpoint publication is disabled.")
    try:
        _require(grant is not None)
        assert grant is not None
        captured, approved = _authority(request, grant)
        environment = _environment()
        state = _preflight(captured, approved, driver)
        initial_oid = _remote(captured, approved, driver)
        if initial_oid == captured.oid:
            return PushResult("already-present", captured.oid, captured.destination_ref, initial_oid,
                              "Exact captured OID is already present; no publication attempted.")
        if initial_oid is not None:
            ancestry = _call(driver, captured.repository,
                             ("merge-base", "--is-ancestor", initial_oid, captured.oid))
            _require(ancestry.returncode == 0)
        _require(_authority(request, grant) == (captured, approved))
        _require(_environment() == environment)
        _require(_preflight(captured, approved, driver) == state)
        _require(_remote(captured, approved, driver) == initial_oid)
        _require(_preflight(captured, approved, driver) == state)
        _require(_environment() == environment)
        _require(_authority(request, grant) == (captured, approved))
        return _publish(captured, approved, driver, on_attempt)
    except (_Denied, ContinuityError, OSError, subprocess.SubprocessError, UnicodeError):
        return PushResult("denied", request.oid, request.destination_ref, None,
                          "Checkpoint refused; reconcile current authority and repository safety.")


def _publish(request: PushRequest, grant: PushGrant, driver: GitDriver,
             on_attempt: Callable[[], None] | None = None) -> PushResult:
    """After an attempt, only one exact readback is permitted, even on failure."""
    try:
        _call(driver, request.repository,
              ("-c", "push.followTags=false", "-c", "push.recurseSubmodules=no",
               "push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
               "--", grant.remote_url, f"{request.oid}:{request.destination_ref}"),
              on_attempt=on_attempt)
    except (_Denied, OSError, subprocess.SubprocessError, UnicodeError):
        # A failed command is not evidence of rejection: reconcile, never retry.
        return _reconcile(request, grant, driver)
    return _reconcile(request, grant, driver)


def _reconcile(request: PushRequest, grant: PushGrant, driver: GitDriver) -> PushResult:
    try:
        remote_oid = _remote(request, grant, driver)
    except (_Denied, ContinuityError, OSError, subprocess.SubprocessError, UnicodeError):
        return PushResult("unknown", request.oid, request.destination_ref, None,
                          "Publication outcome unreadable; reconcile the exact ref before another attempt.")
    return PushResult("pushed" if remote_oid == request.oid else "failed",
                      request.oid, request.destination_ref, remote_oid,
                      "Exact remote ref observed; no retry. Local commit retained.")
