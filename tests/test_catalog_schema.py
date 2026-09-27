"""Assert shipped schema/data independently and exercise production selection helpers."""

import json
import math
import re
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Final, Literal, TypedDict

from skills.references.model_config import (
    ConfigError, JsonObject, JsonValue, distinct_families, load_json, menu,
    normalize_config, selected_models,
)


class Rule(TypedDict, total=False):
    type: Literal["object", "array", "string", "integer"]
    properties: dict[str, "Rule"]
    required: list[str]
    additionalProperties: bool
    items: "Rule"
    minItems: int
    uniqueItems: bool
    enum: list[str]
    const: int
    minimum: int
    minLength: int
    pattern: str
    model_key: bool
    anyOf: list["Rule"]


class LegacyRule(Rule):
    root: str
    mapping: dict[str, str]
    wrap_in_array: list[str]


class ConfigSchema(Rule):
    schema_version: int
    defaults: JsonObject
    legacy: LegacyRule


REFERENCES: Final = Path(__file__).resolve().parents[1] / "skills" / "references"
OPERATIONAL_DEFAULTS: Final[JsonObject] = {
    "schema_version": 2, "review_families_min": 2, "max_layers": 3,
    "frozen_paths": [], "ecosystems": ["omo", "omh", "gsd"], "delegation": "auto",
}


class CatalogSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_json(str(REFERENCES / "models.json"))
        self.schema: ConfigSchema = json.loads(
            (REFERENCES / "config.schema.json").read_text(encoding="utf-8"))
        self.classes: JsonObject = {
            "planner": "sol", "executors": ["fable51"], "reviewers": ["opus5", "sol"],
        }
        self.config: JsonObject = {"classes": self.classes}

    def test_shipped_json_has_unique_object_keys_and_finite_numbers(self) -> None:
        for filename in ("models.json", "config.schema.json"):
            with self.subTest(filename=filename):
                objects: list[list[tuple[str, JsonValue]]] = []
                constants: list[str] = []
                numbers: list[float] = []
                json.loads((REFERENCES / filename).read_text(encoding="utf-8"),
                           object_pairs_hook=objects.append, parse_constant=constants.append,
                           parse_float=lambda text: numbers.append(float(text)))
                self.assertEqual(constants, [])
                self.assertTrue(all(math.isfinite(value) for value in numbers))
                for pairs in objects:
                    self.assertEqual(len(pairs), len({key for key, _ in pairs}))

    def test_model_ids_and_families_match_the_four_documented_keys(self) -> None:
        self.assertEqual(self.catalog["schema_version"], 1)
        rows = menu(self.catalog)
        self.assertEqual({row["key"]: (row["model_id"], row["family"]) for row in rows}, {
            "opus48": ("claude-opus-4-8", "anthropic"),
            "opus5": ("us.anthropic.claude-opus-5", "anthropic"),
            "fable51": ("us.anthropic.claude-fable-5-1", "anthropic"),
            "sol": ("gpt-5.6-sol", "openai"),
        })

    def test_catalog_harnesses_match_only_documented_dispatches(self) -> None:
        models = self.catalog["models"]
        assert isinstance(models, dict)
        expected = {
            "opus48": [("claude", "anthropic"), ("hermes", "anthropic")],
            "opus5": [("hermes", "bedrock"), ("opencode", "amazon-bedrock")],
            "fable51": [("hermes", "bedrock"), ("opencode", "amazon-bedrock")],
            "sol": [("codex", "openai-codex")],
        }
        for key, dispatches in expected.items():
            with self.subTest(model=key):
                model = models[key]
                assert isinstance(model, dict)
                self.assertEqual(model["harnesses"], [
                    {"harness": harness, "provider": provider, "model_id": model["model_id"]}
                    for harness, provider in dispatches])

    def test_roster_table_matches_catalog_ids_and_harnesses(self) -> None:
        roster = (REFERENCES / "model-roster.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\|[^|\n]+\| `([a-z][a-z0-9_-]*)` \| `([^`\n]+)`[^|\n]*\| ([^|\n]+) \|",
                          roster, re.MULTILINE)
        self.assertEqual(len(rows), 4)
        models = self.catalog["models"]
        assert isinstance(models, dict)
        for key, model_id, harnesses in rows:
            model = models[key]
            assert isinstance(model, dict)
            self.assertEqual(model["model_id"], model_id)
            mappings = model["harnesses"]
            assert isinstance(mappings, list)
            self.assertEqual([item["harness"] for item in mappings if isinstance(item, dict)],
                             harnesses.split(", "))

    def test_anthropic_variants_cannot_meet_two_family_minimum(self) -> None:
        families = distinct_families(["opus48", "opus5", "fable51"], self.catalog)
        self.assertEqual(families, {"anthropic"})
        self.assertLess(len(families), 2)
        self.assertEqual(self.catalog["families_min_default"], 2)

    def test_added_model_flows_into_production_menu_and_selections(self) -> None:
        catalog = deepcopy(self.catalog)
        models = catalog["models"]
        assert isinstance(models, dict)
        extra = deepcopy(models["sol"])
        assert isinstance(extra, dict)
        extra.update(label="Fixture model", model_id="fixture-model", harnesses=[
            {"harness": "codex", "provider": "openai-codex", "model_id": "fixture-model"}])
        models["fixture"] = extra
        config, _ = normalize_config({"classes": {"planner": "fixture",
            "executors": ["fixture"], "reviewers": ["opus48", "fixture"]}}, catalog)
        self.assertIn(("fixture", "Fixture model"), [(row["key"], row["label"]) for row in menu(catalog)])
        self.assertEqual(selected_models(config, catalog)["planner"], "fixture")
        self.assertEqual(distinct_families(["opus48", "fixture"], catalog), {"anthropic", "openai"})

    def test_menu_reports_availability_without_selecting_or_mutating(self) -> None:
        original = deepcopy(self.catalog)
        rows = menu(self.catalog, {"sol": "unavailable", "fable51": "available"})
        self.assertEqual({row["key"]: row["available"] for row in rows}, {
            "sol": "unavailable", "fable51": "available", "opus48": "unknown", "opus5": "unknown"})
        self.assertEqual(self.catalog, original)
        self.assertEqual(selected_models(self.config, self.catalog)["planner"], "sol")

    def test_all_reviewers_expand_beyond_planner_and_executors(self) -> None:
        self.classes["reviewers"] = "all"
        result = selected_models(self.config, self.catalog)
        self.assertEqual(result["candidates"], ["fable51", "opus48", "opus5", "sol"])
        self.assertEqual(result["reviewers"], result["candidates"])
        self.assertEqual(result["explicit"], ["fable51", "sol"])

    def test_missing_family_fails_in_production_menu(self) -> None:
        models = self.catalog["models"]
        assert isinstance(models, dict)
        model = models["opus48"]
        assert isinstance(model, dict)
        del model["family"]
        with self.assertRaises(ConfigError):
            menu(self.catalog)

    def test_schema_requires_only_explicit_complete_classes(self) -> None:
        self.assertEqual(self.schema["schema_version"], 2)
        self.assertEqual(self.schema.get("required"), ["classes"])
        rule = self.schema.get("properties", {})["classes"]
        self.assertEqual(rule.get("required"), ["planner", "executors", "reviewers"])
        properties = rule.get("properties", {})
        self.assertEqual(properties["planner"], {"type": "string", "model_key": True})
        keys = {"type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"type": "string", "model_key": True}}
        self.assertEqual(properties["executors"], keys)
        self.assertEqual(properties["reviewers"], {
            "anyOf": [{"type": "string", "enum": ["all"]}, keys]})

    def test_defaults_exclude_model_choices_and_decision_timestamp(self) -> None:
        self.assertEqual(self.schema["defaults"], OPERATIONAL_DEFAULTS)
        classes = self.catalog["classes"]
        assert isinstance(classes, dict)
        self.assertNotIn("defaults", classes)

    def test_reader_applies_only_in_memory_operational_defaults(self) -> None:
        original = deepcopy(self.config)
        normalized, warnings = normalize_config(self.config, self.catalog)
        self.assertEqual(normalized, {**OPERATIONAL_DEFAULTS, "classes": self.classes})
        self.assertEqual(self.config, original)
        self.assertTrue(warnings)

    def test_reader_preserves_explicit_options_and_decided_at(self) -> None:
        supplied = {**self.config, "schema_version": 2, "max_layers": 1,
                    "review_families_min": 3, "ecosystems": [], "delegation": "off",
                    "frozen_paths": ["src/config.json"], "decided_at": "2026-01-02"}
        normalized, warnings = normalize_config(supplied, self.catalog)
        self.assertEqual(normalized, supplied)
        self.assertEqual(warnings, [])

    def test_required_choices_are_never_filled_from_defaults(self) -> None:
        cases: list[JsonObject] = [{}, {"classes": {}}, {"models": {"plan": "sol"}},
                                  {**self.config, "models": {}}]
        cases.extend({"classes": {key: value for key, value in self.classes.items() if key != absent}}
                     for absent in self.classes)
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ConfigError):
                normalize_config(config, self.catalog)

    def test_production_reader_rejects_invalid_choices_and_counts(self) -> None:
        cases: list[tuple[str, JsonValue]] = [
            ("planner", "missing"), ("planner", ["opus48"]), ("executors", []),
            ("executors", ["opus48", "opus48"]), ("executors", ["missing"]),
            ("reviewers", []), ("reviewers", ["sol", "sol"]),
            ("reviewers", ["missing"]), ("reviewers", "sol"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                normalize_config({"classes": {**self.classes, field: value}}, self.catalog)
        for field, value in [("review_families_min", 1), ("review_families_min", 2.0),
                             ("max_layers", 0), ("max_layers", True), ("schema_version", 3),
                             ("delegation", "unknown"), ("decided_at", 1)]:
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                normalize_config({**self.config, field: value}, self.catalog)

    def test_schema_closes_objects_and_constrains_operational_values(self) -> None:
        properties = self.schema.get("properties", {})
        self.assertEqual(set(properties), {*OPERATIONAL_DEFAULTS, "classes", "decided_at"})
        for rule in (self.schema, properties["classes"], self.schema["legacy"]):
            self.assertIs(rule.get("additionalProperties"), False)
        self.assertEqual(properties["schema_version"], {"type": "integer", "const": 2})
        self.assertEqual(properties["max_layers"], {"type": "integer", "minimum": 1})
        self.assertEqual(properties["review_families_min"], {"type": "integer", "minimum": 2})
        self.assertEqual(properties["decided_at"].get("type"), "string")
        self.assertEqual(properties["ecosystems"], {"type": "array", "uniqueItems": True,
                         "items": {"type": "string", "enum": ["omo", "omh", "gsd"]}})
        self.assertEqual(properties["delegation"], {"type": "string", "enum": ["auto", "off"]})

    def test_frozen_path_pattern_rejects_escape_and_nonportable_paths(self) -> None:
        rule = self.schema.get("properties", {})["frozen_paths"].get("items", {})
        self.assertEqual((rule.get("type"), rule.get("minLength")), ("string", 1))
        pattern = rule.get("pattern")
        assert pattern is not None
        for path in ("", "/absolute", "../outside", "src/../outside", "src/..", "C:relative",
                     "C:/absolute", "src\\x.py", "\\\\server\\share", "src/\0x", "src/\nx",
                     "src/\tx", "src/\rx", "src/\x7fx", "src/\n/../outside"):
            with self.subTest(path=path):
                self.assertIsNone(re.fullmatch(pattern, path))
        for path in ("src/config.json", ".github/workflows/check.yml", "src/my file.py", ".", "./src"):
            with self.subTest(path=path):
                self.assertIsNotNone(re.fullmatch(pattern, path))

    def test_legacy_schema_uses_the_same_review_choices(self) -> None:
        legacy = self.schema["legacy"]
        self.assertEqual(legacy["root"], "models")
        self.assertEqual(legacy.get("required"), ["plan", "critical_path", "review"])
        self.assertEqual(set(legacy.get("properties", {})), {"plan", "critical_path", "review"})
        self.assertEqual(legacy["mapping"], {"plan": "classes.planner",
            "critical_path": "classes.executors", "review": "classes.reviewers"})
        self.assertEqual(legacy["wrap_in_array"], ["critical_path"])
        self.assertEqual(legacy.get("properties", {})["review"],
                         self.schema.get("properties", {})["classes"].get("properties", {})["reviewers"])

    def test_complete_legacy_review_all_and_lists_are_read_only_previews(self) -> None:
        reviews: list[JsonValue] = ["all", ["opus5", "sol"]]
        versions: list[JsonObject] = [{}, {"schema_version": 1}, {"schema_version": 2}]
        for reviewers in reviews:
            for version in versions:
                config: JsonObject = {**version, "models": {"plan": "sol",
                                      "critical_path": "fable51", "review": reviewers}}
                original = deepcopy(config)
                normalized, warnings = normalize_config(config, self.catalog)
                self.assertEqual(normalized, {**OPERATIONAL_DEFAULTS, "classes": {
                    **self.classes, "reviewers": reviewers}})
                self.assertEqual(config, original)
                self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
