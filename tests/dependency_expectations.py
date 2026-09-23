"""Independent expectations for the native-peer contract tests."""

import re
from typing import Final

PINS: Final = {
    "omo": ("oh-my-openagent", "5.0.0-beta.81"),
    "omh": ("oh-my-hermes", "2.0.3"),
}
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
ROOT_KINDS: Final = {"omo": "package", "omh": "omh"}
SKILL_PREFIXES: Final = {"omo": "dist/skills", "omh": "skills"}
PEER_ROOTS: Final = {
    "omo": ("package", "package.json", "dist/skills/<skill_name>/SKILL.md"),
    "omh": ("omh", "manifest.json", "skills/<category>/<skill_name>/SKILL.md"),
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
# Entrypoint fingerprints pinned from the trusted artifact inventories, independent of the manifest.
ENTRYPOINT_SHA256: Final = {
    ("omo", "ulw-research"): "989f86f1920f783aed6156438f1ab29d5e10c12aea3bcebff5362295777c6afe",
    ("omo", "ulw-plan"): "27a0f81ccb76431beb2889ec83239d525946697aa4fb5e86071ec07af6861dbd",
    ("omo", "ulw-execute"): "071a86e7981278d678f35e8c2d00dd007494688b64eac7a60b1fcc14d339cba7",
    ("omo", "visual-qa"): "5ea017377d1d2789722bdb0545cdf01b0fbeb3fb251f8a981325fa948ba13c6f",
    ("omo", "debugging"): "49fb22e0a1adb577cce543acc4e823212bd1af2e360b0e89aec041498081ed5e",
    ("omo", "coding-agent-sessions"): "9b00f11c1aadc51604f67376f45f33e416dc90915fd83f616a7936d5bd173d61",
    ("omh", "ultrawork/ulw-interview"): "c3a9d80a041ad8695e6bb513be9fc9e48c3b54444f53b134dca4cb6249076317",
    ("omh", "planner/omh-codebase-onboarding"): "ad50a185e1ccbfaaf71a590125ac66ed628cbc66b427861d07583ed93b791c17",
    ("omh", "ultrawork/ulw-research"): "95186ac5e6ec0a70ccfd4509ed15e8a3869077dd7866288fed7b635f1022968a",
    ("omh", "operator/omh-skill-scout"): "8e39141b6cb54da294799738304ffa76d49ff436caa6c44df52db6f7343d400e",
    ("omh", "ultrawork/ulw-plan"): "ad7f130b32e8333c3cbbf290ef3fb4131e7a2012298a10a82d91ffe746e23481",
    ("omh", "ultrawork/ulw-work"): "738485b872799d09e39d1bb9a4faead1e2794848615791cdae659f42bb259ebc",
    ("omh", "reviewer/omh-code-review"): "6043c0cbd886314d8577913727c0527c7cb50fe72677c6bded6f68dee74b8cd2",
    ("omh", "operator/omh-visual-qa"): "1cc3ed7024fb1093433d2eafd1dfd0950262c1b104aa14b0cd391c866d7b821e",
    ("omh", "reviewer/omh-native-debugging"): "109ebcb5eab82b2ceb646fca9e25068afc40b921ad15dd4cb118a1ffd216de4d",
    ("omh", "reviewer/omh-verification-gate"): "5ac11ff9c08bf1c5f5933daf659deb22046c927328d85bb1fb7adbd7b3af1583",
}
OMH_RAIL_SHA256: Final = "8762158b58981df3127c86bffc8172c91c4d371d16bca668feb76910460c4a19"
SHARED_FILES: Final = {"omh": {OMH_RAIL: OMH_RAIL_SHA256}}
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
    "tk-grill": {("omh", "ultrawork/ulw-interview", "component", ("interview",))},
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
    },
    "tk-ship": {("omh", "reviewer/omh-verification-gate", "component", ("prepare",))},
    "tk-audit": {("omh", "reviewer/omh-verification-gate", "component", ("audit",))},
    "tk-handoff": {("omo", "coding-agent-sessions", "component", ("lookup",))},
}
