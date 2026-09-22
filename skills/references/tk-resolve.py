"""Resolve a native route without invoking tools or changing persistent state.

CLI: python3 tk-resolve.py --skill NAME [--operation OP] --config PATH
     --capabilities PATH [--json] [--catalog PATH] [--manifest PATH]
Import API: resolve(argv) returns the decision; main(argv=None) prints it and
returns 0 for delegate/owned/fallback, 1 for blocked, or 2 for malformed input.

Capabilities snapshot schema (JSON, schema_version must be integer 1):
{
  "schema_version": 1, "host": "opencode|codex|hermes|claude|<other>",
  "peers": {
    "omo": {"package": "oh-my-openagent", "version": "...", "source": "...",
            "loaded_skills": {"<skill_name>": {"path": "...", "sha256": null}}},
    "omh": {"package": "oh-my-hermes", "version": "...", "skills_root": "...",
            "loaded_skills": {"<category/skill>": {"path": "...", "sha256": null}}}
  },
  "tools": ["skill", "delegate_task", "omh_delegate_route"],
  "model_bindings": {"<class>": {"requested": "<model key>",
                     "effective": "<provider/model_id>", "source": "agent:oracle"}},
  "runtime_home": {"path": "...", "task_owned": true, "active_process_home": true},
  "consents": ["dispatch", "delivery:disabled", "lookup"]
}
sha256 and effective may be null; sha256 otherwise holds a string. runtime_home
may be null. Class bindings use a scalar planner and ordered requested/effective
arrays for executors/reviewers; a singleton array may also be a scalar. Every
selected member needs an effective ID. Reviewers "all" expands to catalog keys.
Missing evidence maps/lists mean empty; unknown fields, including ready, do not
grant capabilities. Source/fingerprint metadata is not fetched or rehashed.
Only models.json/dependencies.json beside this script or in ../references/ are
searched, in that order; explicit --catalog/--manifest paths override discovery.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
from typing import Final, Literal, NoReturn, TypeAlias

_BYTECODE_POLICY: Final = sys.dont_write_bytecode
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_config
sys.dont_write_bytecode = _BYTECODE_POLICY

JsonValue: TypeAlias = model_config.JsonValue
JsonObject: TypeAlias = model_config.JsonObject
Decision: TypeAlias = Literal["delegate", "owned", "fallback", "blocked"]
Reason: TypeAlias = Literal["compatible", "disabled", "owned_policy", "invalid_config", "peer_missing",
                            "unsupported_host", "version_mismatch", "source_mismatch", "capability_missing",
                            "model_mismatch", "unsafe_runtime_home", "missing_evidence"]
Outcome: TypeAlias = tuple[Decision, Reason, str]
CLASSES: Final = ("planner", "executors", "reviewers")
TARGET_FIELDS: Final = ("ecosystem", "package", "version", "skill_name", "selector", "mode")


class _Arguments(argparse.Namespace):
    skill: str = ""
    operation: str | None = None
    config: str = ""
    capabilities: str = ""
    catalog: str | None = None
    manifest: str | None = None
    json: bool = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise model_config.ConfigError(message)


def _object(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise model_config.ConfigError(f"{field} must be a JSON object")
    return value


def _text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise model_config.ConfigError(f"{field} must be a string")
    return value


def _strings(value: JsonValue, field: str) -> list[str]:
    if not isinstance(value, list):
        raise model_config.ConfigError(f"{field} must be a list of strings")
    return [_text(item, field) for item in value]


def _keys(value: JsonValue) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return [_text(item, "binding") for item in value]
    return []


def _resource(name: str, override: str | None) -> str:
    if override is not None:
        return override
    directory = Path(os.path.abspath(__file__)).parent
    for path in (directory / name, directory.parent / "references" / name):
        if path.is_file():
            return str(path)
    raise model_config.ConfigError(f"{name} missing beside the script and in ../references/")


def _safe_home(value: JsonValue) -> str | None:
    if not isinstance(value, dict) or value.get("task_owned") is not True or value.get("active_process_home") is not True:
        return None
    path = value.get("path")
    if not isinstance(path, str) or "\0" in path or not Path(path).is_absolute():
        return None
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return str(resolved) if "/.thunderkit/runs/" in resolved.as_posix() else None


def _qualify(target: JsonObject, snapshot: JsonObject, requested: JsonObject) -> Outcome:
    ecosystem = _text(target["ecosystem"], "target.ecosystem")
    peer = _object(snapshot.get("peers", {}), "peers").get(ecosystem)
    if peer is None:
        return "fallback", "peer_missing", f"No {ecosystem} peer evidence"
    peer = _object(peer, f"peers.{ecosystem}")
    if peer.get("version") != target["version"]:
        return "fallback", "version_mismatch", f"{ecosystem} version differs from the exact pin"
    if peer.get("package") != target["package"]:
        return "fallback", "source_mismatch", f"{ecosystem} package identity differs from the pin"
    selector = _text(target["selector"], "target.selector")
    skills = _object(peer.get("loaded_skills", {}), "loaded_skills")
    loaded = skills.get(selector)
    if ecosystem == "omh" and ("/" not in selector or (loaded is None and target["skill_name"] in skills)):
        return "fallback", "source_mismatch", "The loaded skill must match the categorized selector"
    if loaded is None:
        return "fallback", "peer_missing", f"No loaded skill evidence for {ecosystem}:{selector}"
    loaded = _object(loaded, "loaded skill")
    path = _text(loaded.get("path", ""), "loaded skill.path")
    if ecosystem == "omo" and target["package"] not in Path(os.path.normpath(path)).parts:
        return "fallback", "source_mismatch", "Loaded skill path is outside the pinned package"
    tools = _strings(snapshot.get("tools", []), "tools")
    consents = _strings(snapshot.get("consents", []), "consents")
    bindings = _object(snapshot.get("model_bindings", {}), "model_bindings")
    for requirement in _strings(target["requires"], "target.requires"):
        prefix, _, name = requirement.partition(":")
        if prefix == "tool":
            if name not in tools:
                return "fallback", "capability_missing", f"Required tool {name!r} is absent"
        elif prefix == "model-binding":
            if name not in requested:
                raise model_config.ConfigError(f"Unknown model-binding class {name!r}")
            binding = _object(bindings.get(name, {}), f"model_bindings.{name}")
            chosen = _keys(requested[name])
            if _keys(binding.get("requested")) != chosen or len(_keys(binding.get("effective"))) != len(chosen):
                decision: Decision = "blocked" if target["mode"] == "handoff" else "fallback"
                return decision, "model_mismatch", f"Requested {name} binding is not fully effective"
        elif requirement == "runtime_home:isolated":
            if snapshot["runtime_home"] is None:
                return "blocked", "unsafe_runtime_home", "Execution requires an active task-owned home under .thunderkit/runs/"
        elif requirement == "delivery:disabled":
            if requirement not in consents:
                return "blocked", "capability_missing", "Execution requires an enforceable delivery opt-out"
        elif requirement == "user-request:explicit":
            if "lookup" not in consents:
                return "fallback", "missing_evidence", "Session lookup requires an explicit user request"
        else:
            raise model_config.ConfigError(f"Unknown target requirement {requirement!r}")
    return "delegate", "compatible", "Pinned peer, loaded selector, and required capabilities are compatible"


def resolve(argv: Sequence[str]) -> JsonObject:
    """Compute one decision from CLI-style arguments; print nothing and write nothing."""
    args = _Arguments()
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("skill", "config", "capabilities", "operation", "catalog", "manifest"):
        parser.add_argument(f"--{name}", required=name in ("skill", "config", "capabilities"))
    parser.add_argument("--json", action="store_true")
    argument_error: model_config.ConfigError | None = None
    try:
        parser.parse_args(argv, namespace=args)
    except model_config.ConfigError as exc:
        argument_error = exc
    bindings: JsonObject = {"requested": {}, "effective": {}, "observed": None}
    record: JsonObject = {"schema_version": 1, "skill": args.skill, "operation": args.operation,
                          "decision": "blocked", "reason_code": "invalid_config", "detail": "",
                          "target": None, "bindings": bindings, "runtime_home": None,
                          "evidence_paths": [args.config, args.capabilities]}

    def finish(decision: Decision, reason: Reason, detail: str) -> JsonObject:
        return {**record, "decision": decision, "reason_code": reason, "detail": detail}

    try:
        if argument_error is not None:
            raise argument_error
        catalog = model_config.load_json(_resource("models.json", args.catalog))
        cfg, _warnings = model_config.normalize_config(model_config.load_json(args.config), catalog)
        selected = model_config.selected_models(cfg, catalog)
        requested: JsonObject = {}
        for name in CLASSES:
            selection = selected[name]
            requested[name] = [key for key in selection] if isinstance(selection, list) else selection
        bindings["requested"] = requested
        bindings["effective"] = {name: None for name in CLASSES}
        manifest = model_config.load_json(_resource("dependencies.json", args.manifest))
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise model_config.ConfigError("manifest.schema_version must be 1")
        skills = _object(manifest.get("skills"), "manifest.skills")
        if args.skill not in skills:
            raise model_config.ConfigError(f"Unknown skill {args.skill!r}")
        skill = _object(skills[args.skill], "skill")
        operation = args.operation if args.operation is not None else _text(skill.get("default_operation"), "default_operation")
        record["operation"] = operation
        if operation not in _strings(skill.get("operations"), "skill.operations"):
            raise model_config.ConfigError(f"Unknown operation {operation!r} for {args.skill}")
        if cfg["delegation"] == "off":
            return finish("owned", "disabled", "Native delegation is disabled")
        targets = skill.get("targets")
        if not isinstance(targets, list):
            raise model_config.ConfigError("skill.targets must be a list")
        ecosystems = _object(manifest.get("ecosystems"), "manifest.ecosystems")
        allowed = _strings(cfg["ecosystems"], "ecosystems")
        candidates: list[JsonObject] = []
        for raw_target in targets:
            target = _object(raw_target, "target")
            ecosystem = _text(target.get("ecosystem"), "target.ecosystem")
            if operation not in _strings(target.get("operations"), "target.operations") or ecosystem not in allowed:
                continue
            pin = _object(ecosystems.get(ecosystem), f"ecosystems.{ecosystem}")
            candidate: JsonObject = {**target, "package": pin.get("package"), "version": pin.get("version"),
                                     "hosts": pin.get("hosts")}
            for field in TARGET_FIELDS:
                _text(candidate.get(field), f"target.{field}")
            _strings(candidate.get("requires"), "target.requires")
            if candidate["mode"] not in ("handoff", "component"):
                raise model_config.ConfigError("target.mode must be handoff or component")
            candidates.append(candidate)
        if not candidates:
            return finish("owned", "owned_policy", "No native target is enabled for this operation")
        snapshot = model_config.load_json(args.capabilities)
        if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
            raise model_config.ConfigError("capabilities.schema_version must be 1")
        host = _text(snapshot.get("host"), "host")
        compatible_hosts = [target for target in candidates if host in _strings(target["hosts"], "ecosystem.hosts")]
        if not compatible_hosts:
            return finish("fallback", "unsupported_host", f"No enabled target supports host {host!r}")
        snapshot["runtime_home"] = _safe_home(snapshot.get("runtime_home"))
        reported = _object(snapshot.get("model_bindings", {}), "model_bindings")
        effective: JsonObject = {}
        for name in CLASSES:
            effective_value = _object(reported.get(name, {}), f"model_bindings.{name}").get("effective")
            effective[name] = effective_value if _keys(effective_value) else None
        bindings["effective"] = effective
        failures: list[JsonObject] = []
        for target in compatible_hosts:
            record["target"] = {field: target[field] for field in TARGET_FIELDS}
            outcome = _qualify(target, snapshot, requested)
            if outcome[0] == "delegate":
                if "runtime_home:isolated" in _strings(target["requires"], "target.requires"):
                    record["runtime_home"] = snapshot["runtime_home"]
                return finish(*outcome)
            failures.append(finish(*outcome))
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
