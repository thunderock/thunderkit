"""Validate the pinned, qualified native-peer manifest without external packages."""

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any, Final, TypedDict


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
ROOT_KINDS: Final = {"omo": "package", "omh": "omh"}
OMH_RAIL: Final = "skills/guide/omh-routing/references/skill-common-rail.md"
SHA256: Final = re.compile(r"[0-9a-f]{64}")
# Entrypoint fingerprints pinned from the trusted artifact inventories, independent of the manifest.
ENTRYPOINT_SHA256: Final = {
    ("omo", "ulw-research"): "989f86f1920f783aed6156438f1ab29d5e10c12aea3bcebff5362295777c6afe",
    ("omo", "ulw-plan"): "27a0f81ccb76431beb2889ec83239d525946697aa4fb5e86071ec07af6861dbd",
    ("omo", "ulw-execute"): "071a86e7981278d678f35e8c2d00dd007494688b64eac7a60b1fcc14d339cba7",
    ("omo", "visual-qa"): "5ea017377d1d2789722bdb0545cdf01b0fbeb3fb251f8a981325fa948ba13c6f",
    ("omo", "debugging"): "49fb22e0a1adb577cce543acc4e823212bd1af2e360b0e89aec041498081ed5e",
    ("omo", "coding-agent-sessions"): "9b00f11c1aadc51604f67376f45f33e416dc90915fd83f616a7936d5bd173d61",
    ("omh", "ultrawork/ulw-interview"): "c3a9d80a041ad8695e6bb513be9fc9e48c3b54444f53b134dca4cb6249076317",
    ("omh", "planner/omh-codebase-onboarding"): "ad50a185e1ccbfaaf71a590125ac66ed628cbc66b427861d07583ed93b791c17",
    ("omh", "ultrawork/ulw-research"): "95186ac5e6ec0a70ccfd4509ed15e8a3869077dd7866288fed7b635f1022968a",
    ("omh", "operator/omh-skill-scout"): "8e39141b6cb54da294799738304ffa76d49ff436caa6c44df52db6f7343d400e",
    ("omh", "ultrawork/ulw-plan"): "ad7f130b32e8333c3cbbf290ef3fb4131e7a2012298a10a82d91ffe746e23481",
    ("omh", "ultrawork/ulw-work"): "738485b872799d09e39d1bb9a4faead1e2794848615791cdae659f42bb259ebc",
    ("omh", "reviewer/omh-code-review"): "6043c0cbd886314d8577913727c0527c7cb50fe72677c6bded6f68dee74b8cd2",
    ("omh", "operator/omh-visual-qa"): "1cc3ed7024fb1093433d2eafd1dfd0950262c1b104aa14b0cd391c866d7b821e",
    ("omh", "reviewer/omh-native-debugging"): "109ebcb5eab82b2ceb646fca9e25068afc40b921ad15dd4cb118a1ffd216de4d",
    ("omh", "reviewer/omh-verification-gate"): "5ac11ff9c08bf1c5f5933daf659deb22046c927328d85bb1fb7adbd7b3af1583",
}
OMH_RAIL_SHA256: Final = "8762158b58981df3127c86bffc8172c91c4d371d16bca668feb76910460c4a19"
# Required companions per target root, pinned from the same trusted inventories (entrypoint and rail excluded).
COMPANIONS: Final[dict[tuple[str, str], frozenset[str]]] = {
    ("omo", "ulw-research"): frozenset({"ATTRIBUTION.md"}),
    ("omo", "ulw-plan"): frozenset({"agents/openai.yaml", "references/full-workflow.md", "references/intent-clear.md",
                                    "references/intent-unclear.md", "scripts/scaffold-plan.mjs"}),
    ("omo", "ulw-execute"): frozenset(),
    ("omo", "visual-qa"): frozenset({"AGENTS.md", "references/browser-setup.md", "scripts/visual-qa.mjs", "scripts/cli.ts",
                                     "scripts/ansi.ts", "scripts/east-asian-width.ts", "scripts/image-diff.ts",
                                     "scripts/png-crc.ts", "scripts/png-decode.ts", "scripts/png-synth.ts",
                                     "scripts/tui-grid.ts", "scripts/types.ts", "scripts/ansi.test.ts", "scripts/cli.test.ts",
                                     "scripts/east-asian-width.test.ts", "scripts/image-diff.test.ts",
                                     "scripts/png-decode.test.ts", "scripts/tui-grid.test.ts"}),
    ("omo", "debugging"): frozenset({
        *(f"references/methodology/{name}.md" for name in ("00-setup", "02-investigate", "03-flaky-triage",
                                                             "04-oracle-triple", "05-escalate", "06-fix", "08-qa",
                                                             "09-cleanup", "partial-runtime-evidence")),
        *(f"references/runtimes/{name}.md" for name in ("bundled-js-binary", "go", "native-binary", "node", "python", "rust")),
        *(f"references/tools/{name}.md" for name in ("dap", "frida", "ghidra", "playwright-cli", "pwndbg", "pwntools")),
        "references/scripts/dap.mjs", "references/scripts/dap.test.ts", "references/scripts/fixture-adapter.mjs"}),
    ("omo", "coding-agent-sessions"): frozenset({
        "AGENTS.md", "agents/openai.yaml", "scripts/find-agent-sessions.py",
        *(f"references/{name}.md" for name in ("all-platforms", "claude", "codex", "opencode", "senpi")),
        *(f"scripts/agent_sessions/{name}.py" for name in (
            "__init__", "aside_scanner", "claude", "cli", "codex", "file_scanners", "jsonio", "kiro_scanner",
            "opencode", "pi_family", "scanners", "sqlite_optional_scanners", "sqlite_scanners", "timeparse",
            "transcript", "types"))}),
    ("omh", "ultrawork/ulw-interview"): frozenset(),
    ("omh", "planner/omh-codebase-onboarding"): frozenset(),
    ("omh", "ultrawork/ulw-research"): frozenset({"references/briefing-format.md"}),
    ("omh", "operator/omh-skill-scout"): frozenset(),
    ("omh", "ultrawork/ulw-plan"): frozenset(),
    ("omh", "ultrawork/ulw-work"): frozenset({"references/campaign-orchestrator.md", "references/dependency-topology.md",
                                              "references/tdd-red-green.md"}),
    ("omh", "reviewer/omh-code-review"): frozenset({"references/review-dispatch.md", "references/review-response.md",
                                                    "references/smell-baseline.md"}),
    ("omh", "operator/omh-visual-qa"): frozenset({"references/visual-verdict-contract.md"}),
    ("omh", "reviewer/omh-native-debugging"): frozenset({"references/native-debug-loop.md"}),
    ("omh", "reviewer/omh-verification-gate"): frozenset(),
}
OMH_CANONICAL: Final = {
    "ultrawork/ulw-interview": "deep-interview",
    "planner/omh-codebase-onboarding": "codebase-onboarding",
    "ultrawork/ulw-research": "research",
    "operator/omh-skill-scout": "skill-scout",
    "ultrawork/ulw-plan": "ralplan",
    "ultrawork/ulw-work": "ultrawork",
    "reviewer/omh-code-review": "code-review",
    "operator/omh-visual-qa": "visual-qa",
    "reviewer/omh-native-debugging": "native-debugging",
    "reviewer/omh-verification-gate": "verification-gate",
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

    def test_every_native_target_carries_pinned_provenance(self) -> None:
        seen: set[tuple[str, str]] = set()
        for name, skill in self.doc["skills"].items():
            for target in skill["targets"]:
                with self.subTest(skill=name, selector=target["selector"]):
                    provenance = target["provenance"]
                    self.assertEqual(provenance["root_kind"], ROOT_KINDS[target["ecosystem"]])
                    self.assertEqual(provenance["files"][provenance["entrypoint"]],
                                     ENTRYPOINT_SHA256[(target["ecosystem"], target["selector"])])
                    seen.add((target["ecosystem"], target["selector"]))
        self.assertEqual(seen, set(ENTRYPOINT_SHA256))

    def test_omh_targets_share_one_rail_fingerprint(self) -> None:
        rails = {target["provenance"]["files"][OMH_RAIL]
                 for skill in self.doc["skills"].values()
                 for target in skill["targets"] if target["ecosystem"] == "omh"}
        self.assertEqual(rails, {OMH_RAIL_SHA256})

    def test_same_selector_targets_share_identical_provenance(self) -> None:
        by_selector: dict[tuple[str, str], list[Provenance]] = {}
        for skill in self.doc["skills"].values():
            for target in skill["targets"]:
                by_selector.setdefault((target["ecosystem"], target["selector"]), []).append(target["provenance"])
        for key, records in by_selector.items():
            with self.subTest(target=key):
                self.assertEqual(len({json.dumps(record, sort_keys=True) for record in records}), 1)

    def test_same_name_ulw_plan_targets_have_distinct_fingerprints(self) -> None:
        omo, omh = self.doc["skills"]["tk-plan"]["targets"]
        self.assertEqual((omo["provenance"]["root_kind"], omh["provenance"]["root_kind"]), ("package", "omh"))
        self.assertNotEqual(omo["provenance"]["files"][omo["provenance"]["entrypoint"]],
                            omh["provenance"]["files"][omh["provenance"]["entrypoint"]])
        self.assertEqual(omh["provenance"].get("canonical_name"), "ralplan")

    def _first_target(self, skill: str, index: int = 0) -> Target:
        return self.doc["skills"][skill]["targets"][index]

    def test_rejects_missing_provenance(self) -> None:
        loose: dict[str, Any] = json.loads(json.dumps(self.doc))
        del loose["skills"]["tk-plan"]["targets"][0]["provenance"]
        with self.assertRaisesRegex(AssertionError, "unqualified target"):
            validate_manifest(loose, self.skill_dirs)  # type: ignore[arg-type]

    def test_rejects_missing_shared_rail(self) -> None:
        target = self._first_target("tk-plan", 1)
        del target["provenance"]["files"][OMH_RAIL]
        with self.assertRaisesRegex(AssertionError, "omh shared rail missing"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_missing_entrypoint_fingerprint(self) -> None:
        provenance = self._first_target("tk-plan")["provenance"]
        del provenance["files"][provenance["entrypoint"]]
        with self.assertRaisesRegex(AssertionError, "provenance entrypoint fingerprint missing"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_relocated_entrypoint(self) -> None:
        self._first_target("tk-plan")["provenance"]["entrypoint"] = "dist/skills/ulw-plan/README.md"
        with self.assertRaisesRegex(AssertionError, "provenance entrypoint location"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_missing_companion(self) -> None:
        provenance = self._first_target("tk-plan")["provenance"]
        del provenance["files"]["dist/skills/ulw-plan/references/full-workflow.md"]
        with self.assertRaisesRegex(AssertionError, "frozen companion set mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_escaping_paths(self) -> None:
        for path in ("../dist/skills/ulw-plan/x.md", "/dist/skills/ulw-plan/x.md", "dist/skills/ulw-plan/./x.md",
                     "dist\\skills\\ulw-plan\\x.md", "dist/skills/ulw-plan/x:y.md", "dist/skills/ulw-plan/\x01.md"):
            with self.subTest(path=path):
                doc = copy.deepcopy(self.doc)
                doc["skills"]["tk-plan"]["targets"][0]["provenance"]["files"][path] = "0" * 64
                with self.assertRaisesRegex(AssertionError, "escaping provenance path"):
                    validate_manifest(doc, self.skill_dirs)

    def test_rejects_companion_outside_skill_root(self) -> None:
        self._first_target("tk-plan")["provenance"]["files"]["dist/skills/ulw-research/SKILL.md"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "companion outside skill root"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_malformed_fingerprints(self) -> None:
        for digest in ("", None, "0" * 63, "G" * 64, "sha256:" + "0" * 64, "0" * 64 + "\n"):
            with self.subTest(digest=digest):
                doc = copy.deepcopy(self.doc)
                provenance = doc["skills"]["tk-plan"]["targets"][0]["provenance"]
                provenance["files"][provenance["entrypoint"]] = digest  # type: ignore[assignment]
                with self.assertRaisesRegex(AssertionError, "malformed provenance fingerprint"):
                    validate_manifest(doc, self.skill_dirs)

    def test_rejects_drifted_entrypoint_fingerprint(self) -> None:
        provenance = self._first_target("tk-plan")["provenance"]
        provenance["files"][provenance["entrypoint"]] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "provenance entrypoint fingerprint mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_swapped_root_kind(self) -> None:
        self._first_target("tk-plan")["provenance"]["root_kind"] = "omh"
        with self.assertRaisesRegex(AssertionError, "provenance root_kind mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_omo_target_with_omh_canonical_name(self) -> None:
        self._first_target("tk-plan")["provenance"]["canonical_name"] = "ralplan"
        with self.assertRaisesRegex(AssertionError, "provenance shape"):
            validate_manifest(self.doc, self.skill_dirs)

    def test_rejects_omh_display_label_as_canonical_name(self) -> None:
        self._first_target("tk-plan", 1)["provenance"]["canonical_name"] = "ulw-plan"
        with self.assertRaisesRegex(AssertionError, "omh canonical name mismatch"):
            validate_manifest(self.doc, self.skill_dirs)

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
