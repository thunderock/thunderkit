"""Run global setup through removal from a lone relocated skill payload."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_checkpoint_cli_global_schedule as CLI
import test_checkpoint_global_schedule as F

ROOT = Path(__file__).resolve().parents[1]


class GlobalPayloadTests(unittest.TestCase):
    def test_materialized_global_cli_survives_source_and_sibling_removal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="global-payload-", dir=os.environ["TMPDIR"]) as temporary:
            self.base = Path(temporary)
            source = self.base / "source"
            references = source / "skills/references"
            shutil.copytree(ROOT / "skills/references", references,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for marker in sorted((ROOT / "skills").glob("tk-*/SKILL.md")):
                destination = source / "skills" / marker.parent.name / "SKILL.md"
                destination.parent.mkdir()
                shutil.copyfile(marker, destination)
            command = [sys.executable, "-B", str(ROOT / "tools/materialize_skills.py"), "--root", str(source)]
            generated = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            installed = self.base / "relocated"
            shutil.copytree(source / "skills/tk-restore", installed)
            shutil.rmtree(source)
            self.assertFalse(source.exists())
            scripts = installed / "scripts"
            self.assertTrue((scripts / "checkpoint_global.py").is_file())
            forbidden = str(ROOT / "skills")
            audit = F.CHILD_GUARD + f'''
from pathlib import Path
def payload_reads(event,args):
    if event=='open' and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).absolute()
        if path.is_relative_to(Path({forbidden!r})):
            rejected.append('source_or_sibling_read')
            raise RuntimeError('Relocated payload read original sources')
sys.addaudithook(payload_reads)
'''
            with patch.object(F, "REFERENCES", scripts), patch.object(CLI, "CLI", scripts / "checkpoint_cli.py"), \
                    patch.object(F, "CHILD_GUARD", audit), patch.dict(sys.modules):
                for name in ("continuity_config", "backup_store", "checkpoint_push", "checkpoint_runtime",
                             "checkpoint_setup", "checkpoint_host", "checkpoint_global"):
                    sys.modules.pop(name, None)
                case = CLI.GlobalScheduleCLITests()
                try:
                    case.setUp()
                    case.test_setup_preview_install_tick_and_remove_use_real_global_scope()
                finally:
                    self.assertTrue(case.doCleanups())
                    self.assertFalse(case.c.base.exists())
        self.assertFalse(self.base.exists())


if __name__ == "__main__":
    unittest.main()
