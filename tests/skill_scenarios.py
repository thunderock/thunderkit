#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run contracts: python3 tests/skill_scenarios.py --skill tk-plan --case all."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Final, NoReturn
from unittest.mock import patch

_BYTECODE_POLICY: Final = sys.dont_write_bytecode
sys.dont_write_bytecode = True
from scenario_fixtures import (ROOT, REFERENCES, ConfigError, JsonObject, JsonValue, Prepared,
                               CaseInputs as CaseInputs, FixtureLibrary as FixtureLibrary,
                               items, load_json, object_value, text_value)
from tools.skill_frontmatter import FrontmatterError, is_legacy_nested_metadata, parse_skill_file, validate_thunderkit
sys.dont_write_bytecode = _BYTECODE_POLICY
FIXTURES: Final = ROOT / "tests/fixtures"
SCRATCH: Final = Path(os.environ.get("THUNDERKIT_TEST_TMPDIR", str(ROOT / ".thunderkit/scenario-attempts")))
METADATA: Final = {"thunderkit-role", "thunderkit-tier", "thunderkit-delegates", "thunderkit-contract"}


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    name: str
    inputs: CaseInputs
    expect: tuple[tuple[str, JsonValue], ...]
    sections: tuple[str, ...]
    frontmatter: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    assertions: int = 0
    failures: tuple[str, ...] = ()


class _Arguments(argparse.Namespace):
    skill: str | None = None
    all: bool = False
    case: str = "all"
    skills_root: Path = ROOT / "skills"
    scenarios_dir: Path = ROOT / "tests/scenarios"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ConfigError(message)


def parse_case(value: JsonValue, skill: str) -> ScenarioCase:
    raw = object_value(value, "case")
    required = {"name", "operation", "config", "capabilities", "expect"}
    if required - raw.keys() or raw.keys() - required - {"sections", "frontmatter"}:
        raise ConfigError(f"case fields: missing {sorted(required - raw.keys())}; unknown {sorted(raw.keys() - required - {'sections', 'frontmatter'})}")
    expect = object_value(raw["expect"], "expect")
    required_expect = {"decision", "reason_code", "target_ecosystem", "target_selector", "exit"}
    if required_expect - expect.keys() or expect.keys() - required_expect - {"target_mode", "requested_bindings"}:
        raise ConfigError("expect requires decision, reason_code, target_ecosystem, target_selector, exit; zero/unknown assertions rejected")
    for key in ("decision", "reason_code"):
        text_value(expect[key], f"expect.{key}")
    for key in ("target_ecosystem", "target_selector", "target_mode"):
        if expect.get(key) is not None:
            text_value(expect[key], f"expect.{key}")
    if type(expect["exit"]) is not int or expect["exit"] not in (0, 1, 2):
        raise ConfigError("expect.exit must be integer 0, 1 or 2")
    if "requested_bindings" in expect:
        choices = object_value(expect["requested_bindings"], "expect.requested_bindings")
        if choices:
            if set(choices) != {"planner", "executors", "reviewers"}:
                raise ConfigError("expect.requested_bindings requires exactly the three classes or an empty object")
            text_value(choices["planner"], "requested planner")
            for key in ("executors", "reviewers"):
                if key == "reviewers" and choices[key] == "all":
                    continue
                for model in items(choices[key], f"requested {key}"):
                    text_value(model, f"requested {key} member")
    sections = tuple(text_value(item, "sections") for item in items(raw.get("sections", []), "sections"))
    if ("sections" in raw and not sections) or len(set(sections)) != len(sections):
        raise ConfigError("sections must be nonempty and unique when supplied")
    metadata = object_value(raw.get("frontmatter", {}), "frontmatter")
    if metadata.keys() - METADATA:
        raise ConfigError("frontmatter assertions support only the four thunderkit metadata fields")
    values = {key: None if raw[key] is None else text_value(raw[key], key) for key in ("operation", "config", "capabilities")}
    return ScenarioCase(text_value(raw["name"], "name"), CaseInputs(skill, values["operation"], values["config"], values["capabilities"]),
                        tuple(expect.items()), sections, tuple((key, text_value(value, key)) for key, value in metadata.items()))


def body_sections(body: str) -> frozenset[str]:
    sections: set[str] = set()
    fence = ""
    for line in body.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.fullmatch(r" {0,3}##[ \t]+(.+)", line)
        if heading:
            sections.add(re.sub(r"[ \t]+#+[ \t]*$", "", heading[1]).strip())
    return frozenset(sections)


def _resolver() -> Callable[[Prepared], tuple[JsonObject, int]]:
    spec = importlib.util.spec_from_file_location("tk_scenario_resolver", REFERENCES / "tk-resolve.py")
    if spec is None or spec.loader is None:
        raise ConfigError("cannot import the canonical resolver")
    module = importlib.util.module_from_spec(spec)
    bytecode_policy = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = bytecode_policy
    entry: Callable[[Sequence[str]], int] = module.main

    def run(prepared: Prepared) -> tuple[JsonObject, int]:
        output = io.StringIO()
        with patch.object(module.capability_gates, "read_mountinfo", return_value=prepared.mountinfo), redirect_stdout(output), redirect_stderr(io.StringIO()):
            status = entry([*prepared.argv, "--json"])
        value: JsonValue = json.loads(output.getvalue())
        return object_value(value, "resolver result"), status

    return run


