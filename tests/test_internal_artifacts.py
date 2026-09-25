from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Final
import unittest


ROOT: Final = Path(__file__).resolve().parents[1]


class InternalArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory(
            prefix="thunderkit-ignores-",
            dir=os.environ.get("THUNDERKIT_TEST_TMPDIR") or os.environ.get("TMPDIR"),
        ))
        self.repo = Path(temporary)
        self.environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.repo),
            "XDG_CONFIG_HOME": str(self.repo / ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_MASTER": "1",
            "GIT_AUTOPUSH_DISABLE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        result = self.git(("init", "--quiet", "--template="))
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copyfile(ROOT / ".gitignore", self.repo / ".gitignore")

    def git(self, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", *arguments), cwd=self.repo, env=self.environment,
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_files_when_internal_are_ignored(self) -> None:
        for relative in (
            ".omo/notes.md", ".omo-tmp/x.json", ".omh/plans/x.md", ".omc/state.json",
            ".thunderkit/DECISIONS.md", ".thunderkit/runs/lane.json",
            ".thunderkit/scratch-note.md", ".thunderkit/archive/NORTH_STAR.md",
            ".thunderkit/config.json.bak", ".thunderkit/PHILOSOPHY.md.bak",
            "__pycache__/x.pyc", "x.pyc", ".DS_Store", "wt-sample/x.txt",
        ):
            with self.subTest(path=relative):
                # Given an internal file under the real repository rules.
                path = self.repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                # When Git classifies the untracked path.
                result = self.git(("check-ignore", "--quiet", "--", relative))
                # Then the file is excluded.
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_files_when_retained_are_trackable(self) -> None:
        skill = min(ROOT.glob("skills/*/SKILL.md")).relative_to(ROOT).as_posix()
        for relative in (
            ".thunderkit/NORTH_STAR.md", ".thunderkit/PHILOSOPHY.md",
            ".thunderkit/config.json", "NORTH_STAR.md", "README.md", skill,
            "bin/thunderkit.js", "site/_site/index.html",
        ):
            with self.subTest(path=relative):
                # Given a retained context file or a product path.
                path = self.repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                # When Git classifies the untracked path.
                result = self.git(("check-ignore", "--quiet", "--", relative))
                # Then the file remains eligible for tracking.
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_product_when_personal_excludes_exist_is_trackable(self) -> None:
        # Given a conflicting personal exclude list in the temporary home.
        (self.repo / ".gitconfig").write_text(
            "[core]\nexcludesFile = ~/personal-excludes\n", encoding="utf-8",
        )
        (self.repo / "personal-excludes").write_text("README.md\n", encoding="utf-8")
        (self.repo / "README.md").touch()
        # When Git runs with isolated configuration.
        result = self.git(("check-ignore", "--quiet", "--", "README.md"))
        # Then the repository rules alone determine eligibility.
        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
