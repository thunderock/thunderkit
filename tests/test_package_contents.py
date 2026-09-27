"""Check package readiness against real offline tarballs and relocated entry points."""
from __future__ import annotations

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from typing import Final, Literal, assert_never
import unittest

from payload_fixtures import EXPECTED, OWNED_SKILLS, ROOT, PayloadFixture, registered_skills, snapshot
from resolution_fixtures import mapping, read_json, sequence, text, write_json
import validate_frontmatter

ROOT_FILES: Final = ("package.json", "README.md", "LICENSE", "NORTH_STAR.md", "DEPENDENCIES.md")
CANONICAL: Final = {f"skills/references/{Path(asset).name}" for asset in EXPECTED}
OWNED: Final = {"bin/thunderkit.js", "skills/tk-test/scripts/tk-test.py",
                "skills/tk-test/scripts/preflight_protocols.py"}
CONTENTS: Final = {*(f"package/{name}" for name in (*ROOT_FILES, *CANONICAL, *OWNED)),
                   *(f"package/skills/{skill}/{name}" for skill in registered_skills()
                     for name in ("SKILL.md", *EXPECTED))}


class DevelopmentInventoryTests(PayloadFixture):
    def test_inventory_when_disposable_bytecode_exists_preserves_it(self) -> None:
        # Given development caches alongside the exact installed payload.
        skill = self.isolated_skill("tk-test")
        for name in ("scripts/__pycache__/tk-test.cpython-311.pyc", "scripts/tk-test.pyc"):
            cache = skill / name
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(b"preserved development cache")
        before = snapshot(skill)
        # When checking development source inventory.
        self.assert_inventory(skill)
        # Then disposable bytecode is tolerated without changing any bytes or metadata.
        self.assertEqual(snapshot(skill), before)

    def test_inventory_when_unexpected_files_exist_rejects_them(self) -> None:
        for name in ("references/unexpected.md", ".omo/notes.md", "scripts/__pycache__/notes.md",
                     "scripts/helper.pyc.txt"):
            with self.subTest(path=name):
                # Given an unexpected file, even under an ignored development directory.
                skill = self.isolated_skill("tk-test")
                unexpected = skill / name
                unexpected.parent.mkdir(parents=True, exist_ok=True)
                unexpected.write_bytes(b"not disposable bytecode")
                # When checking inventory, then arbitrary extra files still fail.
                with self.assertRaises(AssertionError):
                    self.assert_inventory(skill)


