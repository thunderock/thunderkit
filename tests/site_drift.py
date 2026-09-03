#!/usr/bin/env python3
"""Site drift gate: the COMMITTED published skill set must equal skills/ on disk.

Reads the committed site/_site/skills.json (the last build's published set) and asserts it
equals the current skills/<name>/ directories. This catches the real drift: a skill added or
removed without rebuilding the site. Run `python3 site/build.py` to refresh after changing
skills. Fails non-zero on mismatch or if the site was never built.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
PUBLISHED = os.path.join(ROOT, "site", "_site", "skills.json")


def on_disk():
    return sorted(
        d for d in os.listdir(SKILLS)
        if os.path.isdir(os.path.join(SKILLS, d)) and d != "references"
    )


def main():
    if not os.path.isfile(PUBLISHED):
        print(f"FAIL: {os.path.relpath(PUBLISHED, ROOT)} missing — run `python3 site/build.py`")
        sys.exit(1)
    published = json.load(open(PUBLISHED))
    disk = on_disk()
    if published != disk:
        print("FAIL: site drift — committed site is stale, run `python3 site/build.py`")
        print(f"  on disk  : {disk}")
        print(f"  published: {published}")
        missing = set(disk) - set(published)
        extra = set(published) - set(disk)
        if missing:
            print(f"  in skills/ but not published: {sorted(missing)}")
        if extra:
            print(f"  published but not in skills/: {sorted(extra)}")
        sys.exit(1)
    print(f"OK: site drift gate — {len(disk)} skills, committed site matches disk")


if __name__ == "__main__":
    main()
