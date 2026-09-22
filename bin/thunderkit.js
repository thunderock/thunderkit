#!/usr/bin/env node
// Keep installation delegated to the pinned distribution CLI.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const REPO = "thunderock/thunderkit";
const DISTRIBUTION_CLI = "skills@1.7.0";
const INSTALL_NODE = ">=22.20.0";
const DEPS_NOTE = "Thunderkit never runs these ecosystem install or doctor commands; hints are informational only.";

/** @param {string} version @param {string} min @returns {boolean} */
export function nodeSatisfies(version, min) {
  const current = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  const required = /^(?:>=)?(\d+)\.(\d+)\.(\d+)$/.exec(min);
  if (!current || !required) return false;
  for (let i = 1; i <= 3; i++) {
    const difference = Number(current[i]) - Number(required[i]);
    if (difference !== 0) return difference > 0;
  }
  return true;
}

/** @typedef {{package: string, version: string, license: string, hosts: string[], runtime?: Record<string, string>, install_hint: string, doctor_hint: string}} Ecosystem */
/** @typedef {{schema_version: number, ecosystems: {omo: Ecosystem, omh: Ecosystem}, distribution_cli: {package: string, version: string, node: string}} DependencyManifest */

/** @param {DependencyManifest} manifest @param {{json?: boolean}} options @returns {string} */
export function renderDeps(manifest, { json = false } = {}) {
  if (manifest?.schema_version !== 1 || !manifest.ecosystems?.omo || !manifest.ecosystems?.omh || !manifest.distribution_cli) {
    throw new TypeError("unsupported or incomplete dependency manifest");
  }
  const { omo, omh } = manifest.ecosystems;
  const output = {
    schema_version: 1,
    ecosystems: { omo, omh },
    distribution_cli: manifest.distribution_cli,
    note: DEPS_NOTE,
  };
  if (json) return JSON.stringify(output, null, 2) + "\n";

  const lines = [];
  for (const [name, peer] of Object.entries(output.ecosystems)) {
    const runtime = Object.entries(peer.runtime ?? {}).map(([name, version]) => `${name} ${version}`).join(", ");
    lines.push(
      `${name}: ${peer.package}@${peer.version}`,
      `  license: ${peer.license}`,
      `  hosts: ${peer.hosts.join(", ")}`,
      `  runtime: ${runtime || "host-managed (not specified)"}`,
      `  install_hint: ${peer.install_hint}`,
      `  doctor_hint: ${peer.doctor_hint}`,
      "",
    );
  }
  const cli = output.distribution_cli;
  lines.push(DEPS_NOTE, `distribution_cli: ${cli.package}@${cli.version} (Node ${cli.node})`, "");
  return lines.join("\n");
}

/** @param {string[]} argv @returns {{command: "deps", json: boolean} | {command: "install" | "list" | "version" | "help"}} */
export function parseArgs(argv) {
  switch (argv[0]) {
    case "deps": {
      const options = argv.slice(1);
      const unknown = options.find((option) => option !== "--json");
      if (unknown !== undefined) throw new RangeError(`unknown option for deps: ${unknown}`);
      return { command: "deps", json: options.includes("--json") };
    }
    case "install":
    case "add":
      return { command: "install" };
    case "list":
    case "ls":
      return { command: "list" };
    case "-v":
    case "--version":
      return { command: "version" };
    default:
      return { command: "help" };
  }
}

function help() {
  process.stdout.write(`thunderkit v${pkg.version} — opinionated Agent Skills for big-repo multi-model work

  thunderkit is a set of plain SKILL.md files (the open Agent Skills standard),
  installed with the vercel \`skills\` CLI. This command is just a pointer.

  Usage:
    npx thunderkit install     Install the whole pack into every detected agent
    npx thunderkit list        List the skills in the pack (no install)
    npx thunderkit deps        Show ecosystem dependencies and manual hints
    npx thunderkit deps --json Print dependency information as JSON
    npx thunderkit help        Show this
    npx thunderkit --version   Show the package version

  install/list require Node ${INSTALL_NODE}; help/version/deps work on Node >=18.

  Equivalent direct commands:
    npx -y ${DISTRIBUTION_CLI} add ${REPO} --all
    npx -y ${DISTRIBUTION_CLI} add ${REPO} -s '*' -g --agent claude-code codex opencode hermes-agent
    npx -y ${DISTRIBUTION_CLI} add ${REPO} -l

  Docs: ${pkg.homepage}
`);
}

/** @param {string[]} extra @returns {void} */
function run(extra) {
  if (!nodeSatisfies(process.versions.node, INSTALL_NODE)) {
    process.stderr.write(`install/list require Node ${INSTALL_NODE} for ${DISTRIBUTION_CLI} (current ${process.versions.node}); help/version/deps work on Node >=18\n`);
    process.exitCode = 2;
    return;
  }
  const r = spawnSync("npx", ["-y", DISTRIBUTION_CLI, "add", REPO, ...extra], { stdio: "inherit" });
  if (r.error) {
    process.stderr.write(`thunderkit: could not run npx: ${r.error.message}\n`);
    process.exitCode = 1;
  } else if (r.signal) {
    process.stderr.write(`thunderkit: npx terminated by ${r.signal}\n`);
    process.exitCode = 1;
  } else {
    process.exitCode = r.status === null ? 1 : r.status;
  }
}

function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    process.stderr.write(`thunderkit: ${error.message}\n`);
    process.exitCode = 2;
    return;
  }

  switch (options.command) {
    case "install":
      run(["--all"]);
      break;
    case "list":
      run(["-l"]);
      break;
    case "deps":
      try {
        // THUNDERKIT_DEPS_MANIFEST is a test-only override for failure fixtures.
        const path = process.env.THUNDERKIT_DEPS_MANIFEST || new URL("../skills/references/dependencies.json", import.meta.url);
        const manifest = JSON.parse(readFileSync(path, "utf8"));
        process.stdout.write(renderDeps(manifest, options));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        process.stderr.write(`thunderkit: dependency manifest: ${message.replace(/[\r\n]+/g, " ")}\n`);
        process.exitCode = 1;
      }
      break;
    case "version":
      process.stdout.write(pkg.version + "\n");
      break;
    case "help":
      help();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
