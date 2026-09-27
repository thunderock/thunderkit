from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Final
import unittest

ROOT: Final = Path(__file__).resolve().parents[1]
TOOL: Final = ROOT / "tools" / "upstream_coverage.py"


def scratch_root() -> str:
    for name in ("THUNDERKIT_TEST_TMPDIR", "TMPDIR"):
        if value := os.environ.get(name):
            return value
    fallback = ROOT / ".omo-tmp"
    fallback.mkdir(exist_ok=True)
    return str(fallback)


class UpstreamCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=scratch_root())))

    def skill(self, relative: str) -> None:
        path = self.root / relative / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("# fixture\n", encoding="utf-8")

    def run_tool(self, *inventories: str) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, "-B", str(TOOL)]
        for item in inventories:
            argv += ["--inventory", item]
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)

    def test_reports_present_missing_and_unwrapped_per_layout(self) -> None:
        for name in ("ulw-plan", "ulw-execute", "ulw-research", "visual-qa", "debugging", "refactor"):
            self.skill(f"omo/dist/skills/{name}")
        self.skill("omh/skills/ultrawork/ulw-plan")
        commands = self.root / "gsd/commands/gsd"
        commands.mkdir(parents=True)
        for name in ("fast", "debug", "explore", "quick"):
            (commands / f"{name}.md").write_text("x\n", encoding="utf-8")
        result = self.run_tool(*(f"{peer}={self.root / peer}" for peer in ("omo", "omh", "gsd")))
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["omo"]["wrapped_missing"], ["coding-agent-sessions"])
        self.assertEqual(report["omo"]["unwrapped"], ["refactor"])
        self.assertIn("ultrawork/ulw-plan", report["omh"]["wrapped_present"])
        self.assertIn("reviewer/omh-code-review", report["omh"]["wrapped_missing"])
        self.assertEqual(report["gsd"], {"wrapped_present": ["gsd-debug", "gsd-explore", "gsd-fast"],
                                         "wrapped_missing": [], "unwrapped": ["gsd-quick"]})

    def test_complete_inventory_exits_zero(self) -> None:
        for name in ("gsd-fast", "gsd-debug", "gsd-explore"):
            self.skill(f"gsd/skills/{name}")
        result = self.run_tool(f"gsd={self.root / 'gsd'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["gsd"]["wrapped_missing"], [])

    def test_invalid_inventories_exit_two(self) -> None:
        for item in ("omo", f"unknown={self.root}", f"omo={self.root / 'absent'}"):
            with self.subTest(item=item):
                self.assertEqual(self.run_tool(item).returncode, 2)


if __name__ == "__main__":
    unittest.main()
