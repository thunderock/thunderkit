#!/usr/bin/env python3
"""thunderkit test suite: validates every SKILL.md, the model roster, and leakage denylist.

Checks (fail = non-zero exit):
  1. Every skills/<name>/SKILL.md has valid YAML frontmatter delimited by ---.
  2. frontmatter `name` == parent directory name.
  3. frontmatter `description` present, non-empty, and trigger-shaped:
       - starts with "Use when" / "Use to" (a trigger, not a noun phrase)
       - >= 40 chars (self-contained), <= 500 chars (skill-description budget)
  4. The shared roster names all four current fleet model ids.
  5. No leakage: denylisted (Adobe/secret/internal) strings appear nowhere in skills/ or docs.

No third-party deps — stdlib only, so `make run_tests` works offline on a fresh machine.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
ROSTER = os.path.join(SKILLS, "references", "model-roster.md")

FLEET_IDS = [
    "us.anthropic.claude-fable-5-1",
    "claude-opus-4-8",
    "us.anthropic.claude-opus-5",
    "gpt-5.6-sol",
]

# Public-repo leakage denylist. Case-insensitive. These must never ship.
DENYLIST = [
    r"\badobe\b",
    r"\bastiwari\b",
    r"sensei-fs",
    r"AWS_BEARER",
    r"\.internal\b",
    r"\bcorp\.",
    r"firefly",
    r"\borion\b",
]

errors = []


def fail(msg):
    errors.append(msg)


def parse_frontmatter(text, path):
    """Return dict of top-level scalar frontmatter keys, or None if malformed."""
    if not text.startswith("---"):
        fail(f"{path}: no frontmatter (must start with ---)")
        return None
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        fail(f"{path}: frontmatter not closed with ---")
        return None
    fm = {}
    for line in m.group(1).splitlines():
        # only care about top-level scalars name: / description:
        km = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            val = val.strip('"').strip("'")
            fm[key] = val
    return fm


def check_skill(skill_dir):
    name = os.path.basename(skill_dir)
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        fail(f"{name}: missing SKILL.md")
        return
    text = open(path, encoding="utf-8").read()
    fm = parse_frontmatter(text, f"{name}/SKILL.md")
    if fm is None:
        return
    # name matches dir
    if fm.get("name") != name:
        fail(f"{name}/SKILL.md: frontmatter name '{fm.get('name')}' != dir '{name}'")
    # description trigger-shaped
    desc = fm.get("description", "")
    if not desc:
        fail(f"{name}/SKILL.md: empty description")
    else:
        if not re.match(r"^Use \w+\b", desc):
            fail(f"{name}/SKILL.md: description must start with 'Use <verb>' (trigger-shaped): {desc[:50]!r}")
        if len(desc) < 40:
            fail(f"{name}/SKILL.md: description too short ({len(desc)} chars, need >=40)")
        if len(desc) > 500:
            fail(f"{name}/SKILL.md: description too long ({len(desc)} chars, max 500)")


def check_roster():
    if not os.path.isfile(ROSTER):
        fail("references/model-roster.md: missing")
        return
    text = open(ROSTER, encoding="utf-8").read()
    for mid in FLEET_IDS:
        if mid not in text:
            fail(f"model-roster.md: missing fleet model id '{mid}'")


def check_leakage():
    for dp, dn, fn in os.walk(ROOT):
        if os.sep + ".git" in dp:
            continue
        # scan skills/ and top-level docs only
        rel = os.path.relpath(dp, ROOT)
        if not (rel == "." or rel.startswith("skills") or rel.startswith("site")):
            continue
        for f in fn:
            if not f.endswith((".md", ".html", ".js", ".py", ".json", ".yml", ".yaml", ".css")):
                continue
            p = os.path.join(dp, f)
            try:
                text = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for pat in DENYLIST:
                for m in re.finditer(pat, text, re.I):
                    fail(f"LEAKAGE: {os.path.relpath(p, ROOT)} contains denylisted /{pat}/ ('{m.group(0)}')")


def main():
    if not os.path.isdir(SKILLS):
        fail("skills/ directory missing")
    else:
        for entry in sorted(os.listdir(SKILLS)):
            d = os.path.join(SKILLS, entry)
            if os.path.isdir(d) and entry != "references":
                check_skill(d)
    check_roster()
    check_leakage()

    if errors:
        print(f"FAIL: {len(errors)} problem(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    n = len([d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)) and d != "references"])
    print(f"OK: {n} skills validated, roster has all {len(FLEET_IDS)} fleet ids, no leakage")


if __name__ == "__main__":
    main()
