#!/usr/bin/env python3
"""Compare locally installed peer skill inventories with the targets Thunderkit wraps.

Offline and read-only: each --inventory PEER=DIR names an already-installed or extracted
peer tree. Exit 1 when a wrapped target is missing upstream, 2 on invalid input.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "skills/references/dependencies.json"


def inventory(peer: str, directory: Path) -> set[str]:
    """Return selectors present in a peer tree, using that peer's installed layout."""
    if not directory.is_dir():
        raise ValueError(f"{peer}: inventory is not a directory: {directory}")
    if peer == "omo":
        base = directory / "dist/skills" if (directory / "dist/skills").is_dir() else directory
        return {path.parent.name for path in base.glob("*/SKILL.md")}
    if peer == "omh":
        base = directory / "skills" if (directory / "skills").is_dir() else directory
        return {path.parent.relative_to(base).as_posix() for path in base.glob("*/*/SKILL.md")}
    if peer == "gsd":
        commands = directory / "commands/gsd"
        if commands.is_dir():
            return {f"gsd-{path.stem}" for path in commands.glob("*.md")}
        base = directory / "skills" if (directory / "skills").is_dir() else directory
        return {path.parent.name for path in base.glob("gsd-*/SKILL.md")}
    raise ValueError(f"unknown peer: {peer}")


def wrapped(manifest: dict[str, object]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        raise ValueError("manifest.skills must be an object")
    for skill in skills.values():
        for target in skill.get("targets", []) if isinstance(skill, dict) else []:
            result.setdefault(str(target["ecosystem"]), set()).add(str(target["selector"]))
    return result


def report(manifest: dict[str, object], inventories: dict[str, set[str]]) -> dict[str, dict[str, list[str]]]:
    targets = wrapped(manifest)
    return {peer: {"wrapped_present": sorted(targets.get(peer, set()) & present),
                   "wrapped_missing": sorted(targets.get(peer, set()) - present),
                   "unwrapped": sorted(present - targets.get(peer, set()))}
            for peer, present in sorted(inventories.items())}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--inventory", action="append", default=[], metavar="PEER=DIR", required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        inventories: dict[str, set[str]] = {}
        for item in args.inventory:
            peer, separator, directory = item.partition("=")
            if not separator or peer in inventories:
                raise ValueError(f"invalid or repeated --inventory {item!r}")
            inventories[peer] = inventory(peer, Path(directory))
        result = report(manifest, inventories)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"upstream_coverage: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return int(any(row["wrapped_missing"] for row in result.values()))


if __name__ == "__main__":
    sys.exit(main())
