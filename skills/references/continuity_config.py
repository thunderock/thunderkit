"""Model-free, read-only continuity v1 contracts.

Settings and manifests are data, never permission. These decoders check structure
and bounds, not file integrity, storage durability, remote proof or current grants.
S/R/G/H must establish those properties at the point of use. Paths are literal;
there is no shell, home scan, model dependency, write or network operation here.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Literal, NoReturn, TypeAlias, cast

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
ScopeKind: TypeAlias = Literal["project", "global"]
ContextSource: TypeAlias = Literal["explicit", "project", "global-project", "global", "memory", "unconfigured"]
HistoryStatus: TypeAlias = Literal["not-requested", "not-applicable", "remote-verified", "bundled", "unprotected"]
SnapshotStatus: TypeAlias = Literal["complete", "partial", "unchanged"]
PushStatus: TypeAlias = Literal["disabled", "denied", "pushed", "already-present", "failed", "unknown"]
CONFIG_MAX_BYTES = 1024 * 1024
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
JSON_MAX_DEPTH = 32
HISTORY_STATUSES = ("not-requested", "not-applicable", "remote-verified", "bundled", "unprotected")
MAXIMUMS = {
    "interval_minutes": 10_080, "retention": 10_000,
    "max_file_bytes": 1024 ** 3, "max_snapshot_bytes": 10 * 1024 ** 3,
    "max_context_bytes": 16 * 1024 ** 2, "max_files": 100_000,
    "max_path_bytes": 4096, "max_sources": 10_000,
}


class ContinuityError(Exception):
    """Stable machine code and caller-safe detail (never pass raw OS errors)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _fail(code: str = "invalid_config") -> NoReturn:
    details = {
        "invalid_config": "Continuity data does not satisfy schema v1.",
        "invalid_manifest": "Backup manifest does not satisfy schema v1.",
        "unsafe_path": "Path is unsupported, unsafe or outside the approved root.",
        "identity_required": "An explicit scope identity or bound private identity is required.",
        "identity_mismatch": "Scope identity or project binding does not match.",
        "context_not_found": "The selected context is not available; no fallback was used.",
        "read_failed": "The selected document could not be read safely.",
    }
    raise ContinuityError(code, details[code])


