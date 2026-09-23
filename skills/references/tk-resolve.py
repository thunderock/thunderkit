"""Compute a route without dispatch, network access or configuration writes.

resolve(argv) returns one record; main(argv) emits it, with detail on stderr.
JSON mode exits 0 for computed routes, 1 for blocked, 2 for malformed input.
Native evidence uses the snapshot schema documented in capability_gates.py.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
from typing import Final, NoReturn, TypeAlias

_BYTECODE_POLICY: Final = sys.dont_write_bytecode
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_config
import capability_gates
sys.dont_write_bytecode = _BYTECODE_POLICY

from capability_gates import (Decision, Reason, Request, digest, expect_list, expect_object,
                              expect_strings, expect_text, path_text)

JsonObject: TypeAlias = model_config.JsonObject
JsonValue: TypeAlias = model_config.JsonValue
CLASSES: Final = ("planner", "executors", "reviewers")
TARGET_FIELDS: Final = ("ecosystem", "package", "version", "skill_name", "selector", "mode")
MODEL_FREE: Final = frozenset({("tk-ask", "validate"), ("tk-memory", "view"),
                              ("tk-router", "bootstrap"), ("tk-handoff", "save")})


class _Arguments(argparse.Namespace):
    skill: str = ""
    operation: str | None = None
    config: str | None = None
    capabilities: str | None = None
    catalog: str | None = None
    manifest: str | None = None
    project_root: str | None = None
    json: bool = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise model_config.ConfigError(message)


def _relative(value: JsonValue, field: str) -> str:
    result = path_text(value, field)
    if ":" in result or "\\" in result or any(part in ("", ".", "..") for part in result.split("/")):
        raise model_config.ConfigError(f"{field} must be a portable root-relative path")
    return result


def validate_candidate(candidate: JsonObject, pin: JsonObject) -> None:
    for field in TARGET_FIELDS:
        if not expect_text(candidate.get(field), f"target.{field}"):
            raise model_config.ConfigError(f"target.{field} must not be empty")
    for field in ("package", "version", "source"):
        if not expect_text(pin.get(field), f"pin.{field}"):
            raise model_config.ConfigError(f"pin.{field} must not be empty")
    if "source_commit" in pin:
        expect_text(pin["source_commit"], "pin.source_commit")
    expect_strings(pin.get("hosts"), "pin.hosts")
    if candidate.get("mode") not in ("handoff", "component"):
        raise model_config.ConfigError("target.mode must be handoff or component")
    identity = expect_object(pin.get("provenance_root"), "provenance_root")
    _relative(identity.get("identity_file"), "identity_file")
    identity_fields = expect_object(identity.get("identity_fields"), "identity_fields")
    if not identity_fields or any(type(value) not in (str, int) for value in identity_fields.values()):
        raise model_config.ConfigError("identity_fields must contain string or integer values, not booleans")
    provenance = expect_object(candidate.get("provenance"), "provenance")
    if set(provenance) != {"root_kind", "entrypoint", "files"} or provenance.get("root_kind") != identity.get("root_kind"):
        raise model_config.ConfigError("provenance must have exactly root_kind, entrypoint and files matching the pin")
    entrypoint = _relative(provenance.get("entrypoint"), "entrypoint")
    files = expect_object(provenance.get("files"), "provenance.files")
    if entrypoint not in files:
        raise model_config.ConfigError("provenance.files must include the entrypoint")
    for relative, fingerprint in files.items():
        _relative(relative, "provenance.files key")
        if not digest(fingerprint):
            raise model_config.ConfigError("provenance.files values must be lowercase SHA-256 digests")
    selector = _relative(candidate.get("selector"), "selector")
    name = _relative(candidate.get("skill_name"), "skill_name")
    if "/" in name:
        raise model_config.ConfigError("skill_name must be a single path segment")
    match provenance.get("root_kind"):
        case "package":
            expected: JsonObject = {"name": pin["package"], "version": pin["version"]}
            if selector != name or entrypoint != f"dist/skills/{name}/SKILL.md" or "canonical_name" in candidate:
                raise model_config.ConfigError("Package selector must identify its exact skill entrypoint")
        case "omh":
            expected = {"schema_version": 1, "package": pin["package"], "version": pin["version"]}
            if (selector.count("/") != 1 or entrypoint != f"skills/{selector}/SKILL.md"
                    or not expect_text(candidate.get("canonical_name"), "canonical_name")
                    or identity.get("manifest_record_fields") != ["name", "path", "sha256", "source"]
                    or not expect_strings(identity.get("manifest_source_values"), "manifest_source_values")):
                raise model_config.ConfigError("OMH selector and installer identity must be fully qualified")
        case _:
            raise model_config.ConfigError("root_kind must be package or omh")
    if identity_fields != expected:
        raise model_config.ConfigError("Root identity fields must match exact package metadata")
    required: set[str] = set()
    for requirement in expect_strings(candidate.get("requires"), "target.requires"):
        match requirement.partition(":"):
            case ("model-binding", ":", cls) if cls in CLASSES:
                required.add(cls)
            case ("tool", ":", tool) if tool:
                pass
            case ("runtime_home", ":", "isolated") | ("delivery", ":", "disabled") | ("user-request", ":", "explicit"):
                pass
            case _:
                raise model_config.ConfigError("Unknown target requirement")
    if "native_roles" in candidate:
        roles = expect_object(candidate["native_roles"], "native_roles")
        values = [expect_text(value, "role class") for value in roles.values()]
        if not roles or any(not key for key in roles) or set(values) != required or not required:
            raise model_config.ConfigError("native_roles must cover exactly the required model classes")


def _resource(name: str, override: str | None) -> str:
    if override is not None:
        return override
    directory = Path(os.path.abspath(__file__)).parent
    for path in (directory / name, directory.parent / "references" / name):
        if path.is_file():
            return str(path)
    raise model_config.ConfigError(f"{name} missing beside the script and in ../references/")


def resolve(argv: Sequence[str]) -> JsonObject:
    """Compute one decision from CLI-style arguments; print nothing and write nothing."""
    args = _Arguments()
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("skill", "config", "capabilities", "operation", "catalog", "manifest", "project-root"):
        parser.add_argument(f"--{name}", required=name == "skill")
    parser.add_argument("--json", action="store_true")
    argument_error: model_config.ConfigError | None = None
    try:
        parser.parse_args(argv, namespace=args)
    except model_config.ConfigError as exc:
        argument_error = exc
    bindings: JsonObject = {"requested": {}, "effective": {}, "observed": None}
    evidence_paths: list[JsonValue] = []
    record: JsonObject = {"schema_version": 1, "skill": args.skill, "operation": args.operation,
                          "decision": "blocked", "reason_code": "invalid_config", "detail": "",
                          "target": None, "bindings": bindings, "runtime_home": None, "evidence_paths": evidence_paths}

    def finish(decision: Decision, reason: Reason, detail: str) -> JsonObject:
        return {**record, "decision": decision, "reason_code": reason, "detail": detail}

    try:
        if argument_error is not None:
            raise argument_error
        try:
            project = Path(path_text(args.project_root or os.getcwd(), "project-root")).resolve(strict=True)
        except RuntimeError:
            raise model_config.ConfigError("--project-root cannot be resolved") from None
        if not project.is_dir():
            raise model_config.ConfigError("--project-root must be an existing directory")

        def consume(value: str | None, field: str) -> JsonObject:
            if value is None:
                raise model_config.ConfigError(f"--{field} is required for this operation")
            try:
                path = Path(path_text(value, field)).resolve(strict=True)
                relative = path.relative_to(project).as_posix()
                if not path.is_file():
                    raise model_config.ConfigError(f"--{field} must be a regular file")
            except (OSError, RuntimeError, ValueError):
                raise model_config.ConfigError(f"--{field} must be inside --project-root; evidence paths are repository-relative") from None
            with path.open("rb") as stream:
                stream.read(1)
                evidence_paths.append(relative)
            return model_config.load_json(str(path))

        manifest = model_config.load_json(_resource("dependencies.json", args.manifest))
        if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
            raise model_config.ConfigError("manifest.schema_version must be 1")
        skills = expect_object(manifest.get("skills"), "manifest.skills")
        if args.skill not in skills:
            raise model_config.ConfigError(f"Unknown skill {args.skill!r}")
        skill = expect_object(skills[args.skill], "skill")
        operation = args.operation if args.operation is not None else expect_text(skill.get("default_operation"), "default_operation")
        record["operation"] = operation
        if operation not in expect_strings(skill.get("operations"), "skill.operations"):
            raise model_config.ConfigError(f"Unknown operation {operation!r} for {args.skill}")
        if (args.skill, operation) in MODEL_FREE:
            return finish("owned", "owned_policy", "Model-free Thunderkit-owned operation; no model configuration read")
        if args.config is None:
            raise model_config.ConfigError(f"--config is required for model-bearing operation {args.skill} {operation}")
        raw_config = consume(args.config, "config")
        catalog = model_config.load_json(_resource("models.json", args.catalog))
        cfg, _warnings = model_config.normalize_config(raw_config, catalog)
        selected: JsonObject = {key: [item for item in value] if isinstance(value, list) else value
                                for key, value in model_config.selected_models(cfg, catalog).items()}
        bindings["requested"] = expect_object(cfg.get("classes"), "classes")
        if cfg.get("delegation") == "off":
            return finish("owned", "disabled", "Native delegation is disabled")
        ecosystems = expect_object(manifest.get("ecosystems"), "manifest.ecosystems")
        allowed = expect_strings(cfg.get("ecosystems"), "ecosystems")
        candidates: list[JsonObject] = []
        for raw_target in expect_list(skill.get("targets"), "skill.targets"):
            target = expect_object(raw_target, "target")
            ecosystem = expect_text(target.get("ecosystem"), "target.ecosystem")
            if operation not in expect_strings(target.get("operations"), "target.operations") or ecosystem not in allowed:
                continue
            pin = expect_object(ecosystems.get(ecosystem), f"ecosystems.{ecosystem}")
            candidate: JsonObject = {**target, "package": pin.get("package"), "version": pin.get("version"),
                                     "hosts": pin.get("hosts"), "pin": pin}
            validate_candidate(candidate, pin)
            candidates.append(candidate)
        if not candidates:
            return finish("owned", "owned_policy", "No native target is enabled for this operation")
        snapshot = consume(args.capabilities, "capabilities")
        if type(snapshot.get("schema_version")) is not int or snapshot.get("schema_version") != 1:
            raise model_config.ConfigError("capabilities.schema_version must be 1")
        host = expect_text(snapshot.get("host"), "host")
        compatible = [target for target in candidates if host in expect_strings(target.get("hosts"), "hosts")]
        if not compatible:
            return finish("fallback", "unsupported_host", f"No enabled target supports host {host!r}")
        request = Request(host, project, selected, catalog)
        failures: list[JsonObject] = []
        for candidate in compatible:
            record["target"] = {field: candidate[field] for field in TARGET_FIELDS}
            verdict = capability_gates.qualify(candidate, snapshot, request)
            if verdict.decision == "delegate":
                bindings["effective"] = verdict.effective
                record["runtime_home"] = verdict.runtime_home
                return finish(verdict.decision, verdict.reason, verdict.detail)
            failures.append(finish(verdict.decision, verdict.reason, verdict.detail))
        return failures[0]
    except (model_config.ConfigError, ValueError, OSError) as exc:
        return finish("blocked", "invalid_config", str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    """Emit one routing result, keeping JSON stdout separate from diagnostics."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    result = resolve(arguments)
    if "--json" in arguments:
        print(json.dumps(result))
    else:
        print(f"{result['skill']} {result['operation']}: {result['decision']} ({result['reason_code']})")
    print(result["detail"], file=sys.stderr)
    if result["reason_code"] == "invalid_config":
        return 2
    return 1 if result["decision"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
