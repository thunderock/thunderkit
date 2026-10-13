"""Runtime and JavaScript-regex regressions for continuity metadata."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/references"))
import continuity_config as c


def manifest() -> c.JsonObject:
    return {
        "schema": 1, "identity": {"kind": "global", "id": "notes"},
        "snapshot_id": "snapshot-1", "timestamp": "2026-01-01T00:00:00Z",
        "repository": None, "ref": None, "head": None, "base": None,
        "files": [{"relpath": "note.md", "size": 4, "sha256": "a" * 64, "mode": 420}],
        "deleted_paths": [], "context_entrypoints": ["note.md"],
        "history": {"status": "not-applicable", "file": None, "size": 0,
                    "sha256": None, "ref": None, "head": None},
        "omissions": [], "completion": "complete",
    }


class ContinuityManifestTests(unittest.TestCase):
    def test_schema_paths_reject_traversal_across_unicode_line_separators(self) -> None:
        script = r'''
          const fs = require('node:fs');
          const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
          process.stdout.write(JSON.stringify(rows.map(([pattern, input]) => new RegExp(pattern).test(input))));
        '''
        schemas = {name: json.loads((ROOT / f"skills/references/{name}.schema.json").read_text())
                   for name in ("continuity", "backup-manifest")}
        patterns = [
            (schemas["continuity"]["$defs"]["path"]["pattern"], "x{sep}/../../outside", "context.md"),
            (schemas["backup-manifest"]["$defs"]["relpath"]["pattern"], "x{sep}/../../outside", "payload/context.md"),
            (schemas["backup-manifest"]["$defs"]["absolute-path"]["pattern"], "/x{sep}/../outside", "/approved/context.md"),
            (schemas["backup-manifest"]["$defs"]["ref"]["pattern"], "refs/heads/x{sep}/../outside", "refs/heads/work"),
        ]
        for separator in ("\u2028", "\u2029"):
            rows = [(pattern, invalid.format(sep=separator)) for pattern, invalid, _ in patterns]
            rows += [(pattern, valid) for pattern, _, valid in patterns]
            process = subprocess.run(["node", "-e", script], input=json.dumps(rows),
                                     capture_output=True, text=True, timeout=10,
                                     env={"PATH": os.environ["PATH"]})
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), [False] * 4 + [True] * 4)

    def test_cli_structural_validation_accepts_schema_bounds_not_default_capture_limits(self) -> None:
        raw = manifest()
        files = raw["files"]
        assert isinstance(files, list) and isinstance(files[0], dict)
        files[0]["size"] = 20 * 1024 * 1024
        # Actual payload consumers keep safe defaults and may supply stricter policy.
        with self.assertRaises(c.ContinuityError) as caught:
            c.validate_manifest(raw, limits=c.Limits())
        self.assertEqual(caught.exception.code, "invalid_manifest")
        approved = c.validate_manifest(raw, limits=c.Limits(max_file_bytes=30 * 1024 * 1024))
        self.assertEqual(approved.files[0].size, 20 * 1024 * 1024)
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(raw))
            process = subprocess.run([sys.executable, str(ROOT / "skills/references/continuity_config.py"),
                                      "validate", str(path), "--kind", "manifest"],
                                     capture_output=True, text=True, timeout=10,
                                     env={"PATH": os.environ["PATH"], "HOME": temporary})
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertTrue(result["valid"])
            self.assertFalse(result["integrity_verified"])

    def test_manifest_consistency_refuses_inert_but_contradictory_metadata(self) -> None:
        for name in ("duplicate", "prefix", "entrypoint", "deleted", "omitted", "mode", "unknown"):
            with self.subTest(case=name):
                raw = manifest()
                files = raw["files"]
                assert isinstance(files, list) and isinstance(files[0], dict)
                if name == "duplicate":
                    files.append(deepcopy(files[0]))
                elif name == "prefix":
                    files.append({"relpath": "note.md/child", "size": 0, "sha256": "b" * 64, "mode": 420})
                elif name == "entrypoint":
                    raw["context_entrypoints"] = ["missing.md"]
                elif name == "deleted":
                    raw["deleted_paths"] = ["note.md"]
                elif name == "omitted":
                    raw["omissions"] = ["omitted source"]
                elif name == "mode":
                    files[0]["mode"] = 0o4755
                else:
                    raw["execute"] = "not authority"
                with self.assertRaises(c.ContinuityError) as caught:
                    c.validate_manifest(raw)
                self.assertEqual(caught.exception.code, "invalid_manifest")

    def test_json_reader_bounds_and_filesystem_refusals(self) -> None:
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}',
                    b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}'):
            with self.assertRaises(c.ContinuityError) as caught:
                c.decode_json(raw)
            self.assertEqual(caught.exception.code, "invalid_config")
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temporary:
            root = Path(temporary)
            target = root / "document.json"
            target.write_text('{"schema":1}')
            os.link(target, root / "alias.json")
            with self.assertRaises(c.ContinuityError) as caught:
                c.read_json(target)
            self.assertEqual(caught.exception.code, "unsafe_path")
            with self.assertRaises(c.ContinuityError):
                c.decode_json(b'{"x":"oversized"}', max_bytes=4)


if __name__ == "__main__":
    unittest.main()
