"""Independent expectations for the native-peer contract tests."""

import re
from typing import Final

CHANNELS: Final = {
    "omo": ("oh-my-openagent", "max-prerelease:5.x:beta"),
    "omh": ("oh-my-hermes", "dist-tag:latest"),
    "gsd": ("get-shit-done-cc", "dist-tag:latest"),
}
HOST_PEERS: Final = {"hermes": "omh", "opencode": "omo", "default": "gsd"}
TARGET_ECOSYSTEMS: Final = frozenset({"omo", "omh", "gsd"})
STATIC_PIN_FIELDS: Final = ("version", "integrity", "registry")
OPERATIONS: Final = {
    "tk-router": ("route", ("bootstrap", "route")),
    "tk-test": ("preflight", ("preflight",)),
    "tk-ask": ("validate", ("validate",)),
    "tk-grill": ("interview", ("interview",)),
    "tk-spec": ("clarify", ("clarify",)),
    "tk-map": ("map", ("map",)),
    "tk-discuss": ("discuss", ("discuss",)),
    "tk-research": ("research", ("research",)),
    "tk-learn": ("research", ("research", "discover")),
    "tk-plan": ("plan", ("plan",)),
    "tk-execute": ("execute", ("execute",)),
    "tk-review": ("diff", ("diff", "plan")),
    "tk-verify-work": ("cli", ("cli", "api", "visual")),
    "tk-debug": ("general", ("general", "native-fault")),
    "tk-ship": ("prepare", ("prepare",)),
    "tk-docs": ("docs", ("docs",)),
    "tk-audit": ("audit", ("audit",)),
    "tk-memory": ("view", ("view", "save")),
    "tk-handoff": ("save", ("save", "restore", "lookup")),
}
ROOT_KINDS: Final = {"omo": "package", "omh": "omh", "gsd": "gsd"}
SKILL_PREFIXES: Final = {"omo": "dist/skills", "omh": "skills", "gsd": "skills"}
PEER_ROOTS: Final = {
    "omo": ("package", "package.json", "dist/skills/<skill_name>/SKILL.md"),
    "omh": ("omh", "manifest.json", "skills/<category>/<skill_name>/SKILL.md"),
    "gsd": ("gsd", "gsd-file-manifest.json", "skills/gsd-<name>/SKILL.md"),
}
TARGET_KEYS: Final = frozenset({
    "ecosystem", "skill_name", "selector", "mode", "operations", "requires", "notes", "provenance",
})
NATIVE_ROLES: Final = {
    ("omo", "ulw-plan"): {
        "root": "planner", "explore": "executors", "librarian": "executors", "metis": "executors",
        "momus": "reviewers", "oracle": "reviewers",
    },
    ("omh", "ultrawork/ulw-plan"): {"root": "planner"},
    ("omo", "ulw-execute"): {
        "root": "executors", "worker": "executors", "explore": "executors", "librarian": "executors",
        "gate-reviewer": "reviewers",
    },
    ("omh", "ultrawork/ulw-work"): {
        "root": "executors", "lane": "executors", "verification": "executors", "code-review-gate": "reviewers",
    },
}
SINGLE_CLASS: Final = {
    "tk-grill": "planner", "tk-spec": "planner", "tk-map": "executors", "tk-discuss": "planner",
    "tk-research": "executors", "tk-learn": "executors", "tk-review": "reviewers", "tk-verify-work": "reviewers",
    "tk-debug": "planner", "tk-ship": "reviewers", "tk-audit": "reviewers",
}
OMH_RAIL: Final = "skills/guide/omh-routing/references/skill-common-rail.md"
SHA256: Final = re.compile(r"[0-9a-f]{64}")
SHARED_PATHS: Final = {"omh": frozenset({OMH_RAIL})}
# Skill-local inventory entries are expanded to peer-root-relative paths below.
_LOCAL_COMPANIONS: Final[dict[tuple[str, str], frozenset[str]]] = {
    ("omo", "ulw-research"): frozenset({"ATTRIBUTION.md"}),
    ("omo", "ulw-plan"): frozenset({"agents/openai.yaml", "references/full-workflow.md", "references/intent-clear.md",
                                    "references/intent-unclear.md", "scripts/scaffold-plan.mjs"}),
    ("omo", "ulw-execute"): frozenset(),
    ("omo", "visual-qa"): frozenset({"AGENTS.md", "references/browser-setup.md", "scripts/visual-qa.mjs", "scripts/cli.ts",
                                     "scripts/ansi.ts", "scripts/east-asian-width.ts", "scripts/image-diff.ts",
                                     "scripts/png-crc.ts", "scripts/png-decode.ts", "scripts/png-synth.ts",
                                     "scripts/tui-grid.ts", "scripts/types.ts", "scripts/ansi.test.ts", "scripts/cli.test.ts",
                                     "scripts/east-asian-width.test.ts", "scripts/image-diff.test.ts",
                                     "scripts/png-decode.test.ts", "scripts/tui-grid.test.ts"}),
    ("omo", "debugging"): frozenset({
        *(f"references/methodology/{name}.md" for name in ("00-setup", "02-investigate", "03-flaky-triage",
                                                             "04-oracle-triple", "05-escalate", "06-fix", "08-qa",
                                                             "09-cleanup", "partial-runtime-evidence")),
        *(f"references/runtimes/{name}.md" for name in ("bundled-js-binary", "go", "native-binary", "node", "python", "rust")),
        *(f"references/tools/{name}.md" for name in ("dap", "frida", "ghidra", "playwright-cli", "pwndbg", "pwntools")),
        "references/scripts/dap.mjs", "references/scripts/dap.test.ts", "references/scripts/fixture-adapter.mjs"}),
    ("omo", "coding-agent-sessions"): frozenset({
        "AGENTS.md", "agents/openai.yaml", "scripts/find-agent-sessions.py",
        *(f"references/{name}.md" for name in ("all-platforms", "claude", "codex", "opencode", "senpi")),
        *(f"scripts/agent_sessions/{name}.py" for name in (
            "__init__", "aside_scanner", "claude", "cli", "codex", "file_scanners", "jsonio", "kiro_scanner",
            "opencode", "pi_family", "scanners", "sqlite_optional_scanners", "sqlite_scanners", "timeparse",
            "transcript", "types"))}),
    ("omh", "ultrawork/ulw-interview"): frozenset(),
    ("omh", "planner/omh-codebase-onboarding"): frozenset(),
    ("omh", "ultrawork/ulw-research"): frozenset({"references/briefing-format.md"}),
    ("omh", "operator/omh-skill-scout"): frozenset(),
    ("omh", "ultrawork/ulw-plan"): frozenset(),
    ("omh", "ultrawork/ulw-work"): frozenset({"references/campaign-orchestrator.md", "references/dependency-topology.md",
                                              "references/tdd-red-green.md"}),
    ("omh", "reviewer/omh-code-review"): frozenset({"references/review-dispatch.md", "references/review-response.md",
                                                    "references/smell-baseline.md"}),
    ("omh", "operator/omh-visual-qa"): frozenset({"references/visual-verdict-contract.md"}),
    ("omh", "reviewer/omh-native-debugging"): frozenset({"references/native-debug-loop.md"}),
    ("omh", "reviewer/omh-verification-gate"): frozenset(),
    ("gsd", "gsd-debug"): frozenset(),
    ("gsd", "gsd-explore"): frozenset(),
}
COMPANIONS: Final = {
    (ecosystem, selector): frozenset(f"{SKILL_PREFIXES[ecosystem]}/{selector}/{path}" for path in paths)
    for (ecosystem, selector), paths in _LOCAL_COMPANIONS.items()
}
OMH_CANONICAL: Final = {
    "ultrawork/ulw-interview": "deep-interview",
    "planner/omh-codebase-onboarding": "codebase-onboarding",
    "ultrawork/ulw-research": "research",
    "operator/omh-skill-scout": "skill-scout",
    "ultrawork/ulw-plan": "ralplan",
    "ultrawork/ulw-work": "ultrawork",
    "reviewer/omh-code-review": "code-review",
    "operator/omh-visual-qa": "visual-qa",
    "reviewer/omh-native-debugging": "native-debugging",
    "reviewer/omh-verification-gate": "verification-gate",
}
TARGETS: Final = {
    "tk-grill": {("omh", "ultrawork/ulw-interview", "component", ("interview",)), ("gsd", "gsd-explore", "component", ("interview",))},
    "tk-spec": {("omh", "ultrawork/ulw-interview", "component", ("clarify",))},
    "tk-map": {
        ("omo", "ulw-research", "component", ("map",)),
        ("omh", "planner/omh-codebase-onboarding", "component", ("map",)),
    },
    "tk-discuss": {("omh", "ultrawork/ulw-interview", "component", ("discuss",))},
    "tk-research": {
        ("omo", "ulw-research", "handoff", ("research",)),
        ("omh", "ultrawork/ulw-research", "handoff", ("research",)),
    },
    "tk-learn": {
        ("omo", "ulw-research", "component", ("research",)),
        ("omh", "ultrawork/ulw-research", "component", ("research",)),
        ("omh", "operator/omh-skill-scout", "component", ("discover",)),
    },
    "tk-plan": {
        ("omo", "ulw-plan", "handoff", ("plan",)),
        ("omh", "ultrawork/ulw-plan", "handoff", ("plan",)),
    },
    "tk-execute": {
        ("omo", "ulw-execute", "handoff", ("execute",)),
        ("omh", "ultrawork/ulw-work", "handoff", ("execute",)),
    },
    "tk-review": {("omh", "reviewer/omh-code-review", "component", ("diff",))},
    "tk-verify-work": {
        ("omo", "visual-qa", "component", ("visual",)),
        ("omh", "operator/omh-visual-qa", "component", ("visual",)),
    },
    "tk-debug": {
        ("omo", "debugging", "handoff", ("general", "native-fault")),
        ("omh", "reviewer/omh-native-debugging", "component", ("native-fault",)),
        ("gsd", "gsd-debug", "handoff", ("general", "native-fault")),
    },
    "tk-ship": {("omh", "reviewer/omh-verification-gate", "component", ("prepare",))},
    "tk-audit": {("omh", "reviewer/omh-verification-gate", "component", ("audit",))},
    "tk-handoff": {("omo", "coding-agent-sessions", "component", ("lookup",))},
}
