"""Validate the pinned, qualified native-peer manifest without external packages."""

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Final, TypedDict


class Peer(TypedDict):
    package: str
    version: str
    registry: str
    install_hint: str


class Target(TypedDict):
    ecosystem: str
    skill_name: str
    selector: str
    mode: str
    operations: list[str]
    requires: list[str]
    notes: str


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


ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "skills/references/dependencies.json"
PINS: Final = {
    "omo": ("oh-my-openagent", "5.0.0-beta.81"),
    "omh": ("oh-my-hermes", "2.0.3"),
}
OPERATIONS: Final = {
    "tk-router": ("route", ("bootstrap", "route")),
    "tk-test": ("preflight", ("preflight",)),
    "tk-ask": ("validate", ("validate",)),
    "tk-grill": ("interview", ("interview",)),
    "tk-spec": ("clarify", ("clarify",)),
    "tk-map": ("map", ("map",)),
    "tk-discuss": ("discuss", ("discuss",)),
    "tk-research": ("research", ("research",)),
    "tk-learn": ("research", ("research", "discover")),
    "tk-plan": ("plan", ("plan",)),
    "tk-execute": ("execute", ("execute",)),
    "tk-review": ("diff", ("diff", "plan")),
    "tk-verify-work": ("cli", ("cli", "api", "visual")),
    "tk-debug": ("general", ("general", "native-fault")),
    "tk-ship": ("prepare", ("prepare",)),
    "tk-docs": ("docs", ("docs",)),
    "tk-audit": ("audit", ("audit",)),
    "tk-memory": ("view", ("view", "save")),
    "tk-handoff": ("save", ("save", "restore", "lookup")),
}
TARGETS: Final = {
    "tk-grill": {("omh", "ultrawork/ulw-interview", "component", ("interview",))},
    "tk-spec": {("omh", "ultrawork/ulw-interview", "component", ("clarify",))},
    "tk-map": {
        ("omo", "ulw-research", "component", ("map",)),
        ("omh", "planner/omh-codebase-onboarding", "component", ("map",)),
    },
    "tk-discuss": {("omh", "ultrawork/ulw-interview", "component", ("discuss",))},
    "tk-research": {
        ("omo", "ulw-research", "handoff", ("research",)),
        ("omh", "ultrawork/ulw-research", "handoff", ("research",)),
    },
    "tk-learn": {
        ("omo", "ulw-research", "component", ("research",)),
        ("omh", "ultrawork/ulw-research", "component", ("research",)),
        ("omh", "operator/omh-skill-scout", "component", ("discover",)),
    },
    "tk-plan": {
        ("omo", "ulw-plan", "handoff", ("plan",)),
        ("omh", "ultrawork/ulw-plan", "handoff", ("plan",)),
    },
    "tk-execute": {
        ("omo", "ulw-execute", "handoff", ("execute",)),
        ("omh", "ultrawork/ulw-work", "handoff", ("execute",)),
    },
    "tk-review": {("omh", "reviewer/omh-code-review", "component", ("diff",))},
    "tk-verify-work": {
        ("omo", "visual-qa", "component", ("visual",)),
        ("omh", "operator/omh-visual-qa", "component", ("visual",)),
    },
    "tk-debug": {
        ("omo", "debugging", "handoff", ("general", "native-fault")),
        ("omh", "reviewer/omh-native-debugging", "component", ("native-fault",)),
    },
    "tk-ship": {("omh", "reviewer/omh-verification-gate", "component", ("prepare",))},
    "tk-audit": {("omh", "reviewer/omh-verification-gate", "component", ("audit",))},
    "tk-handoff": {("omo", "coding-agent-sessions", "component", ("lookup",))},
}


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
            actual.add((ecosystem, selector, target["mode"], tuple(target_ops)))
            restricted.append(json.dumps(target))
        assert actual == TARGETS.get(name, set()), f"{name}: frozen target map mismatch"
    assert not re.search(r"gsd|omc", "\n".join(restricted), re.IGNORECASE), "excluded reference"


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc: Manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.skill_dirs = {path.name for path in (ROOT / "skills").glob("tk-*") if path.is_dir()}

    def test_manifest(self) -> None:
        validate_manifest(self.doc, self.skill_dirs)

    def test_roles_match_skill_frontmatter(self) -> None:
        for name, skill in self.doc["skills"].items():
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                roles = re.findall(r"(?m)^metadata:\n  thunderkit:\n    role: (\S+)$", text.split("---", 2)[1])
                self.assertEqual(roles, [skill["role"]])

    def test_same_name_planners_resolve_to_distinct_packages(self) -> None:
        targets = self.doc["skills"]["tk-plan"]["targets"]
        packages = {self.doc["ecosystems"][target["ecosystem"]]["package"] for target in targets}
        self.assertEqual([target["skill_name"] for target in targets], ["ulw-plan", "ulw-plan"])
        self.assertEqual(packages, {"oh-my-openagent", "oh-my-hermes"})

    def test_rejects_swapped_peer_records(self) -> None:
        peers = self.doc["ecosystems"]
        peers["omo"], peers["omh"] = peers["omh"], peers["omo"]
        with self.assertRaisesRegex(AssertionError, "peer pin mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_swapped_target_ecosystem(self) -> None:
        self.doc["skills"]["tk-plan"]["targets"][0]["ecosystem"] = "omh"
        with self.assertRaisesRegex(AssertionError, "selector ecosystem mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_unpinned_versions(self) -> None:
        for ecosystem in PINS:
            with self.subTest(ecosystem=ecosystem):
                doc = copy.deepcopy(self.doc)
                doc["ecosystems"][ecosystem]["version"] = "latest"
                with self.assertRaisesRegex(AssertionError, "peer pin mismatch"):
                    validate_manifest(doc, self.skill_dirs)

    def test_rejects_duplicate_target(self) -> None:
        targets = self.doc["skills"]["tk-plan"]["targets"]
        targets.append(copy.deepcopy(targets[0]))
        with self.assertRaisesRegex(AssertionError, "duplicate qualified target"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_unqualified_duplicate_target(self) -> None:
        target = copy.deepcopy(self.doc["skills"]["tk-plan"]["targets"][0])
        target["ecosystem"] = ""
        self.doc["skills"]["tk-plan"]["targets"].append(target)
        with self.assertRaisesRegex(AssertionError, "ineligible target ecosystem"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_excluded_target(self) -> None:
        self.doc["skills"]["tk-plan"]["targets"][0]["ecosystem"] = self.doc["excluded"][0].upper()
        with self.assertRaisesRegex(AssertionError, "ineligible target ecosystem"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_excluded_fallback(self) -> None:
        for excluded in map(str.upper, self.doc["excluded"]):
            with self.subTest(excluded=excluded):
                doc = copy.deepcopy(self.doc)
                doc["skills"]["tk-docs"]["fallback"] = excluded
                with self.assertRaisesRegex(AssertionError, "excluded reference"):
                    validate_manifest(doc, self.skill_dirs)

    def test_rejects_excluded_install_hint(self) -> None:
        for excluded in map(str.upper, self.doc["excluded"]):
            with self.subTest(excluded=excluded):
                doc = copy.deepcopy(self.doc)
                doc["ecosystems"]["omo"]["install_hint"] = excluded
                with self.assertRaisesRegex(AssertionError, "excluded reference"):
                    validate_manifest(doc, self.skill_dirs)


if __name__ == "__main__":
    unittest.main()
