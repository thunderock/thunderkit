from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import importlib
import os
from pathlib import Path
import runpy
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
sys.path.insert(0, str(REFERENCES))

CATALOG: Final[JsonObject] = {
    "schema_version": 1, "models": {
        key: {"label": label, "provider": provider, "model_id": model_id,
              "family": family, "harnesses": [harness]}
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


class ModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = importlib.import_module("model_config")
        self.catalog = deepcopy(CATALOG)
        scratch = ROOT / ".omo-tmp"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name)

    def normalize(self, raw: JsonObject) -> tuple[JsonObject, list[str]]:
        before, catalog_before = deepcopy(raw), deepcopy(self.catalog)
        files, environment = set(self.sandbox.rglob("*")), dict(os.environ)
        try:
            with patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
                    patch("io.open", side_effect=AssertionError("unexpected file access")):
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
        return detail

    def test_live_choices_are_retained_when_version_is_absent(self) -> None:
        normalized, warnings = self.normalize(deepcopy(LIVE))
        self.assertEqual(normalized, {**LIVE, "schema_version": 2,
                                      "ecosystems": ["omo", "omh"], "delegation": "auto"})
        self.assertEqual(warnings, ["schema_version absent; assuming 2"])

    def test_defaults_do_not_choose_models_when_only_classes_are_supplied(self) -> None:
        classes: JsonObject = {"planner": "sol", "executors": ["opus5"], "reviewers": ["fable51"]}
        normalized, _ = self.normalize({"classes": classes})
        self.assertEqual(normalized, {"schema_version": 2, "classes": classes,
                                      "review_families_min": 2, "max_layers": 3,
                                      "frozen_paths": [], "ecosystems": ["omo", "omh"],
                                      "delegation": "auto"})

    def test_explicit_options_are_retained_when_they_differ_from_defaults(self) -> None:
        raw: JsonObject = {**deepcopy(LIVE), "schema_version": 2, "review_families_min": 4,
                           "max_layers": 1, "ecosystems": [], "delegation": "off", "decided_at": "",
                           "frozen_paths": [".", "./src/file", "folder\\file", "name..md"]}
        normalized, warnings = self.normalize(raw)
        self.assertEqual(normalized, raw)
        self.assertEqual(warnings, [])

    def test_output_is_detached_when_caller_changes_a_normalized_list(self) -> None:
        raw = deepcopy(LIVE)
        normalized, _ = self.normalize(raw)
        classes = normalized["classes"]
        assert isinstance(classes, dict)
        executors = classes["executors"]
        assert isinstance(executors, list)
        executors.append("sol")
        self.assertEqual(raw, LIVE)

    def test_all_reviewers_include_models_outside_planner_and_executors(self) -> None:
        cfg, _ = self.normalize({"classes": {"planner": "opus48", "executors": ["opus48"],
                                             "reviewers": "all"}})
        before = deepcopy(cfg)
        selected = self.api.selected_models(cfg, self.catalog)
        self.assertEqual(selected, {"planner": "opus48", "executors": ["opus48"],
                                    "reviewers": ["fable51", "opus48", "opus5", "sol"],
                                    "reviewers_mode": "all", "explicit": ["opus48"],
                                    "candidates": ["fable51", "opus48", "opus5", "sol"]})
        self.assertEqual(cfg, before)
        self.assertEqual(self.catalog, CATALOG)

    def test_explicit_selections_keep_order_and_deduplicate_required_keys(self) -> None:
        cfg, _ = self.normalize({"classes": {"planner": "opus5", "executors": ["sol", "opus48"],
                                             "reviewers": ["sol", "opus5"]}})
        selected = self.api.selected_models(cfg, self.catalog)
        self.assertEqual(selected, {"planner": "opus5", "executors": ["sol", "opus48"],
                                    "reviewers": ["sol", "opus5"], "reviewers_mode": "explicit",
                                    "explicit": ["opus48", "opus5", "sol"], "candidates": []})

    def test_complete_legacy_schema_is_converted_only_in_memory(self) -> None:
        reviews: tuple[JsonValue, ...] = (["sol", "opus5"], "all")
        for review in reviews:
            for version in (None, 1, 2):
                with self.subTest(review=review, version=version):
                    raw = deepcopy(LIVE)
                    del raw["classes"]
                    raw["models"] = {"plan": "opus48", "critical_path": "opus5", "review": review}
                    if version is not None:
                        raw["schema_version"] = version
                    normalized, warnings = self.normalize(raw)
                    self.assertEqual(normalized, {**LIVE, "schema_version": 2,
                        "classes": {"planner": "opus48", "executors": ["opus5"], "reviewers": review},
                        "ecosystems": ["omo", "omh"], "delegation": "auto"})
                    self.assertEqual(warnings, ["legacy models schema converted (preview only; not saved)"])

    def test_invalid_fields_report_the_key_without_side_effects(self) -> None:
        cases: dict[str, list[JsonValue]] = {
            "schema_version": [1, 3, "2", 2.0, True, None],
            "classes": [None, [], {}],
            "classes.planner": [None, [], {}, 42, "", "missing"],
            "classes.executors": [None, "opus48", [], ["opus48", "opus48"], [1], [[]], ["missing"]],
            "classes.reviewers": [None, "opus48", [], ["sol", "sol"], [True], ["all"], ["missing"]],
            "review_families_min": [1, "2", 2.0, True, None],
            "max_layers": [0, "1", 1.0, True, None],
            "frozen_paths": ["LICENSE", ["../x"], ["a/../x"], ["/abs"], ["a\\..\\x"],
                             ["C:\\x"], ["C:x"], [""], ["\u0000"], [2], [None]],
            "ecosystems": ["omo", ["unsupported"], [False], None],
            "delegation": ["maybe", None, True, []],
            "decided_at": [20260904, None],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    raw = deepcopy(LIVE)
                    parent = raw["classes"] if field.startswith("classes.") else raw
                    assert isinstance(parent, dict)
                    parent[field.split(".")[-1]] = value
                    self.assertIn(field, self.error_detail(lambda: self.normalize(raw)))

    def test_mixed_or_incomplete_legacy_schema_is_rejected(self) -> None:
        complete: JsonObject = {"plan": "opus48", "critical_path": "opus5", "review": ["sol"]}
        cases: list[JsonObject] = [{**deepcopy(LIVE), "models": complete}, {"models": None}]
        cases.extend({"models": {key: value for key, value in complete.items() if key != absent}}
                     for absent in complete)
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.error_detail(lambda: self.normalize(raw)),
                                 "mixed or incomplete legacy schema")

    def test_invalid_complete_legacy_fields_name_the_original_key(self) -> None:
        cases: tuple[tuple[str, JsonValue], ...] = (
            ("plan", "missing"), ("critical_path", ["opus5"]), ("review", []))
        for key, value in cases:
            with self.subTest(key=key):
                models: JsonObject = {"plan": "opus48", "critical_path": "opus5", "review": "all"}
                models[key] = value
                self.assertIn(f"models.{key}", self.error_detail(lambda: self.normalize({"models": models})))

    def test_catalog_and_missing_classes_are_validated(self) -> None:
        self.assertIn("classes", self.error_detail(lambda: self.normalize({"schema_version": 2})))
        invalid_models: tuple[JsonValue, ...] = (None, [], "opus48")
        for models in invalid_models:
            with self.subTest(models=models):
                self.catalog = {"models": models}
                self.assertIn("catalog.models", self.error_detail(lambda: self.normalize(deepcopy(LIVE))))

    def test_family_lookup_uses_family_not_provider(self) -> None:
        for key, family in (("opus48", "anthropic"), ("opus5", "anthropic"),
                            ("fable51", "anthropic"), ("sol", "openai")):
            with self.subTest(key=key):
                self.assertEqual(self.api.family_of(key, self.catalog), family)

    def test_distinct_families_deduplicate_and_accept_empty_input(self) -> None:
        for keys, expected in (([], set()), (["opus48", "opus5", "fable51"], {"anthropic"}),
                               (["sol", "opus5", "sol"], {"openai", "anthropic"})):
            with self.subTest(keys=keys):
                self.assertEqual(self.api.distinct_families(iter(keys), self.catalog), expected)

    def test_menu_annotates_availability_without_filtering_or_choosing(self) -> None:
        for availability in (None, {}, {"opus48": "reachable", "sol": "unreachable"}):
            with self.subTest(availability=availability):
                before = deepcopy(availability)
                rows = self.api.menu(self.catalog, availability)
                models = self.catalog["models"]
                assert isinstance(models, dict)
                expected = []
                for key, metadata in models.items():
                    assert isinstance(metadata, dict)
                    expected.append({"key": key, **{field: metadata[field] for field in
                        ("label", "provider", "model_id", "family")},
                        "available": (availability or {}).get(key, "unknown")})
                self.assertEqual(rows, expected)
                self.assertEqual(availability, before)
                self.assertEqual(self.catalog, CATALOG)

    def test_unknown_models_and_malformed_metadata_raise_config_error(self) -> None:
        self.assertIn("missing", self.error_detail(lambda: self.api.family_of("missing", self.catalog)))
        for field in ("family", "label", "provider", "model_id"):
            with self.subTest(field=field):
                catalog = deepcopy(CATALOG)
                models = catalog["models"]
                assert isinstance(models, dict)
                metadata = models["opus48"]
                assert isinstance(metadata, dict)
                del metadata[field]
                self.assertIn(f"catalog.models.opus48.{field}", self.error_detail(lambda: self.api.menu(catalog)))

    def test_load_json_reads_utf8_without_writing(self) -> None:
        path = self.sandbox / "config.json"
        payload = '{"label": "caf\u00e9", "classes": {"reviewers": "all"}}'.encode("utf-8")
        path.write_bytes(payload)
        loaded = self.api.load_json(str(path))
        self.assertEqual(loaded, {"label": "caf\u00e9", "classes": {"reviewers": "all"}})
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(list(self.sandbox.iterdir()), [path])

    def test_load_json_rejects_missing_malformed_or_non_object_documents(self) -> None:
        path = self.sandbox / "config.json"
        for payload in (None, b"{", b"[]", b"null", b"1", b'"text"', b"\xff"):
            with self.subTest(payload=payload):
                if payload is not None:
                    path.write_bytes(payload)
                self.assertIn(str(path), self.error_detail(lambda: self.api.load_json(str(path))))
                self.assertEqual(path.read_bytes() if path.exists() else None, payload)

    def test_helper_works_when_copied_to_an_isolated_skill_script(self) -> None:
        scripts = self.sandbox / "example-skill" / "scripts"
        scripts.mkdir(parents=True)
        helper = scripts / "model_config.py"
        helper.write_bytes((REFERENCES / "model_config.py").read_bytes())
        namespace = runpy.run_path(str(helper))
        normalized, _ = namespace["normalize_config"](deepcopy(LIVE), self.catalog)
        self.assertEqual(normalized["classes"], LIVE["classes"])

    def test_integration_live_config_normalizes_with_the_shared_catalog(self) -> None:
        catalog_path = REFERENCES / "models.json"
        if not catalog_path.is_file():
            self.skipTest("skills/references/models.json is absent; shared catalog not available yet")
        config_path = ROOT / ".thunderkit" / "config.json"
        before = config_path.read_bytes()
        raw = self.api.load_json(str(config_path))
        normalized, _ = self.api.normalize_config(raw, self.api.load_json(str(catalog_path)))
        for key, value in raw.items():
            self.assertEqual(normalized[key], value)
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(config_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
