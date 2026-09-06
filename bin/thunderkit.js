#!/usr/bin/env node
// thunderkit — thin entry point. thunderkit is plain Agent Skills, not a launcher;
// this bin exists only so `npx thunderkit` / a global install gives a friendly pointer
// to the real, standard install path (the vercel `skills` CLI). It owns no install logic.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
const REPO = "thunderock/thunderkit";
const args = process.argv.slice(2);
const cmd = args[0] || "help";

const ADD_ALL = ["skills", "add", REPO, "--all"];

function help() {
  process.stdout.write(`thunderkit v${pkg.version} — opinionated Agent Skills for big-repo multi-model work

  thunderkit is a set of plain SKILL.md files (the open Agent Skills standard),
  installed with the vercel \`skills\` CLI. This command is just a pointer.

  Usage:
    npx thunderkit install     Install the whole pack into every detected agent
    npx thunderkit list        List the skills in the pack (no install)
    npx thunderkit help        Show this

  Equivalent direct commands:
    npx skills add ${REPO} --all
    npx skills add ${REPO} -s '*' -g --agent claude-code codex opencode hermes-agent
    npx skills add ${REPO} -l

  Docs: ${pkg.homepage}
`);
}

function run(extra) {
  // Delegate to the real installer; never reimplement it.
  const r = spawnSync("npx", ["-y", ...extra], { stdio: "inherit" });
  process.exit(r.status ?? 0);
}

switch (cmd) {
  case "install":
  case "add":
    run(ADD_ALL);
    break;
  case "list":
  case "ls":
    run(["skills", "add", REPO, "-l"]);
    break;
  case "-v":
  case "--version":
    process.stdout.write(pkg.version + "\n");
    break;
  case "help":
  case "-h":
  case "--help":
  default:
    help();
}
