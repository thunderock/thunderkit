"""Validate the pinned, qualified native-peer manifest without external packages."""

import copy
import json
import unittest
from pathlib import Path
from typing import Any, Final

from dependency_contract import Manifest, Provenance, Target, validate_manifest, validate_role
from dependency_expectations import ENTRYPOINT_SHA256, OMH_RAIL, OMH_RAIL_SHA256, PINS, ROOT_KINDS


ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "skills/references/dependencies.json"


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
                validate_role(text, skill["role"])

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
