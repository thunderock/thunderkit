"""Check the portable catalog and configuration contracts using only the stdlib."""

import json
import re
import unittest
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NotRequired, TypeAlias, TypedDict, assert_never

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class Harness(TypedDict):
    harness: str
    provider: str
    model_id: str


class Model(TypedDict):
    label: str
    provider: str
    model_id: str
    family: NotRequired[str]
    harnesses: list[Harness]
    auth: str
    character: str


class Catalog(TypedDict):
    schema_version: int
    models: dict[str, Model]
    classes: JsonObject
    families_min_default: int


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


class ConfigSchema(Rule):
    schema_version: int
    defaults: JsonObject
    legacy: JsonObject


@dataclass(frozen=True, slots=True)
class ContractError(ValueError):
    field: str

    def __str__(self) -> str:
        return self.field


def unique_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(key)
        result[key] = value
    return result


DISPATCH_PROVIDERS: Final = {
    ("anthropic", "claude"): "anthropic",
    ("bedrock", "hermes"): "bedrock",
    ("bedrock", "opencode"): "amazon-bedrock",
    ("openai-codex", "codex"): "openai-codex",
}


def validate_catalog(catalog: Catalog) -> None:
    if catalog["schema_version"] != 1 or not catalog["models"]:
        raise ContractError("catalog")
    for key, model in catalog["models"].items():
        texts = (model.get("label"), model.get("provider"), model.get("model_id"),
                 model.get("family"), model.get("auth"), model.get("character"))
        if not all(isinstance(value, str) and value for value in texts):
            raise ContractError(key)
        if not model["harnesses"]:
            raise ContractError(f"{key}.harnesses")
        for dispatch in model["harnesses"]:
            provider = DISPATCH_PROVIDERS.get((model["provider"], dispatch["harness"]))
            if provider != dispatch["provider"] or dispatch["model_id"] != model["model_id"]:
                raise ContractError(f"{key}.harnesses")


def accepts(value: JsonValue, rule: Rule, catalog: Catalog) -> bool:
    match rule.get("type"):
        case "object":
            if not isinstance(value, dict):
                return False
            properties = rule.get("properties", {})
            return (set(rule.get("required", [])) <= value.keys()
                    and (rule.get("additionalProperties", True) or value.keys() <= properties.keys())
                    and all(accepts(item, properties[key], catalog)
                            for key, item in value.items() if key in properties))
        case "array":
            if not isinstance(value, list):
                return False
            return (len(value) >= rule.get("minItems", 0)
                    and (not rule.get("uniqueItems") or all(value.count(item) == 1 for item in value))
                    and all(accepts(item, rule.get("items", {}), catalog) for item in value))
        case "string":
            if not isinstance(value, str):
                return False
            return (len(value) >= rule.get("minLength", 0)
                    and ("enum" not in rule or value in rule["enum"])
                    and (not rule.get("model_key") or value in catalog["models"])
                    and ("pattern" not in rule or re.fullmatch(rule["pattern"], value) is not None))
        case "integer":
            return (type(value) is int and value >= rule.get("minimum", 0)
                    and ("const" not in rule or value == rule["const"]))
        case None:
            return any(accepts(value, option, catalog) for option in rule.get("anyOf", []))
        case unreachable:
            assert_never(unreachable)


def validate_config(cfg: JsonObject, catalog: Catalog) -> None:
    validate_catalog(catalog)
    if not accepts(cfg, SCHEMA, catalog):
        raise ContractError("config")


def model_menu(catalog: Catalog) -> list[tuple[str, str]]:
    return [(key, model["label"]) for key, model in catalog["models"].items()]


def distinct_families(keys: Iterable[str], catalog: Catalog) -> set[str]:
    families: set[str] = set()
    for key in keys:
        model = catalog["models"][key]
        if "family" not in model:
            raise ContractError(f"{key}.family")
        families.add(model["family"])
    return families


ROOT: Final = Path(__file__).resolve().parents[1]
REFERENCES: Final = ROOT / "skills" / "references"
SCHEMA: Final[ConfigSchema] = json.loads(
    (REFERENCES / "config.schema.json").read_text(encoding="utf-8"), object_pairs_hook=unique_object,
)


class CatalogSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog: Catalog = json.loads((REFERENCES / "models.json").read_text(encoding="utf-8"),
                                           object_pairs_hook=unique_object)
        self.config = deepcopy(SCHEMA["defaults"])
        self.roster = (REFERENCES / "model-roster.md").read_text(encoding="utf-8")

    def test_model_ids_match_the_four_documented_keys(self) -> None:
        expected = {"opus48": "claude-opus-4-8", "opus5": "us.anthropic.claude-opus-5",
                    "fable51": "us.anthropic.claude-fable-5-1", "sol": "gpt-5.6-sol"}
        actual = {key: model["model_id"] for key, model in self.catalog["models"].items()}
        self.assertEqual(actual, expected)
        for model_id in actual.values():
            self.assertIn(f"`{model_id}`", self.roster)

    def test_families_match_the_provider_lineages(self) -> None:
        actual = {key: model.get("family") for key, model in self.catalog["models"].items()}
        self.assertEqual(actual, {"opus48": "anthropic", "opus5": "anthropic",
                                  "fable51": "anthropic", "sol": "openai"})

    def test_anthropic_variants_count_as_one_family(self) -> None:
        actual = distinct_families({"opus48", "opus5", "fable51"}, self.catalog)
        self.assertEqual(actual, {"anthropic"})
        self.assertLess(len(actual), self.catalog["families_min_default"])

    def test_harnesses_are_only_the_documented_dispatches(self) -> None:
        actual = {key: [(item["harness"], item["provider"]) for item in model["harnesses"]]
                  for key, model in self.catalog["models"].items()}
        self.assertEqual(actual, {
            "opus48": [("claude", "anthropic")], "sol": [("codex", "openai-codex")],
            "opus5": [("hermes", "bedrock"), ("opencode", "amazon-bedrock")],
            "fable51": [("hermes", "bedrock"), ("opencode", "amazon-bedrock")],
        })

    def test_added_model_flows_through_config_menu_and_family_count(self) -> None:
        catalog = deepcopy(self.catalog)
        extra = deepcopy(catalog["models"]["sol"])
        extra.update(label="Fixture model", model_id="fixture-model",
                     harnesses=[{"harness": "codex", "provider": "openai-codex",
                                 "model_id": "fixture-model"}])
        catalog["models"]["fixture"] = extra
        self.config["classes"] = {"planner": "fixture", "executors": ["fixture"],
                                  "reviewers": ["opus48", "fixture"]}
        validate_config(self.config, catalog)
        self.assertIn(("fixture", "Fixture model"), model_menu(catalog))
        self.assertEqual(distinct_families(["opus48", "fixture"], catalog), {"anthropic", "openai"})
        self.assertNotIn("fixture", self.catalog["models"])

    def test_defaults_match_the_catalog(self) -> None:
        self.assertEqual(SCHEMA["schema_version"], 2)
        self.assertEqual(self.catalog["families_min_default"], 2)
        self.assertEqual(self.config, {
            "schema_version": 2,
            "classes": {"planner": "opus48", "executors": ["opus48", "opus5", "fable51"], "reviewers": "all"},
            "review_families_min": 2, "max_layers": 3, "frozen_paths": [],
            "ecosystems": ["omo", "omh"], "delegation": "auto", "decided_at": "",
        })
        self.assertEqual(self.config["classes"], self.catalog["classes"]["defaults"])

    def test_current_and_versionless_configs_validate_without_mutation(self) -> None:
        live: JsonObject = json.loads((ROOT / ".thunderkit" / "config.json").read_text(encoding="utf-8"))
        manual = {**self.config, "delegation": "off", "ecosystems": [], "max_layers": 1}
        single = {**self.config, "ecosystems": ["omh"], "frozen_paths": ["src/config.json"]}
        for config in (self.config, live, manual, single):
            with self.subTest(config=config):
                original = deepcopy(config)
                validate_config(config, self.catalog)
                self.assertEqual(config, original)

    def test_schema_rejects_invalid_config_values(self) -> None:
        cases: list[tuple[str, JsonValue]] = [
            ("classes.planner", "missing"), ("classes.executors", []),
            ("classes.executors", ["opus48", "opus48"]), ("classes.executors", ["missing"]),
            ("classes.reviewers", []), ("classes.reviewers", ["sol", "sol"]),
            ("classes.reviewers", ["missing"]), ("classes.reviewers", "sol"),
            ("review_families_min", 1), ("review_families_min", 2.0),
            ("max_layers", 0), ("max_layers", True), ("schema_version", 3),
            ("frozen_paths", ["/absolute"]), ("frozen_paths", ["src/../outside"]),
            ("frozen_paths", ["C:\\absolute"]), ("frozen_paths", [""]),
            ("ecosystems", ["unknown"]), ("ecosystems", ["omo", "omo"]),
            ("delegation", "unknown"), ("decided_at", 1), ("models", {}),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                config = deepcopy(self.config)
                parent = config
                parts = field.split(".")
                for part in parts[:-1]:
                    child = parent[part]
                    assert isinstance(child, dict)
                    parent = child
                parent[parts[-1]] = value
                with self.assertRaises(ContractError):
                    validate_config(config, self.catalog)

    def test_duplicate_json_keys_are_rejected_before_loading(self) -> None:
        for raw in ('{"models":{"opus48":{},"opus48":{}}}',
                    '{"classes":{"planner":"sol","planner":"opus48"}}'):
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                json.loads(raw, object_pairs_hook=unique_object)

    def test_missing_family_is_rejected(self) -> None:
        del self.catalog["models"]["opus48"]["family"]
        with self.assertRaises(ContractError):
            validate_config(self.config, self.catalog)

    def test_invented_harness_mappings_are_rejected(self) -> None:
        for harness, provider in (("codex", "openai-codex"), ("opencode", "bedrock"),
                                  ("unknown", "bedrock")):
            with self.subTest(harness=harness, provider=provider):
                model = self.catalog["models"]["opus5"]
                model["harnesses"] = [{"harness": harness, "provider": provider,
                                       "model_id": model["model_id"]}]
                with self.assertRaises(ContractError):
                    validate_config(self.config, self.catalog)

    def test_legacy_mapping_recognizes_only_the_documented_names(self) -> None:
        legacy = SCHEMA["legacy"]
        self.assertEqual(legacy["root"], "models")
        self.assertEqual(legacy["mapping"], {"plan": "classes.planner",
                                           "critical_path": "classes.executors",
                                           "review": "classes.reviewers"})
        self.assertEqual(legacy["wrap_in_array"], ["critical_path"])
        self.assertEqual(legacy["required"], ["plan", "critical_path", "review"])
        self.assertIs(legacy["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
