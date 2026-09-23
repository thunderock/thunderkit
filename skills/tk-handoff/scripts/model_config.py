"""Read and normalize model selections without changing their source."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
import json
from math import isfinite
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


@dataclass(frozen=True, slots=True)
class _Model:
    label: str
    provider: str
    model_id: str
    family: str


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


def _nonempty_text(value: JsonValue, field: str) -> str:
    text = _text(value, field)
    if not text or text != text.strip():
        raise ConfigError(f"{field} must be nonempty without surrounding whitespace")
    return text


def _known_keys(value: JsonObject, allowed: set[str], field: str) -> None:
    if value.keys() - allowed:
        raise ConfigError(f"{field} contains unknown keys; allowed keys: {', '.join(sorted(allowed))}")


def _models(catalog: JsonObject) -> dict[str, _Model]:
    source = _object(catalog, "catalog")
    entries = _object(source.get("models"), "catalog.models")
    if not entries:
        raise ConfigError("catalog.models must contain at least one model")
    if type(source.get("schema_version")) is not int or source["schema_version"] != 1:
        raise ConfigError("catalog.schema_version must be 1")
    models = {}
    for index, (key, value) in enumerate(entries.items()):
        field = f"catalog.models[{index}]"
        _nonempty_text(key, f"{field}.key")
        metadata = _object(value, field)
        model = _Model(
            label=_nonempty_text(metadata.get("label"), f"{field}.label"),
            provider=_nonempty_text(metadata.get("provider"), f"{field}.provider"),
            model_id=_nonempty_text(metadata.get("model_id"), f"{field}.model_id"),
            family=_nonempty_text(metadata.get("family"), f"{field}.family"),
        )
        harnesses = metadata.get("harnesses")
        if not isinstance(harnesses, list) or not harnesses:
            raise ConfigError(f"{field}.harnesses must be a nonempty list of mappings")
        names: set[str] = set()
        for position, value in enumerate(harnesses):
            location = f"{field}.harnesses[{position}]"
            mapping = _object(value, location)
            name = _nonempty_text(mapping.get("harness"), f"{location}.harness")
            _nonempty_text(mapping.get("provider"), f"{location}.provider")
            _nonempty_text(mapping.get("model_id"), f"{location}.model_id")
            if name in names:
                raise ConfigError(f"{field}.harnesses contains duplicate harness mappings")
            names.add(name)
        models[key] = model
    return models


def _model_key(value: JsonValue, models: dict[str, _Model], field: str) -> str:
    key = _text(value, field)
    if key not in models:
        raise ConfigError(f"{field}: unknown model key; choose a key from catalog.models")
    return key


def _model_keys(value: JsonValue, models: dict[str, _Model], field: str) -> tuple[str, ...]:
    keys = _strings(value, field)
    if not keys:
        raise ConfigError(f"{field} must contain at least one model key")
    if len(set(keys)) != len(keys):
        raise ConfigError(f"{field} contains duplicate model keys; remove repeated entries")
    return tuple(_model_key(key, models, f"{field}[{index}]") for index, key in enumerate(keys))


def _classes(raw: JsonObject, models: dict[str, _Model]) -> _Classes:
    if "models" in raw:
        legacy = raw["models"]
        if ("classes" in raw or not isinstance(legacy, dict)
                or not {"plan", "critical_path", "review"}.issubset(legacy)):
            raise ConfigError("mixed or incomplete legacy schema")
        _known_keys(legacy, {"plan", "critical_path", "review"}, "models")
        planner = _model_key(legacy["plan"], models, "models.plan")
        executors: tuple[str, ...] = (_model_key(legacy["critical_path"], models, "models.critical_path"),)
        review, review_field = legacy["review"], "models.review"
    else:
        classes = _object(raw.get("classes"), "classes")
        _known_keys(classes, {"planner", "executors", "reviewers"}, "classes")
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


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("JSON object contains duplicate keys; use each key only once")
        result[key] = value
    return result


def _finite_float(number: str) -> float:
    value = float(number)
    if not isfinite(value):
        raise ConfigError("JSON numbers must be finite")
    return value


def load_json(path: str) -> JsonObject:
    """Read a UTF-8 JSON object, reporting file and parse failures uniformly."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value: JsonValue = json.load(stream, object_pairs_hook=_unique_object,
                                         parse_constant=_finite_float, parse_float=_finite_float)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc.detail}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}") from None
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(f"{path}: cannot load JSON ({type(exc).__name__})") from None
    return _object(value, f"{path}: JSON document")


def normalize_config(raw: JsonObject, catalog: JsonObject) -> tuple[JsonObject, list[str]]:
    """Return a detached canonical preview; never persist or replace selections."""
    source = _object(raw, "config")
    _known_keys(source, {"schema_version", "classes", "models", "review_families_min", "max_layers",
                         "frozen_paths", "ecosystems", "delegation", "decided_at"}, "config")
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
        if (not path or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or "\\" in path or path.startswith("/")
                or PureWindowsPath(path).drive or ".." in path.split("/")):
            raise ConfigError(f"frozen_paths[{index}] must be repo-relative using forward slashes, "
                              "without a drive, '..' segments or ASCII control characters")
    normalized["frozen_paths"] = list(paths)
    ecosystems = _strings(source.get("ecosystems", ["omo", "omh"]), "ecosystems")
    if len(set(ecosystems)) != len(ecosystems):
        raise ConfigError("ecosystems contains duplicate entries; choose each ecosystem only once")
    for ecosystem in ecosystems:
        if ecosystem not in ("omo", "omh"):
            raise ConfigError("ecosystems: unsupported value; choose 'omo' or 'omh'")
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
    return models[known].family


def distinct_families(keys: Iterable[str], catalog: JsonObject) -> set[str]:
    models = _models(catalog)
    return {models[_model_key(key, models, "model")].family for key in keys}


def menu(catalog: JsonObject, availability: dict[str, str] | None = None) -> list[dict[str, str]]:
    """List catalog entries in catalog order; unprobed availability is 'unknown'."""
    return [{"key": key, "family": model.family, "label": model.label,
             "provider": model.provider, "model_id": model.model_id,
             "available": availability.get(key, "unknown") if availability is not None else "unknown"}
            for key, model in _models(catalog).items()]
