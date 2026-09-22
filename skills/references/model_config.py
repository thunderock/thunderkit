"""Read and normalize model selections without changing their source."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import PureWindowsPath
from typing import Literal, NoReturn, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class ConfigError(ValueError):
    """Invalid configuration with a stable code and actionable detail."""

    code: Literal["invalid_config"] = "invalid_config"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _Classes:
    planner: str
    executors: tuple[str, ...]
    reviewers: Literal["all"] | tuple[str, ...]


def _assert_never(value: NoReturn) -> NoReturn:
    raise AssertionError(f"Unexpected value: {value!r}")


def _object(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a JSON object")
    return value


def _text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    return value


def _strings(value: JsonValue, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be a list of strings")
    return [_text(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _models(catalog: JsonObject) -> JsonObject:
    return _object(_object(catalog, "catalog").get("models"), "catalog.models")


def _model_key(value: JsonValue, models: JsonObject, field: str) -> str:
    key = _text(value, field)
    if key not in models:
        raise ConfigError(f"{field}: unknown model key {key!r}; choose from {', '.join(sorted(models))}")
    return key


def _model_keys(value: JsonValue, models: JsonObject, field: str) -> tuple[str, ...]:
    keys = _strings(value, field)
    if not keys:
        raise ConfigError(f"{field} must contain at least one model key")
    if len(set(keys)) != len(keys):
        raise ConfigError(f"{field} contains duplicate model keys; remove repeated entries")
    return tuple(_model_key(key, models, f"{field}[{index}]") for index, key in enumerate(keys))


def _classes(raw: JsonObject, models: JsonObject) -> _Classes:
    if "models" in raw:
        legacy = raw["models"]
        if ("classes" in raw or not isinstance(legacy, dict)
                or not {"plan", "critical_path", "review"}.issubset(legacy)):
            raise ConfigError("mixed or incomplete legacy schema")
        planner = _model_key(legacy["plan"], models, "models.plan")
        executors: tuple[str, ...] = (_model_key(legacy["critical_path"], models, "models.critical_path"),)
        review, review_field = legacy["review"], "models.review"
    else:
        classes = _object(raw.get("classes"), "classes")
        planner = _model_key(classes.get("planner"), models, "classes.planner")
        executors = _model_keys(classes.get("executors"), models, "classes.executors")
        review, review_field = classes.get("reviewers"), "classes.reviewers"
    reviewers: Literal["all"] | tuple[str, ...] = (
        "all" if review == "all" else _model_keys(review, models, review_field)
    )
    return _Classes(planner, executors, reviewers)


def _minimum(value: JsonValue, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{field} must be an integer >= {minimum}")
    return value


def load_json(path: str) -> JsonObject:
    """Read a UTF-8 JSON object, reporting file and parse failures uniformly."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value: JsonValue = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: cannot load JSON ({exc})") from exc
    return _object(value, f"{path}: JSON document")


def normalize_config(raw: JsonObject, catalog: JsonObject) -> tuple[JsonObject, list[str]]:
    """Return a detached canonical preview; never persist or replace selections."""
    source = _object(raw, "config")
    classes = _classes(source, _models(catalog))
    legacy = "models" in source
    version = source.get("schema_version", 2)
    if type(version) is not int or version not in ((1, 2) if legacy else (2,)):
        raise ConfigError("schema_version must be 2 (legacy models may use 1)")

    normalized = deepcopy(source)
    normalized.pop("models", None)
    normalized["schema_version"] = 2
    reviewers: JsonValue
    match classes.reviewers:
        case "all":
            reviewers = "all"
        case tuple() as keys:
            reviewers = list(keys)
        case unreachable:
            _assert_never(unreachable)
    normalized["classes"] = {
        "planner": classes.planner, "executors": list(classes.executors), "reviewers": reviewers,
    }
    normalized["review_families_min"] = _minimum(source.get("review_families_min", 2),
                                                "review_families_min", 2)
    normalized["max_layers"] = _minimum(source.get("max_layers", 3), "max_layers", 1)

    paths = _strings(source.get("frozen_paths", []), "frozen_paths")
    for index, path in enumerate(paths):
        # Recognize both separator styles without consulting the filesystem.
        if (not path or "\0" in path or path.startswith(("/", "\\"))
                or PureWindowsPath(path).drive or ".." in path.replace("\\", "/").split("/")):
            raise ConfigError(f"frozen_paths[{index}] must be repo-relative without '..' segments: {path!r}")
    normalized["frozen_paths"] = list(paths)
    ecosystems = _strings(source.get("ecosystems", ["omo", "omh"]), "ecosystems")
    for ecosystem in ecosystems:
        if ecosystem not in ("omo", "omh"):
            raise ConfigError(f"ecosystems: unsupported value {ecosystem!r}; choose 'omo' or 'omh'")
    normalized["ecosystems"] = list(ecosystems)
    delegation = _text(source.get("delegation", "auto"), "delegation")
    if delegation not in ("auto", "off"):
        raise ConfigError("delegation must be 'auto' or 'off'")
    normalized["delegation"] = delegation
    if "decided_at" in source:
        _text(source["decided_at"], "decided_at")

    warnings = []
    if legacy:
        warnings.append("legacy models schema converted (preview only; not saved)")
    elif "schema_version" not in source:
        warnings.append("schema_version absent; assuming 2")
    return normalized, warnings


def selected_models(cfg: JsonObject, catalog: JsonObject) -> dict[str, str | list[str]]:
    """Separate required selections from the full-catalog review candidate pool."""
    models = _models(catalog)
    classes = _classes(_object(cfg, "config"), models)
    explicit = {classes.planner, *classes.executors}
    candidates: list[str] = []
    match classes.reviewers:
        case "all":
            mode = "all"
            candidates = sorted(models)
            reviewers = candidates.copy()
        case tuple() as keys:
            mode = "explicit"
            reviewers = list(keys)
            explicit.update(keys)
        case unreachable:
            _assert_never(unreachable)
    return {"planner": classes.planner, "executors": list(classes.executors),
            "reviewers": reviewers, "reviewers_mode": mode,
            "explicit": sorted(explicit), "candidates": candidates}


def family_of(key: str, catalog: JsonObject) -> str:
    """Resolve a model's family independently of its provider or harness."""
    models = _models(catalog)
    known = _model_key(key, models, "model")
    field = f"catalog.models.{known}"
    metadata = _object(models[known], field)
    family = _text(metadata.get("family"), f"{field}.family")
    if not family:
        raise ConfigError(f"{field}.family must be a nonempty string")
    return family


def distinct_families(keys: Iterable[str], catalog: JsonObject) -> set[str]:
    return {family_of(key, catalog) for key in keys}


def menu(catalog: JsonObject, availability: dict[str, str] | None = None) -> list[dict[str, str]]:
    """List catalog entries in catalog order; unprobed availability is 'unknown'."""
    rows = []
    for key, value in _models(catalog).items():
        field = f"catalog.models.{key}"
        metadata = _object(value, field)
        rows.append({"key": key, "family": family_of(key, catalog),
                     **{name: _text(metadata.get(name), f"{field}.{name}")
                        for name in ("label", "provider", "model_id")},
                     "available": availability.get(key, "unknown") if availability is not None else "unknown"})
    return rows
