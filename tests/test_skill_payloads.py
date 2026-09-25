#!/usr/bin/env python3
"""Exercise self-contained skill payloads through the materializer CLI."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from itertools import product
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from payload_fixtures import (EXPECTED, FIXTURE_SKILLS, IMPORT_PROBE, INVALID_REGISTRIES, MANAGED_PATHS,
                              OWNED_SKILLS, REFERENCES, REGISTRY_PATH_CASES, ROOT, RUNTIME_ASSETS, SOURCE_CASES, TOOL,
                              Damage, JsonObject, PayloadFixture, config, damage, registered_skills, snapshot)


class SkillPayloadTests(PayloadFixture):
    def test_real_tree_when_checked_from_an_unrelated_directory(self) -> None:
        # Given the installed payload inventory, not a generated test fixture.
        skills = registered_skills()
        self.assertEqual((len(skills), len(EXPECTED)), (19, 8))
        self.assertEqual({path.parent.name for path in (ROOT / "skills").glob("tk-*/SKILL.md")}, set(skills))
        module = self.load_module(TOOL)
        self.assertEqual(tuple(module.MANAGED_FILES), EXPECTED)
        self.assertEqual(tuple(module.MANIFEST),
                         tuple((f"skills/references/{Path(asset).name}", asset) for asset in EXPECTED))
        before = snapshot(ROOT / "skills")
        # When the CLI derives its root from __file__ rather than cwd.
        result = self.cli(check=True)
        # Then all 152 copies are current, byte-exact, and the inventory is unchanged.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("152/152", result.stdout)
        self.assertEqual(snapshot(ROOT / "skills"), before)
        for name in skills:
            skill = ROOT / "skills" / name
            self.assert_inventory(skill)
            for asset in EXPECTED:
                self.assertEqual((skill / asset).read_bytes(), (REFERENCES / Path(asset).name).read_bytes())

    def test_generation_when_repeated_is_byte_exact_and_changes_nothing(self) -> None:
        # Given two skills and entries that must not be discovered as skills.
        root = self.make_repo()
        (root / "skills" / "tk-unmarked").mkdir()
        (root / "skills" / "notes.txt").write_bytes(b"not a skill")
        (root / "skills" / "references" / "SKILL.md").write_bytes(b"not a skill")
        (root / "skills" / "notes").mkdir()
        (root / "skills" / "notes" / "SKILL.md").write_bytes(b"not a tk skill")
        (root / "skills" / "alias").symlink_to(root, target_is_directory=True)
        self.generate(root)
        before = snapshot(root)
        # When generation runs again.
        result = self.cli(root)
        # Then bytes, paths, permissions, and modification times stay unchanged.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 skills", result.stdout)
        self.assertEqual(snapshot(root), before)
        for name in FIXTURE_SKILLS:
            skill = root / "skills" / name
            self.assertEqual({str(path.relative_to(skill)) for path in skill.rglob("*")
                              if path.is_file()}, {"SKILL.md", *EXPECTED})
            for relative in EXPECTED:
                self.assertEqual((skill / relative).read_bytes(),
                                 (root / "skills" / "references" / Path(relative).name).read_bytes())
        self.assertEqual(self.cli(root, check=True).returncode, 0)

    def test_check_when_copies_are_corrupt_or_missing_lists_every_path_without_writes(self) -> None:
        # Given several damaged managed copies across both skills.
        root = self.make_repo()
        self.generate(root)
        paths = [root / "skills" / name / asset for name, asset in product(FIXTURE_SKILLS, EXPECTED)]
        for path in paths:
            damage(path, "corrupt" if path.parent.parent.name == "tk-x" else "missing")
        before = snapshot(root)
        # When check observes every damaged or absent asset.
        result = self.cli(root, check=True)
        # Then it reports all sixteen paths and does not repair or create anything.
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for path in paths:
            self.assertIn(str(path.relative_to(root)), result.stdout + result.stderr)
        self.assertEqual(snapshot(root), before)

    def test_check_when_unmaterialized_does_not_create_directories(self) -> None:
        # Given a repository containing only canonical sources and skill markers.
        root = self.make_repo()
        before = snapshot(root)
        # When checking an entirely missing payload.
        result = self.cli(root, check=True)
        # Then all sixteen missing paths are listed without any filesystem writes.
        self.assertEqual(result.returncode, 1)
        for name in FIXTURE_SKILLS:
            for relative in EXPECTED:
                self.assertIn(f"skills/{name}/{relative}", result.stdout + result.stderr)
        self.assertEqual(snapshot(root), before)

    def test_check_when_only_one_copy_is_missing_fails_without_repair(self) -> None:
        # Given one missing copy in an otherwise current payload.
        root = self.make_repo()
        self.generate(root)
        missing = root / "skills" / "tk-x" / EXPECTED[0]
        missing.unlink()
        before = snapshot(root)
        # When checking the single defect.
        result = self.cli(root, check=True)
        # Then the missing path is reported without repair.
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(missing.relative_to(root)), result.stdout + result.stderr)
        self.assertEqual(snapshot(root), before)

    def test_generation_when_one_copy_is_stale_repairs_only_that_copy(self) -> None:
        # Given stale bytes beside current copies.
        root = self.make_repo()
        self.generate(root)
        stale = root / "skills" / "tk-x" / EXPECTED[0]
        canonical = root / "skills" / "references" / stale.name
        stale.write_bytes(b"stale")
        other = root / "skills" / "tk-test"
        before = snapshot(other)
        # When regenerating the payload.
        self.generate(root)
        # Then the supplied root's bytes are restored and the other skill stays untouched.
        self.assertEqual(stale.read_bytes(), canonical.read_bytes())
        self.assertEqual(snapshot(other), before)
        self.assertEqual(self.cli(root, check=True).returncode, 0)

    def test_missing_canonical_source_when_generating_or_checking_refuses_before_writes(self) -> None:
        for index, ((asset, kind), check) in enumerate(product(SOURCE_CASES, (False, True))):
            with self.subTest(asset=asset, kind=kind, check=check):
                # Given a missing or unreadable canonical input, including the last asset.
                root = self.make_repo(f"source-{index}")
                source = root / "skills" / "references" / Path(asset).name
                damage(source, kind)
                before = snapshot(root)
                # When either mode preflights the complete source set.
                result = self.cli(root, check=check)
                # Then a named error is returned without partially materializing skills.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(source), result.stdout + result.stderr)
                self.assertEqual(snapshot(root), before)

    def test_symlinks_when_present_on_any_managed_path_are_refused(self) -> None:
        for index, (relative, contained, check) in enumerate(product(MANAGED_PATHS, (False, True), (False, True))):
            with self.subTest(path=relative, contained=contained, check=check):
                # Given a contained or escaping link on a canonical or managed path.
                root = self.make_repo(f"case-{index}")
                link = self.symlinked_path(root, (relative, contained))
                target = link.resolve()
                before = snapshot(root), snapshot(target)
                # When either mode inspects the managed paths.
                result = self.cli(root, check=check)
                # Then no target, earlier skill, or source is changed.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr.lower())
                self.assertIn(str(link), result.stderr)
                self.assertEqual((snapshot(root), snapshot(target)), before)

    def test_symlinked_root_when_dotdot_is_present_is_not_normalized_away(self) -> None:
        # Given an alias followed by .. in the supplied root path.
        root = self.make_repo()
        link = self.sandbox / "alias"
        link.symlink_to(root, target_is_directory=True)
        before = snapshot(self.sandbox)
        # When the root contains an otherwise hidden symlink component.
        result = self.cli(link / ".." / root.name)
        # Then the original path is rejected rather than resolved before inspection.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())
        self.assertEqual(snapshot(self.sandbox), before)

    def test_unmanaged_files_when_generating_are_untouched_and_not_reported(self) -> None:
        # Given the real tk-test script beside an unrelated reference file.
        root = self.make_repo()
        script = root / "skills" / "tk-test" / "scripts" / "tk-test.py"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "skills" / "tk-test" / "scripts" / "tk-test.py", script)
        note = root / "skills" / "tk-test" / "references" / "custom.json"
        note.parent.mkdir()
        note.write_bytes(b"unmanaged")
        before = {path: snapshot(path) for path in (script, note)}
        # When generation writes the allowlisted files beside them.
        self.generate(root)
        result = self.cli(root, check=True)
        # Then neither unmanaged file is changed or reported as extra.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path, state in before.items():
            self.assertEqual(snapshot(path), state)
            self.assertNotIn(path.name, result.stdout + result.stderr)

    def test_relocated_payload_when_resolving_needs_no_repository_context(self) -> None:
        for name in registered_skills():
            with self.subTest(skill=name):
                # Given only this skill, local inputs and an empty peer inventory.
                skill = self.isolated_skill(name)
                argv = self.resolver_arguments(skill)
                before = snapshot(skill.parent)
                # When its real CLI runs with empty PYTHONPATH and an explicit project root.
                result = self.run_cli(argv, skill.parent)
                # Then the exact safe outcome is owned or peer-missing, never delegation.
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                decision: JsonObject = json.loads(result.stdout)
                expected = ("owned", "owned_policy") if name in OWNED_SKILLS else ("fallback", "peer_missing")
                self.assertEqual((decision["skill"], decision["decision"], decision["reason_code"]), (name, *expected))
                self.assertEqual(snapshot(skill.parent), before)

    def test_relocated_model_config_when_imported_normalizes_from_local_catalog(self) -> None:
        # Given a standalone helper and the catalog from the same copied skill.
        skill = self.isolated_skill()
        raw = config()
        before = snapshot(self.sandbox)
        # When a fresh process imports the local runtime and normalizes against its catalog.
        result = self.run_cli([sys.executable, "-S", "-c", IMPORT_PROBE, str(skill), json.dumps(raw)], skill.parent)
        # Then canonical classes and defaults require no repository imports or writes.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        imported: JsonObject = json.loads(result.stdout)
        self.assertEqual(imported["normalized"], dict(raw, review_families_min=2, max_layers=3,
                                                   frozen_paths=[], ecosystems=["omo", "omh"], delegation="auto"))
        self.assertEqual(imported["warnings"], [])
        self.assertIs(imported["bytecode_policy"], False)
        self.assertEqual(imported["module_paths"], [str(skill / "scripts")] * 2)
        self.assertEqual(snapshot(self.sandbox), before)

    def test_registered_inventory_when_markers_disagree_refuses_before_writes(self) -> None:
        for index, ((relative, kind), check) in enumerate(product(REGISTRY_PATH_CASES, (False, True))):
            with self.subTest(path=relative, kind=kind, check=check):
                # Given a rogue marker, absent registered skill or invalid marker.
                root = self.make_repo(f"registry-{index}")
                damage(root / relative, kind)
                before = snapshot(root)
                # When generation or check compares markers with this root's registry.
                result = self.cli(root, check)
                # Then discovery fails before any managed output is written.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(root / relative), result.stderr)
                self.assertEqual(snapshot(root), before)

    def test_registry_when_malformed_refuses_before_writes(self) -> None:
        for index, (content, check) in enumerate(product(INVALID_REGISTRIES, (False, True))):
            with self.subTest(content=content, check=check):
                # Given invalid shape, duplicate keys or a nonportable skill name.
                root = self.make_repo(f"json-{index}")
                registry = root / "skills/references/dependencies.json"
                registry.write_bytes(content)
                before = snapshot(root)
                # When the canonical registry is parsed.
                result = self.cli(root, check)
                # Then the invalid registry is named and no payload is partially generated.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(registry), result.stderr)
                self.assertEqual(snapshot(root), before)

    def test_nonregular_managed_paths_when_present_refuse_before_writes(self) -> None:
        for index, (relative, fifo, check) in enumerate(product(MANAGED_PATHS, (False, True), (False, True))):
            with self.subTest(path=relative, fifo=fifo, check=check):
                # Given a FIFO or a file/directory in the wrong role.
                root = self.make_repo(f"type-{index}")
                kind: Damage = "fifo" if fifo else "directory" if Path(relative).suffix else "file"
                damage(root / relative, kind)
                before = snapshot(root)
                # When either mode preflights the managed tree.
                result = self.cli(root, check)
                # Then it refuses without reading a FIFO or changing earlier destinations.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(root / relative), result.stderr)
                self.assertEqual(snapshot(root), before)

    def test_manifest_when_paths_are_outside_allowlist_refuses_before_writes(self) -> None:
        module = self.load_module(TOOL)
        for index, pair in enumerate((("skills/references/../references/models.json", EXPECTED[0]),
                                      ("skills/references/models.json", "references/unknown.json"),
                                      ("skills/references/models.json", "../escape.json"))):
            with self.subTest(pair=pair):
                # Given a source alias, unknown output or escaping destination in the mapping.
                root = self.make_repo(f"mapping-{index}")
                before = snapshot(root)
                # When the callable CLI consumes that mapping.
                with (patch.object(module, "MANIFEST", (pair, *module.MANIFEST[1:])),
                      redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO())):
                    result = module.main(["--root", str(root)])
                # Then no unexpected path is produced, including before the failure.
                self.assertNotEqual(result, 0)
                self.assertEqual(snapshot(root), before)

    def test_generated_resolver_when_no_peers_runs_without_repository_imports(self) -> None:
        # Given a small registry and payload produced by the actual CLI.
        root = self.make_repo()
        self.generate(root)
        argv = self.resolver_arguments(root / "skills/tk-test")
        before = snapshot(root)
        # When its resolver loads the generated sibling scripts.
        result = self.run_cli(argv)
        # Then it computes the registered owned operation, without external imports or writes.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        decision: JsonObject = json.loads(result.stdout)
        self.assertEqual((decision["decision"], decision["reason_code"]), ("owned", "owned_policy"))
        self.assertEqual(snapshot(root), before)

    def test_relocated_support_when_missing_or_corrupt_never_repairs_from_parents(self) -> None:
        for asset, missing in product(RUNTIME_ASSETS, (False, True)):
            with self.subTest(asset=asset, missing=missing):
                # Given broken local support with valid decoys in a parent and fake home.
                skill = self.isolated_skill()
                self.shadow_support(skill)
                damage(skill / asset, "missing" if missing else "corrupt")
                argv = self.resolver_arguments(skill)
                before = snapshot(self.sandbox)
                # When the isolated resolver attempts to load its local contract.
                result = self.run_cli(argv, skill.parent)
                # Then it fails closed, names the asset, and leaves all locations unchanged.
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(Path(asset).stem, result.stdout + result.stderr)
                self.assertEqual(snapshot(self.sandbox), before)


if __name__ == "__main__":
    unittest.main()
