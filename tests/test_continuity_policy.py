"""Pin the preservation amendment without weakening delivery boundaries."""
from __future__ import annotations

from pathlib import Path
import re
import shlex
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
                self.assertIn("Protected-branch writes are forbidden", text)
                self.assertIn("current authority", text)


class LifecycleCheckpointPolicyTests(unittest.TestCase):
    """Check real stage instructions, not runtime or installed-host protection."""

    @staticmethod
    def text(relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_owned_stage_boundaries_link_the_controller_capture_procedure(self) -> None:
        # A short local instruction plus one shared rule avoids copying the
        # complete CLI/safety contract into every lifecycle stage.
        stages = {
            "execute": "Layer gating and recovery",
            "fast": "Fallback",
            "quick": "Fallback",
            "handoff": "Save",
            "memory": "Save approved changes",
            "router": "Context discipline — restore before you re-ask",
        }
        anchors = {
            "approved checkpoint controller": r"approved checkpoint controller",
            "native delivery stays disabled": r"native\b[^.\n]*delivery(?:-disabled|:disabled)",
            "save current artifact/context": r"sav(?:e|ed|ing)\b[^.\n]*current[^.\n]*(?:artifact|context|handoff)",
            "real capture helper": r"checkpoint_cli\.py[`\"]?\s+capture\b",
            "linked shared preservation rule": (
                r"\]\(references/delegation\.md#approved-preservation-checkpoints\)"
            ),
        }
        for stage, heading in stages.items():
            relative = f"skills/tk-{stage}/SKILL.md"
            with self.subTest(path=relative, boundary=heading):
                match = re.search(
                    rf"^#{{2,3}} {re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)",
                    self.text(relative), re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(match, f"Existing owned boundary disappeared: {heading}")
                assert match is not None
                section = " ".join(match.group(1).split())
                missing = [name for name, pattern in anchors.items()
                           if re.search(pattern, section, re.IGNORECASE) is None]
                self.assertEqual(missing, [], f"{relative}::{heading} lacks preservation procedure")

    def test_shared_rule_keeps_cadence_and_authority_separate_from_delivery(self) -> None:
        source = self.text("skills/references/delegation.md")
        with self.subTest(rule="shared link target exists"):
            self.assertTrue(re.search(r"(?m)^## Approved preservation checkpoints\s*$", source),
                            "Shared delegation rule needs the linked preservation heading")
        text = " ".join(source.split())
        # Independent assertions reach the actual source even when the new rule
        # or CLI example is absent. Positive policy words alone are insufficient:
        # denial clauses must name what remains forbidden.
        rules = {
            "controller-only exception": r"approved checkpoint controller\b[^.]*\b(?:only|separate)\b",
            "native workers still disabled": r"native workers\b[^.]*delivery(?:-disabled|:disabled)",
            "regular saved context": r"sav(?:e|ed|ing)\b[^.]*artifact[^.]*context[^.]*regular intervals",
            "pre-risk and pause": r"before risky transitions[^.]*paus(?:e|ing)",
            "fresh publication authority": r"fresh[^.]*PushGrant[^.]*exact[^.]*repository[^.]*remote[^.]*ref",
            "no saved grant": r"(?:never|do not)\s+(?:save|persist|restore)\b[^.]*PushGrant",
            "no automatic host activation": r"(?:no automatic|never automatically)\s+[^.]*host[^.]*activat",
            "separate qualified host setup": r"host[^.]*activat[^.]*separate[^.]*approv[^.]*qualif",
            "no automatic model selection": r"(?:no automatic|never automatically)\s+[^.]*model[^.]*select",
            "no PR/publish/tag/merge/protected-ref writes": (
                r"(?:no|never)\b[^.]*\bPR\b[^.]*publish[^.]*tag[^.]*merge[^.]*protected[^.]*writ"
            ),
            "ship remains preparation-only": r"tk-ship[`\s]*[^.]*preparation-only",
            "backup is not executable authority": r"backup (?:text|data)[^.]*inert[^.]*not[^.]*authority",
        }
        for rule, pattern in rules.items():
            with self.subTest(rule=rule):
                self.assertTrue(re.search(pattern, text, re.IGNORECASE),
                                f"Shared delegation rule lacks: {rule}")

    def test_shared_capture_command_requires_explicit_scope_and_fresh_approval(self) -> None:
        blocks = re.findall(r"```(?:sh|bash)?\n(.*?)```",
                            self.text("skills/references/delegation.md"), re.DOTALL)
        commands = []
        for block in blocks:
            for line in block.replace("\\\n", " ").splitlines():
                if "checkpoint_cli.py" in line:
                    argv = shlex.split(line)
                    if "capture" in argv:
                        commands.append(argv)
        self.assertTrue(commands, "Shared rule must show a real checkpoint_cli.py capture command")
        for argv in commands:
            with self.subTest(command=argv):
                self.assertEqual(argv[:3],
                                 ["python3", "$SKILL_ROOT/scripts/checkpoint_cli.py", "capture"])
                for option in ("--project-root", "--scope", "--identity", "--backup-root",
                               "--source", "--context-entrypoint", "--state-dir"):
                    self.assertIn(option, argv)
                    index = argv.index(option)
                    self.assertLess(index + 1, len(argv), f"Missing value for {option}")
                    self.assertFalse(argv[index + 1].startswith("--"), f"Missing value for {option}")
                self.assertEqual(argv[argv.index("--scope") + 1], "project")
                self.assertIn("--approve", argv)
                self.assertNotIn("--push-grant", argv)
                self.assertNotIn("--activate", argv)

    def test_inert_load_does_not_replace_handoff_continuation_gates(self) -> None:
        handoff = " ".join(self.text("skills/tk-handoff/SKILL.md").split())
        gates = (
            "Restore requires valid project selections",
            "Check repository identity, branch and full HEAD",
            "compare exact SHA-256 values",
            "Compare the saved model contract with current validated selections",
            "Require the real session ID and original harness",
            "current permission to continue the named stage",
            "Never run a stored `resume` command",
        )
        for gate in gates:
            with self.subTest(existing_resume_gate=gate):
                self.assertIn(gate, handoff)
        for stage in ("handoff", "router"):
            with self.subTest(inert_load_boundary=stage):
                paragraphs = [" ".join(part.split()) for part in
                              self.text(f"skills/tk-{stage}/SKILL.md").split("\n\n")]
                self.assertTrue(any(
                    "tk-restore" in paragraph and "model-free" in paragraph
                    and "inert" in paragraph and "tk-handoff" in paragraph
                    and re.search(r"(?:does not|never|no automatic)[^.]*resume", paragraph, re.IGNORECASE)
                    for paragraph in paragraphs
                ), "Distinguish model-free inert tk-restore load from gated tk-handoff resume")


if __name__ == "__main__":
    unittest.main()
