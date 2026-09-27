"""Static guards for workflows that must never publish, tag or push."""

from pathlib import Path
import re
from typing import Final
import unittest

ROOT: Final = Path(__file__).resolve().parents[1]
WORKFLOWS: Final = ROOT / ".github" / "workflows"


class WorkflowTests(unittest.TestCase):
    def test_only_the_release_workflow_requests_oidc(self) -> None:
        holders = sorted(path.name for path in WORKFLOWS.glob("*.yml")
                         if "id-token: write" in path.read_text(encoding="utf-8"))
        self.assertEqual(holders, ["pages.yml", "release-please.yml"])

    def test_peers_watch_is_read_only_and_scheduled(self) -> None:
        text = (WORKFLOWS / "peers-watch.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertNotIn("push:", text)
        self.assertEqual(re.findall(r"^\s*(contents|id-token|pull-requests|packages): *(\w+)", text, re.MULTILINE),
                         [("contents", "read")])
        for forbidden in ("npm publish", "git push", "git tag", "gh release", "gh pr", "secrets."):
            self.assertNotIn(forbidden, text)
        self.assertTrue(all(re.search(r"@[0-9a-f]{40}$", line.strip()) for line in text.splitlines()
                            if line.strip().startswith("- uses:")))


if __name__ == "__main__":
    unittest.main()
