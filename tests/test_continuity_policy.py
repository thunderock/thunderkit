"""Pin the preservation amendment without weakening delivery boundaries."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ContinuityPolicyTests(unittest.TestCase):
    def test_both_north_stars_require_verified_preservation(self) -> None:
        for relative in ("NORTH_STAR.md", ".thunderkit/NORTH_STAR.md"):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("Verified preservation", text)
                self.assertIn("unfinished files", text)
                self.assertIn("unpublished commits", text)
                self.assertIn("regular intervals", text)
                self.assertIn("before risky transitions", text)
                self.assertIn("same-disk snapshot", text)

    def test_opt_in_connections_never_become_runtime_ownership(self) -> None:
        for relative in ("NORTH_STAR.md", ".thunderkit/NORTH_STAR.md"):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("explicit setup approval", text)
                self.assertIn("existing host", text)
                self.assertIn("no new daemon", text)
                self.assertIn("never replace global hooks", text)
                self.assertIn("credentials", text)

    def test_checkpoint_controller_exception_preserves_native_and_ship_rules(self) -> None:
        for relative in ("NORTH_STAR.md", ".thunderkit/NORTH_STAR.md"):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("checkpoint controller", text)
                self.assertIn("exact repository, remote and working-branch ref", text)
                self.assertIn("Native workers remain delivery-disabled", text)
                self.assertIn("tk-ship remains preparation-only", text)
                self.assertIn("no force, tags, PR, release, merge or default-branch writes", text)


if __name__ == "__main__":
    unittest.main()