def _integer(value: object, maximum: int, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        _fail()
    return value


def _text(value: object, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _fail()
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        _fail()
    return value


def _token(value: object) -> str:
    text = _text(value, 128)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text) is None or ".." in text:
        _fail()
    return text


def validate_path_text(value: object, *, absolute: bool = False,
                       relative: bool = False, max_bytes: int = 4096) -> str:
    """Validate without expansion, normalization or filesystem access; return unchanged."""
    try:
        text = _text(value)
        if len(text.encode("utf-8")) > max_bytes:
            _fail("unsafe_path")
    except ContinuityError:
        _fail("unsafe_path")
    if ("\\" in text or ":" in text or text.startswith("//")
            or ".." in text.split("/") or (absolute and not text.startswith("/"))
            or (relative and (text.startswith("/") or any(p in ("", ".") for p in text.split("/"))))):
        _fail("unsafe_path")
    return text


def validate_relpath(value: object, *, max_bytes: int = 4096) -> str:
    return validate_path_text(value, relative=True, max_bytes=max_bytes)


def literal_path(value: str | Path, *, base: Path | None = None,
                 absolute: bool = False) -> Path:
    text = validate_path_text(str(value), absolute=absolute)
    path = Path(text)
    if not path.is_absolute():
        path = (base if base is not None else Path.cwd()) / path
    validate_path_text(str(path), absolute=True)
    return path


def safe_path(value: str | Path, *, base: Path | None = None,
              root: Path | None = None, must_exist: bool = False,
              missing_code: str = "unsafe_path") -> Path:
    """lstat every existing component; never dereference a link or create a root.

    This is a point-in-time inspection, not a race-proof authorization to open a
    payload later. read_json uses a separate descriptor-relative no-follow walk.
    """
    path = literal_path(value, base=base)
    if root is not None and not path.is_relative_to(literal_path(root)):
        _fail("unsafe_path")
    current = Path(path.anchor)
    try:
        for i, part in enumerate(path.parts[1:]):
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if must_exist:
                    _fail(missing_code)
                break
            if stat.S_ISLNK(info.st_mode):
                _fail("unsafe_path")
            if i < len(path.parts) - 2:
                if not stat.S_ISDIR(info.st_mode):
                    _fail("unsafe_path")
            elif not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                _fail("unsafe_path")
    except OSError:
        _fail("unsafe_path")
    return path


def _path_field(value: object) -> None:
    if not isinstance(value, Path):
        _fail("unsafe_path")
    validate_path_text(str(value), absolute=True)


def _paths(values: tuple[Path, ...], maximum: int = 10_000) -> None:
    if type(values) is not tuple or len(values) > maximum:
        _fail()
    for value in values:
        _path_field(value)
    if len(set(values)) != len(values):
        _fail()


def _strings(values: tuple[str, ...], *, paths: bool = False, maximum: int = 100_000) -> None:
    if type(values) is not tuple or len(values) > maximum:
        _fail()
    for value in values:
        if paths:
            validate_relpath(value)
        else:
            _text(value)
    if paths and len(set(values)) != len(values):
        _fail()


def validate_oid(value: object) -> str:
    text = _text(value, 64)
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", text) is None:
        _fail()
    return text


def validate_ref(value: object) -> str:
    """Only full branch refs; this is syntax validation, not branch-safety proof."""
    text = _text(value, 1024)
    if (not text.startswith("refs/heads/") or text.endswith(("/", "."))
            or any(c in text for c in " ~^:?*[\\") or ".." in text or "@{" in text
            or any(not p or p.startswith(".") or p.endswith(".lock") for p in text.split("/"))):
        _fail()
    return text


def validate_sha256(value: object) -> str:
    text = _text(value, 64)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        _fail()
    return text


def _timestamp(value: object) -> str:
    text = _text(value, 40)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", text) is None:
        _fail()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    if parsed.tzinfo is None:
        _fail()
    return text


@dataclass(frozen=True)
class ScopeIdentity:
    kind: ScopeKind
    id: str

    def __post_init__(self) -> None:
        if self.kind not in ("project", "global"):
            _fail()
        _token(self.id)


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_snapshot_bytes: int = 100 * 1024 * 1024
    max_context_bytes: int = 64 * 1024
    max_files: int = 10_000
    max_path_bytes: int = 4096
    max_sources: int = 1000

    def __post_init__(self) -> None:
        for name in ("max_file_bytes", "max_snapshot_bytes", "max_context_bytes",
                     "max_files", "max_path_bytes", "max_sources"):
            _integer(getattr(self, name), MAXIMUMS[name])
        if self.max_file_bytes > self.max_snapshot_bytes:
            _fail()


@dataclass(frozen=True)
class BackupPolicy:
    enabled: bool = False
    interval_minutes: int = 15
    retention: int = 20
    prune: bool = False

    def __post_init__(self) -> None:
        _boolean(self.enabled)
        _boolean(self.prune)
        _integer(self.interval_minutes, MAXIMUMS["interval_minutes"])
        _integer(self.retention, MAXIMUMS["retention"])


@dataclass(frozen=True)
class GitPolicy:
    push_after_commit: bool = True

    def __post_init__(self) -> None:
        _boolean(self.push_after_commit)


@dataclass(frozen=True)
class ResolvedSettings:
    identity: ScopeIdentity
    project_root: Path | None
    backup_root: Path | None
    context_path: Path | None
    context_source: ContextSource
    backup: BackupPolicy = field(default_factory=BackupPolicy)
    git: GitPolicy = field(default_factory=GitPolicy)
    limits: Limits = field(default_factory=Limits)
    approved_sources: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.identity, ScopeIdentity) or not isinstance(self.backup, BackupPolicy)
                or not isinstance(self.git, GitPolicy) or not isinstance(self.limits, Limits)):
            _fail()
        for path in (self.project_root, self.backup_root, self.context_path):
            if path is not None:
                _path_field(path)
        if self.context_source not in ("explicit", "project", "global-project", "global", "memory", "unconfigured"):
            _fail()
        if (self.context_path is None) != (self.context_source == "unconfigured"):
            _fail()
        if self.identity.kind == "global" and self.project_root is not None:
            _fail("identity_mismatch")
        _paths(self.approved_sources, self.limits.max_sources)


@dataclass(frozen=True)
class StorageIdentity:
    root: Path
    device: int
    inode: int
    backing_fingerprint: str | None = None
    mount_fingerprint: str | None = None
    durability: str = "unqualified"

    def __post_init__(self) -> None:
        _path_field(self.root)
        _integer(self.device, 2 ** 64 - 1, 0)
        _integer(self.inode, 2 ** 64 - 1, 0)
        for value in (self.backing_fingerprint, self.mount_fingerprint, self.durability):
            if value is not None:
                _text(value)


@dataclass(frozen=True)
class SnapshotRequest:
    settings: ResolvedSettings
    storage: StorageIdentity
    approved_sources: tuple[Path, ...]
    context_entrypoints: tuple[str, ...] = ()
    include_history: bool = False
    history_ref: str | None = None
    verified_remote_oid: str | None = None
    remote_evidence: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.settings, ResolvedSettings) or not isinstance(self.storage, StorageIdentity):
            _fail()
        if self.settings.backup_root != self.storage.root:
            _fail("identity_mismatch")
        _paths(self.approved_sources, self.settings.limits.max_sources)
        _strings(self.context_entrypoints, paths=True, maximum=self.settings.limits.max_files)
        if (self.settings.identity.kind == "global"
                and not set(self.approved_sources) <= set(self.settings.approved_sources)):
            _fail("identity_mismatch")
        _boolean(self.include_history)
        if self.settings.identity.kind == "global" and any(
            value is not None for value in (self.history_ref, self.verified_remote_oid, self.remote_evidence)
        ):
            _fail("identity_mismatch")
        if self.settings.identity.kind == "global" and self.include_history:
            _fail("identity_mismatch")
        if self.include_history and self.history_ref is None:
            _fail()
        if self.history_ref is not None:
            validate_ref(self.history_ref)
        if (self.verified_remote_oid is None) != (self.remote_evidence is None):
            _fail()
        if self.verified_remote_oid is not None:
            validate_oid(self.verified_remote_oid)
            _text(self.remote_evidence)