@dataclass(frozen=True, slots=True)
class ScenarioRunner:
    skills_root: Path
    library: FixtureLibrary
    resolve: Callable[[Prepared], tuple[JsonObject, int]]

    def run_case(self, case: ScenarioCase) -> CaseResult:
        skill = case.inputs.skill
        label = f"{skill}/{case.name}"
        path = self.skills_root / skill / "SKILL.md"
        try:
            try:
                fm = parse_skill_file(path)
            except FrontmatterError as exc:
                if is_legacy_nested_metadata(path.read_text(encoding="utf-8")):
                    raise ConfigError("frontmatter not migrated") from exc
                raise
            validate_thunderkit(fm, skill)
            definition = object_value(object_value(self.library.manifest.get("skills"), "skills").get(skill), f"dependencies skill {skill}")
            declarations = [f"{object_value(target, 'target')['ecosystem']}:{object_value(target, 'target')['selector']}"
                            for target in items(definition.get("targets"), "targets")]
            SCRATCH.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="skill-scenario-", dir=SCRATCH) as temporary:
                resolved, exit_code = self.resolve(self.library.prepare(Path(temporary).resolve(), case.inputs))
            target = object_value(resolved["target"], "result.target") if resolved["target"] is not None else {}
            actual: JsonObject = {"decision": resolved["decision"], "reason_code": resolved["reason_code"], "exit": exit_code,
                "target_ecosystem": target.get("ecosystem"), "target_selector": target.get("selector"), "target_mode": target.get("mode"),
                "requested_bindings": object_value(resolved["bindings"], "result.bindings")["requested"]}
            sections = body_sections(fm.body)
            checks: list[tuple[str, JsonValue, JsonValue]] = [
                ("metadata.thunderkit-delegates", " ".join(dict.fromkeys(declarations)) or "none", fm.metadata["thunderkit-delegates"]),
                *((key, wanted, actual[key]) for key, wanted in case.expect),
                *((f"frontmatter.{key}", wanted, fm.metadata.get(key)) for key, wanted in case.frontmatter),
                *((f"sections.{name}", True, name in sections) for name in case.sections),
            ]
            failures = tuple(f"{key}: expected {wanted!r}, got {got!r}" for key, wanted, got in checks
                             if type(wanted) is not type(got) or wanted != got)
            return CaseResult(label, len(checks), failures)
        except (ConfigError, FrontmatterError, OSError, UnicodeError) as exc:
            return CaseResult(label, failures=(str(exc),))

    def run_fixture(self, path: Path, groups: Sequence[str]) -> list[CaseResult]:
        skill = path.stem
        try:
            fixture = load_json(str(path))
            if fixture.get("skill") != skill:
                raise ConfigError(f"fixture skill must match filename {skill!r}")
            if set(fixture) != {"skill", "cases"}:
                raise ConfigError("fixture requires only skill and cases")
            cases = object_value(fixture.get("cases"), "cases")
            if cases.keys() - {"happy", "failure"} or not groups or set(groups) - {"happy", "failure"}:
                raise ConfigError("case lists/groups must be happy or failure")
        except ConfigError as exc:
            return [CaseResult(f"{skill}/fixture", failures=(str(exc),))]
        results = []
        for group in groups:
            try:
                selected = items(cases.get(group), f"{group} case list")
                if not selected:
                    raise ConfigError(f"{group} case list is empty")
            except ConfigError as exc:
                results.append(CaseResult(f"{skill}/{group}", failures=(str(exc),)))
                continue
            seen: set[str] = set()
            for index, raw in enumerate(selected, 1):
                try:
                    case = parse_case(raw, skill)
                    if case.name in seen:
                        raise ConfigError("case names must be unique within their group")
                    seen.add(case.name)
                    results.append(self.run_case(case))
                except ConfigError as exc:
                    results.append(CaseResult(f"{skill}/{group}/{index}", failures=(str(exc),)))
        return results


def main(argv: Sequence[str] | None = None) -> int:
    """Return success only for a nonempty run with every selected contract checked."""
    parser = _Parser(description="Run fixture contracts, not native workflows", allow_abbrev=False)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--skill")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--case", choices=("happy", "failure", "all"), default="all")
    parser.add_argument("--skills-root", type=Path, default=ROOT / "skills")
    parser.add_argument("--scenarios-dir", type=Path, default=ROOT / "tests/scenarios")
    args = _Arguments()
    try:
        parser.parse_args(argv, namespace=args)
        runner = ScenarioRunner(args.skills_root, FixtureLibrary.load(FIXTURES), _resolver())
        names = ({path.stem for path in args.scenarios_dir.glob("*.json") if not path.name.startswith("_")} |
                 {path.name for path in args.skills_root.glob("tk-*") if path.is_dir()}) if args.all else {args.skill or ""}
        if any(re.fullmatch(r"tk-[a-z0-9]+(?:-[a-z0-9]+)*", name) is None for name in names):
            raise ConfigError("skill and fixture names must be tk-name slugs")
        groups = ("happy", "failure") if args.case == "all" else (args.case,)
        results = [result for name in sorted(names) for result in runner.run_fixture(args.scenarios_dir / f"{name}.json", groups)]
        if not results:
            results = [CaseResult("selection", failures=("no skill fixtures or cases selected",))]
    except (ConfigError, OSError) as exc:
        results = [CaseResult("inputs", failures=(str(exc),))]
    print("CASE | RESULT | ASSERTIONS | DETAIL")
    for result in results:
        state = "FAILED" if result.failures or result.assertions == 0 else "PASSED"
        detail = "; ".join(result.failures).replace("\n", " ") or "ok"
        print(f"{result.name} | {state} | {result.assertions} | {detail}")
    count = sum(result.assertions > 0 for result in results)
    passed = sum(not result.failures and result.assertions > 0 for result in results)
    print(f"CASES={count} PASSED={passed} FAILED={count - passed} ERRORS={len(results) - count}")
    print(f"ASSERTIONS={sum(result.assertions for result in results)}")
    return int(passed != len(results))


if __name__ == "__main__":
    raise SystemExit(main())
