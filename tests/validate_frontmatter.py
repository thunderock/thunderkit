#!/usr/bin/env python3
"""Validate public skill metadata, catalog consistency and leakage without network access."""
from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from dependency_contract import json_array, json_object, json_string, validate_manifest
from skills.references.model_config import ConfigError, JsonObject, load_json, menu
from tools.materialize_skills import PayloadError, discover_skills
from tools.skill_frontmatter import FrontmatterError, parse_skill_file, validate_thunderkit

DENYLIST: Final = (r"\badobe\b", r"\bastiwari\b", r"sensei-fs", r"AWS_BEARER", r"\.internal\b",
                  r"\bcorp\.", r"firefly", r"\borion\b")
PUBLIC_DOCS: Final = ("README.md", "NORTH_STAR.md", "DEPENDENCIES.md", "CHANGELOG.md",
                     ".thunderkit/NORTH_STAR.md", ".thunderkit/PHILOSOPHY.md", ".thunderkit/config.json")


def check_skill(skill: Path, definition: JsonObject) -> None:
    fm = parse_skill_file(skill / "SKILL.md")
    validate_thunderkit(fm, skill.name)
    delegates = [f"{json_string(target['ecosystem'])}:{json_string(target['selector'])}"
                 for raw in json_array(definition["targets"]) for target in (json_object(raw),)]
    expected = {"thunderkit-role": json_string(definition["role"]),
                "thunderkit-delegates": " ".join(delegates) or "none"}
    for key, value in expected.items():
        if fm.metadata[key] != value:
            raise FrontmatterError(str(skill / "SKILL.md"), 1, f"{key} does not match dependencies.json")
    if not fm.compatibility or re.fullmatch(r"[a-z][a-z0-9-]*", fm.metadata["thunderkit-tier"]) is None:
        raise FrontmatterError(str(skill / "SKILL.md"), 1, "compatibility and a slug-shaped tier are required")


def check_roster(root: Path, catalog: JsonObject) -> None:
    rows = menu(catalog)
    models = json_object(catalog["models"])
    expected = {row["key"]: (row["model_id"], tuple(json_string(json_object(item)["harness"])
                for item in json_array(json_object(models[row["key"]])["harnesses"]))) for row in rows}
    roster = (root / "skills/references/model-roster.md").read_text(encoding="utf-8")
    actual = re.findall(r"^\|[^|\n]+\| `([a-z][a-z0-9_-]*)` \| `([^`\n]+)`[^|\n]*\| ([^|\n]+) \|",
                        roster, re.MULTILINE)
    if (len(actual) != len(expected)
            or {key: (model_id, tuple(harnesses.split(", "))) for key, model_id, harnesses in actual} != expected):
        raise ConfigError("model-roster.md: table keys, model IDs or harness mappings differ from models.json")


def check_leakage(root: Path) -> list[str]:
    paths = [root / name for name in PUBLIC_DOCS if (root / name).exists()]
    paths.extend(path for name in ("skills", "site", "bin") for path in (root / name).rglob("*")
                 if path.suffix in (".md", ".html", ".js", ".py", ".json", ".yml", ".yaml", ".css")
                 and (path.is_file() or path.is_symlink()))
    errors: list[str] = []
    for path in sorted(paths):
        if path.is_symlink():
            errors.append(f"public source is a symlink: {path.relative_to(root)}")
            continue
        for line, content in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in DENYLIST:
                if re.search(pattern, content, re.IGNORECASE):
                    errors.append(f"LEAKAGE: {path.relative_to(root)}:{line}: denylisted /{pattern}/")
    return errors


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        skills = discover_skills(root)
        manifest = load_json(str(root / "skills/references/dependencies.json"))
        catalog = load_json(str(root / "skills/references/models.json"))
        validate_manifest(manifest, {skill.name for skill in skills})
        check_roster(root, catalog)
        entries = json_object(manifest["skills"])
        for skill in skills:
            try:
                check_skill(skill, json_object(entries[skill.name]))
            except FrontmatterError as error:
                errors.append(str(error))
        errors.extend(check_leakage(root))
    except (AssertionError, ConfigError, PayloadError, OSError, UnicodeError) as error:
        errors.append(str(error) or type(error).__name__)
    return errors


def main() -> int:
    errors = check()
    if errors:
        print(f"FAIL: {len(errors)} problem(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("OK: strict skill metadata, dependency manifest, catalog/roster and public leakage checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
