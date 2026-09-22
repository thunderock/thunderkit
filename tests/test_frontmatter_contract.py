from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Final
import unittest

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.skill_frontmatter import (
    Frontmatter,
    FrontmatterError,
    is_legacy_nested_metadata,
    parse_skill_file,
    parse_skill_md,
    validate_thunderkit,
)

DESCRIPTION: Final = "Use when checking frontmatter contracts for local skills."
CORE: Final = f'---\nname: tk-example\ndescription: "{DESCRIPTION}"\n'
METADATA: Final = {
    "thunderkit-role": "router",
    "thunderkit-tier": "entry",
    "thunderkit-delegates": "none",
    "thunderkit-contract": "1",
}
HEADER: Final = CORE + "metadata:\n" + "".join(
    f'  {key}: "{value}"\n' for key, value in METADATA.items()
) + "---\n"


class FrontmatterContractTests(unittest.TestCase):
    def test_flat_header_preserves_fields_and_body(self) -> None:
        body = '\n# Café\r\n\tTrailing spaces  \n---\nmetadata:\n  nested:\nNo newline'
        fm = parse_skill_md(HEADER + body)
        self.assertEqual(
            fm, Frontmatter("tk-example", DESCRIPTION, None, None, None, METADATA, body)
        )

    def test_optional_scalars_and_escaped_quotes(self) -> None:
        text = CORE + (
            "license: 'MIT'\ncompatibility: Python 3.10+\n"
            'allowed-tools: "Read Bash(\\\"git status\\\")"\n---\n'
        )
        fm = parse_skill_md(text)
        self.assertEqual(
            (fm.license, fm.compatibility, fm.allowed_tools, fm.metadata, fm.body),
            ("MIT", "Python 3.10+", 'Read Bash("git status")', {}, ""),
        )

    def test_quoted_scalars_strip_only_the_outer_layer(self) -> None:
        cases = [('"\'MIT\'"', "'MIT'"), ("'\"MIT\"'", '"MIT"'), ('""', "")]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                fm = parse_skill_md(CORE + f"license: {raw}\n---\n")
                self.assertEqual(fm.license, expected)

    def test_generic_metadata_is_a_flat_string_mapping(self) -> None:
        fm = parse_skill_md(CORE + "metadata:\n  custom_key: 'value'\n  empty: \"\"\n---\n")
        self.assertEqual(fm.metadata, {"custom_key": "value", "empty": ""})

    def test_unknown_top_level_keys_report_the_source_line(self) -> None:
        for key in ("requires", "dependencies", "model", "agent", "unknown"):
            with self.subTest(key=key):
                with self.assertRaises(FrontmatterError) as caught:
                    parse_skill_md(CORE + f"{key}: value\n---\n", "skill.md")
                error = caught.exception
                self.assertIsInstance(error, ValueError)
                self.assertEqual((error.path, error.line), ("skill.md", 4))
                self.assertIn(key, error.detail)
                self.assertIn("skill.md:4:", str(error))

    def test_duplicate_keys_are_rejected(self) -> None:
        cases = [
            (CORE + "name: tk-other\n---\n", 4),
            (CORE + 'metadata:\n  role: "x"\n  role: "y"\n---\n', 6),
            (CORE + 'metadata:\n  role: "x"\nmetadata:\n  tier: "y"\n---\n', 6),
        ]
        for text, line in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(FrontmatterError, "duplicate") as caught:
                    parse_skill_md(text)
                self.assertEqual(caught.exception.line, line)

    def test_nested_metadata_is_rejected_and_detected(self) -> None:
        text = CORE + "metadata:\n  thunderkit:\n    role: x\n---\n"
        with self.assertRaises(FrontmatterError) as caught:
            parse_skill_md(text)
        self.assertEqual(caught.exception.line, 5)
        self.assertTrue(is_legacy_nested_metadata(text))

    def test_metadata_requires_quoted_values_and_exact_indentation(self) -> None:
        for entry in (
            '  role: router\n', '  role: 1\n', '  - "router"\n',
            ' role: "router"\n', '   role: "router"\n', '    role: "router"\n',
            '\trole: "router"\n', '  role:\n', '  role: ["router"]\n',
        ):
            with self.subTest(entry=entry):
                with self.assertRaises(FrontmatterError):
                    parse_skill_md(CORE + "metadata:\n" + entry + "---\n")

    def test_metadata_requires_a_nonempty_block(self) -> None:
        for suffix in ("metadata:\n", "metadata:\nlicense: MIT\n", 'metadata: "x"\n'):
            with self.subTest(suffix=suffix):
                with self.assertRaises(FrontmatterError):
                    parse_skill_md(CORE + suffix + "---\n")

    def test_nested_scalars_and_other_yaml_constructs_are_rejected(self) -> None:
        for suffix in (
            'license:\n  kind: "MIT"\n', 'license: MIT\n  kind: "MIT"\n',
            'license: [MIT]\n', 'license: {kind: MIT}\n', 'license: |\n',
            'license: >\n', '- license: MIT\n', '# comment\n', '\n',
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaises(FrontmatterError):
                    parse_skill_md(CORE + suffix + "---\n")

    def test_malformed_quotes_are_rejected(self) -> None:
        for scalar in ('"MIT', "'MIT", '"MIT\'', '\'MIT"', '"MIT" extra', '"a"b"', '"a\\"'):
            with self.subTest(scalar=scalar):
                with self.assertRaises(FrontmatterError):
                    parse_skill_md(CORE + f"license: {scalar}\n---\n")

    def test_frontmatter_requires_exact_opening_and_closing_lines(self) -> None:
        for text in (
            "", "---", "\n" + HEADER, "\ufeff" + HEADER,
            HEADER.replace("---\n", "--- \n", 1), HEADER.replace("---\n", "---\r\n", 1),
            CORE, CORE + "---", CORE + "--- \n", CORE + "---\r\n",
        ):
            with self.subTest(text=text):
                with self.assertRaises(FrontmatterError) as caught:
                    parse_skill_md(text)
                self.assertEqual(caught.exception.path, "<memory>")
                self.assertGreaterEqual(caught.exception.line, 1)

    def test_required_fields_cannot_be_missing(self) -> None:
        for line in ('name: tk-example\n', f'description: "{DESCRIPTION}"\n'):
            with self.subTest(line=line):
                with self.assertRaises(FrontmatterError):
                    parse_skill_md(CORE.replace(line, "") + "---\n")

    def test_name_syntax_and_length_are_enforced(self) -> None:
        for name in ("", " ", "Upper", "-name", "name-", "two--parts", "under_score", "a" * 65):
            with self.subTest(name=name):
                with self.assertRaisesRegex(FrontmatterError, "name"):
                    parse_skill_md(CORE.replace("tk-example", name) + "---\n")

    def test_spec_boundaries_allow_names_and_descriptions_outside_repo_policy(self) -> None:
        for name, description in (("a", "x"), ("a" * 64, "x" * 1024)):
            with self.subTest(name=name):
                fm = parse_skill_md(f'---\nname: {name}\ndescription: "{description}"\n---\n')
                self.assertEqual((fm.name, fm.description), (name, description))

    def test_description_must_be_nonempty_and_within_spec_limit(self) -> None:
        for description in ("", "   ", "x" * 1025):
            with self.subTest(length=len(description)):
                with self.assertRaisesRegex(FrontmatterError, "description"):
                    parse_skill_md(CORE.replace(DESCRIPTION, description) + "---\n")

    def test_file_adapter_preserves_body_bytes(self) -> None:
        body = "\r\n# Café\r\nline\rnext\n\tend  "
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "SKILL.md"
            path.write_bytes((HEADER + body).encode("utf-8"))
            fm = parse_skill_file(path)
        self.assertEqual(fm.body.encode("utf-8"), body.encode("utf-8"))

    def test_file_adapter_reports_the_file_path(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "SKILL.md"
            path.write_bytes((CORE + "requires: x\n---\n").encode("utf-8"))
            with self.assertRaises(FrontmatterError) as caught:
                parse_skill_file(str(path))
        self.assertEqual((caught.exception.path, caught.exception.line), (str(path), 4))

    def test_module_is_importable_from_the_tools_directory(self) -> None:
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT / 'tools')!r}); "
            "from skill_frontmatter import parse_skill_md; "
            f"assert parse_skill_md({HEADER!r}).name == 'tk-example'"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repo_policy_accepts_valid_delegates_and_length_boundaries(self) -> None:
        for delegates in ("none", "omo:planner", "omh:tools/agent-2", "omo:0 omh:tools/agent-2"):
            for length in (40, 500):
                with self.subTest(delegates=delegates, length=length):
                    fm = replace(parse_skill_md(HEADER), description="Use " + "x" * (length - 4),
                                 compatibility="x" * 500,
                                 metadata={**METADATA, "thunderkit-delegates": delegates})
                    self.assertIsNone(validate_thunderkit(fm, "tk-example"))

    def test_repo_policy_rejects_a_different_directory_name(self) -> None:
        with self.assertRaisesRegex(FrontmatterError, "name"):
            validate_thunderkit(parse_skill_md(HEADER), "tk-other")

    def test_repo_policy_rejects_nontrigger_or_wrong_length_descriptions(self) -> None:
        for description in ("Helps with X" + "x" * 40, "Use " + "x" * 35, "Use " + "x" * 497):
            with self.subTest(description=description):
                fm = replace(parse_skill_md(HEADER), description=description)
                with self.assertRaisesRegex(FrontmatterError, "description"):
                    validate_thunderkit(fm, "tk-example")

    def test_repo_policy_rejects_long_compatibility(self) -> None:
        fm = replace(parse_skill_md(HEADER), compatibility="x" * 501)
        with self.assertRaisesRegex(FrontmatterError, "compatibility"):
            validate_thunderkit(fm, "tk-example")

    def test_repo_policy_requires_exact_metadata_keys_and_contract_version(self) -> None:
        cases = [{key: value for key, value in METADATA.items() if key != missing} for missing in METADATA]
        cases += [{**METADATA, "unknown": "x"}, {**METADATA, "thunderkit-contract": "2"}]
        for metadata in cases:
            with self.subTest(metadata=metadata):
                fm = replace(parse_skill_md(HEADER), metadata=metadata)
                with self.assertRaises(FrontmatterError):
                    validate_thunderkit(fm, "tk-example")

    def test_repo_policy_rejects_invalid_delegate_tokens(self) -> None:
        for delegates in ("gsd:foo", "", "none omo:planner", "omo:-foo", "omo:Upper",
                          "omo:a/b/c", "omh:foo/", "omo:foo\tomh:bar", "omo:foo\nomh:bar"):
            with self.subTest(delegates=delegates):
                fm = replace(parse_skill_md(HEADER), metadata={**METADATA, "thunderkit-delegates": delegates})
                with self.assertRaisesRegex(FrontmatterError, "thunderkit-delegates"):
                    validate_thunderkit(fm, "tk-example")

    def test_legacy_detector_ignores_flat_metadata_and_body_lookalikes(self) -> None:
        for text in (HEADER, CORE + 'metadata:\n  empty: ""\n---\n',
                     HEADER + "metadata:\n  thunderkit:\n", "metadata:\n  thunderkit:\n",
                     CORE + "license:\n  thunderkit:\n---\n",
                     CORE + "metadata:\n    thunderkit:\n---\n"):
            with self.subTest(text=text):
                self.assertFalse(is_legacy_nested_metadata(text))

    def test_all_current_skill_headers_are_legacy(self) -> None:
        paths = sorted((ROOT / "skills").glob("tk-*/SKILL.md"))
        self.assertEqual(len(paths), 19)
        for path in paths:
            with self.subTest(skill=path.parent.name):
                self.assertTrue(is_legacy_nested_metadata(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
