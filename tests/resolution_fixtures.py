"""Small owned peer installations with independently supplied trust manifests."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Final, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
ROOT: Final = Path(__file__).resolve().parents[1]
REFERENCES: Final = ROOT / "skills" / "references"
SCRIPT: Final = REFERENCES / "tk-resolve.py"
SCRATCH: Final = Path(os.environ.get("THUNDERKIT_TEST_TMPDIR", str(
    ROOT.parent.parent / ".omo/evidence/thunderkit-skill-deps-review")))
KEYS: Final = {"schema_version", "skill", "operation", "decision", "reason_code", "detail",
               "target", "bindings", "runtime_home", "evidence_paths"}
CONFIGLESS_ROWS: Final = (("tk-ask", "validate"), ("tk-memory", "view"),
                         ("tk-router", "bootstrap"), ("tk-handoff", "save"))
EXPECTED_HOST_IDENTITIES: Final = (
    ("opencode", "opus5", "amazon-bedrock", "us.anthropic.claude-opus-5"),
    ("opencode", "fable51", "amazon-bedrock", "us.anthropic.claude-fable-5-1"),
    ("hermes", "opus5", "bedrock", "us.anthropic.claude-opus-5"),
    ("hermes", "fable51", "bedrock", "us.anthropic.claude-fable-5-1"),
    ("hermes", "opus48", "anthropic", "claude-opus-4-8"),
    ("codex", "sol", "openai-codex", "gpt-5.6-sol"),
)
PROVENANCE_ROWS: Final = (
    ("package", "foreign", "source_mismatch"), ("source", "foreign", "source_mismatch"),
    ("version", "0.0.0", "version_mismatch"), ("root", "relative", "source_mismatch"),
    ("source_commit", "foreign", "source_mismatch"),
)
ROLE_ROWS: Final[tuple[tuple[str, JsonValue, str], ...]] = (
    ("descriptor", "", "missing_evidence"), ("members", [], "missing_evidence"),
    ("method", "delegate_route", "capability_missing"),
    ("method", "explicit_dispatch", "capability_missing"),
)
HOME_ROWS: Final = ("missing", "booleans", "mismatch", "relative", "outside", "substring",
                    "traversal", "double-slash", "contained-link", "escaping-link", "prefix-link", "file")
SHAPE_ROWS: Final[tuple[tuple[str, JsonValue], ...]] = (
    ("schema_version", True), ("host", []), ("peers", []), ("tools", "skill"),
    ("consents", True), ("model_bindings", []),
)
BAD_PATHS: Final = ("", "/absolute", "C:/drive", "a:b", "a/b:c", "a\\b", "../a",
                   "a/../b", "a//b", "./a", "a/", "a/./b", "a\x00b", "a\x1fb", "a\x7fb")


def mapping(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def sequence(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def text(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def read_json(path: Path) -> JsonObject:
    value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    return mapping(value)


def write_json(path: Path, value: JsonObject) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def materialize_peer(root: Path, pin: JsonObject, targets: Sequence[JsonObject]) -> dict[str, dict[str, str]]:
    hashes: dict[str, dict[str, str]] = {}
    records: dict[str, JsonValue] = {}
    root.mkdir(parents=True, exist_ok=True)
    for target in targets:
        selector = text(target["selector"])
        provenance = mapping(target["provenance"])
        digests: dict[str, str] = {}
        for relative in mapping(provenance["files"]):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"thunderkit fixture {relative}\n".encode())
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[selector] = digests
        entrypoint = text(provenance["entrypoint"])
        records[selector] = {"name": target.get("canonical_name", selector),
                             "path": entrypoint.removeprefix("skills/"),
                             "sha256": digests[entrypoint], "source": "builtin"}
    identity = mapping(pin["provenance_root"])
    document = deepcopy(mapping(identity["identity_fields"]))
    if identity["root_kind"] == "omh":
        document.update(skills_dir=str(root / "skills"), source="builtin", skills=list(records.values()))
    write_json(root / text(identity["identity_file"]), document)
    return hashes


def fixture_manifest(base: JsonObject, hashes: dict[tuple[str, str], dict[str, str]]) -> JsonObject:
    result = deepcopy(base)
    for value in mapping(result["skills"]).values():
        for raw in sequence(mapping(value)["targets"]):
            target = mapping(raw)
            key = (text(target["ecosystem"]), text(target["selector"]))
            if key in hashes:
                mapping(target["provenance"])["files"] = dict(hashes[key])
    return result


def slot_bindings(catalog: JsonObject, host: str, selection: JsonObject,
                  slots: JsonObject, method: str = "configured") -> JsonObject:
    """Keep independent host, selection and native-slot inputs for shared fixtures."""
    bindings: JsonObject = {}
    models = mapping(catalog["models"])
    for slot, raw_class in slots.items():
        cls = text(raw_class)
        chosen = selection[cls]
        keys = [chosen] if isinstance(chosen, str) else sequence(chosen)
        members: list[JsonValue] = []
        for raw_key in keys:
            key = text(raw_key)
            host_maps = [mapping(value) for value in sequence(mapping(models[key])["harnesses"])
                         if mapping(value)["harness"] == host]
            if cls == "reviewers" and selection.get("reviewers_mode") == "all" and not host_maps:
                continue
            assert len(host_maps) == 1, (host, key)
            members.append({"catalog_key": key, "provider": host_maps[0]["provider"],
                            "model_id": host_maps[0]["model_id"]})
        bindings[slot] = {"descriptor": f"fixture:{slot}", "method": method, "members": members}
    return bindings


def snapshot(host: str, peers: JsonObject, bindings: JsonObject, home: JsonObject | None = None) -> JsonObject:
    return {"schema_version": 1, "host": host, "peers": peers, "model_bindings": bindings,
            "tools": ["skill", "delegate_task", "omh_delegate_route"], "runtime_home": home,
            "consents": ["dispatch", "delivery:disabled", "lookup"]}


def make_home(project_root: Path, run_id: str) -> str:
    path = project_root / ".thunderkit" / "runs" / run_id / "hermes-home"
    path.mkdir(parents=True)
    return str(path)


def home_variant(root: Path, variant: str) -> JsonObject:
    path = make_home(root, "run")
    match variant:
        case "missing":
            Path(path).rmdir()
        case "booleans":
            return {"path": path, "task_owned": True, "active_process_home": True}
        case "mismatch":
            return {"path": path, "parent_home": path, "dispatcher_home": str(root)}
        case "relative":
            path = ".thunderkit/runs/run/hermes-home"
        case "outside":
            path = str(root.parent / ".thunderkit/runs/run/hermes-home")
        case "substring":
            path = str(root / "extra/.thunderkit/runs/run/hermes-home")
        case "traversal":
            path = path.replace("/runs/", "/runs/other/../")
        case "double-slash":
            path = path.replace("/runs/", "/runs//")
        case "contained-link" | "escaping-link":
            Path(path).rmdir()
            Path(path).symlink_to(root if variant == "contained-link" else root.parent, target_is_directory=True)
        case "prefix-link":
            (root / "alias").symlink_to(root, target_is_directory=True)
            path = str(root / "alias" / Path(path).relative_to(root))
        case "file":
            Path(path).rmdir()
            Path(path).write_bytes(b"not a directory")
        case _:
            raise AssertionError(variant)
    return {key: path for key in ("path", "parent_home", "dispatcher_home")}


class Fixture:
    """Mutable test scenario; only arguments() persists deliberate input mutations."""

    def __init__(self, root: Path, host: str = "opencode", skill: str = "tk-plan") -> None:
        self.root, self.skill = root, skill
        self.catalog = read_json(REFERENCES / "models.json")
        base = read_json(REFERENCES / "dependencies.json")
        self.ecosystem = "omh" if host == "hermes" else "omo"
        pin = mapping(mapping(base["ecosystems"])[self.ecosystem])
        target = next(mapping(value) for value in sequence(mapping(mapping(base["skills"])[skill])["targets"])
                      if mapping(value)["ecosystem"] == self.ecosystem)
        self.peer_root = root / text(pin["package"])
        hashes = materialize_peer(self.peer_root, pin, [target])
        self.manifest = fixture_manifest(base, {(self.ecosystem, key): value for key, value in hashes.items()})
        self.target = next(mapping(value) for value in sequence(mapping(mapping(self.manifest["skills"])[skill])["targets"])
                           if mapping(value)["ecosystem"] == self.ecosystem)
        classes: JsonObject = {"planner": "opus5", "executors": ["fable51"], "reviewers": ["fable51", "opus5"]}
        if host == "codex":
            classes = {"planner": "sol", "executors": ["sol"], "reviewers": ["sol"]}
        if host == "hermes":
            classes["reviewers"] = ["opus48", "opus5"]
        self.config: JsonObject = {"schema_version": 2, "classes": classes}
        self.slots = mapping(target.get("native_roles", {text(req).partition(":")[2]: text(req).partition(":")[2]
                             for req in sequence(target["requires"]) if text(req).startswith("model-binding:")}))
        entrypoint = text(mapping(target["provenance"])["entrypoint"])
        self.loaded: JsonObject = {"path": str(self.peer_root / entrypoint),
                                   "sha256": hashes[text(target["selector"])][entrypoint]}
        self.peer: JsonObject = {key: pin[key] for key in ("package", "version", "source")}
        self.peer.update(root=str(self.peer_root), loaded_skills={text(target["selector"]): self.loaded})
        self.snapshot = snapshot(host, {self.ecosystem: self.peer}, slot_bindings(self.catalog, host, classes, self.slots))

    def arguments(self, operation: str | None = None) -> list[str]:
        for name, document in (("config", self.config), ("capabilities", self.snapshot), ("dependencies", self.manifest)):
            write_json(self.root / f"{name}.json", document)
        result = ["--skill", self.skill, "--config", str(self.root / "config.json"),
                  "--capabilities", str(self.root / "capabilities.json"),
                  "--manifest", str(self.root / "dependencies.json"), "--project-root", str(self.root)]
        return result + (["--operation", operation] if operation is not None else [])
