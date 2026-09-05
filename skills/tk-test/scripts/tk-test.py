#!/usr/bin/env python3
"""tk-test: prove the fleet configured by tk-router is actually reachable.

Reads .thunderkit/config.json (the three model classes), resolves each short name to a
harness + provider id via the embedded roster table, dispatches a one-word tk-ask ping
("Reply with exactly one word: pong") through the real CLI, and reports per model:
reachable / unreachable / not-installed, with the captured resumable id.

Stdlib only. Never fakes a result: a model that cannot be reached is reported as such.

Usage:
  python3 tk-test.py [--config PATH] [--timeout SECS] [--json]
Exit 0 when every configured model answered; 1 otherwise.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

PING = "Reply with exactly one word: pong"

# short name -> (harness, provider, provider-id). Mirrors skills/references/model-roster.md.
ROSTER = {
    "opus48":  ("claude", "anthropic", "claude-opus-4-8"),
    "opus5":   ("hermes", "bedrock",   "us.anthropic.claude-opus-5"),
    "fable51": ("hermes", "bedrock",   "us.anthropic.claude-fable-5-1"),
    "sol":     ("codex",  "openai-codex", "gpt-5.6-sol"),
}
FAMILY = {"opus48": "anthropic", "opus5": "anthropic", "fable51": "anthropic", "sol": "openai"}


def run(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s", time.time() - t0


def probe(short, timeout):
    harness, provider, mid = ROSTER[short]
    if not shutil.which(harness):
        return dict(model=short, harness=harness, id=mid, status="not-installed",
                    detail=f"`{harness}` not on PATH", resume=None, secs=0)

    if harness == "claude":
        cmd = ["claude", "-p", PING, "--model", mid, "--output-format", "json"]
        rc, out, err, secs = run(cmd, timeout)
        text, resume = "", None
        try:
            d = json.loads(out)
            text, resume = (d.get("result") or ""), d.get("session_id")
            if d.get("is_error"):
                rc = rc or 1
        except ValueError:
            pass
        resume_cmd = f"claude -p --resume {resume}" if resume else None

    elif harness == "codex":
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-m", mid, PING]
        rc, out, err, secs = run(cmd, timeout)
        text, resume = "", None
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "thread.started":
                resume = d.get("thread_id")
            elif d.get("type") == "item.completed" and d.get("item", {}).get("type") == "agent_message":
                text = d["item"].get("text", "")
            elif d.get("type") == "error":
                err = (err + " " + json.dumps(d)).strip()
                rc = rc or 1
        resume_cmd = f"codex exec resume {resume} --skip-git-repo-check" if resume else None

    else:  # hermes
        cmd = ["hermes", "chat", "-q", PING, "--oneshot", "-Q", "--provider", provider, "-m", mid, "-t", ""]
        rc, out, err, secs = run(cmd, timeout)
        m = re.search(r"session_id:\s*(\S+)", out)
        resume = m.group(1) if m else None
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("session_id:")]
        text = lines[-1] if lines else ""
        resume_cmd = f"hermes chat --resume {resume}" if resume else None

    ok = rc == 0 and "pong" in text.lower()
    detail = text.strip()[:60] if ok else (err.strip().splitlines()[-1][:120] if err.strip() else f"rc={rc} text={text[:60]!r}")
    return dict(model=short, harness=harness, id=mid, status="reachable" if ok else "unreachable",
                detail=detail, resume=resume_cmd, secs=round(secs, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=".thunderkit/config.json")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.config):
        print(f"FAIL: {a.config} not found — run tk-router first to choose model classes")
        sys.exit(1)
    cfg = json.load(open(a.config))
    classes = cfg.get("classes", {})
    planner = classes.get("planner")
    executors = list(classes.get("executors", []))
    reviewers = classes.get("reviewers", "all")
    if reviewers == "all":
        reviewers = sorted({planner, *executors} - {None})

    wanted = []
    for role, names in (("planner", [planner]), ("executor", executors), ("reviewer", reviewers)):
        for n in names:
            if n and n not in ROSTER:
                print(f"FAIL: unknown model short name {n!r} in config (roster: {', '.join(ROSTER)})")
                sys.exit(1)
            if n:
                wanted.append((role, n))

    uniq = []
    for _, n in wanted:
        if n not in uniq:
            uniq.append(n)

    results = {n: probe(n, a.timeout) for n in uniq}

    if a.json:
        print(json.dumps({"models": results, "classes": classes}, indent=2))
    else:
        print(f"tk-test — fleet reachability ({a.config})\n")
        print(f"{'model':9} {'harness':8} {'status':13} {'secs':>5}  detail / resume")
        for n in uniq:
            r = results[n]
            mark = {"reachable": "✓", "unreachable": "✗", "not-installed": "–"}[r["status"]]
            print(f"{mark} {n:7} {r['harness']:8} {r['status']:13} {r['secs']:>5}  {r['detail']}")
            if r["resume"]:
                print(f"{'':32}resume: {r['resume']}")
        print()
        for role, names in (("planner", [planner]), ("executors", executors), ("reviewers", reviewers)):
            st = [f"{n}:{results[n]['status']}" for n in names if n]
            print(f"  {role:10} {'  '.join(st)}")
        fams = {FAMILY[n] for n in reviewers if results[n]["status"] == "reachable"}
        need = cfg.get("review_families_min", 2)
        print(f"\n  reviewer families reachable: {len(fams)} ({', '.join(sorted(fams)) or 'none'}); required ≥ {need}"
              + ("" if len(fams) >= need else "  ← cross-family review NOT possible"))

    bad = [n for n in uniq if results[n]["status"] != "reachable"]
    if bad:
        print(f"\nFAIL: {len(bad)} configured model(s) not reachable: {', '.join(bad)}")
        sys.exit(1)
    print(f"\nOK: all {len(uniq)} configured models reachable")


if __name__ == "__main__":
    main()
