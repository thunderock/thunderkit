"""Exercise the full-suite runtime boundary through the real Make targets."""
from __future__ import annotations

import os
from pathlib import Path
from shlex import quote
import shutil
import subprocess
import sys
import tempfile
import unittest

from payload_fixtures import ROOT, PayloadFixture


class RuntimeTests(PayloadFixture):
    def setUp(self) -> None:
        super().setUp()
        node, npm = shutil.which("node"), shutil.which("npm")
        assert node is not None, "Node is required for runtime tests"
        assert npm is not None, "npm is required for runtime tests"
        self.node, self.npm = node, npm
        self.env["PATH"] = os.pathsep.join((str(Path(node).parent), str(Path(npm).parent), os.defpath))

    def python_runtime(self, version: tuple[int, int, int]) -> Path:
        runtime = self.sandbox / f"python-{'-'.join(map(str, version))}"
        runtime.write_text(
            '#!/bin/sh\nif [ "$1" = "-c" ]; then\n'
            f'  exec {quote(sys.executable)} -c "import sys; sys.version_info = {version!r}; $2"\n'
            f'fi\nexec {quote(sys.executable)} "$@"\n', encoding="utf-8")
        runtime.chmod(0o755)
        return runtime

    def node_runtime(self, version: str) -> Path:
        preload = self.sandbox / f"node-{version}.cjs"
        preload.write_text(
            f"Object.defineProperty(process.versions, 'node', {{ value: '{version}' }});\n",
            encoding="utf-8")
        runtime = self.sandbox / f"node-{version}"
        runtime.write_text(
            f'#!/bin/sh\nexec {quote(self.node)} --require {quote(str(preload))} "$@"\n',
            encoding="utf-8")
        runtime.chmod(0o755)
        return runtime

    def run_make(self, target: str, overrides: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess[str], bool]:
        source = Path(tempfile.mkdtemp(prefix="make-", dir=self.sandbox))
        shutil.copyfile(ROOT / "Makefile", source / "Makefile")
        tests = source / "tests"
        tests.mkdir()
        (tests / "validate_frontmatter.py").write_text(
            'from pathlib import Path\nPath("checks-started").touch()\nraise SystemExit(73)\n',
            encoding="utf-8")
        result = self.run_cli(["make", target, f"PY={sys.executable}", f"NODE={self.node}",
                               f"NPM={self.npm}", *overrides], source)
        return result, (source / "checks-started").exists()

    def test_check_runtime_when_node_major_matches_accepts_any_patch(self) -> None:
        for version in ("24.0.0", "24.99.99"):
            with self.subTest(version=version):
                # Given a real Node interpreter reporting a supported major.
                runtime = self.node_runtime(version)
                # When Make evaluates its actual runtime predicate.
                result, started = self.run_make("check-runtime", (f"NODE={runtime}",))
                # Then every patch of that major is accepted without running checks.
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(started)

    def test_check_runtime_when_python_minor_matches_accepts_any_patch(self) -> None:
        for version in ((3, 12, 0), (3, 12, 99)):
            with self.subTest(version=version):
                # Given a real Python interpreter reporting a supported minor.
                runtime = self.python_runtime(version)
                # When Make evaluates its actual runtime predicate.
                result, started = self.run_make("check-runtime", (f"PY={runtime}",))
                # Then every patch of that minor is accepted without running checks.
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(started)

    def test_targets_when_node_major_is_incompatible_refuse_before_checks(self) -> None:
        for version in ("18.0.0", "23.0.0", "25.0.0"):
            for target in ("check-runtime", "setup", "run_tests"):
                with self.subTest(version=version, target=target):
                    # Given an older or future Node major and an observable first check.
                    runtime = self.node_runtime(version)
                    # When a public Make entry point starts.
                    result, started = self.run_make(target, (f"NODE={runtime}",))
                    # Then refusal happens at the runtime boundary, not inside a suite.
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(started)
                    self.assertIn("Tests require Node 24.x", result.stdout + result.stderr)

    def test_targets_when_python_minor_is_incompatible_refuse_before_checks(self) -> None:
        for version in ((3, 11, 14), (3, 13, 0), (4, 0, 0)):
            for target in ("check-runtime", "setup", "run_tests"):
                with self.subTest(version=version, target=target):
                    # Given an older minor, future minor or future major of Python.
                    runtime = self.python_runtime(version)
                    # When a public Make entry point starts.
                    result, started = self.run_make(target, (f"PY={runtime}",))
                    # Then refusal happens before the first validator executes.
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(started)
                    self.assertIn("Tests require Python 3.12.x", result.stdout + result.stderr)

    def test_runner_when_runtimes_are_supported_reaches_checks(self) -> None:
        # Given the supported real runtimes and a first-check sentinel that fails.
        # When the normal runner starts.
        result, started = self.run_make("run_tests")
        # Then the runtime gate admits the validator and preserves its failure.
        self.assertTrue(started)
        self.assertNotEqual(result.returncode, 0)

    def test_setup_when_runtimes_are_supported_reports_exact_requirements(self) -> None:
        # Given the supported real runtimes.
        # When setup reports the development requirements without installing anything.
        result, started = self.run_make("setup")
        # Then the advertised full-suite requirements match the exact runtime gate.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(started)
        self.assertIn("Python 3.12.x, Node 24.x, npm 11.19.1", result.stdout)

    def test_runner_when_source_is_es_module_keeps_fixture_programs_commonjs(self) -> None:
        # Given an ESM checkout with repository-local scratch and a CommonJS child tool.
        source = self.sandbox / "module-source"
        source.mkdir()
        shutil.copyfile(ROOT / "Makefile", source / "Makefile")
        (source / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        python = source / "python-pass"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        tests = source / "tests"
        tests.mkdir()
        (tests / "cli.test.mjs").write_text("", encoding="utf-8")
        (tests / "release_scope.test.mjs").write_text("""
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
test("temporary child tools retain CommonJS semantics", () => {
  const directory = mkdtempSync(join(tmpdir(), "child-tool-"));
  try {
    const tool = join(directory, "fixture-tool");
    writeFileSync(tool, 'require("node:fs");\\n');
    const result = spawnSync(process.execPath, [tool], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
""", encoding="utf-8")
        # When the real Make runner launches its Node suites.
        result = self.run_cli(["make", "run_tests", f"PY={python}", f"NODE={self.node}",
                               f"NPM={self.npm}", f"TMPDIR={source / 'scratch'}"], source)
        # Then child tools execute without inheriting the enclosing package's module mode.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
