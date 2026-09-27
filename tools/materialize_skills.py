#!/usr/bin/env python3
"""Copy canonical support files into self-contained skill directories."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Final, Literal, NamedTuple, TypeAlias

MANIFEST: Final = (
    ("skills/references/models.json", "references/models.json"),
    ("skills/references/dependencies.json", "references/dependencies.json"),
    ("skills/references/model-roster.md", "references/model-roster.md"),
    ("skills/references/delegation.md", "references/delegation.md"),
    ("skills/references/config.schema.json", "references/config.schema.json"),
    ("skills/references/model_config.py", "scripts/model_config.py"),
    ("skills/references/capability_gates.py", "scripts/capability_gates.py"),
    ("skills/references/tk-resolve.py", "scripts/tk-resolve.py"),
    ("skills/references/peer_lock.py", "scripts/peer_lock.py"),
)
MANAGED_FILES: Final = tuple(destination for _, destination in MANIFEST)
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class PayloadError(ValueError):
    path: Path
    detail: str

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


class PendingCopy(NamedTuple):
    destination: Path
    content: bytes
    reason: Literal["missing", "stale"]


class Options(argparse.Namespace):
    root: Path
    check: bool


def _lstat(path: Path) -> os.stat_result | None:
    """Inspect ancestors before the leaf so links cannot disappear during normalization."""
    result = None
    for component in (*reversed(path.parents), path):
        try:
            result = component.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(result.st_mode):
            raise PayloadError(component, "symlink refused")
        if component != path and not stat.S_ISDIR(result.st_mode):
            raise PayloadError(component, "not a directory")
    return result


def _read_regular(path: Path) -> bytes | None:
    metadata = _lstat(path)
    if metadata is None:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise PayloadError(path, "not a regular file")
    return path.read_bytes()


def _json_object(path: Path, content: bytes) -> JsonObject:
    def unique(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise PayloadError(path, "duplicate JSON key")
            result[key] = value
        return result

    try:
        value: JsonValue = json.loads(content, object_pairs_hook=unique)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PayloadError(path, "invalid JSON source") from error
    if not isinstance(value, dict):
        raise PayloadError(path, "JSON source must be an object")
    return value


def _read_source(path: Path) -> bytes:
    content = _read_regular(path)
    if content is None:
        raise PayloadError(path, "missing canonical source")
    try:
        text = content.decode("utf-8")
        if not text.strip():
            raise PayloadError(path, "empty canonical source")
        if path.name.endswith(".json"):
            _json_object(path, content)
        if path.name.endswith(".py"):
            compile(text, str(path), "exec")
    except (UnicodeDecodeError, SyntaxError) as error:
        raise PayloadError(path, "corrupt canonical source") from error
    return content


def discover_skills(root: Path, registry: bytes | None = None) -> list[Path]:
    path = root / "skills/references/dependencies.json"
    document = _json_object(path, _read_source(path) if registry is None else registry)
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 2:
        raise PayloadError(path, "registry schema_version must be 2")
    entries = document.get("skills")
    if not isinstance(entries, dict) or not entries:
        raise PayloadError(path, "registry skills must be a nonempty object")
    for name, entry in entries.items():
        if re.fullmatch(r"tk-[a-z0-9]+(?:-[a-z0-9]+)*", name) is None or not isinstance(entry, dict):
            raise PayloadError(path, "registry requires portable tk-* names with object entries")
    skills = root / "skills"
    metadata = _lstat(skills)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise PayloadError(skills, "missing skills directory")
    found = []
    for child in sorted(skills.iterdir()):
        if not child.name.startswith("tk-"):
            continue
        child_metadata = _lstat(child)
        if child_metadata is None or not stat.S_ISDIR(child_metadata.st_mode):
            if child.name in entries:
                raise PayloadError(child, "registered skill is not a directory")
            continue
        marker = _lstat(child / "SKILL.md")
        if marker is None:
            continue
        if not stat.S_ISREG(marker.st_mode):
            raise PayloadError(child / "SKILL.md", "skill marker is not a regular file")
        if child.name not in entries:
            raise PayloadError(child / "SKILL.md", "unregistered marked skill")
        found.append(child)
    missing = set(entries) - {skill.name for skill in found}
    if missing:
        raise PayloadError(skills / sorted(missing)[0] / "SKILL.md", "missing registered skill marker")
    return found


def materialize(root: Path, check: bool = False) -> int:
    """Preflight all managed paths, then report drift or write only changed copies."""
    directory = root.absolute()
    sources: dict[str, bytes] = {}
    for source, relative in MANIFEST:
        if relative not in MANAGED_FILES or source != f"skills/references/{Path(relative).name}":
            raise PayloadError(directory / source, "source or destination outside the managed mapping")
        sources[relative] = _read_source(directory / source)
    if set(sources) != set(MANAGED_FILES) or len(MANIFEST) != len(MANAGED_FILES):
        raise PayloadError(directory, "managed mapping must contain each asset exactly once")
    skills = discover_skills(directory, sources["references/dependencies.json"])

    pending = []
    for skill in skills:
        for relative, content in sources.items():
            destination = skill / relative
            if ".." in Path(relative).parts or not destination.is_relative_to(skill):
                raise PayloadError(destination, "destination outside skill directory")
            current = _read_regular(destination)
            if current != content:
                pending.append(PendingCopy(destination, content, "missing" if current is None else "stale"))

    total = len(skills) * len(MANAGED_FILES)
    if check:
        for copy in pending:
            print(f"{copy.reason}: {copy.destination.relative_to(directory)}")
        print(f"Checked {len(skills)} skills: {total - len(pending)}/{total} managed files current")
        return int(bool(pending))

    for copy in pending:
        _lstat(copy.destination)
        copy.destination.parent.mkdir(parents=True, exist_ok=True)
        _lstat(copy.destination)
        copy.destination.write_bytes(copy.content)
    print(f"Materialized {len(skills)} skills: {len(pending)} written, {total - len(pending)} unchanged")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check", action="store_true", help="report missing or stale managed files without writing")
    parser.add_argument("--root", type=Path, default=Path(__file__).absolute().parents[1],
                        help="repository root (default: the directory containing this tool's parent directory)")
    options = parser.parse_args(argv, namespace=Options())
    try:
        return materialize(options.root, options.check)
    except (PayloadError, OSError) as error:
        print(f"materialize_skills: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
