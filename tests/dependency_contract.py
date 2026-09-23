"""Test-only validation of the native-peer contract."""

import json
import re
from typing import TypedDict

from dependency_expectations import (
    COMPANIONS, ENTRYPOINT_SHA256, OMH_CANONICAL, OMH_RAIL, OMH_RAIL_SHA256,
    OPERATIONS, PINS, ROOT_KINDS, SHA256, TARGETS,
)


class Peer(TypedDict):
    package: str
    version: str
    registry: str
    install_hint: str


class _ProvenanceCore(TypedDict):
    root_kind: str
    entrypoint: str
    files: dict[str, str]


class Provenance(_ProvenanceCore, total=False):
    canonical_name: str


class Target(TypedDict):
    ecosystem: str
    skill_name: str
    selector: str
    mode: str
    operations: list[str]
    requires: list[str]
    notes: str
    provenance: Provenance


class SkillDependency(TypedDict):
    role: str
    default_operation: str
    operations: list[str]
    targets: list[Target]
    fallback: str


class DistributionCLI(TypedDict):
    package: str
    version: str
    node: str
    note: str


class Manifest(TypedDict):
    schema_version: int
    ecosystems: dict[str, Peer]
    distribution_cli: DistributionCLI
    skills: dict[str, SkillDependency]
    excluded: list[str]
    excluded_note: str


def _root_relative(path: str) -> None:
    parts = path.split("/")
    assert path and not path.startswith("/") and "\\" not in path and ":" not in path, "escaping provenance path"
    assert all(part and part not in {".", ".."} for part in parts), "escaping provenance path"
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in path), "escaping provenance path"


def validate_provenance(ecosystem: str, selector: str, provenance: Provenance) -> None:
    """Raise AssertionError unless the target carries trustworthy deployed fingerprints."""
    expected_keys = {"root_kind", "entrypoint", "files"} | ({"canonical_name"} if ecosystem == "omh" else set())
    assert set(provenance) == expected_keys, "provenance shape"
    assert provenance["root_kind"] == ROOT_KINDS[ecosystem], "provenance root_kind mismatch"
    files = provenance["files"]
    assert isinstance(files, dict) and files, "provenance files missing"
    for path, digest in files.items():
        _root_relative(path)
        assert isinstance(digest, str) and SHA256.fullmatch(digest), "malformed provenance fingerprint"
    entrypoint = provenance["entrypoint"]
    skill_dir = f"dist/skills/{selector}" if ecosystem == "omo" else f"skills/{selector}"
    assert entrypoint == f"{skill_dir}/SKILL.md", "provenance entrypoint location"
    assert entrypoint in files, "provenance entrypoint fingerprint missing"
    assert files[entrypoint] == ENTRYPOINT_SHA256[(ecosystem, selector)], "provenance entrypoint fingerprint mismatch"
    companions = {path for path in files if path != entrypoint}
    if ecosystem == "omh":
        assert OMH_RAIL in files, "omh shared rail missing"
        assert files[OMH_RAIL] == OMH_RAIL_SHA256, "omh shared rail fingerprint mismatch"
        companions.discard(OMH_RAIL)
        assert provenance.get("canonical_name") == OMH_CANONICAL[selector], "omh canonical name mismatch"
    assert all(path.startswith(f"{skill_dir}/") for path in companions), "companion outside skill root"
    relative = {path[len(skill_dir) + 1:] for path in companions}
    assert relative == COMPANIONS[(ecosystem, selector)], "frozen companion set mismatch"


def validate_manifest(doc: Manifest, skill_dirs: set[str]) -> None:
    """Raise AssertionError when a declared peer or operation violates the contract."""
    assert type(doc["schema_version"]) is int and doc["schema_version"] == 1
    assert set(doc["ecosystems"]) == set(PINS), "ecosystems must be exactly omo and omh"
    assert set(doc["skills"]) == skill_dirs == set(OPERATIONS), "skill inventory mismatch"
    assert len(skill_dirs) == 19
    assert doc["excluded"] == ["gsd", "omc"]
    cli = doc["distribution_cli"]
    assert (cli["package"], cli["version"], cli["node"]) == ("skills", "1.7.0", ">=22.20.0")
    restricted = []
    for ecosystem, (package, version) in PINS.items():
        peer = doc["ecosystems"][ecosystem]
        assert (peer["package"], peer["version"]) == (package, version), "peer pin mismatch"
        assert peer["registry"] == f"https://registry.npmjs.org/{package}/{version}"
        restricted.append(peer["install_hint"])
    for name, skill in doc["skills"].items():
        operations = skill["operations"]
        assert isinstance(operations, list) and operations
        assert len(operations) == len(set(operations)), "duplicate operation"
        assert skill["default_operation"] in operations, "invalid default operation"
        assert (skill["default_operation"], tuple(operations)) == OPERATIONS[name]
        assert skill["role"] and skill["fallback"].strip()
        restricted.append(skill["fallback"])
        assert isinstance(skill["targets"], list)
        seen = set()
        actual = set()
        for target in skill["targets"]:
            assert set(target) == Target.__required_keys__, "unqualified target"
            ecosystem, selector = target["ecosystem"], target["selector"]
            assert ecosystem in PINS, "ineligible target ecosystem"
            assert (ecosystem == "omh") == ("/" in selector), "selector ecosystem mismatch"
            assert re.fullmatch(r"[a-z0-9-]+(?:/[a-z0-9-]+)?", selector)
            assert target["skill_name"] == selector.rsplit("/", 1)[-1]
            assert target["mode"] in {"handoff", "component"}, "invalid target mode"
            target_ops = target["operations"]
            assert isinstance(target_ops, list) and target_ops
            assert set(target_ops) <= set(operations), "target operation mismatch"
            assert len(target_ops) == len(set(target_ops)), "duplicate target operation"
            assert (ecosystem, selector) not in seen, "duplicate qualified target"
            seen.add((ecosystem, selector))
            assert isinstance(target["requires"], list)
            assert all(re.fullmatch(r"[a-z][a-z_-]*:[a-z][a-z_-]*", cap) for cap in target["requires"])
            assert target["notes"].strip()
            validate_provenance(ecosystem, selector, target["provenance"])
            actual.add((ecosystem, selector, target["mode"], tuple(target_ops)))
            restricted.append(json.dumps(target))
        assert actual == TARGETS.get(name, set()), f"{name}: frozen target map mismatch"
    assert not re.search(r"gsd|omc", "\n".join(restricted), re.IGNORECASE), "excluded reference"


def validate_role(text: str, role: str) -> None:
    roles = re.findall(r"(?m)^metadata:\n  thunderkit:\n    role: (\S+)$", text.split("---", 2)[1])
    assert roles == [role], "skill role mismatch"
