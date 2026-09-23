"""Test-only validation of the native-peer contract."""

import json
import re
import sys
from pathlib import Path
from typing import TypeAlias

from dependency_expectations import (
    COMPANIONS, ENTRYPOINT_SHA256, NATIVE_ROLES, OMH_CANONICAL, OPERATIONS, PEER_ROOTS, PINS,
    ROOT_KINDS, SHA256, SHARED_FILES, SINGLE_CLASS, SKILL_PREFIXES, TARGET_KEYS, TARGETS,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.skill_frontmatter import parse_skill_md

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def json_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict), "expected JSON object"
    return value


def json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list), "expected JSON array"
    return value


def json_string(value: JsonValue) -> str:
    assert isinstance(value, str), "expected JSON string"
    return value


def _strings(value: JsonValue) -> tuple[str, ...]:
    return tuple(json_string(item) for item in json_array(value))


def _root_relative(path: str) -> None:
    parts = path.split("/")
    assert path and not path.startswith("/") and "\\" not in path and ":" not in path, "escaping provenance path"
    assert all(part and part not in {".", ".."} for part in parts), "escaping provenance path"
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in path), "escaping provenance path"


def validate_provenance(ecosystem: str, selector: str, raw: JsonValue) -> None:
    """Raise AssertionError unless the target carries trustworthy deployed fingerprints."""
    provenance = json_object(raw)
    assert set(provenance) == {"root_kind", "entrypoint", "files"}, "provenance shape"
    assert provenance["root_kind"] == ROOT_KINDS[ecosystem], "provenance root_kind mismatch"
    files = json_object(provenance["files"])
    assert files, "provenance files missing"
    for path, digest in files.items():
        _root_relative(path)
        assert isinstance(digest, str) and SHA256.fullmatch(digest), "malformed provenance fingerprint"
    entrypoint = f"{SKILL_PREFIXES[ecosystem]}/{selector}/SKILL.md"
    assert provenance["entrypoint"] == entrypoint, "provenance entrypoint location"
    assert entrypoint in files, "provenance entrypoint fingerprint missing"
    assert files[entrypoint] == ENTRYPOINT_SHA256[(ecosystem, selector)], "provenance entrypoint fingerprint mismatch"
    companions = set(files) - {entrypoint}
    for path, digest in SHARED_FILES.get(ecosystem, {}).items():
        assert path in files, "omh shared rail missing"
        assert files[path] == digest, "omh shared rail fingerprint mismatch"
        companions.discard(path)
    assert companions == COMPANIONS[(ecosystem, selector)], "frozen companion set mismatch"


def validate_manifest(raw: JsonValue, skill_dirs: set[str]) -> None:
    """Raise AssertionError when a declared peer or operation violates the contract."""
    doc = json_object(raw)
    version = doc.get("schema_version")
    assert type(version) is int and version == 1, "manifest schema version"
    peers = json_object(doc.get("ecosystems"))
    skills = json_object(doc.get("skills"))
    assert set(peers) == set(PINS), "ecosystems must be exactly omo and omh"
    assert set(skills) == skill_dirs == set(OPERATIONS), "skill inventory mismatch"
    assert len(skill_dirs) == 19
    assert doc.get("excluded") == ["gsd", "omc"]
    cli = json_object(doc.get("distribution_cli"))
    assert (cli.get("package"), cli.get("version"), cli.get("node")) == ("skills", "1.7.0", ">=22.20.0")
    restricted = []
    for ecosystem, (package, pinned_version) in PINS.items():
        peer = json_object(peers[ecosystem])
        assert (peer.get("package"), peer.get("version")) == (package, pinned_version), "peer pin mismatch"
        assert peer.get("registry") == f"https://registry.npmjs.org/{package}/{pinned_version}"
        root = json_object(peer.get("provenance_root"))
        assert tuple(root.get(key) for key in ("root_kind", "identity_file", "entrypoint_pattern")) == PEER_ROOTS[ecosystem], "peer provenance root mismatch"
        restricted.append(json_string(peer.get("install_hint")))
    for name, raw_skill in skills.items():
        skill = json_object(raw_skill)
        operations = _strings(skill.get("operations"))
        assert operations and len(operations) == len(set(operations)), "duplicate operation"
        assert skill.get("default_operation") in operations, "invalid default operation"
        assert (skill.get("default_operation"), operations) == OPERATIONS[name]
        assert json_string(skill.get("role")) and json_string(skill.get("fallback")).strip()
        restricted.append(json_string(skill["fallback"]))
        seen = set()
        actual = set()
        for raw_target in json_array(skill.get("targets")):
            target = json_object(raw_target)
            assert TARGET_KEYS <= target.keys(), "unqualified target"
            ecosystem, selector = json_string(target["ecosystem"]), json_string(target["selector"])
            assert ecosystem in PINS, "ineligible target ecosystem"
            assert (ecosystem == "omh") == ("/" in selector), "selector ecosystem mismatch"
            assert re.fullmatch(r"[a-z0-9-]+(?:/[a-z0-9-]+)?", selector)
            assert target["skill_name"] == selector.rsplit("/", 1)[-1]
            mode = json_string(target["mode"])
            assert mode in {"handoff", "component"}, "invalid target mode"
            target_ops = _strings(target["operations"])
            assert target_ops and set(target_ops) <= set(operations), "target operation mismatch"
            assert len(target_ops) == len(set(target_ops)), "duplicate target operation"
            key = (ecosystem, selector)
            assert key not in seen, "duplicate qualified target"
            seen.add(key)
            assert key in ENTRYPOINT_SHA256, "unqualified target"
            canonical = OMH_CANONICAL.get(selector)
            assert target.get("canonical_name") == canonical, "omh canonical name mismatch"
            roles = NATIVE_ROLES.get(key)
            assert target.get("native_roles") == roles, "native role map mismatch"
            expected_keys = TARGET_KEYS | ({"canonical_name"} if canonical is not None else set())
            expected_keys |= {"native_roles"} if roles is not None else set()
            assert set(target) == expected_keys, "unqualified target"
            requires = _strings(target["requires"])
            assert len(requires) == len(set(requires)), "duplicate capability"
            assert all(re.fullmatch(r"[a-z][a-z_-]*:[a-z][a-z_-]*", cap) for cap in requires)
            assert "tool:skill" in requires, "skill tool missing"
            bindings = {cap.removeprefix("model-binding:") for cap in requires if cap.startswith("model-binding:")}
            expected = set(roles.values()) if roles else {SINGLE_CLASS[name]} if name in SINGLE_CLASS else set()
            assert bindings == expected, "model binding class mismatch"
            assert json_string(target["notes"]).strip()
            validate_provenance(ecosystem, selector, target["provenance"])
            actual.add((ecosystem, selector, mode, target_ops))
            restricted.append(json.dumps(target))
        assert actual == TARGETS.get(name, set()), f"{name}: frozen target map mismatch"
    assert not re.search(r"gsd|omc", "\n".join(restricted), re.IGNORECASE), "excluded reference"


def validate_role(text: str, role: str) -> None:
    header = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert header is not None, "skill header missing"
    legacy = re.findall(r"(?m)^metadata:\n  thunderkit:\n    role: (\S+)$", header[1])
    flat = re.search(r"(?m)^  thunderkit-role:", header[1])
    roles = legacy if legacy and flat is None else [parse_skill_md(text).metadata.get("thunderkit-role")]
    assert roles == [role], "skill role mismatch"
