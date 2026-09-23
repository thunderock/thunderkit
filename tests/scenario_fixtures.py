"""Build owned test inputs; reported bindings never replace requested choices."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import sys
from typing import Final, assert_never

ROOT: Final = Path(__file__).resolve().parents[1]
REFERENCES: Final = ROOT / "skills/references"
sys.path.insert(0, str(ROOT))
from skills.references.model_config import (ConfigError as ConfigError, JsonObject as JsonObject, JsonValue as JsonValue,
                                            load_json as load_json, normalize_config, selected_models)
from resolution_fixtures import fixture_manifest, make_home, mapping, materialize_peer, sequence, slot_bindings, text, write_json

SAMPLE_SKILL: Final = '''---
name: tk-plan
description: "Use when checking frontmatter contracts for local skills."
metadata:
  thunderkit-role: "planner"
  thunderkit-tier: "workflow"
  thunderkit-delegates: "omo:ulw-plan omh:ultrawork/ulw-plan"
  thunderkit-contract: "1"
---
## Delegation
## Fallback
'''


def object_value(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be an object")
    return value


def text_value(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a nonempty string")
    return value


def items(value: JsonValue, field: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be a list")
    return value


class Fault(StrEnum):
    NONE = "none"
    PEER_MISSING = "peer_missing"
    NO_CONSENTS = "no_consents"
    TAMPER = "tamper"
    SELF_HASHED_TAMPER = "self_hashed_tamper"
    MISSING_COMPANION = "missing_companion"
    MISSING_ROLE = "missing_role"
    BINDING_MISMATCH = "binding_mismatch"
    MIXED_SAME_NAME = "mixed_same_name"


class Home(StrEnum):
    NONE = "none"
    TASK = "task"
    SHARED = "shared"


@dataclass(frozen=True, slots=True)
class CaseInputs:
    skill: str
    operation: str | None
    config: str | None
    capabilities: str | None


@dataclass(frozen=True, slots=True)
class Prepared:
    argv: tuple[str, ...]
    mountinfo: str | None


@dataclass(frozen=True, slots=True)
class Recipe:
    host: str
    peers: tuple[str, ...]
    bindings: str
    binding_host: str
    method: str
    home: Home
    fault: Fault

    @classmethod
    def parse(cls, raw: JsonObject) -> Recipe:
        if raw.keys() - {"host", "peers", "bindings", "binding_host", "method", "home", "fault"}:
            raise ConfigError("unknown capabilities recipe field")
        host = text_value(raw.get("host"), "host")
        peers = tuple(text_value(item, "peers") for item in items(raw.get("peers"), "peers"))
        if len(set(peers)) != len(peers) or set(peers) - {"omo", "omh"}:
            raise ConfigError("peers must contain unique omo/omh identities")
        method = text_value(raw.get("method", "configured"), "method")
        if method not in ("configured", "delegate_route", "explicit_dispatch"):
            raise ConfigError("unknown binding method")
        try:
            home = Home(text_value(raw.get("home", "none"), "home"))
            fault = Fault(text_value(raw.get("fault", "none"), "fault"))
        except ValueError as exc:
            raise ConfigError(f"invalid capabilities recipe: {exc}") from None
        return cls(host, peers, text_value(raw.get("bindings"), "bindings"),
                   text_value(raw.get("binding_host", host), "binding_host"), method, home, fault)


def _mutate(fault: Fault, targets: list[JsonObject], snapshot: JsonObject) -> None:
    peers, bindings = mapping(snapshot["peers"]), mapping(snapshot["model_bindings"])
    match fault:
        case Fault.NONE:
            return
        case Fault.PEER_MISSING:
            snapshot.update(peers={}, ready=True)
        case Fault.NO_CONSENTS:
            snapshot["consents"] = []
        case Fault.MISSING_ROLE | Fault.BINDING_MISMATCH:
            if not bindings:
                raise ConfigError("role mutation requires a model-bearing target")
            slot = next(iter(bindings))
            match fault:
                case Fault.MISSING_ROLE:
                    bindings.pop(slot)
                case Fault.BINDING_MISMATCH:
                    mapping(sequence(mapping(bindings[slot])["members"])[0])["model_id"] = "fixture/wrong-model"
                case unknown_role_fault:
                    assert_never(unknown_role_fault)
        case Fault.TAMPER | Fault.SELF_HASHED_TAMPER | Fault.MISSING_COMPANION:
            if not targets:
                raise ConfigError("file mutation requires an installed target for this host/operation")
            target = targets[0]
            peer = mapping(peers[text(target["ecosystem"])])
            provenance = mapping(target["provenance"])
            entry = text(provenance["entrypoint"])
            path = Path(text(peer["root"])) / entry
            match fault:
                case Fault.MISSING_COMPANION:
                    companions = sorted(set(mapping(provenance["files"])) - {entry})
                    if not companions:
                        raise ConfigError("missing_companion requires a mandatory companion")
                    (Path(text(peer["root"])) / companions[0]).unlink()
                case Fault.TAMPER:
                    path.write_bytes(b"modified fixture bytes\n")
                case Fault.SELF_HASHED_TAMPER:
                    path.write_bytes(b"modified fixture bytes\n")
                    loaded = mapping(mapping(peer["loaded_skills"])[text(target["selector"])])
                    loaded["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                case unknown_file_fault:
                    assert_never(unknown_file_fault)
        case Fault.MIXED_SAME_NAME:
            omo = mapping(object_value(peers.get("omo"), "omo peer")["loaded_skills"])
            omh = mapping(object_value(peers.get("omh"), "omh peer")["loaded_skills"])
            matches = [target for target in targets if target["ecosystem"] == "omh" and target["skill_name"] in omo]
            if not matches:
                raise ConfigError("mixed_same_name requires both source-qualified names")
            target = matches[0]
            omh[text(target["selector"])] = dict(mapping(omo[text(target["skill_name"])]))
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class FixtureLibrary:
    configs: JsonObject
    recipes: JsonObject
    manifest: JsonObject
    catalog: JsonObject

    @classmethod
    def load(cls, directory: Path) -> FixtureLibrary:
        capabilities = load_json(str(directory / "capabilities.json"))
        if type(capabilities.get("schema_version")) is not int or capabilities["schema_version"] != 2:
            raise ConfigError("capabilities fixture schema_version must be 2")
        if set(capabilities) != {"schema_version", "recipes"}:
            raise ConfigError("capabilities fixture requires only schema_version and recipes")
        return cls(load_json(str(directory / "configs.json")), object_value(capabilities.get("recipes"), "recipes"),
                   load_json(str(REFERENCES / "dependencies.json")), load_json(str(REFERENCES / "models.json")))

    def prepare(self, root: Path, request: CaseInputs) -> Prepared:
        def reference(source: JsonObject, key: str | None, field: str) -> JsonObject | None:
            if key is None:
                return None
            if key not in source:
                raise ConfigError(f"unknown {field} fixture {key!r}")
            return object_value(source[key], f"{field} fixture {key!r}")

        config = reference(self.configs, request.config, "config")
        raw_recipe = reference(self.recipes, request.capabilities, "capabilities")
        definition = object_value(mapping(self.manifest["skills"]).get(request.skill), "manifest skill")
        operation = request.operation if request.operation is not None else text(definition["default_operation"])
        targets = [mapping(item) for item in sequence(definition["targets"]) if operation in sequence(mapping(item)["operations"])]
        hashes: dict[tuple[str, str], dict[str, str]] = {}
        peers: JsonObject = {}
        trusted = self.manifest
        snapshot: JsonObject | None = None
        mountinfo = None
        if raw_recipe is not None:
            recipe = Recipe.parse(raw_recipe)
            pins = mapping(self.manifest["ecosystems"])
            for ecosystem in recipe.peers:
                matching = [target for target in targets if target["ecosystem"] == ecosystem]
                if not matching:
                    continue
                pin = mapping(pins[ecosystem])
                peer_root = root / "peers" / ecosystem
                digests = materialize_peer(peer_root, pin, matching)
                hashes.update({(ecosystem, key): value for key, value in digests.items()})
                loaded: JsonObject = {}
                for target in matching:
                    selector, entry = text(target["selector"]), text(mapping(target["provenance"])["entrypoint"])
                    loaded[selector] = {"path": str(peer_root / entry), "sha256": digests[selector][entry]}
                peers[ecosystem] = {**{key: pin[key] for key in ("package", "version", "source")},
                                    "root": str(peer_root), "loaded_skills": loaded}
            active = [target for target in targets if target["ecosystem"] in peers
                      and recipe.host in sequence(mapping(pins[text(target["ecosystem"])])["hosts"])]
            slots: JsonObject = {}
            for target in active:
                slots.update(mapping(target.get("native_roles", {text(req).partition(":")[2]: text(req).partition(":")[2]
                    for req in sequence(target["requires"]) if text(req).startswith("model-binding:")})))
            reported = object_value(reference(self.configs, recipe.bindings, "bindings"), "binding profile")
            normalized, _ = normalize_config(reported, self.catalog)
            selection: JsonObject = {}
            for key, value in selected_models(normalized, self.catalog).items():
                match value:
                    case str():
                        selection[key] = value
                    case list():
                        selection[key] = [item for item in value]
                    case unreachable:
                        assert_never(unreachable)
            try:
                bindings = slot_bindings(self.catalog, recipe.binding_host, selection, slots, recipe.method)
            except AssertionError:
                raise ConfigError("reported binding profile has no exact catalog mapping for binding_host") from None
            home: JsonObject | None = None
            match recipe.home:
                case Home.NONE:
                    pass
                case Home.TASK | Home.SHARED:
                    path = make_home(root, "scenario")
                    match recipe.home:
                        case Home.TASK:
                            pass
                        case Home.SHARED:
                            shared = root / "shared-hermes-home"
                            shared.mkdir()
                            path = str(shared)
                        case unreachable:
                            assert_never(unreachable)
                    home = {key: path for key in ("path", "parent_home", "dispatcher_home")}
                    mountinfo = "1 0 1:1 / / rw - ext4 /dev/fixture rw\n"
                case unreachable:
                    assert_never(unreachable)
            snapshot = {"schema_version": 1, "host": recipe.host, "peers": peers, "model_bindings": bindings,
                        "tools": ["skill", "delegate_task", "omh_delegate_route"], "consents": ["dispatch", "delivery:disabled", "lookup"],
                        "runtime_home": home}
            trusted = fixture_manifest(self.manifest, hashes)
            _mutate(recipe.fault, active, snapshot)
        argv = ["--skill", request.skill, "--project-root", str(root), "--catalog", str(REFERENCES / "models.json")]
        if request.operation is not None:
            argv.extend(["--operation", request.operation])
        for name, document in (("manifest", trusted), ("config", config), ("capabilities", snapshot)):
            if document is not None:
                input_path = root / ("dependencies.json" if name == "manifest" else f"{name}.json")
                write_json(input_path, document)
                argv.extend([f"--{name}", str(input_path)])
        return Prepared(tuple(argv), mountinfo)
