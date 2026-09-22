"""The deliberately small frontmatter dialect used by Thunderkit skills."""

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
import re
from typing import Final

_TOP_LEVEL_KEYS: Final = frozenset({
    "name", "description", "license", "compatibility", "allowed-tools", "metadata",
})
_METADATA_KEYS: Final = frozenset({
    "thunderkit-role", "thunderkit-tier", "thunderkit-delegates", "thunderkit-contract",
})
_ENTRY: Final = re.compile(r"([a-zA-Z0-9_][a-zA-Z0-9_.-]*):(?:[ \t]+(.*))?")
_CLOSING: Final = re.compile(r"^---\n", re.MULTILINE)
_NAME: Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_DELEGATE: Final = re.compile(r"(omo|omh):[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?")


class FrontmatterError(ValueError):
    path: str
    line: int
    detail: str

    def __init__(self, path: str, line: int, detail: str) -> None:
        self.path = path
        self.line = line
        self.detail = detail
        super().__init__(f"{path}:{line}: {detail}")


@dataclass(frozen=True, slots=True)
class Frontmatter:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    allowed_tools: str | None
    metadata: dict[str, str]
    body: str


def _scalar(value: str, path: str, line: int) -> str:
    if value.startswith("'"):
        if re.fullmatch(r"'[^']*'", value) is None:
            raise FrontmatterError(path, line, "invalid single-quoted string")
        return value[1:-1]
    if value.startswith('"'):
        if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value) is None:
            raise FrontmatterError(path, line, "invalid double-quoted string")
        return re.sub(r'\\(["\\])', r'\1', value[1:-1])
    if value.startswith(("[", "{", "|", ">", "&", "*", "!", "- ")):
        raise FrontmatterError(path, line, "unsupported YAML construct; expected a scalar string")
    return value


def parse_skill_md(text: str, path: str = "<memory>") -> Frontmatter:
    if not text.startswith("---\n"):
        raise FrontmatterError(path, 1, "frontmatter must start with '---' followed by LF")
    closing = _CLOSING.search(text, 4)
    if closing is None:
        raise FrontmatterError(path, text.count("\n") + 1, "unterminated frontmatter; expected '---' followed by LF")

    scalars: dict[str, str] = {}
    metadata: dict[str, str] = {}
    key_lines: dict[str, int] = {}
    in_metadata = False
    for number, line in enumerate(text[4:closing.start()].split("\n")[:-1], 2):
        if line.startswith((" ", "\t")):
            if not in_metadata:
                raise FrontmatterError(path, number, "nested mappings are allowed only under metadata")
            if not line.startswith("  ") or line[2:3].isspace():
                raise FrontmatterError(path, number, "metadata entries must be indented exactly two spaces")
            entry = _ENTRY.fullmatch(line[2:])
            if entry is None:
                raise FrontmatterError(path, number, "expected a metadata key and quoted value; lists are unsupported")
            key, raw = entry[1], (entry[2] or "").strip()
            if key in metadata:
                raise FrontmatterError(path, number, f"duplicate metadata key: {key}")
            if not raw.startswith(('"', "'")):
                raise FrontmatterError(path, number, f"metadata {key} must be a quoted string, not a bare value or nested mapping")
            metadata[key] = _scalar(raw, path, number)
            continue

        if in_metadata and not metadata:
            raise FrontmatterError(path, key_lines["metadata"], "metadata must contain at least one quoted entry")
        in_metadata = False
        entry = _ENTRY.fullmatch(line)
        if entry is None:
            raise FrontmatterError(path, number, "expected a top-level key: value")
        key, raw = entry[1], (entry[2] or "").strip()
        if key not in _TOP_LEVEL_KEYS:
            raise FrontmatterError(path, number, f"unknown top-level key: {key}")
        if key in key_lines:
            raise FrontmatterError(path, number, f"duplicate top-level key: {key}")
        key_lines[key] = number
        if key == "metadata":
            if raw:
                raise FrontmatterError(path, number, "metadata must be an indented block")
            in_metadata = True
        else:
            if not raw:
                raise FrontmatterError(path, number, f"{key} must have a scalar string value")
            scalars[key] = _scalar(raw, path, number)

    if in_metadata and not metadata:
        raise FrontmatterError(path, key_lines["metadata"], "metadata must contain at least one quoted entry")
    name = scalars.get("name", "")
    if len(name) > 64 or _NAME.fullmatch(name) is None:
        raise FrontmatterError(path, key_lines.get("name", 1), "name must be a nonempty lowercase slug of at most 64 characters")
    description = scalars.get("description", "")
    if not description.strip() or len(description) > 1024:
        raise FrontmatterError(path, key_lines.get("description", 1), "description must be nonempty and at most 1024 characters")
    return Frontmatter(
        name=name,
        description=description,
        license=scalars.get("license"),
        compatibility=scalars.get("compatibility"),
        allowed_tools=scalars.get("allowed-tools"),
        metadata=metadata,
        body=text[closing.end():],
    )


def parse_skill_file(path: str | PathLike[str]) -> Frontmatter:
    with Path(path).open(encoding="utf-8", newline="") as source:
        return parse_skill_md(source.read(), str(path))


def validate_thunderkit(fm: Frontmatter, dir_name: str) -> None:
    """Apply repository policy; errors identify the directory's document at line 1."""
    path = f"{dir_name}/SKILL.md"
    if fm.name != dir_name:
        raise FrontmatterError(path, 1, f"name {fm.name!r} must match directory {dir_name!r}")
    if re.match(r"^Use \w+", fm.description) is None:
        raise FrontmatterError(path, 1, "description must start with 'Use <word>'")
    if not 40 <= len(fm.description) <= 500:
        raise FrontmatterError(path, 1, "description must contain 40 to 500 characters")
    if fm.compatibility is not None and len(fm.compatibility) > 500:
        raise FrontmatterError(path, 1, "compatibility must contain at most 500 characters")
    missing = _METADATA_KEYS - fm.metadata.keys()
    if missing:
        raise FrontmatterError(path, 1, f"missing metadata keys: {', '.join(sorted(missing))}")
    unknown = fm.metadata.keys() - _METADATA_KEYS
    if unknown:
        raise FrontmatterError(path, 1, f"unknown metadata keys: {', '.join(sorted(unknown))}")
    if fm.metadata["thunderkit-contract"] != "1":
        raise FrontmatterError(path, 1, 'thunderkit-contract must equal "1"')
    delegates = fm.metadata["thunderkit-delegates"]
    if delegates != "none":
        for token in delegates.split(" "):
            if _DELEGATE.fullmatch(token) is None:
                raise FrontmatterError(path, 1, f"invalid thunderkit-delegates token: {token!r}")


def is_legacy_nested_metadata(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    closing = _CLOSING.search(text, 4)
    header = text[4:closing.start()] if closing is not None else text[4:]
    in_metadata = False
    for line in header.split("\n"):
        if line.startswith((" ", "\t")):
            entry = _ENTRY.fullmatch(line[2:]) if line.startswith("  ") else None
            if in_metadata and entry is not None and not (entry[2] or "").strip():
                return True
        else:
            in_metadata = line.rstrip(" \t") == "metadata:"
    return False
