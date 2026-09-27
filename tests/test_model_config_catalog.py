from __future__ import annotations

from copy import deepcopy
import runpy
import unittest
from unittest.mock import patch

from model_config_fixtures import CATALOG, LIVE, REFERENCES, ROOT, SENSITIVE, JsonObject, JsonValue, ModelConfigCase


class ModelConfigCatalogTests(ModelConfigCase):
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
        self.assertIn("unknown", self.error_detail(lambda: self.api.family_of(SENSITIVE, self.catalog)))
        for field in ("family", "label", "provider", "model_id"):
            with self.subTest(field=field):
                catalog = deepcopy(CATALOG)
                models = catalog["models"]
                assert isinstance(models, dict)
                metadata = models["opus48"]
                assert isinstance(metadata, dict)
                del metadata[field]
                self.assertIn(field, self.error_detail(lambda: self.api.menu(catalog)))

    def test_all_consumers_reject_malformed_unselected_catalog_entries(self) -> None:
        config: JsonObject = {"classes": {"planner": "opus48", "executors": ["opus48"], "reviewers": ["opus48"]}}
        duplicate: list[JsonValue] = [
            {"harness": "codex", "provider": "openai-codex", "model_id": "gpt-5.6-sol"}] * 2
        invalid: dict[str, list[JsonValue]] = {
            **{field: [None, "", " ", "anthropic ", 1, [], {}]
               for field in ("label", "provider", "model_id", "family")},
            "harnesses": [None, [], "codex", ["codex"], [{}],
                          [{"harness": "codex", "provider": "openai-codex"}],
                          [{"harness": "", "provider": "openai-codex", "model_id": "gpt-5.6-sol"}],
                          [{"harness": "codex", "provider": "", "model_id": "gpt-5.6-sol"}],
                          [{"harness": "codex", "provider": "openai-codex", "model_id": None}], duplicate],
        }
        for field, values in invalid.items():
            for value in values:
                catalog = deepcopy(CATALOG)
                models = catalog["models"]
                assert isinstance(models, dict)
                model = models["sol"]
                assert isinstance(model, dict)
                model[field] = value
                before = deepcopy(catalog)
                for operation in (lambda: self.api.normalize_config(config, catalog),
                                  lambda: self.api.selected_models(config, catalog),
                                  lambda: self.api.menu(catalog),
                                  lambda: self.api.family_of("opus48", catalog),
                                  lambda: self.api.distinct_families([], catalog)):
                    with self.subTest(field=field, value=value):
                        self.assertIn("catalog.models", self.error_detail(operation))
                        self.assertEqual(catalog, before)

    def test_catalog_identity_and_nonempty_model_map_are_required(self) -> None:
        for version in (None, True, 1.0, "1", 2):
            with self.subTest(version=version):
                self.assertIn("catalog", self.error_detail(lambda: self.api.menu({**CATALOG, "schema_version": version})))
        valid = self.catalog["models"]
        assert isinstance(valid, dict)
        invalid: tuple[JsonObject, ...] = ({}, {"": valid["sol"]}, {" ": valid["sol"]}, {SENSITIVE: None})
        for models in invalid:
            with self.subTest(models=models):
                self.assertIn("catalog.models", self.error_detail(lambda: self.api.menu({**CATALOG, "models": models})))

    def test_added_model_is_selectable_without_code_or_default_changes(self) -> None:
        models = self.catalog["models"]
        assert isinstance(models, dict)
        models["fixture"] = {"label": "Fixture", "family": "openai", "provider": "openai-codex",
                             "model_id": "fixture-model", "harnesses": [
                                 {"harness": "codex", "provider": "openai-codex", "model_id": "fixture-model"}]}
        cfg, _ = self.normalize({"classes": {"planner": "fixture", "executors": ["fixture"], "reviewers": "all"}})
        self.assertEqual(self.api.selected_models(cfg, self.catalog)["planner"], "fixture")
        self.assertEqual(self.api.menu(self.catalog)[-1]["key"], "fixture")
        self.assertEqual(self.api.distinct_families(["opus48", "fixture"], self.catalog), {"anthropic", "openai"})

    def test_load_json_reads_utf8_without_writing(self) -> None:
        path = self.sandbox / "config.json"
        payload = '{"label": "caf\u00e9", "left": {"x": 0.5}, "right": {"x": 2e3}}'.encode("utf-8")
        path.write_bytes(payload)
        loaded = self.api.load_json(str(path))
        self.assertEqual(loaded, {"label": "caf\u00e9", "left": {"x": 0.5}, "right": {"x": 2000.0}})
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

    def test_load_json_rejects_duplicates_and_nonfinite_numbers_at_every_depth(self) -> None:
        path = self.sandbox / "config.json"
        payloads = ['{"a": 1, "a": 2}', '{"classes": {"planner": "sol", "planner": "opus48"}}',
                    '{"models": {"sol": {}, "sol": {}}}', '{"nested": [{"a": 1, "\\u0061": 2}]}',
                    '{"nested": {"' + SENSITIVE + '": 1, "' + SENSITIVE + '": 2}}']
        payloads.extend('{"nested": [{"number": ' + number + '}]}'
                        for number in ("NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"))
        for payload in payloads:
            with self.subTest(payload=payload):
                path.write_text(payload, encoding="utf-8")
                self.error_detail(lambda: self.api.load_json(str(path)))
                self.assertEqual(path.read_text(encoding="utf-8"), payload)

    def test_helper_works_when_copied_to_an_isolated_skill_script(self) -> None:
        scripts = self.sandbox / "example-skill" / "scripts"
        scripts.mkdir(parents=True)
        helper = scripts / "model_config.py"
        helper.write_bytes((REFERENCES / "model_config.py").read_bytes())
        with patch("builtins.open", side_effect=AssertionError("implicit file access")), \
                patch("io.open", side_effect=AssertionError("implicit file access")):
            namespace = runpy.run_path(str(helper))
            normalized, _ = namespace["normalize_config"](deepcopy(LIVE), self.catalog)
        self.assertEqual(normalized["classes"], LIVE["classes"])
        malformed = self.sandbox / "config.json"
        malformed.write_text('{"a": 1, "a": 2}', encoding="utf-8")
        with self.assertRaises(namespace["ConfigError"]):
            namespace["load_json"](str(malformed))

    def test_integration_live_config_normalizes_with_the_shared_catalog(self) -> None:
        catalog_path = REFERENCES / "models.json"
        config_path = ROOT / ".thunderkit" / "config.json"
        before, catalog_before = config_path.read_bytes(), catalog_path.read_bytes()
        raw = self.api.load_json(str(config_path))
        catalog = self.api.load_json(str(catalog_path))
        normalized, _ = self.api.normalize_config(raw, catalog)
        for key, value in raw.items():
            self.assertEqual(normalized[key], value)
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(config_path.read_bytes(), before)
        self.assertEqual(catalog_path.read_bytes(), catalog_before)
        selected = self.api.selected_models(normalized, catalog)
        self.assertEqual(selected["executors"], ["opus48", "opus5", "fable51"])
        self.assertEqual(len(self.api.menu(catalog)), 4)
        self.assertEqual(self.api.distinct_families(["opus48", "opus5", "fable51"], catalog), {"anthropic"})


if __name__ == "__main__":
    unittest.main()
