"""Strict reader for the machine-local peer lock (.thunderkit/peers.lock.json).

The lock records the peer version resolved at install time and SHA-256 digests of
the INSTALLED files (trust on first lock). It is never packed or committed.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from model_config import ConfigError, JsonObject, JsonValue, load_json

LOCK_KEYS: Final = frozenset({"schema_version", "host", "peer", "package", "channel", "version",
                              "registry_integrity", "locked_at", "root", "files"})
TEXT_KEYS: Final = ("host", "peer", "package", "channel", "version", "locked_at")
HEX: Final = frozenset("0123456789abcdef")


def _text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"lock.{field} must be a nonempty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(f"lock.{field} must not contain control characters")
    return value


def _relative(path: str) -> str:
    parts = path.split("/")
    if (not path or path.startswith("/") or "\\" in path or ":" in path
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise ConfigError("lock.files keys must be portable root-relative paths")
    return path


def is_digest(value: JsonValue) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def validate_lock(raw: JsonValue) -> JsonObject:
    """Return the lock unchanged when every field is well formed; raise ConfigError otherwise."""
    if not isinstance(raw, dict):
        raise ConfigError("lock must be a JSON object")
    if set(raw) != LOCK_KEYS:
        raise ConfigError("lock must contain exactly the schema 1 fields")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ConfigError("lock.schema_version must be 1")
    for field in TEXT_KEYS:
        _text(raw[field], field)
    integrity = raw["registry_integrity"]
    if not isinstance(integrity, str) or (integrity and not integrity.startswith("sha512-")):
        raise ConfigError("lock.registry_integrity must be empty or an sha512 SRI string")
    root = _text(raw["root"], "root")
    if not PurePosixPath(root).is_absolute():
        raise ConfigError("lock.root must be an absolute path")
    files = raw["files"]
    if not isinstance(files, dict) or not files:
        raise ConfigError("lock.files must be a nonempty object")
    for path, fingerprint in files.items():
        _relative(path)
        if not is_digest(fingerprint):
            raise ConfigError("lock.files values must be lowercase SHA-256 digests")
    return raw


def load_lock(path: str) -> JsonObject:
    """Read and validate a lock file without writing anything."""
    return validate_lock(load_json(path))


def matches_peer(lock: JsonObject, host: str, peer: str, package: str) -> bool:
    return (lock.get("host"), lock.get("peer"), lock.get("package")) == (host, peer, package)
