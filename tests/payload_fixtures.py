from __future__ import annotations

from collections.abc import Sequence
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Final, Literal, TypeAlias, assert_never
import unittest

from resolution_fixtures import JsonObject as JsonObject, mapping, read_json, write_json
from resolution_fixtures import snapshot as capability_snapshot

ROOT: Final = Path(__file__).resolve().parents[1]
TOOL: Final = ROOT / "tools" / "materialize_skills.py"
REFERENCES: Final = ROOT / "skills" / "references"
SCRATCH: Final = Path(os.environ.get("THUNDERKIT_TEST_TMPDIR", str(ROOT / ".thunderkit" / "runs")))
EXPECTED: Final = (
    "references/models.json",
    "references/dependencies.json",
    "references/model-roster.md",
    "references/delegation.md",
    "references/config.schema.json",
    "scripts/model_config.py",
    "scripts/capability_gates.py",
    "scripts/tk-resolve.py",
    "scripts/peer_lock.py",
)
FIXTURE_SKILLS: Final = ("tk-x", "tk-test")
OWNED_SKILLS: Final = frozenset({"tk-router", "tk-test", "tk-ask", "tk-docs", "tk-memory",
                               "tk-handoff", "tk-verify-work", "tk-quick"})
MANAGED_PATHS: Final = (
    "skills", "skills/references", "skills/tk-x", "skills/tk-x/SKILL.md",
    "skills/tk-test/references", "skills/tk-test/scripts",
    *(f"skills/references/{Path(asset).name}" for asset in EXPECTED),
    *(f"skills/tk-test/{asset}" for asset in EXPECTED),
)
RUNTIME_ASSETS: Final = ("references/models.json", "references/dependencies.json",
                         "scripts/model_config.py", "scripts/capability_gates.py", "scripts/tk-resolve.py",
                         "scripts/peer_lock.py")
Damage: TypeAlias = Literal["missing", "corrupt", "empty", "syntax", "directory", "fifo", "file"]
SOURCE_KINDS: Final[tuple[Damage, ...]] = ("missing", "corrupt", "empty", "syntax")
SOURCE_CASES: Final[tuple[tuple[str, Damage], ...]] = tuple(
    (asset, kind) for asset in EXPECTED for kind in SOURCE_KINDS
    if kind != "syntax" or Path(asset).suffix != ".md")
REGISTRY_PATH_CASES: Final[tuple[tuple[str, Damage], ...]] = (
    ("skills/tk-rogue/SKILL.md", "file"), ("skills/tk-x/SKILL.md", "missing"),
    ("skills/tk-x/SKILL.md", "directory"), ("skills/tk-x", "missing"), ("skills/tk-x", "file"),
)
INVALID_REGISTRIES: Final = (
    b"[]", b"{}", b'{"schema_version":true,"skills":{"tk-x":{},"tk-test":{}}}',
    b'{"schema_version":2,"skills":[]}', b'{"schema_version":2,"skills":{}}',
    b'{"schema_version":2,"skills":{"tk-x":null,"tk-test":{}}}',
    b'{"schema_version":2,"skills":{"tk-x":{},"tk-x":{},"tk-test":{}}}',
    b'{"schema_version":1,"skills":{"tk-x":{},"tk-test":{}}}',
    *(json.dumps({"schema_version": 2, "skills": {key: {}}}).encode()
      for key in ("../tk-x", "/tk-x", "tk-x/../y", "other", "tk-", "tk-x\\y")),
)
TreeState: TypeAlias = tuple[tuple[str, int, int, str], ...]
IMPORT_PROBE: Final = """
import sys
sys.dont_write_bytecode = True
import __future__
import argparse
import collections.abc
import copy
import dataclasses
import hashlib
import math
import os
import stat
import typing
import json
from pathlib import Path
skill = Path(sys.argv[1])
script = skill / "scripts" / "tk-resolve.py"
namespace = {"__name__": "payload_probe", "__file__": str(script)}
sys.dont_write_bytecode = False
exec(compile(script.read_bytes(), str(script), "exec"), namespace)
api = namespace["model_config"]
normalized, warnings = api.normalize_config(json.loads(sys.argv[2]), api.load_json(str(skill / "references/models.json")))
print(json.dumps({"normalized": normalized, "warnings": warnings, "bytecode_policy": sys.dont_write_bytecode,
                  "module_paths": [str(Path(sys.modules[name].__file__).parent)
                                   for name in ("model_config", "capability_gates")]}))
"""


