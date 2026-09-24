"""Isolated CLI processes with documented, synthetic protocol records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Final
import unittest
from unittest.mock import patch

from resolution_fixtures import JsonObject as JsonObject, JsonValue as JsonValue, mapping, read_json, write_json

ROOT: Final = Path(__file__).resolve().parents[1]
SKILL: Final = ROOT / "skills/tk-test"
SCRATCH: Final = Path(os.environ.get("THUNDERKIT_TEST_TMPDIR", str(
    ROOT.parents[1] / ".omo/evidence/thunderkit-skill-deps-review")))
UUID: Final = "0199a213-81c0-7800-8aa1-bbab2a035a53"
HERMES_ID: Final = "20260923_120000_a1b2c3"
OPEN_ID: Final = "ses_abc123"
SENSITIVE: Final = "PRIVATE_SENTINEL_token=https://secret.invalid/?key=credential"


def claude(answer: str = "pong") -> JsonObject:
    return {"type": "result", "subtype": "success", "is_error": False, "result": answer,
            "session_id": UUID, "stop_reason": "end_turn", "num_turns": 1,
            "modelUsage": {"claude-opus-4-8": {"inputTokens": 2, "outputTokens": 1}}}


def codex(answer: str = "pong") -> list[JsonObject]:
    return [{"type": "thread.started", "thread_id": UUID}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": answer}},
            {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 1}}]


def hermes(answer: str = "pong") -> list[JsonObject]:
    return [{"type": "system", "subtype": "init", "model": "us.anthropic.claude-fable-5-1",
             "session_id": HERMES_ID, "timestamp": 0},
            {"type": "text", "text": answer, "timestamp": 1},
            {"type": "result", "session_id": HERMES_ID, "exit_code": 0, "text": answer,
             "tokens": {"input": 2, "output": 1, "total": 3}, "timestamp": 2, "duration_ms": 2}]


def opencode(answer: str = "pong") -> list[JsonObject]:
    parts: list[JsonObject] = [
        {"type": "step-start"},
        {"type": "text", "text": answer, "time": {"start": 0, "end": 1}},
        {"type": "step-finish", "reason": "stop", "tokens": {"input": 2, "output": 1}},
    ]
    return [{"type": kind, "timestamp": index, "sessionID": OPEN_ID,
             "part": dict(part, id=f"prt_{index}", sessionID=OPEN_ID, messageID="msg_1")}
            for index, (kind, part) in enumerate(zip(("step_start", "text", "step_finish"), parts))]


def jsonl(events: Sequence[JsonObject]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def config(reviewers: JsonValue = "all") -> JsonObject:
    return {"schema_version": 2, "classes": {
        "planner": "opus48", "executors": ["opus48"], "reviewers": reviewers,
    }}


@dataclass(frozen=True, slots=True)
class Stub:
    stdout: str
    returncode: int = 0
    stderr: str = ""
    hang: bool = False


STUB_PROGRAM: Final = """
import json
import os
from pathlib import Path
import signal
import sys
with open(os.environ["PREFLIGHT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": sys.argv, "pid": os.getpid()}) + "\\n")
data = json.loads(Path(__file__).with_suffix(".json").read_text())
sys.stdout.write(data["stdout"])
sys.stdout.flush()
sys.stderr.write(data["stderr"])
if data["hang"]:
    signal.pause()
sys.exit(data["returncode"])
"""


class PreflightFixture(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="preflight-", dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)
        self.skill = Path(shutil.copytree(SKILL, self.sandbox / "tk-test"))
        self.bin = self.sandbox / "bin"
        self.bin.mkdir()
        home = self.sandbox / "home"
        home.mkdir()
        self.log = self.sandbox / "launches.jsonl"
        self.config = self.sandbox / "config.json"
        write_json(self.config, config(["opus48"]))
        self.env = {"PATH": str(self.bin), "HOME": str(home), "PYTHONPATH": "",
                    "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(self.sandbox),
                    "PYTHONPYCACHEPREFIX": str(self.sandbox / "bytecode"),
                    "PREFLIGHT_LOG": str(self.log)}

    def stub(self, name: str, response: Stub) -> Path:
        executable = self.bin / name
        executable.write_text(f"#!{sys.executable}\n" + STUB_PROGRAM, encoding="utf-8")
        executable.chmod(0o700)
        write_json(executable.with_suffix(".json"), {"stdout": response.stdout,
                   "returncode": response.returncode, "stderr": response.stderr, "hang": response.hang})
        return executable

    def fleet(self) -> None:
        self.stub("claude", Stub(json.dumps(claude())))
        self.stub("codex", Stub(jsonl(codex())))
        self.stub("hermes", Stub(jsonl(hermes())))
        self.stub("opencode", Stub(jsonl(opencode())))

    def cli(self, args: Sequence[str] = ()) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-S", str(self.skill / "scripts/tk-test.py"),
                               "--config", str(self.config), *args], cwd=self.sandbox,
                              env=self.env, capture_output=True, text=True, timeout=10, check=False)

    def launches(self) -> list[JsonObject]:
        return [mapping(json.loads(line)) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def load_script(self, name: str) -> ModuleType:
        script = self.skill / "scripts" / name
        spec = importlib.util.spec_from_file_location("preflight_under_test", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules), patch.object(sys, "path", [str(script.parent), *sys.path]):
            for key in ("model_config", "preflight_protocols"):
                sys.modules.pop(key, None)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return module

    def catalog(self) -> JsonObject:
        return read_json(self.skill / "references/models.json")
