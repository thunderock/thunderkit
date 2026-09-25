"""Public rendering and read-only documentation drift regressions."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import site_drift
from site_drift import site_build

HEADER = '''---
name: tk-example
description: "Use when testing public documentation and local reference resolution."
compatibility: "Python 3.11+ & a supported host"
metadata:
  thunderkit-role: "planner"
  thunderkit-tier: "prep"
  thunderkit-delegates: "omo:ulw-plan omh:ultrawork/ulw-plan"
  thunderkit-contract: "1"
---
'''
BODY = '''# Example

[Roster](references/model-roster.md) and [catalog](references/models.json).
[Schema](references/config.schema.json) and [policy](references/delegation.md).
[Dependencies](references/dependencies.json) and [helper](scripts/model_config.py).
'''


class SiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/tk-example/SKILL.md"
        self.published = self.root / "site/_site"
        self.put("NORTH_STAR.md", "# Public thesis\n")
        self.put("skills/references/model-roster.md", "# Shared roster\n[catalog](models.json)")
        self.put("skills/references/models.json", '{"key": "shared"}')
        self.put("skills/tk-example/SKILL.md", HEADER + BODY)
        for name, text in {
            "model-roster.md": "# Local roster\n[catalog](models.json)",
            "models.json": '{"key": "local <value>"}',
            "config.schema.json": '{"type": "object"}',
            "delegation.md": "# Policy\n[registry](dependencies.json)",
            "dependencies.json": '{"targets": []}',
        }.items():
            self.put(f"skills/tk-example/references/{name}", text)
        self.put("skills/tk-example/scripts/model_config.py", 'print("<value>")\n')
        self.put(".thunderkit/PRIVATE.md", "PRIVATE_SENTINEL")

    def put(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def render(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            site_build.build(self.published, self.root)
        return (self.published / "tk-example.html").read_text(encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_real_metadata_and_manifest(self) -> None:
        page = self.render()
        for value in ("planner", "prep", "omo:ulw-plan", "omh:ultrawork/ulw-plan", "Contract: 1"):
            self.assertIn(value, page)
        self.assertIn("Python 3.11+ &amp; a supported host", page)
        self.assertEqual(json.loads((self.published / "skills.json").read_text()), ["tk-example"])

    def test_links_resolve_to_local_payload_not_shared_copy(self) -> None:
        page = self.render()
        self.assertIn('href="skills/tk-example/references/models.json.html"', page)
        self.assertEqual(site_drift.validate_links(self.published), [])
        catalog = self.published / "skills/tk-example/references/models.json.html"
        self.assertIn("local &lt;value&gt;", catalog.read_text())
        self.assertTrue((self.published / "skills/tk-example/scripts/model_config.py.html").is_file())

    def test_public_root_links_resolve_when_reached_from_north_star(self) -> None:
        # Given public documents with cycles, fragments and nested reference links.
        self.put("NORTH_STAR.md", "# Public thesis\n\n[Guide](DEPENDENCIES.md#optional-peers)")
        self.put("DEPENDENCIES.md", "# Dependencies\n\n## Optional peers\n\n"
                 "[README](README.md?view=full&lang=en#install)\n"
                 "[Roster](skills/references/model-roster.md#shared-roster)\n"
                 "[Skill](skills/tk-example/SKILL.md#example)\n")
        self.put("README.md", "# Readme\n\n## Install\n\n[License](LICENSE)\n"
                 "[Thesis](NORTH_STAR.md#public-thesis)\n[Guide](DEPENDENCIES.md#optional-peers)")
        self.put("LICENSE", "<license>")
        self.put("skills/tk-example/references/delegation.md",
                 "# Policy\n[Guide](../../../DEPENDENCIES.md#optional-peers)")
        # When the existing entry points discover their linked documents.
        self.render()
        # Then generated destinations preserve relative paths, queries and anchors.
        expected = {
            "north-star.html": "DEPENDENCIES.md.html#optional-peers",
            "DEPENDENCIES.md.html": "README.md.html?view=full&amp;lang=en#install",
            "README.md.html": "LICENSE.html",
            "skills/tk-example/references/delegation.md.html": "../../../DEPENDENCIES.md.html#optional-peers",
        }
        for name, href in expected.items():
            with self.subTest(page=name):
                self.assertIn(f'href="{href}"', (self.published / name).read_text())
        self.assertIn("<pre><code>&lt;license&gt;</code></pre>", (self.published / "LICENSE.html").read_text())
        self.assertEqual(site_drift.validate_links(self.published), [])

    def test_root_license_link_resolves_when_label_is_a_badge(self) -> None:
        # Given the linked-badge form used by public documentation.
        self.put("NORTH_STAR.md", "[README](README.md)")
        self.put("README.md", "[![<License>](https://example.com/badge.svg)](LICENSE)")
        self.put("LICENSE", "MIT")
        # When rendering the referenced README.
        self.render()
        # Then the escaped alt text links to the license, not the badge image.
        page = (self.published / "README.md.html").read_text()
        self.assertIn('href="LICENSE.html">&lt;License&gt;</a>', page)
        self.assertEqual(site_drift.validate_links(self.published), [])

    def test_root_documents_stay_unpublished_when_unreferenced(self) -> None:
        # Given optional public files that no entry point links.
        for name in ("DEPENDENCIES.md", "README.md", "LICENSE"):
            self.put(name, "UNREFERENCED")
        # When rendering the ordinary entry points.
        self.render()
        # Then the allowlist does not eagerly publish unused documents.
        for name in ("DEPENDENCIES.md.html", "README.md.html", "LICENSE.html"):
            self.assertFalse((self.published / name).exists())

    def test_root_document_dependencies_fail_when_not_explicitly_public(self) -> None:
        # Given a public guide linking private files or nested public-name lookalikes.
        self.put("NORTH_STAR.md", "[Guide](DEPENDENCIES.md)")
        for name in ("PRIVATE.md", "notes.md", "docs/README.md", "docs/DEPENDENCIES.md",
                     "docs/LICENSE", ".thunderkit/PRIVATE.md"):
            for label in ("source", "![source](https://example.com/badge.svg)"):
                with self.subTest(target=name, label=label):
                    self.put(name, "PRIVATE_SENTINEL")
                    self.put("DEPENDENCIES.md", f"[{label}]({name})")
                    # When rendering, then reject the dependency before publishing any page.
                    with self.assertRaisesRegex(ValueError, "public source"):
                        self.render()
                    self.assertFalse(self.published.exists())

    def test_root_documents_fail_when_symlinked_to_private_content(self) -> None:
        for name in ("DEPENDENCIES.md", "README.md", "LICENSE"):
            with self.subTest(target=name):
                # Given an allowed filename aliasing a private source.
                self.put("NORTH_STAR.md", f"[source]({name})")
                (self.root / name).symlink_to(self.root / ".thunderkit/PRIVATE.md")
                # When rendering, then refuse the symlink rather than expose its target.
                with self.assertRaisesRegex(ValueError, "regular public source"):
                    self.render()

    def test_root_documents_fail_when_referenced_but_missing(self) -> None:
        for name in ("DEPENDENCIES.md", "README.md", "LICENSE"):
            with self.subTest(target=name):
                # Given a link to an absent public document.
                self.put("NORTH_STAR.md", f"[source]({name})")
                # When rendering, then refuse a generated destination without a source.
                with self.assertRaisesRegex(ValueError, "regular public source"):
                    self.render()

    def test_root_document_anchor_validation_fails_when_heading_missing(self) -> None:
        # Given a valid public path with an invalid fragment.
        self.put("NORTH_STAR.md", "[Guide](DEPENDENCIES.md#missing)")
        self.put("DEPENDENCIES.md", "# Dependencies\n")
        self.render()
        # When checking the complete generated link graph.
        errors = site_drift.validate_links(self.published)
        # Then the missing fragment is reported against its generated destination.
        self.assertIn("missing link anchor: north-star.html: DEPENDENCIES.md.html#missing", errors)

    def test_escaping_does_not_create_markup_or_reparse_code(self) -> None:
        self.skill.write_text(HEADER.replace('"planner"', "'<img src=x>'") +
                              '# <script>\n\n`[x](javascript:bad)` **<svg>**\n\n'
                              '[<img>](https://example.com/?x=1&y=2)\n', encoding="utf-8")
        page = self.render()
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertIn("<code>[x](javascript:bad)</code>", page)
        self.assertIn('href="https://example.com/?x=1&amp;y=2"', page)

    def test_unsafe_source_links_fail_closed(self) -> None:
        for url in ("javascript:alert(1)", "JaVaScRiPt:bad", "data:text/html,bad", "vbscript:bad",
                    "file:///etc/passwd", "//example.com", "java\tscript:bad", "javascript&colon;bad"):
            for body in (f"[source]({url})", f"[![source]({url})](../../README.md)",
                         f"[![source](https://example.com/badge.svg)]({url})"):
                with self.subTest(url=url, body=body):
                    self.skill.write_text(HEADER + body + "\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "unsafe link"):
                        self.render()

    def test_legacy_and_malformed_headers_fail_closed(self) -> None:
        for header in (HEADER.replace('  thunderkit-role: "planner"', '  thunderkit:\n    role: "planner"'),
                       HEADER.replace('name: tk-example', 'name: tk-other'),
                       HEADER.replace('  thunderkit-contract: "1"', '  thunderkit-contract: "2"'),
                       HEADER.replace('name: tk-example', 'name: tk-example\nname: tk-example')):
            with self.subTest(header=header):
                self.skill.write_text(header + BODY, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.render()

    def test_private_and_escaping_links_are_not_published(self) -> None:
        for url in ("../../.thunderkit/PRIVATE.md", "../../../outside.md", "%2e%2e/%2e%2e/.thunderkit/PRIVATE.md"):
            with self.subTest(url=url):
                self.skill.write_text(HEADER + f"[private]({url})", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "public source"):
                    self.render()

    def test_symlink_reference_cannot_publish_private_content(self) -> None:
        asset = self.skill.parent / "references/models.json"
        asset.unlink()
        asset.symlink_to(self.root / ".thunderkit/PRIVATE.md")
        with self.assertRaisesRegex(ValueError, "public source"):
            self.render()

    def test_private_content_is_never_discovered(self) -> None:
        self.render()
        for path in self.published.rglob("*"):
            if path.is_file():
                self.assertNotIn("PRIVATE_SENTINEL", path.read_text())

    def test_missing_source_reference_fails_build(self) -> None:
        (self.skill.parent / "references/models.json").unlink()
        with self.assertRaisesRegex(ValueError, "public source"):
            self.render()

    def test_same_tree_passes_without_repository_writes(self) -> None:
        self.render()
        before = self.snapshot()
        self.assertEqual(site_drift.check(self.root), [])
        self.assertEqual(self.snapshot(), before)

    def test_body_change_fails_drift_without_repair(self) -> None:
        self.render()
        self.skill.write_text(HEADER + BODY + "\nChanged body.\n", encoding="utf-8")
        before = self.snapshot()
        self.assertIn("changed: tk-example.html", site_drift.check(self.root))
        self.assertEqual(self.snapshot(), before)

    def test_metadata_change_fails_drift(self) -> None:
        self.render()
        self.skill.write_text(HEADER.replace('"prep"', '"deliver"') + BODY, encoding="utf-8")
        self.assertIn("changed: tk-example.html", site_drift.check(self.root))

    def test_asset_change_fails_drift(self) -> None:
        self.render()
        self.put("skills/tk-example/references/models.json", '{"key": "changed"}')
        self.assertIn("changed: skills/tk-example/references/models.json.html", site_drift.check(self.root))

    def test_added_and_removed_public_files_fail_drift(self) -> None:
        self.render()
        self.put("site/_site/extra.bin", "extra")
        (self.published / "north-star.html").unlink()
        errors = site_drift.check(self.root)
        self.assertIn("extra: extra.bin", errors)
        self.assertIn("missing: north-star.html", errors)

    def test_deleted_target_fails_link_validation(self) -> None:
        self.render()
        (self.published / "skills/tk-example/references/models.json.html").unlink()
        errors = site_drift.validate_links(self.published)
        self.assertTrue(any("models.json.html" in error and "missing link target" in error for error in errors))

    def test_unsafe_and_escaping_output_links_fail_validation(self) -> None:
        self.render()
        self.put("site/_site/bad.html", '<a href="javascript:bad">x</a><a href="../../NORTH_STAR.md">y</a>')
        errors = site_drift.validate_links(self.published)
        self.assertTrue(any("unsafe link" in error for error in errors))
        self.assertTrue(any("escaping link" in error for error in errors))

    def test_cli_drift_exit_status_and_read_only_behavior(self) -> None:
        for name in ("site/build.py", "tests/site_drift.py", "tools/skill_frontmatter.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.render()
        self.skill.write_text(HEADER + BODY + "\nCLI change.\n", encoding="utf-8")
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(self.root / "tests/site_drift.py")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("changed: tk-example.html", result.stdout)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