class PackageContentsTests(PayloadFixture):
    def setUp(self) -> None:
        super().setUp()
        node, npm = shutil.which("node"), shutil.which("npm")
        assert node is not None, "Node is required for package tests"
        assert npm is not None, "npm is required for real offline package tests"
        self.node, self.npm = node, npm
        self.env.update(PATH=os.pathsep.join((str(Path(npm).parent), str(Path(node).parent), os.defpath)),
                        NPM_CONFIG_USERCONFIG=str(self.sandbox / "user.npmrc"),
                        NPM_CONFIG_GLOBALCONFIG=str(self.sandbox / "global.npmrc"),
                        NPM_CONFIG_CACHE=str(self.sandbox / "npm-cache"),
                        NPM_CONFIG_OFFLINE="true", NPM_CONFIG_IGNORE_SCRIPTS="true",
                        NPM_CONFIG_AUDIT="false", NPM_CONFIG_FUND="false", NPM_CONFIG_UPDATE_NOTIFIER="false")
        self.source = self.sandbox / "source"
        self.source.mkdir()
        for name in (*ROOT_FILES, ".npmignore", ".gitignore", "Makefile"):
            shutil.copyfile(ROOT / name, self.source / name)
        for name in ("skills", "bin"):
            shutil.copytree(ROOT / name, self.source / name)

    def pack(self, root: Path = ROOT) -> Path:
        destination = Path(tempfile.mkdtemp(prefix="pack-", dir=self.sandbox))
        result = self.run_cli([self.npm, "pack", "--offline", "--ignore-scripts", "--json",
                               "--pack-destination", str(destination)], root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list(destination.glob("*.tgz"))
        self.assertEqual(len(archives), 1)
        return archives[0]

    def unpack(self, archive: Path, source: Path = ROOT) -> Path:
        destination = Path(tempfile.mkdtemp(prefix="unpack-", dir=self.sandbox))
        with tarfile.open(archive, "r:gz") as packed:
            members = packed.getmembers()
            self.assertEqual(len(members), len(CONTENTS), "duplicate or missing package member")
            self.assertEqual({member.name for member in members}, CONTENTS)
            self.assertLess(sum(member.size for member in members), 64 * 1024 * 1024)
            for member in members:
                self.assertTrue(member.isfile(), f"nonregular package member: {member.name}")
                original = source / member.name.removeprefix("package/")
                stream = packed.extractfile(member)
                assert stream is not None
                with stream:
                    self.assertEqual(stream.read(), original.read_bytes(), member.name)
                self.assertEqual(member.mode & 0o111, original.stat().st_mode & 0o111, member.name)
            packed.extractall(destination, filter="data")
        return destination / "package"

    def test_package_when_packed_from_checkout_has_exact_owned_inventory(self) -> None:
        # Given the actual checkout, not a filtered fixture or a dry run.
        before = snapshot(ROOT / "skills")
        # When npm creates and the checker inspects a real tarball.
        package = self.unpack(self.pack())
        # Then only nineteen complete owned payloads ship; source caches are untouched.
        self.assertEqual({path.parent.name for path in (package / "skills").glob("*/SKILL.md")},
                         set(registered_skills()))
        self.assertEqual(len(registered_skills()), 21)
        self.assertEqual(snapshot(ROOT / "skills"), before)

    def test_package_when_git_free_source_has_caches_and_private_files_excludes_them(self) -> None:
        # Given real disposable bytecode and local artifacts inside publishable directories.
        for name in ("skills/tk-test/scripts/__pycache__/tk-test.cpython-311.pyc", "skills/tk-test/scripts/tk-test.pyc",
                     "skills/references/__pycache__/model_config.cpython-312.pyc", "bin/cli.pyc",
                     "skills/tk-test/.omo/private.md", "bin/.omh/private.json", ".omo-tmp/private.md",
                     ".thunderkit/private.md", "node_modules/oh-my-openagent/private.md"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE_SENTINEL")
        before = snapshot(self.source)
        # When the Git-free tree is packed without deleting or filtering its files.
        package = self.unpack(self.pack(self.source), self.source)
        # Then the actual npm inclusion rules exclude caches and private/native payloads.
        self.assertFalse(any("__pycache__" in path.parts or path.suffix == ".pyc" for path in package.rglob("*")))
        self.assertEqual(snapshot(self.source), before)

    def test_package_when_unexpected_payload_is_added_fails_inventory(self) -> None:
        # Given a foreign workflow body inside an otherwise allowed skill directory.
        (self.source / "skills/tk-plan/foreign.md").write_text("foreign body", encoding="utf-8")
        # When checking its real tarball, then the extra member is rejected.
        with self.assertRaises(AssertionError):
            self.unpack(self.pack(self.source), self.source)

    def test_package_when_cache_exclusions_are_removed_detects_shipped_bytecode(self) -> None:
        # Given a Git-free source with bytecode and only its npm cache rules removed.
        (self.source / "skills/tk-test/scripts/tk-test.pyc").write_bytes(b"cache")
        ignore = self.source / ".npmignore"
        ignore.write_text(ignore.read_text().replace("**/*.pyc\n", "").replace("**/__pycache__/\n", ""), encoding="utf-8")
        # When packing, then the real tarball fails inventory rather than relying on fixture filters.
        with self.assertRaises(AssertionError):
            self.unpack(self.pack(self.source), self.source)

    def test_package_when_metadata_adds_native_peers_or_install_hooks_fails(self) -> None:
        # Given each forbidden executable package relationship independently.
        original = read_json(self.source / "package.json")
        for key in ("dependencies", "peerDependencies", "optionalDependencies", "scripts"):
            document = deepcopy(original)
            document[key] = {"install": "false"} if key == "scripts" else {"oh-my-hermes": "2.0.3"}
            write_json(self.source / "package.json", document)
            # When checking package metadata, then installation cannot be implicit.
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.assert_metadata(self.source)

    def assert_metadata(self, package: Path) -> None:
        metadata = read_json(package / "package.json")
        for key in ("dependencies", "peerDependencies", "optionalDependencies", "bundledDependencies", "bundleDependencies"):
            self.assertFalse(metadata.get(key), key)
        self.assertEqual(set(mapping(metadata["scripts"])), {"test"})
        self.assertEqual(mapping(metadata["bin"]), {"thunderkit": "bin/thunderkit.js"})

    def test_packed_cli_when_version_requested_reports_package_version(self) -> None:
        # Given the real extracted package outside the checkout.
        package = self.unpack(self.pack())
        self.assert_metadata(package)
        # When its CLI is invoked without any native host or credentials.
        result = self.run_cli([self.node, str(package / "bin/thunderkit.js"), "--version"])
        # Then the published version is read relative to the relocated entry point.
        self.assertEqual((result.returncode, result.stdout), (0, text(read_json(package / "package.json")["version"]) + "\n"))

    def test_packed_cli_when_dependencies_requested_reads_local_manifest(self) -> None:
        # Given an extracted package and an unrelated working directory.
        package = self.unpack(self.pack())
        # When only read-only dependency display is requested.
        result = self.run_cli([self.node, str(package / "bin/thunderkit.js"), "deps", "--json"])
        # Then metadata matches the package manifest with no installer or model call.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mapping(json.loads(result.stdout))["ecosystems"], read_json(package / "skills/references/dependencies.json")["ecosystems"])

    def test_packed_skills_when_individually_relocated_need_no_checkout(self) -> None:
        package = self.unpack(self.pack())
        for name in registered_skills():
            with self.subTest(skill=name):
                # Given one extracted skill alone, without the canonical tree or siblings.
                parent = self.sandbox / name
                skill = Path(shutil.copytree(package / "skills" / name, parent / name))
                # When its resolver runs with isolated imports and empty peer inventory.
                result = self.run_cli(self.resolver_arguments(skill), parent)
                # Then the real local helpers compute the owned or missing-peer route.
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                record = mapping(json.loads(result.stdout))
                expected = ("owned", "owned_policy") if name in OWNED_SKILLS else ("fallback", "peer_missing")
                self.assertEqual((record["decision"], record["reason_code"]), expected)

    def test_archive_when_members_are_tampered_fails_before_extraction(self) -> None:
        original = self.pack()
        kinds: tuple[Literal["missing", "extra", "changed", "duplicate", "symlink", "escape"], ...] = (
            "missing", "extra", "changed", "duplicate", "symlink", "escape")
        for kind in kinds:
            # Given a real tarball with exactly one damaged archive property.
            damaged = self.sandbox / f"{kind}.tgz"
            with tarfile.open(original, "r:gz") as source, tarfile.open(damaged, "w:gz") as target:
                for index, member in enumerate(source.getmembers()):
                    stream = source.extractfile(member)
                    assert stream is not None
                    with stream:
                        content = stream.read()
                    if index == 0:
                        match kind:
                            case "missing":
                                continue
                            case "changed":
                                content = b"modified"
                                member.size = len(content)
                            case "duplicate" | "extra":
                                extra = deepcopy(member)
                                if kind == "extra":
                                    extra.name = "package/foreign.md"
                                target.addfile(extra, io.BytesIO(content))
                            case "symlink":
                                member.type, member.linkname, member.size = tarfile.SYMTYPE, "../../outside", 0
                            case "escape":
                                member.name = "../outside"
                            case unreachable:
                                assert_never(unreachable)
                    target.addfile(member, io.BytesIO(content))
            # When inspecting it, then reject corruption before extracting any member.
            with self.subTest(kind=kind), self.assertRaises(AssertionError):
                self.unpack(damaged)

    def test_validation_when_frontmatter_is_invalid_fails(self) -> None:
        path = self.source / "skills/tk-plan/SKILL.md"
        original = path.read_text(encoding="utf-8")
        for old, new in (('thunderkit-role: "planner"', 'thunderkit-role: "executor"'),
                         ('thunderkit-delegates: "omo:ulw-plan omh:ultrawork/ulw-plan"', 'thunderkit-delegates: "none"'),
                         ('thunderkit-contract: "1"', 'thunderkit-contract: "2"'),
                         ('name: tk-plan', 'name: tk-plan\nname: tk-plan')):
            # Given invalid machine-consumed metadata in otherwise complete source.
            path.write_text(original.replace(old, new), encoding="utf-8")
            # When validating it, then reject the metadata without heuristics over prose.
            with self.subTest(field=old):
                self.assertTrue(validate_frontmatter.check(self.source))

    def test_validation_when_catalog_expands_uses_data_not_a_fixed_fleet(self) -> None:
        # Given one additional catalog model and its corresponding structured roster row.
        path = self.source / "skills/references/models.json"
        catalog = read_json(path)
        models = mapping(catalog["models"])
        extra = deepcopy(mapping(next(iter(models.values()))))
        extra["model_id"] = "fixture-model"
        models["fixture"] = extra
        write_json(path, catalog)
        roster = self.source / "skills/references/model-roster.md"
        harnesses = ", ".join(text(mapping(item)["harness"]) for item in sequence(extra["harnesses"]))
        with roster.open("a", encoding="utf-8") as output:
            output.write(f"\n| Fixture | `fixture` | `fixture-model` | {harnesses} | test | test |\n")
        # When validating against the catalog, then the additional model is covered.
        self.assertEqual(validate_frontmatter.check(self.source), [])

    def run_make(self, overrides: list[str]) -> subprocess.CompletedProcess[str]:
        python = self.sandbox / "python-pass"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        return self.run_cli(["make", "run_tests", f"PY={python}", *overrides], self.source)

    def test_runner_when_node_is_missing_fails_before_checks(self) -> None:
        # Given a missing mandatory runtime and a harmless Python test double.
        missing = self.sandbox / "absent-node"
        # When the normal runner starts, then it fails explicitly rather than skipping Node.
        result = self.run_make([f"NODE={missing}"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MISSING: node", result.stdout + result.stderr)

    def test_runner_when_future_release_suite_is_added_executes_it(self) -> None:
        # Given a new release test not named in today's runner implementation.
        tests = self.source / "tests"
        tests.mkdir()
        (tests / "cli.test.mjs").write_text("", encoding="utf-8")
        (tests / "release_future.test.mjs").write_text('throw new Error("FUTURE_SUITE_EXECUTED");\n', encoding="utf-8")
        # When the real Node runner discovers suites, then the new failure reaches make.
        result = self.run_make([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FUTURE_SUITE_EXECUTED", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
