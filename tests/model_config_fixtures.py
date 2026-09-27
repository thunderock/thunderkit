from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Final, TypeAlias, TypeVar
import unittest
from unittest.mock import patch

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
Result = TypeVar("Result")
ROOT: Final = Path(__file__).resolve().parents[1]
REFERENCES: Final = ROOT / "skills" / "references"
SENSITIVE: Final = "sensitive-input-sentinel"
sys.path.insert(0, str(REFERENCES))

CATALOG: Final[JsonObject] = {
    "schema_version": 1, "models": {
        key: {"label": label, "provider": provider, "model_id": model_id,
              "family": family, "harnesses": [
                  {"harness": harness, "provider": provider, "model_id": model_id}]}
        for key, label, provider, model_id, family, harness in (
            ("opus48", "Opus 4.8", "anthropic", "claude-opus-4-8", "anthropic", "claude"),
            ("opus5", "Opus 5", "bedrock", "us.anthropic.claude-opus-5", "anthropic", "hermes"),
            ("fable51", "Fable 5.1", "bedrock", "us.anthropic.claude-fable-5-1", "anthropic", "hermes"),
            ("sol", "Sol", "openai-codex", "gpt-5.6-sol", "openai", "codex"),
        )
    },
    "classes": {"planner": "opus48", "executors": ["opus48"], "reviewers": "all"},
    "families_min_default": 2,
}
LIVE: Final[JsonObject] = {
    "classes": {"planner": "opus48", "executors": ["opus48", "opus5", "fable51"],
                "reviewers": ["opus48", "opus5", "fable51", "sol"]},
    "review_families_min": 2, "max_layers": 3, "frozen_paths": ["LICENSE"],
    "decided_at": "2026-09-04",
}


class ModelConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        self.api = importlib.import_module("model_config")
        self.catalog = deepcopy(CATALOG)
        scratch = ROOT / ".omo-tmp"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", str(scratch)))
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)

    def normalize(self, raw: JsonObject) -> tuple[JsonObject, list[str]]:
        before, catalog_before = deepcopy(raw), deepcopy(self.catalog)
        files, environment = set(self.sandbox.rglob("*")), dict(os.environ)
        try:
            with patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
                    patch("io.open", side_effect=AssertionError("unexpected file access")), \
                    patch("subprocess.Popen", side_effect=AssertionError("unexpected process")), \
                    patch("os.system", side_effect=AssertionError("unexpected process")), \
                    patch("socket.socket", side_effect=AssertionError("unexpected network")):
                result: tuple[JsonObject, list[str]] = self.api.normalize_config(raw, self.catalog)
                return result
        finally:
            self.assertEqual(raw, before)
            self.assertEqual(self.catalog, catalog_before)
            self.assertEqual(set(self.sandbox.rglob("*")), files)
            self.assertEqual(dict(os.environ), environment)

    def error_detail(self, operation: Callable[[], Result]) -> str:
        with self.assertRaises(self.api.ConfigError) as caught:
            operation()
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.code, "invalid_config")
        detail: str = caught.exception.detail
        self.assertNotIn(SENSITIVE, detail)
        return detail