def registered_skills(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(sorted(mapping(read_json(root / "skills/references/dependencies.json")["skills"])))


def damage(path: Path, kind: Damage) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    match kind:
        case "missing":
            return
        case "corrupt":
            path.write_bytes(b"\xff")
        case "empty":
            path.write_bytes(b"")
        case "syntax":
            path.write_bytes(b"{invalid")
        case "directory":
            path.mkdir()
        case "fifo":
            os.mkfifo(path)
        case "file":
            path.write_bytes(b"not a directory")
        case unreachable:
            assert_never(unreachable)


def snapshot(root: Path) -> TreeState:
    entries = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        if path.is_symlink():
            content = os.readlink(path).encode()
        else:
            content = path.read_bytes() if path.is_file() else b""
        entries.append((str(path.relative_to(root)), metadata.st_mode,
                        metadata.st_mtime_ns, hashlib.sha256(content).hexdigest()))
    return tuple(entries)


def config() -> JsonObject:
    return {"schema_version": 2, "classes": {
        "planner": "opus5", "executors": ["fable51"], "reviewers": ["sol"],
    }}


class PayloadFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.maxDiff = None
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="skill-payloads-", dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)
        self.env = {"PATH": os.defpath, "HOME": str(self.sandbox / "home"),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "", "TMPDIR": str(self.sandbox),
                    "PYTHONPYCACHEPREFIX": str(self.sandbox / "bytecode")}

    def make_repo(self, name: str = "repo") -> Path:
        root = self.sandbox / name
        canonical = root / "skills" / "references"
        canonical.mkdir(parents=True)
        for relative in EXPECTED:
            shutil.copyfile(REFERENCES / Path(relative).name, canonical / Path(relative).name)
        registry = read_json(canonical / "dependencies.json")
        registry["skills"] = {name: {"role": "fixture", "default_operation": "check",
                                     "operations": ["check"], "targets": []} for name in FIXTURE_SKILLS}
        write_json(canonical / "dependencies.json", registry)
        for skill_name in FIXTURE_SKILLS:
            skill = root / "skills" / skill_name
            skill.mkdir()
            (skill / "SKILL.md").write_bytes(b"# Fixture skill\n")
        return root

    def cli(self, root: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        self.assertTrue(TOOL.is_file(), "materialize_skills.py has not been implemented")
        argv = [sys.executable, str(TOOL)]
        if root is not None:
            argv.extend(("--root", str(root)))
        if check:
            argv.append("--check")
        return self.run_cli(argv)

    def run_cli(self, argv: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=self.sandbox if cwd is None else cwd, env=self.env, capture_output=True,
                              text=True, timeout=30, check=False)

    def generate(self, root: Path) -> None:
        result = self.cli(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_inventory(self, skill: Path) -> None:
        owned = {"scripts/tk-test.py", "scripts/preflight_protocols.py"} if skill.name == "tk-test" else set()
        actual = {path.relative_to(skill).as_posix() for path in skill.rglob("*")
                  if path.is_symlink() or (path.is_file() and path.suffix != ".pyc")}
        self.assertEqual(actual, {"SKILL.md", *EXPECTED, *owned})

    def load_module(self, path: Path) -> ModuleType:
        self.assertTrue(path.is_file(), str(path))
        spec = importlib.util.spec_from_file_location(f"payload_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        return module

    def isolated_skill(self, name: str = "tk-plan") -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="isolated-", dir=self.sandbox)
        self.addCleanup(temporary.cleanup)
        return Path(shutil.copytree(ROOT / "skills" / name, Path(temporary.name) / name))

    def resolver_arguments(self, skill: Path) -> list[str]:
        project = skill.parent / "project"
        project.mkdir()
        cfg, caps = project / "config.json", project / "capabilities.json"
        write_json(cfg, config())
        host = {"tk-debug": "opencode", "tk-fast": "claude"}.get(skill.name, "hermes")
        write_json(caps, capability_snapshot(host, {}, {}))
        return [sys.executable, "-S", str(skill / "scripts/tk-resolve.py"), "--skill", skill.name,
                "--config", str(cfg), "--capabilities", str(caps), "--project-root", str(project), "--json"]

    def symlinked_path(self, root: Path, case: tuple[str, bool]) -> Path:
        relative, contained = case
        link = root / relative
        target = (root if contained else self.sandbox) / f"target-{root.name}"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists():
            link.rename(target)
        elif link.suffix:
            target.write_bytes(b"untouched")
        else:
            target.mkdir()
        link.symlink_to(target, target_is_directory=target.is_dir())
        return link

    def shadow_support(self, skill: Path) -> None:
        home = Path(self.env["HOME"]) / ".agents/skills" / skill.name
        shutil.copytree(skill, home, dirs_exist_ok=True)
        for directory in ("references", "scripts"):
            shutil.copytree(skill / directory, skill.parent / directory)
