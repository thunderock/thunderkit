"""Validate the pinned, qualified native-peer manifest without external packages."""

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Final
from unittest.mock import patch

from dependency_contract import (
    JsonObject, JsonValue, json_array, json_object, json_string,
    validate_manifest, validate_provenance, validate_role,
)
from dependency_expectations import (
    COMPANIONS, ENTRYPOINT_SHA256, NATIVE_ROLES, OMH_CANONICAL, OMH_RAIL,
    OMH_RAIL_SHA256, PINS, ROOT_KINDS, SINGLE_CLASS,
)

ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "skills/references/dependencies.json"


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # Given: a fresh mutable JSON fixture, independent of other cases.
        self.doc = json_object(json.loads(MANIFEST.read_text(encoding="utf-8")))
        self.skills = json_object(self.doc["skills"])
        self.peers = json_object(self.doc["ecosystems"])
        self.skill_dirs = {path.name for path in (ROOT / "skills").glob("tk-*") if path.is_dir()}
        self.plan = self._first_target("tk-plan")
        self.provenance = json_object(self.plan["provenance"])
        self.files = json_object(self.provenance["files"])
        self.entrypoint = json_string(self.provenance["entrypoint"])

    def _targets(self, skill: str) -> list[JsonObject]:
        return [json_object(item) for item in json_array(json_object(self.skills[skill])["targets"])]

    def _first_target(self, skill: str, index: int = 0) -> JsonObject:
        return self._targets(skill)[index]

    def assert_invalid(self, reason: str) -> None:
        # When / Then: validating the changed fixture must reject the named violation.
        with self.assertRaisesRegex(AssertionError, reason):
            validate_manifest(self.doc, self.skill_dirs)

    def test_manifest(self) -> None:
        validate_manifest(self.doc, self.skill_dirs)

    def test_roles_match_skill_frontmatter(self) -> None:
        for name, skill in self.skills.items():
            with self.subTest(skill=name):
                text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                validate_role(text, json_string(json_object(skill)["role"]))

    def test_same_name_planners_resolve_to_distinct_packages(self) -> None:
        targets = self._targets("tk-plan")
        packages = {json_string(json_object(self.peers[json_string(t["ecosystem"])])["package"]) for t in targets}
        self.assertEqual([target["skill_name"] for target in targets], ["ulw-plan", "ulw-plan"])
        self.assertEqual(packages, {"oh-my-openagent", "oh-my-hermes"})

    def test_every_native_target_carries_pinned_provenance(self) -> None:
        seen: set[tuple[str, str]] = set()
        for name in self.skills:
            for target in self._targets(name):
                key = (json_string(target["ecosystem"]), json_string(target["selector"]))
                with self.subTest(skill=name, target=key):
                    provenance = json_object(target["provenance"])
                    self.assertEqual(provenance["root_kind"], ROOT_KINDS[key[0]])
                    self.assertEqual(json_object(provenance["files"])[json_string(provenance["entrypoint"])],
                                     ENTRYPOINT_SHA256[key])
                    seen.add(key)
        self.assertEqual(seen, set(ENTRYPOINT_SHA256))

    def test_omh_targets_share_one_rail_fingerprint(self) -> None:
        rails = {json_string(json_object(json_object(t["provenance"])["files"])[OMH_RAIL])
                 for name in self.skills for t in self._targets(name) if t["ecosystem"] == "omh"}
        self.assertEqual(rails, {OMH_RAIL_SHA256})

    def test_same_selector_targets_share_identical_provenance(self) -> None:
        by_selector: dict[tuple[str, str], set[str]] = {}
        for name in self.skills:
            for target in self._targets(name):
                key = (json_string(target["ecosystem"]), json_string(target["selector"]))
                by_selector.setdefault(key, set()).add(json.dumps(target["provenance"], sort_keys=True))
        for key, records in by_selector.items():
            with self.subTest(target=key):
                self.assertEqual(len(records), 1)

    def test_same_name_ulw_plan_targets_have_distinct_fingerprints(self) -> None:
        omh = self._first_target("tk-plan", 1)
        provenance = json_object(omh["provenance"])
        self.assertEqual((self.provenance["root_kind"], provenance["root_kind"]), ("package", "omh"))
        self.assertNotEqual(self.files[self.entrypoint], json_object(provenance["files"])[json_string(provenance["entrypoint"])])
        self.assertEqual(omh.get("canonical_name"), "ralplan")

    def test_rejects_missing_provenance(self) -> None:
        del self.plan["provenance"]
        self.assert_invalid("unqualified target")

    def test_rejects_missing_shared_rail(self) -> None:
        del json_object(json_object(self._first_target("tk-plan", 1)["provenance"])["files"])[OMH_RAIL]
        self.assert_invalid("omh shared rail missing")

    def test_rejects_missing_entrypoint_fingerprint(self) -> None:
        del self.files[self.entrypoint]
        self.assert_invalid("provenance entrypoint fingerprint missing")

    def test_rejects_relocated_entrypoint(self) -> None:
        self.provenance["entrypoint"] = "dist/skills/ulw-plan/README.md"
        self.assert_invalid("provenance entrypoint location")

    def test_rejects_missing_companion(self) -> None:
        del self.files["dist/skills/ulw-plan/references/full-workflow.md"]
        self.assert_invalid("frozen companion set mismatch")

    def test_rejects_escaping_paths(self) -> None:
        for path in ("../dist/skills/ulw-plan/x.md", "/dist/skills/ulw-plan/x.md", "dist/skills/ulw-plan/./x.md",
                     "dist\\skills\\ulw-plan\\x.md", "dist/skills/ulw-plan/x:y.md", "dist/skills/ulw-plan/\x01.md"):
            with self.subTest(path=path), patch.dict(self.files, {path: "0" * 64}):
                self.assert_invalid("escaping provenance path")

    def test_rejects_unknown_companion(self) -> None:
        for path in ("dist/skills/ulw-research/SKILL.md", "dist/skills/ulw-plan/unknown.md"):
            with self.subTest(path=path), patch.dict(self.files, {path: "0" * 64}):
                self.assert_invalid("frozen companion set mismatch")

    def test_rejects_malformed_fingerprints(self) -> None:
        for digest in ("", None, "0" * 63, "G" * 64, "sha256:" + "0" * 64, "0" * 64 + "\n"):
            with self.subTest(digest=digest):
                self.files[self.entrypoint] = digest
                self.assert_invalid("malformed provenance fingerprint")

    def test_rejects_drifted_entrypoint_fingerprint(self) -> None:
        self.files[self.entrypoint] = "0" * 64
        self.assert_invalid("provenance entrypoint fingerprint mismatch")

    def test_rejects_swapped_root_kind(self) -> None:
        self.provenance["root_kind"] = "omh"
        self.assert_invalid("provenance root_kind mismatch")

    def test_rejects_omo_target_with_omh_canonical_name(self) -> None:
        for container in (self.provenance, self.plan):
            with self.subTest(container=list(container)), patch.dict(container, canonical_name="ralplan"):
                self.assert_invalid("provenance shape|canonical name mismatch")

    def test_rejects_omh_display_label_as_canonical_name(self) -> None:
        self._first_target("tk-plan", 1)["canonical_name"] = "ulw-plan"
        self.assert_invalid("omh canonical name mismatch")

    def test_rejects_swapped_peer_records(self) -> None:
        self.peers["omo"], self.peers["omh"] = self.peers["omh"], self.peers["omo"]
        self.assert_invalid("peer pin mismatch")

    def test_rejects_swapped_target_ecosystem(self) -> None:
        self.plan["ecosystem"] = "omh"
        self.assert_invalid("selector ecosystem mismatch")

    def test_rejects_unpinned_versions(self) -> None:
        for ecosystem in PINS:
            with self.subTest(ecosystem=ecosystem), patch.dict(json_object(self.peers[ecosystem]), version="latest"):
                self.assert_invalid("peer pin mismatch")

    def test_rejects_duplicate_target(self) -> None:
        json_array(json_object(self.skills["tk-plan"])["targets"]).append(copy.deepcopy(self.plan))
        self.assert_invalid("duplicate qualified target")

    def test_rejects_unqualified_duplicate_target(self) -> None:
        target = copy.deepcopy(self.plan)
        target["ecosystem"] = ""
        json_array(json_object(self.skills["tk-plan"])["targets"]).append(target)
        self.assert_invalid("ineligible target ecosystem")

    def test_rejects_excluded_target(self) -> None:
        self.plan["ecosystem"] = json_string(json_array(self.doc["excluded"])[0]).upper()
        self.assert_invalid("ineligible target ecosystem")

    def test_rejects_excluded_fallback(self) -> None:
        for excluded in json_array(self.doc["excluded"]):
            with self.subTest(excluded=excluded):
                json_object(self.skills["tk-docs"])["fallback"] = json_string(excluded).upper()
                self.assert_invalid("excluded reference")

    def test_rejects_excluded_install_hint(self) -> None:
        for excluded in json_array(self.doc["excluded"]):
            with self.subTest(excluded=excluded):
                json_object(self.peers["omo"])["install_hint"] = json_string(excluded).upper()
                self.assert_invalid("excluded reference")

    def test_rejects_peer_root_drift(self) -> None:
        root = json_object(json_object(self.peers["omh"])["provenance_root"])
        for key, value in (("root_kind", "skills_root"), ("identity_file", "../manifest.json"),
                           ("entrypoint_pattern", "<category>/<skill_name>/SKILL.md")):
            with self.subTest(field=key), patch.dict(root, {key: value}):
                self.assert_invalid("peer provenance root mismatch")

    def test_omh_canonical_identity_is_target_metadata(self) -> None:
        for name in self.skills:
            for target in self._targets(name):
                with self.subTest(skill=name, selector=target["selector"]):
                    self.assertEqual(target.get("canonical_name"), OMH_CANONICAL.get(json_string(target["selector"])))
                    self.assertEqual(set(json_object(target["provenance"])), {"root_kind", "entrypoint", "files"})

    def test_rejects_missing_or_misplaced_canonical_names(self) -> None:
        target = self._first_target("tk-plan", 1)
        for at_target, at_provenance in ((False, False), (False, True), (True, True)):
            with self.subTest(target=at_target, provenance=at_provenance), patch.dict(target, copy.deepcopy(target), clear=True):
                target.pop("canonical_name", None)
                provenance = json_object(target["provenance"])
                provenance.pop("canonical_name", None)
                if at_target:
                    target["canonical_name"] = "ralplan"
                if at_provenance:
                    provenance["canonical_name"] = "ralplan"
                self.assert_invalid("canonical|provenance shape")

    def test_native_roles_and_binding_classes_are_exact(self) -> None:
        for name in self.skills:
            for target in self._targets(name):
                key = (json_string(target["ecosystem"]), json_string(target["selector"]))
                roles = NATIVE_ROLES.get(key)
                expected = set(roles.values()) if roles else {SINGLE_CLASS[name]} if name in SINGLE_CLASS else set()
                with self.subTest(skill=name, target=key):
                    self.assertEqual(target.get("native_roles"), roles)
                    self.assertEqual({json_string(cap).removeprefix("model-binding:") for cap in json_array(target["requires"])
                                      if json_string(cap).startswith("model-binding:")}, expected)

    def test_rejects_joint_role_and_requirement_removal(self) -> None:
        for name in ("tk-plan", "tk-execute"):
            for target in self._targets(name):
                roles = NATIVE_ROLES[(json_string(target["ecosystem"]), json_string(target["selector"]))]
                for model_class in set(roles.values()):
                    with self.subTest(skill=name, peer=target["ecosystem"], model_class=model_class), patch.dict(target, copy.deepcopy(target), clear=True):
                        actual = json_object(target.get("native_roles", {}))
                        for slot in (slot for slot, bound in roles.items() if bound == model_class):
                            actual.pop(slot, None)
                        target["requires"] = [cap for cap in json_array(target["requires"]) if cap != f"model-binding:{model_class}"]
                        self.assert_invalid("native role map mismatch|model binding class mismatch")

    def test_rejects_missing_binding_requirements(self) -> None:
        for name in self.skills:
            for target in self._targets(name):
                requires = json_array(target["requires"])
                for cap in (cap for cap in requires if json_string(cap).startswith("model-binding:")):
                    with self.subTest(skill=name, peer=target["ecosystem"], cap=cap), patch.dict(target, requires=[c for c in requires if c != cap]):
                        self.assert_invalid("model binding class mismatch")

    def test_rejects_malformed_native_roles(self) -> None:
        cases: tuple[JsonValue, ...] = (None, [], {}, {"root": "reviewers"}, {"root": "planner", "unknown": "executors"})
        for roles in cases:
            with self.subTest(roles=roles), patch.dict(self.plan, {"native_roles": roles}):
                self.assert_invalid("native role map mismatch")

    def test_accepts_declared_peer_root_relative_companion(self) -> None:
        provenance = json_object(self._first_target("tk-execute")["provenance"])
        companion = "dist/skills/ulw-research/SKILL.md"
        json_object(provenance["files"])[companion] = ENTRYPOINT_SHA256[("omo", "ulw-research")]
        with patch.dict(COMPANIONS, {("omo", "ulw-execute"): frozenset({companion})}):
            validate_provenance("omo", "ulw-execute", provenance)

    def test_rejects_malformed_json_shapes(self) -> None:
        mutations: tuple[tuple[str, JsonValue], ...] = (("ecosystems", None), ("skills", False), ("distribution_cli", []))
        for key, value in mutations:
            with self.subTest(field=key), patch.dict(self.doc, {key: value}):
                self.assert_invalid("expected JSON object")

    def test_accepts_flat_and_legacy_headers(self) -> None:
        for metadata in ('  thunderkit-role: "planner"\n', "  thunderkit:\n    role: planner\n    tier: plan\n"):
            text = "---\nname: tk-plan\ndescription: Plan work.\nmetadata:\n" + metadata + "---\n"
            with self.subTest(metadata=metadata):
                validate_role(text + "metadata:\n  thunderkit:\n    role: executor\n", "planner")

    def test_rejects_role_mismatches_and_body_lookalikes(self) -> None:
        for header in ("", 'metadata:\n  thunderkit-role: "executor"\n', "metadata:\n  thunderkit:\n    role: executor\n"):
            text = "---\nname: tk-plan\ndescription: Plan work.\n" + header + "---\n"
            with self.subTest(header=header), self.assertRaisesRegex(AssertionError, "skill role mismatch"):
                validate_role(text + "metadata:\n  thunderkit:\n    role: planner\n", "planner")

    def test_decision_example_preserves_model_selections(self) -> None:
        text = (ROOT / "skills/references/delegation.md").read_text(encoding="utf-8")
        block = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
        assert block is not None
        record = json_object(json.loads(block[1]))
        bindings = json_object(record["bindings"])
        requested = json_object(bindings["requested"])
        checks: dict[str, tuple[JsonValue, JsonValue]] = {
            "schema_version": (record.get("schema_version"), 1),
            "planner": (requested.get("planner"), "opus48"),
            "executors": (requested.get("executors"), ["fable51", "opus5"]),
            "reviewers": (requested.get("reviewers"), ["sol", "opus5"]),
            "observed": (bindings.get("observed"), None),
        }
        for field, (actual, expected) in checks.items():
            with self.subTest(field=field):
                self.assertEqual(actual, expected)
        self.assertEqual(set(record), {"schema_version", "skill", "operation", "decision", "reason_code", "detail",
                                       "target", "bindings", "runtime_home", "evidence_paths"})


if __name__ == "__main__":
    unittest.main()
