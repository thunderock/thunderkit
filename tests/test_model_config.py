from __future__ import annotations

from copy import deepcopy
from itertools import product
import unittest

from model_config_fixtures import LIVE, SENSITIVE, JsonObject, JsonValue, ModelConfigCase


class ModelConfigNormalizationTests(ModelConfigCase):
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
                           "frozen_paths": [".", "./src/file", "folder/file", "name..md", "資料/file"]}
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

    def test_frozen_paths_are_rejected_when_containing_ascii_controls(self) -> None:
        path = f"src/{SENSITIVE}/file"
        for codepoint, offset in product((*range(32), 127), (0, 4, len(path))):
            with self.subTest(codepoint=codepoint, offset=offset):
                raw: JsonObject = {**deepcopy(LIVE), "frozen_paths": [
                    "LICENSE", path[:offset] + chr(codepoint) + path[offset:]]}

                detail = self.error_detail(lambda: self.normalize(raw))

                self.assertIn("frozen_paths[1]", detail)
                self.assertNotIn(chr(codepoint), detail)

    def test_frozen_paths_are_preserved_when_valid_repo_relative_names(self) -> None:
        for path in (".", "./src", "資料/file", "café/😀.txt", "folder/file name",
                     " leading/trailing ", " ", "name..md", "src/.../file",
                     "src/~file", "src/\u0080file", "src/\u00a0file"):
            with self.subTest(path=path):
                raw: JsonObject = {**deepcopy(LIVE), "schema_version": 2,
                                   "ecosystems": ["omh"], "delegation": "off", "frozen_paths": [path]}

                normalized, warnings = self.normalize(raw)

                self.assertEqual(normalized, raw)
                self.assertEqual(warnings, [])

    def test_complete_legacy_schema_is_converted_only_in_memory(self) -> None:
        reviews: tuple[JsonValue, ...] = (["sol", "opus5"], "all")
        for review, version, date in product(reviews, (None, 1, 2), (None, "", "2026-09-04")):
            with self.subTest(review=review, version=version, date=date):
                raw = {key: value for key, value in deepcopy(LIVE).items()
                       if key not in ("classes", "decided_at")}
                raw["models"] = {"plan": "opus48", "critical_path": "opus5", "review": review}
                if version is not None:
                    raw["schema_version"] = version
                if date is not None:
                    raw["decided_at"] = date
                normalized, warnings = self.normalize(raw)
                expected = {key: value for key, value in raw.items() if key != "models"}
                self.assertEqual(normalized, {**expected, "schema_version": 2,
                    "classes": {"planner": "opus48", "executors": ["opus5"], "reviewers": review},
                    "ecosystems": ["omo", "omh"], "delegation": "auto"})
                self.assertEqual(warnings, ["legacy models schema converted (preview only; not saved)"])

    def test_invalid_fields_report_the_key_without_side_effects(self) -> None:
        cases: dict[str, list[JsonValue]] = {
            "schema_version": [1, 3, "2", 2.0, True, None],
            "classes": [None, [], {}],
            "classes.planner": [None, [], {}, 42, "", "missing", SENSITIVE],
            "classes.executors": [None, "opus48", [], ["opus48", "opus48"], [1], [[]], ["missing"]],
            "classes.reviewers": [None, "opus48", [], ["sol", "sol"], [True], ["all"], ["missing"]],
            "review_families_min": [1, "2", 2.0, True, None, float("nan"), float("inf")],
            "max_layers": [0, "1", 1.0, True, None, float("nan"), float("-inf")],
            "frozen_paths": ["LICENSE", ["../x"], ["a/../x"], ["/abs"], ["a\\..\\x"],
                             ["C:\\x"], ["C:x"], [""], ["\u0000"], [2], [None],
                             ["src\\x.py"], ["folder\\file"], ["a/.."], [".."],
                             ["//server/share"], ["C:/x"], ["src/" + SENSITIVE + "/../x"]],
            "ecosystems": ["omo", ["unsupported"], [False], None,
                           ["omo", "omo"], ["omh", "omo", "omh"], [SENSITIVE]],
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

    def test_unknown_keys_are_rejected_in_every_config_object(self) -> None:
        for extra in ("critical_model", "review_families", SENSITIVE):
            cases: list[JsonObject] = [
                {**LIVE, extra: "sol"},
                {"classes": {"planner": "sol", "executors": ["sol"], "reviewers": "all", extra: 1}},
                {"models": {"plan": "sol", "critical_path": "sol", "review": "all", extra: 1}}]
            for raw in cases:
                with self.subTest(extra=extra, raw=raw):
                    self.assertIn("unknown", self.error_detail(lambda: self.normalize(raw)))

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
        for absent in ("planner", "executors", "reviewers"):
            classes: JsonObject = {"planner": "sol", "executors": ["sol"], "reviewers": "all"}
            del classes[absent]
            with self.subTest(absent=absent):
                self.assertIn("classes." + absent, self.error_detail(lambda: self.normalize({"classes": classes})))
        invalid_models: tuple[JsonValue, ...] = (None, [], "opus48")
        for models in invalid_models:
            with self.subTest(models=models):
                self.catalog = {"models": models}
                self.assertIn("catalog.models", self.error_detail(lambda: self.normalize(deepcopy(LIVE))))


if __name__ == "__main__":
    unittest.main()
