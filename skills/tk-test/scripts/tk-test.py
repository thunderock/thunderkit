#!/usr/bin/env python3
"""Probe configured models; exit 0 for verified readiness, 1 for failure, 2 for invalid input."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from contextlib import suppress
import json
from math import isfinite
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Final, NoReturn


def failure(reason: str, json_mode: bool) -> int:
    print(f"preflight: {reason}; check arguments, model classes and skill-local support files", file=sys.stderr)
    if json_mode:
        print(json.dumps({"schema_version": 1, "status": "invalid", "reason_code": reason,
                          "models": {}, "classes": {}, "reviewer_families": [], "reviewer_family_count": 0}))
    else:
        print(f"FAIL: {reason}")
    return 2


try:
    for _name in ("model_config", "preflight_protocols"):
        _path = Path(__file__).absolute().with_name(f"{_name}.py")
        if not _path.is_file() or _path.is_symlink():
            raise ImportError(_name)
    import model_config
    import preflight_protocols
    from model_config import ConfigError, JsonObject, JsonValue, distinct_families, load_json, normalize_config, selected_models
    from preflight_protocols import Harness, Outcome, ProtocolError, Wire, command, decode, integer, mapping, text
    if any(Path(module.__file__ or "").absolute().parent != Path(__file__).absolute().parent
           for module in (model_config, preflight_protocols)):
        raise ImportError("local support required")
except (ImportError, OSError, SyntaxError, UnicodeError):
    if __name__ == "__main__":
        sys.exit(failure("invalid_assets", "--json" in sys.argv[1:]))
    raise

OUTPUT_LIMIT: Final = 1_048_576


class _Arguments(argparse.Namespace):
    config: str = ".thunderkit/config.json"
    timeout: float = 120.0
    json: bool = False
    help: bool = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise argparse.ArgumentError(None, "invalid_cli")


def catalog_wires(catalog: JsonObject) -> dict[str, tuple[Wire, ...]]:
    result = {}
    for key, raw in mapping(catalog.get("models")).items():
        model = mapping(raw)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", text(model["family"])):
            raise ProtocolError()
        entries = model["harnesses"]
        if not isinstance(entries, list):
            raise ProtocolError()
        wires = []
        for raw_entry in entries:
            entry = mapping(raw_entry)
            wire = Wire(Harness(text(entry.get("harness"))), text(entry.get("provider")), text(entry.get("model_id")))
            if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", wire.provider)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", wire.model_id)):
                raise ProtocolError()
            wires.append(wire)
        result[key] = tuple(wires)
    return result


def probe(wire: Wire, timeout: float) -> Outcome:
    if not isfinite(timeout) or timeout <= 0:
        raise ProtocolError("invalid_timeout")
    try:
        with tempfile.TemporaryFile() as output:
            try:
                child = subprocess.Popen(command(wire, timeout), stdin=subprocess.DEVNULL, stdout=output,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError:
                return Outcome(wire, "not-installed", "executable_missing")
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return Outcome(wire, "timeout", "deadline_exceeded")
            finally:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=2)
            if child.returncode != 0:
                return Outcome(wire, "unreachable", "process_exit")
            output.seek(0)
            content = output.read(OUTPUT_LIMIT + 1)
        if len(content) > OUTPUT_LIMIT:
            return Outcome(wire, "malformed", "output_limit")
        return decode(wire, content.decode("utf-8"))
    except subprocess.TimeoutExpired:
        return Outcome(wire, "timeout", "cleanup_timeout")
    except OSError:
        return Outcome(wire, "unreachable", "process_error")
    except UnicodeError:
        return Outcome(wire, "malformed", "invalid_encoding")


def report_row(outcome: Outcome, known_ids: set[str]) -> JsonObject:
    wire, session = outcome.wire, outcome.session_id
    observed: JsonValue = [{"model_id": name, "provider": None} for name in outcome.observed_models if name in known_ids]
    resume: JsonValue = None
    if session is not None:
        prefixes = {Harness.CLAUDE: ["claude", "-p", "--resume"], Harness.CODEX: ["codex", "exec", "resume"],
                    Harness.HERMES: ["hermes", "chat", "--resume"], Harness.OPENCODE: ["opencode", "run", "-s"]}
        resume = [*prefixes[wire.harness], session]
        if wire.harness == Harness.CODEX:
            resume.append("--skip-git-repo-check")
    return {"harness": wire.harness.value, "status": outcome.status, "reason_code": outcome.reason_code,
            "requested": {"provider": wire.provider, "model_id": wire.model_id}, "observed": observed or None,
            "session_id": session, "resumable": session is not None, "resume": resume}


def aggregate(cfg: JsonObject, catalog: JsonObject, outcomes: Mapping[str, Outcome]) -> JsonObject:
    selection = selected_models(cfg, catalog)
    reviewers, explicit = selection["reviewers"], selection["explicit"]
    planner, executors = selection["planner"], selection["executors"]
    mode = selection["reviewers_mode"]
    assert isinstance(reviewers, list) and isinstance(explicit, list)
    assert isinstance(planner, str) and isinstance(executors, list) and isinstance(mode, str)
    verified = {key for key, row in outcomes.items()
                if row.status == "reachable" and row.observed_models == (row.wire.model_id,)}
    ready_reviewers = [key for key in reviewers if key in verified]
    required_failures = [key for key in explicit if key not in verified]
    optional_failures = [key for key in reviewers if key not in explicit and key not in verified]
    families = sorted(distinct_families(ready_reviewers, catalog))
    minimum = integer(cfg["review_families_min"])
    family_gate = len(families) >= minimum
    passed = bool(outcomes) and not required_failures and family_gate
    known_ids = {wire.model_id for wires in catalog_wires(catalog).values() for wire in wires}
    resolved: JsonObject = {"planner": planner, "executors": [*executors], "reviewers": [*ready_reviewers]}
    return {"schema_version": 1, "status": "passed" if passed else "failed",
            "reason_code": "ready" if passed else "required_models_unavailable" if required_failures else "insufficient_review_families",
            "models": {key: report_row(row, known_ids) for key, row in outcomes.items()},
            "classes": cfg["classes"], "resolved_classes": resolved,
            "reviewer_candidates": [*reviewers], "reviewers_mode": mode,
            "required_failures": [*required_failures], "unavailable_candidates": [*optional_failures],
            "reviewer_families": [*families], "reviewer_family_count": len(families),
            "review_families_min": minimum, "family_gate": family_gate}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arguments
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--config", default=".thunderkit/config.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = _Arguments()
    try:
        parser.parse_args(arguments, namespace=args)
        if not isfinite(args.timeout) or args.timeout <= 0:
            raise argparse.ArgumentError(None, "invalid_timeout")
    except argparse.ArgumentError:
        return failure("invalid_cli", json_mode)
    if args.help:
        print(json.dumps({"usage": parser.format_help()}) if json_mode else parser.format_help(), end="\n")
        return 0
    catalog_path = Path(__file__).absolute().parent.parent / "references/models.json"
    try:
        if not catalog_path.is_file() or catalog_path.is_symlink():
            return failure("invalid_assets", json_mode)
        catalog = load_json(str(catalog_path))
        distinct_families((), catalog)
        wires = catalog_wires(catalog)
    except (ConfigError, ProtocolError, ValueError, RecursionError):
        return failure("invalid_assets", json_mode)
    try:
        if not Path(args.config).is_file():
            return failure("invalid_config", json_mode)
        cfg, warnings = normalize_config(load_json(args.config), catalog)
        selection = selected_models(cfg, catalog)
    except (ConfigError, ProtocolError, ValueError, RecursionError):
        return failure("invalid_config", json_mode)
    planner, executors, reviewers = selection["planner"], selection["executors"], selection["reviewers"]
    assert isinstance(planner, str) and isinstance(executors, list) and isinstance(reviewers, list)
    wanted = list(dict.fromkeys([planner, *executors, *reviewers]))
    outcomes = {}
    for key in wanted:
        choices = wires[key]
        wire = next((item for item in choices if shutil.which(item.harness.value)), choices[0])
        outcomes[key] = probe(wire, args.timeout)
    report = aggregate(cfg, catalog, outcomes)
    report["warnings"] = list(warnings)
    for key, outcome in outcomes.items():
        if outcome.status != "reachable":
            print(f"preflight: {key}: {outcome.reason_code}", file=sys.stderr)
    for warning in warnings:
        print(f"preflight: {warning}", file=sys.stderr)
    if json_mode:
        print(json.dumps(report))
    else:
        print(f"classes: {json.dumps(report['classes'])}")
        for key, raw_row in mapping(report["models"]).items():
            row = mapping(raw_row)
            print(f"{key}: {row['harness']} {row['status']} ({row['reason_code']})")
            print(f"  requested: {json.dumps(row['requested'])}; observed: {json.dumps(row['observed'])}")
            if row["resume"]:
                print(f"  resume argv: {json.dumps(row['resume'])}")
        print(f"required failures: {json.dumps(report['required_failures'])}")
        print(f"unavailable optional candidates: {json.dumps(report['unavailable_candidates'])}")
        print(f"reviewer families verified: {report['reviewer_family_count']}; required: {report['review_families_min']}")
        print(f"{report['status']}: {report['reason_code']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