@dataclass(frozen=True)
class SnapshotResult:
    identity: ScopeIdentity
    snapshot_path: Path | None
    status: SnapshotStatus
    omissions: tuple[str, ...]
    history_status: HistoryStatus
    manifest_sha256: str | None = None
    last_locator: Path | None = None
    last_verified_snapshot: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity) or self.status not in ("complete", "partial", "unchanged"):
            _fail()
        _strings(self.omissions)
        if self.history_status not in HISTORY_STATUSES:
            _fail()
        if self.status != "partial" and (self.omissions or self.history_status == "unprotected" or self.snapshot_path is None):
            _fail()
        for path in (self.snapshot_path, self.last_locator, self.last_verified_snapshot):
            if path is not None:
                _path_field(path)
        if self.manifest_sha256 is not None:
            validate_sha256(self.manifest_sha256)


@dataclass(frozen=True)
class ContextMetadata:
    snapshot_id: str | None = None
    timestamp: str | None = None
    repository: str | None = None
    ref: str | None = None
    head: str | None = None
    included_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    omissions: tuple[str, ...] = ()
    history_status: HistoryStatus = "not-requested"

    def __post_init__(self) -> None:
        if self.snapshot_id is not None:
            _token(self.snapshot_id)
        if self.timestamp is not None:
            _timestamp(self.timestamp)
        if self.repository is not None:
            validate_path_text(self.repository, absolute=True)
        if self.ref is not None:
            validate_ref(self.ref)
        if self.head is not None:
            validate_oid(self.head)
        _strings(self.included_paths, paths=True)
        _strings(self.deleted_paths, paths=True)
        _strings(self.omissions)
        if self.history_status not in HISTORY_STATUSES:
            _fail()


@dataclass(frozen=True)
class ContextLoad:
    identity: ScopeIdentity
    source: Path
    text: str
    integrity_verified: bool
    warnings: tuple[str, ...]
    metadata: ContextMetadata = field(default_factory=ContextMetadata)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity) or not isinstance(self.metadata, ContextMetadata):
            _fail()
        _path_field(self.source)
        _boolean(self.integrity_verified)
        _strings(self.warnings)
        if not isinstance(self.text, str):
            _fail()
        try:
            if len(self.text.encode("utf-8")) > MAXIMUMS["max_context_bytes"]:
                _fail()
        except UnicodeError:
            _fail()


@dataclass(frozen=True)
class ExportResult:
    identity: ScopeIdentity
    destination: Path
    status: Literal["complete", "partial"]
    exported_paths: tuple[str, ...]
    omissions: tuple[str, ...]
    history_status: HistoryStatus
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity) or self.status not in ("complete", "partial"):
            _fail()
        _path_field(self.destination)
        _strings(self.exported_paths, paths=True)
        _strings(self.omissions)
        _strings(self.warnings)
        if self.history_status not in HISTORY_STATUSES:
            _fail()
        if self.status == "complete" and (self.omissions or self.history_status == "unprotected"):
            _fail()


@dataclass(frozen=True)
class PushRequest:
    identity: ScopeIdentity
    repository: Path
    remote: str
    destination_ref: str
    oid: str
    policy: GitPolicy = field(default_factory=GitPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity) or self.identity.kind != "project" or not isinstance(self.policy, GitPolicy):
            _fail()
        _path_field(self.repository)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", _text(self.remote, 128)) is None:
            _fail()
        validate_ref(self.destination_ref)
        validate_oid(self.oid)


@dataclass(frozen=True)
class PushGrant:
    """Current caller authority only; unknown branch safety DENIES in G.

    Construction validates syntax, NOT authority. Never deserialize from backup
    or settings. G must compare every binding and independently inspect transport.
    """
    identity: ScopeIdentity
    repository: Path
    remote_url: str
    destination_ref: str
    default_branch: str | None = None
    protected_refs: tuple[str, ...] = ()
    branch_safety_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity) or self.identity.kind != "project":
            _fail()
        _path_field(self.repository)
        _text(self.remote_url)
        validate_ref(self.destination_ref)
        if self.default_branch is not None:
            validate_ref(self.default_branch)
        _strings(self.protected_refs)
        for ref in self.protected_refs:
            validate_ref(ref)
        _boolean(self.branch_safety_verified)
        if self.branch_safety_verified and self.default_branch is None:
            _fail()


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    oid: str
    destination_ref: str
    remote_oid: str | None
    detail: str = "No remote protection inferred."

    def __post_init__(self) -> None:
        if self.status not in ("disabled", "denied", "pushed", "already-present", "failed", "unknown"):
            _fail()
        validate_oid(self.oid)
        validate_ref(self.destination_ref)
        if self.remote_oid is not None:
            validate_oid(self.remote_oid)
        if self.status in ("pushed", "already-present") and self.remote_oid != self.oid:
            _fail()
        _text(self.detail)


def _object(value: JsonValue, allowed: set[str], required: set[str] | None = None) -> JsonObject:
    if not isinstance(value, dict) or not set(value) <= allowed or not (required or set()) <= set(value):
        _fail()
    return value


def _schema(raw: JsonObject, kind: str | None = None) -> None:
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        _fail()
    if kind is not None and raw.get("kind") != kind:
        _fail()


def decode_identity(value: JsonValue) -> ScopeIdentity:
    raw = _object(value, {"kind", "id"}, {"kind", "id"})
    kind = _text(raw["kind"])
    if kind not in ("project", "global"):
        _fail()
    return ScopeIdentity(cast(ScopeKind, kind), _token(raw["id"]))


def _pairs(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _nonfinite(text: str) -> NoReturn:
    _fail()


def _check_json(value: JsonValue, depth: int = 0) -> None:
    if depth > JSON_MAX_DEPTH:
        _fail()
    if isinstance(value, dict):
        for key, child in value.items():
            _text(key)
            _check_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_json(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        _fail()
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail()


def decode_json(data: bytes | str, *, max_bytes: int = CONFIG_MAX_BYTES) -> JsonObject:
    """Bound UTF-8 bytes/depth before parsing; reject duplicates and nonfinite numbers."""
    _integer(max_bytes, MANIFEST_MAX_BYTES)
    try:
        encoded = data.encode("utf-8") if isinstance(data, str) else data
        if len(encoded) > max_bytes:
            _fail()
        text = encoded.decode("utf-8")
        depth = 0
        quoted = escaped = False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > JSON_MAX_DEPTH:
                    _fail()
            elif char in "]}":
                depth -= 1
        raw = cast(JsonValue, json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite))
        if not isinstance(raw, dict):
            _fail()
        _check_json(raw)
        return raw
    except (ValueError, UnicodeError, RecursionError):
        _fail()


def read_json(path: str | Path, *, max_bytes: int = CONFIG_MAX_BYTES) -> JsonObject:
    """Bounded regular-file read through a no-follow dir-fd walk (no writes).

    Unsupported hosts fail closed. Hardlinks, special files and files changing
    while read are refused. Consumers opening payload bytes need the same care.
    """
    _integer(max_bytes, MANIFEST_MAX_BYTES)
    selected = safe_path(path, must_exist=True, missing_code="read_failed")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        _fail("unsafe_path")
    directory = -1
    document = -1
    try:
        directory = os.open(selected.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in selected.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        document = os.open(selected.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(document)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail("unsafe_path")
        if before.st_size > max_bytes:
            _fail()
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(document, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(document)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            _fail("read_failed")
        return decode_json(b"".join(chunks), max_bytes=max_bytes)
    except OSError:
        _fail("read_failed")
    finally:
        if document >= 0:
            os.close(document)
        if directory >= 0:
            os.close(directory)


def _optional_config(path: Path | None) -> JsonObject | None:
    if path is None:
        return None
    selected = safe_path(path)
    try:
        selected.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        _fail("read_failed")
    return read_json(selected)


def validate_policy(value: JsonValue) -> JsonObject:
    """Validate supplied fields, preserving absence (including false overrides)."""
    raw = _object(value, {"schema", "backup", "restore", "git"}, {"schema"})
    _schema(raw)
    if "backup" in raw:
        backup = _object(raw["backup"], {"enabled", "interval_minutes", "retention", "prune", "limits"})
        for name, item in backup.items():
            if name in ("enabled", "prune"):
                _boolean(item)
            elif name == "limits":
                limits = _object(item, {"max_file_bytes", "max_snapshot_bytes", "max_files", "max_path_bytes", "max_sources"})
                for key, limit in limits.items():
                    _integer(limit, MAXIMUMS[key])
                if ("max_file_bytes" in limits and "max_snapshot_bytes" in limits
                        and cast(int, limits["max_file_bytes"]) > cast(int, limits["max_snapshot_bytes"])):
                    _fail()
            else:
                _integer(item, MAXIMUMS[name])
    if "restore" in raw:
        restore = _object(raw["restore"], {"max_context_bytes"})
        if "max_context_bytes" in restore:
            _integer(restore["max_context_bytes"], MAXIMUMS["max_context_bytes"])
    if "git" in raw:
        git = _object(raw["git"], {"push_after_commit"})
        if "push_after_commit" in git:
            _boolean(git["push_after_commit"])
    return raw


@dataclass(frozen=True)
class ContextLocator:
    identity: ScopeIdentity
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity):
            _fail()
        validate_path_text(self.path)


@dataclass(frozen=True)
class ScopeSettings:
    identity: ScopeIdentity
    project_root: Path | None = None
    backup_root: Path | None = None
    latest_context: ContextLocator | None = None
    approved_sources: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScopeIdentity):
            _fail()
        if (self.identity.kind == "project") != (self.project_root is not None):
            _fail("identity_mismatch")
        for path in (self.project_root, self.backup_root):
            if path is not None:
                _path_field(path)
        if self.latest_context is not None:
            if not isinstance(self.latest_context, ContextLocator) or self.latest_context.identity != self.identity:
                _fail("identity_mismatch")
        _paths(self.approved_sources)
        if self.identity.kind == "project" and self.approved_sources:
            _fail()


def decode_locator(value: JsonValue, *, pointer: bool = False) -> ContextLocator:
    keys = {"identity", "path"} | ({"schema", "kind"} if pointer else set())
    raw = _object(value, keys, keys)
    if pointer:
        _schema(raw, "context-pointer")
    return ContextLocator(decode_identity(raw["identity"]), validate_path_text(raw["path"]))


def decode_scope_settings(value: JsonValue, kind: ScopeKind, *, private: bool = False) -> ScopeSettings:
    keys = {"identity", "backup_root", "latest_context"} | ({"project_root"} if kind == "project" else {"approved_sources"})
    required = {"identity"} | ({"project_root"} if kind == "project" else set())
    if private:
        keys |= {"schema", "kind"}
        required |= {"schema", "kind"}
    raw = _object(value, keys, required)
    if private:
        _schema(raw, "project-settings")
    identity = decode_identity(raw["identity"])
    if identity.kind != kind:
        _fail("identity_mismatch")
    sources: tuple[Path, ...] = ()
    if "approved_sources" in raw:
        items = raw["approved_sources"]
        if not isinstance(items, list) or len(items) > MAXIMUMS["max_sources"]:
            _fail()
        sources = tuple(Path(validate_path_text(item, absolute=True)) for item in items)
    return ScopeSettings(
        identity,
        Path(validate_path_text(raw["project_root"], absolute=True)) if "project_root" in raw else None,
        Path(validate_path_text(raw["backup_root"], absolute=True)) if "backup_root" in raw else None,
        decode_locator(raw["latest_context"]) if "latest_context" in raw else None,
        sources,
    )


def validate_continuity_document(value: JsonValue, *, kind: str = "policy") -> JsonObject:
    """Validate one of policy/project-settings/global-settings/context-pointer.

    Filesystem availability is intentionally separate; validation grants nothing.
    """
    if kind == "policy":
        return validate_policy(value)
    if kind == "project-settings":
        decode_scope_settings(value, "project", private=True)
    elif kind == "context-pointer":
        decode_locator(value, pointer=True)
    elif kind == "global-settings":
        raw = _object(value, {"schema", "kind", "policy", "global", "projects"}, {"schema", "kind"})
        _schema(raw, kind)
        if "policy" in raw:
            validate_policy(raw["policy"])
        if "global" in raw:
            decode_scope_settings(raw["global"], "global")
        if "projects" in raw:
            projects = raw["projects"]
            if not isinstance(projects, dict) or len(projects) > 10_000:
                _fail()
            for key, entry in projects.items():
                _token(key)
                if decode_scope_settings(entry, "project").identity.id != key:
                    _fail("identity_mismatch")
    else:
        _fail()
    assert isinstance(value, dict)
    return value


def _global_location(explicit: Path | None, environment: Mapping[str, str]) -> Path | None:
    if explicit is not None:
        return literal_path(explicit)
    for key, suffix in (("THUNDERKIT_HOME", "continuity.json"),
                        ("XDG_CONFIG_HOME", "thunderkit/continuity.json"),
                        ("HOME", ".config/thunderkit/continuity.json")):
        if key in environment:
            return literal_path(validate_path_text(environment[key], absolute=True)) / suffix
    return None


def _merge_policy(global_policy: JsonObject, project_policy: JsonObject,
                  approved_root: bool) -> tuple[BackupPolicy, GitPolicy, Limits]:
    backup: JsonObject = {"enabled": approved_root, "interval_minutes": 15, "retention": 20, "prune": False}
    limits: JsonObject = {"max_file_bytes": 10 * 1024 * 1024, "max_snapshot_bytes": 100 * 1024 * 1024,
                          "max_context_bytes": 64 * 1024, "max_files": 10_000,
                          "max_path_bytes": 4096, "max_sources": 1000}
    push = True
    for policy in (global_policy, project_policy):
        for section, values in policy.items():
            if not isinstance(values, dict):
                continue
            if section == "backup":
                backup.update({k: v for k, v in values.items() if k != "limits"})
                nested = values.get("limits", {})
                assert isinstance(nested, dict)
                limits.update(nested)
            elif section == "restore":
                limits.update(values)
            elif section == "git" and "push_after_commit" in values:
                push = _boolean(values["push_after_commit"])
    return (
        BackupPolicy(_boolean(backup["enabled"]), _integer(backup["interval_minutes"], 10_080),
                     _integer(backup["retention"], 10_000), _boolean(backup["prune"])),
        GitPolicy(push), Limits(**{k: _integer(v, MAXIMUMS[k]) for k, v in limits.items()}),
    )


def resolve_continuity(project_root: Path | None = None, global_config: Path | None = None,
                       explicit_backup_path: Path | None = None, scope: str = "project", *,
                       expected_identity: ScopeIdentity | None = None,
                       memory_pointer: Path | None = None,
                       environment: Mapping[str, str] | None = None) -> ResolvedSettings:
    """Resolve exact scope and locator without writes, grants or model readiness.

    explicit_backup_path is a CONTEXT locator, never a backup destination grant.
    An explicit environment replaces ambient selection entirely. Missing files
    are absence; present invalid documents and selected stale locators fail closed.
    """
    if scope not in ("project", "global"):
        _fail()
    if expected_identity is not None and expected_identity.kind != scope:
        _fail("identity_mismatch")
    root = safe_path(project_root) if project_root is not None and scope == "project" else None
    selected_global = _optional_config(_global_location(global_config, os.environ if environment is None else environment))
    global_raw = validate_continuity_document(selected_global, kind="global-settings") if selected_global is not None else {}
    private_raw = _optional_config(root / ".thunderkit/continuity.local.json") if root is not None else None
    policy_raw = _optional_config(root / ".thunderkit/continuity.json") if root is not None else None
    project_policy = validate_policy(policy_raw) if policy_raw is not None else {}
    private = decode_scope_settings(private_raw, "project", private=True) if private_raw is not None else None
    identity = expected_identity
    if private is not None:
        if private.project_root != root or (identity is not None and private.identity != identity):
            _fail("identity_mismatch")
        identity = private.identity
    selected: ScopeSettings | None = None
    if scope == "global" and "global" in global_raw:
        selected = decode_scope_settings(global_raw["global"], "global")
        if identity is not None and identity != selected.identity:
            _fail("identity_mismatch")
        identity = selected.identity
    if identity is None:
        _fail("identity_required")
    if scope == "project":
        projects = global_raw.get("projects", {})
        assert isinstance(projects, dict)
        if identity.id in projects:
            selected = decode_scope_settings(projects[identity.id], "project")
            if selected.identity != identity or (root is not None and selected.project_root != root):
                _fail("identity_mismatch")
    backup_root = private.backup_root if private is not None and private.backup_root is not None else (selected.backup_root if selected else None)
    if backup_root is not None:
        backup_root = safe_path(backup_root)
        try:
            if not stat.S_ISDIR(backup_root.lstat().st_mode):
                _fail("unsafe_path")
        except FileNotFoundError:
            pass  # Configured but not yet created/qualified is a valid setting.
        except OSError:
            _fail("unsafe_path")
    global_policy = global_raw.get("policy", {})
    assert isinstance(global_policy, dict)
    backup, git, limits = _merge_policy(global_policy, project_policy, backup_root is not None)
    sources = selected.approved_sources if selected is not None and scope == "global" else ()
    for source in sources:
        safe_path(source)
    context: Path | None = None
    source_kind: ContextSource = "unconfigured"
    locator: ContextLocator | None = None
    locator_root: Path | None = None
    if explicit_backup_path is not None:
        context = safe_path(explicit_backup_path, must_exist=True, missing_code="context_not_found")
        source_kind = "explicit"
    elif private is not None and private.latest_context is not None:
        locator, locator_root, source_kind = private.latest_context, backup_root, "project"
    elif selected is not None and selected.latest_context is not None:
        locator, locator_root = selected.latest_context, selected.backup_root
        source_kind = "global" if scope == "global" else "global-project"
    elif memory_pointer is not None:
        locator = decode_locator(read_json(memory_pointer), pointer=True)
        source_kind = "memory"
    if locator is not None:
        if locator.identity != identity:
            _fail("identity_mismatch")
        if not Path(locator.path).is_absolute() and locator_root is None:
            _fail("unsafe_path")
        context = safe_path(locator.path, base=locator_root, root=locator_root,
                            must_exist=True, missing_code="context_not_found")
    for path in (backup_root, context, *sources):
        if path is not None:
            validate_path_text(str(path), absolute=True, max_bytes=limits.max_path_bytes)
    return ResolvedSettings(identity, root, backup_root, context, source_kind, backup, git, limits, sources)


@dataclass(frozen=True)
class ManifestFile:
    relpath: str
    size: int
    sha256: str
    mode: int

    def __post_init__(self) -> None:
        validate_relpath(self.relpath)
        _integer(self.size, MAXIMUMS["max_file_bytes"], 0)
        validate_sha256(self.sha256)
        if type(self.mode) is not int or self.mode not in (0o644, 0o755):
            _fail()


@dataclass(frozen=True)
class HistoryRecord:
    status: HistoryStatus
    file: str | None = None
    size: int = 0
    sha256: str | None = None
    ref: str | None = None
    head: str | None = None
    remote_oid: str | None = None
    remote_evidence: str | None = None

    def __post_init__(self) -> None:
        if self.status not in HISTORY_STATUSES:
            _fail()
        _integer(self.size, MAXIMUMS["max_snapshot_bytes"], 0)
        if self.ref is not None:
            validate_ref(self.ref)
        if self.head is not None:
            validate_oid(self.head)
        if (self.ref is None) != (self.head is None):
            _fail()
        if self.status == "bundled":
            validate_relpath(self.file)
            validate_sha256(self.sha256)
            if self.size == 0 or self.ref is None or self.remote_oid is not None or self.remote_evidence is not None:
                _fail()
        elif self.file is not None or self.size != 0 or self.sha256 is not None:
            _fail()
        if self.status == "remote-verified":
            validate_oid(self.remote_oid)
            _text(self.remote_evidence)
            if self.head is None or self.remote_oid != self.head:
                _fail()
        elif self.remote_oid is not None or self.remote_evidence is not None:
            _fail()
        if self.status == "not-applicable" and self.head is not None:
            _fail()


def _path_set(paths: tuple[str, ...]) -> set[str]:
    """Disallow duplicate files and file/directory alias collisions."""
    known: set[str] = set()
    for path in paths:
        if path in known:
            _fail()
        known.add(path)
    for path in known:
        components = path.split("/")
        if any("/".join(components[:i]) in known for i in range(1, len(components))):
            _fail()
    return known


@dataclass(frozen=True)
class BackupManifest:
    schema: int
    identity: ScopeIdentity
    snapshot_id: str
    timestamp: str
    repository: str | None
    ref: str | None
    head: str | None
    base: str | None
    files: tuple[ManifestFile, ...]
    deleted_paths: tuple[str, ...]
    context_entrypoints: tuple[str, ...]
    history: HistoryRecord
    omissions: tuple[str, ...]
    completion: Literal["complete", "partial"]

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 1 or not isinstance(self.identity, ScopeIdentity):
            _fail()
        _token(self.snapshot_id)
        _timestamp(self.timestamp)
        if self.repository is not None:
            validate_path_text(self.repository, absolute=True)
        if self.ref is not None:
            validate_ref(self.ref)
        for oid in (self.head, self.base):
            if oid is not None:
                validate_oid(oid)
        if self.head is None and (self.ref is not None or self.base is not None):
            _fail()
        if self.identity.kind == "global" and any(v is not None for v in (self.repository, self.ref, self.head, self.base)):
            _fail("identity_mismatch")
        if type(self.files) is not tuple or len(self.files) > MAXIMUMS["max_files"]:
            _fail()
        for entry in self.files:
            if not isinstance(entry, ManifestFile):
                _fail()
        _strings(self.deleted_paths, paths=True)
        _strings(self.context_entrypoints, paths=True)
        _strings(self.omissions)
        if not isinstance(self.history, HistoryRecord):
            _fail()
        file_paths = _path_set(tuple(entry.relpath for entry in self.files))
        if file_paths.intersection(self.deleted_paths) or not set(self.context_entrypoints) <= file_paths:
            _fail()
        if self.history.file is not None:
            _path_set((*tuple(file_paths), self.history.file))
        if self.history.head is not None and (self.history.head != self.head or self.history.ref != self.ref):
            _fail("identity_mismatch")
        if self.completion not in ("complete", "partial"):
            _fail()
        if self.completion == "complete":
            if not self.files or self.omissions or self.history.status == "unprotected":
                _fail()
            if self.head is not None and self.history.status not in ("bundled", "remote-verified"):
                _fail()
        if sum(entry.size for entry in self.files) + self.history.size > MAXIMUMS["max_snapshot_bytes"]:
            _fail()


def _nullable_text(value: JsonValue) -> str | None:
    return None if value is None else _text(value)


def validate_manifest(value: JsonValue, *, expected_identity: ScopeIdentity | None = None,
                      limits: Limits | None = None) -> BackupManifest:
    """Decode inert metadata into immutable types, with cross-field/budget checks.

    No hash, history graph, trusted origin, freshness or storage guarantee is
    implied. Call read_manifest for byte/depth/duplicate-key protections too.
    """
    try:
        keys = {"schema", "identity", "snapshot_id", "timestamp", "repository", "ref", "head", "base",
                "files", "deleted_paths", "context_entrypoints", "history", "omissions", "completion"}
        raw = _object(value, keys, keys)
        _schema(raw)
        identity = decode_identity(raw["identity"])
        if expected_identity is not None and identity != expected_identity:
            _fail("identity_mismatch")
        budget = limits if limits is not None else Limits()
        file_values = raw["files"]
        if not isinstance(file_values, list) or len(file_values) > budget.max_files:
            _fail()
        files: list[ManifestFile] = []
        for item in file_values:
            entry = _object(item, {"relpath", "size", "sha256", "mode"}, {"relpath", "size", "sha256", "mode"})
            files.append(ManifestFile(validate_relpath(entry["relpath"], max_bytes=budget.max_path_bytes),
                                      _integer(entry["size"], budget.max_file_bytes, 0),
                                      validate_sha256(entry["sha256"]), _integer(entry["mode"], 0o755)))
        history_keys = {"status", "file", "size", "sha256", "ref", "head", "remote_oid", "remote_evidence"}
        history = _object(raw["history"], history_keys, {"status", "file", "size", "sha256", "ref", "head"})
        history_record = HistoryRecord(
            cast(HistoryStatus, _text(history["status"])), _nullable_text(history["file"]),
            _integer(history["size"], budget.max_snapshot_bytes, 0), _nullable_text(history["sha256"]),
            _nullable_text(history["ref"]), _nullable_text(history["head"]),
            _nullable_text(history.get("remote_oid")), _nullable_text(history.get("remote_evidence")),
        )
        lists: dict[str, tuple[str, ...]] = {}
        for name in ("deleted_paths", "context_entrypoints", "omissions"):
            items = raw[name]
            if not isinstance(items, list) or len(items) > budget.max_files:
                _fail()
            lists[name] = tuple(_text(item) for item in items)
            if name != "omissions":
                for path in lists[name]:
                    validate_relpath(path, max_bytes=budget.max_path_bytes)
        if history_record.file is not None:
            validate_relpath(history_record.file, max_bytes=budget.max_path_bytes)
        if sum(entry.size for entry in files) + history_record.size > budget.max_snapshot_bytes:
            _fail()
        return BackupManifest(
            1, identity, _token(raw["snapshot_id"]), _timestamp(raw["timestamp"]),
            _nullable_text(raw["repository"]), _nullable_text(raw["ref"]),
            _nullable_text(raw["head"]), _nullable_text(raw["base"]), tuple(files),
            lists["deleted_paths"], lists["context_entrypoints"], history_record, lists["omissions"],
            cast(Literal["complete", "partial"], _text(raw["completion"])),
        )
    except ContinuityError as error:
        if error.code == "invalid_config":
            _fail("invalid_manifest")
        raise


def read_manifest(path: str | Path, *, expected_identity: ScopeIdentity | None = None,
                  limits: Limits | None = None) -> BackupManifest:
    try:
        return validate_manifest(read_json(path, max_bytes=MANIFEST_MAX_BYTES),
                                 expected_identity=expected_identity, limits=limits)
    except ContinuityError as error:
        if error.code == "invalid_config":
            _fail("invalid_manifest")
        raise


def settings_to_json(settings: ResolvedSettings) -> JsonObject:
    """Detached display data; not a private-settings writer or authority format."""
    return {
        "identity": {"kind": settings.identity.kind, "id": settings.identity.id},
        "project_root": str(settings.project_root) if settings.project_root is not None else None,
        "backup_root": str(settings.backup_root) if settings.backup_root is not None else None,
        "context_path": str(settings.context_path) if settings.context_path is not None else None,
        "context_source": settings.context_source,
        "backup": {"enabled": settings.backup.enabled, "interval_minutes": settings.backup.interval_minutes,
                   "retention": settings.backup.retention, "prune": settings.backup.prune},
        "git": {"push_after_commit": settings.git.push_after_commit},
        "limits": {"max_file_bytes": settings.limits.max_file_bytes,
                   "max_snapshot_bytes": settings.limits.max_snapshot_bytes,
                   "max_context_bytes": settings.limits.max_context_bytes,
                   "max_files": settings.limits.max_files, "max_path_bytes": settings.limits.max_path_bytes,
                   "max_sources": settings.limits.max_sources},
        "approved_sources": [str(path) for path in settings.approved_sources],
    }


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _fail()


def main(argv: list[str] | None = None) -> int:
    """Read/inspect only; structured errors never print raw exception/input text."""
    parser = _SafeParser(description="Read-only, model-free continuity v1 inspection")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Resolve settings without setup or writes")
    inspect.add_argument("--project-root", type=Path)
    inspect.add_argument("--global-config", type=Path)
    inspect.add_argument("--scope", choices=("project", "global"), default="project")
    inspect.add_argument("--identity", required=True)
    inspect.add_argument("--context", type=Path)
    inspect.add_argument("--memory-pointer", type=Path)
    validate = commands.add_parser("validate", help="Validate inert metadata, not content integrity")
    validate.add_argument("path", type=Path)
    validate.add_argument("--kind", choices=("policy", "project-settings", "global-settings", "context-pointer", "manifest"), required=True)
    try:
        args = parser.parse_args(argv)
        result: JsonObject
        if args.command == "inspect":
            settings = resolve_continuity(args.project_root, args.global_config, args.context, args.scope,
                                          expected_identity=ScopeIdentity(args.scope, args.identity),
                                          memory_pointer=args.memory_pointer)
            result = settings_to_json(settings)
        elif args.kind == "manifest":
            manifest = read_manifest(args.path)
            result = {"valid": True, "kind": "manifest", "completion": manifest.completion,
                      "files": len(manifest.files), "integrity_verified": False}
        else:
            validate_continuity_document(read_json(args.path), kind=args.kind)
            result = {"valid": True, "kind": args.kind}
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except ContinuityError as error:
        print(json.dumps({"error": {"code": error.code, "detail": error.detail}}, ensure_ascii=True), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"error": {"code": "read_failed", "detail": "Continuity inspection could not read its inputs."}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
