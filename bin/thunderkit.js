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

/** @typedef {{package: string, channel: string, license: string, hosts: string[], runtime?: Record<string, string>, install_hint: string, doctor_hint?: string}} Ecosystem */
/** @typedef {{schema_version: number, hosts: Record<string, string>, ecosystems: {omo: Ecosystem, omh: Ecosystem, gsd: Ecosystem}, distribution_cli: {package: string, version: string, node: string}} DependencyManifest */

/** @param {DependencyManifest} manifest @param {{json?: boolean}} options @returns {string} */
export function renderDeps(manifest, { json = false } = {}) {
  if (manifest?.schema_version !== 2 || !manifest.hosts || !manifest.ecosystems?.omo || !manifest.ecosystems?.omh
      || !manifest.ecosystems?.gsd || !manifest.distribution_cli) {
    throw new TypeError("unsupported or incomplete dependency manifest");
  }
  const { omo, omh, gsd } = manifest.ecosystems;
  const output = {
    schema_version: 2,
    hosts: manifest.hosts,
    ecosystems: { omo, omh, gsd },
    distribution_cli: manifest.distribution_cli,
    note: DEPS_NOTE,
  };
  if (json) return JSON.stringify(output, null, 2) + "\n";

  const lines = [];
  for (const [name, peer] of Object.entries(output.ecosystems)) {
    const runtime = Object.entries(peer.runtime ?? {}).map(([name, version]) => `${name} ${version}`).join(", ");
    lines.push(
      `${name}: ${peer.package} (channel ${peer.channel})`,
      `  license: ${peer.license}`,
      `  hosts: ${peer.hosts.join(", ")}`,
      `  runtime: ${runtime || "host-managed (not specified)"}`,
      `  install_hint: ${peer.install_hint}`,
      `  doctor_hint: ${peer.doctor_hint ?? "none"}`,
      "",
    );
  }
  const cli = output.distribution_cli;
  lines.push(DEPS_NOTE, `distribution_cli: ${cli.package}@${cli.version} (Node ${cli.node})`, "");
  return lines.join("\n");
}

/** @param {string} version @returns {{core: number[], pre: string[]} | null} */
function parseSemver(version) {
  const m = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(version);
  if (!m) return null;
  return { core: [Number(m[1]), Number(m[2]), Number(m[3])], pre: m[4] === undefined ? [] : m[4].split(".") };
}

/** @param {string} a @param {string} b @returns {number} */
export function compareSemver(a, b) {
  const x = parseSemver(a), y = parseSemver(b);
  if (!x || !y) throw new RangeError(`not a canonical version: ${!x ? a : b}`);
  for (let i = 0; i < 3; i++) if (x.core[i] !== y.core[i]) return x.core[i] - y.core[i];
  if (!x.pre.length || !y.pre.length) return y.pre.length - x.pre.length;
  for (let i = 0; i < Math.max(x.pre.length, y.pre.length); i++) {
    if (i >= x.pre.length) return -1;
    if (i >= y.pre.length) return 1;
    const l = x.pre[i], r = y.pre[i], ln = /^\d+$/.test(l), rn = /^\d+$/.test(r);
    if (ln && rn && l !== r) return l.length !== r.length ? l.length - r.length : (l < r ? -1 : 1);
    if (ln !== rn) return ln ? -1 : 1;
    if (l !== r) return l < r ? -1 : 1;
  }
  return 0;
}

/** Resolve a manifest channel to one concrete published version. @param {string} channel @param {string[]} versions @param {Record<string, string>} distTags @returns {string} */
export function resolveChannel(channel, versions, distTags) {
  const published = versions.filter((version) => parseSemver(version) !== null);
  const tag = /^dist-tag:([a-z][a-z0-9-]*)$/.exec(channel);
  if (tag) {
    const version = distTags[tag[1]];
    if (typeof version !== "string" || !published.includes(version)) throw new RangeError(`dist-tag ${tag[1]} does not name a published version`);
    return version;
  }
  const series = /^max-prerelease:(0|[1-9]\d*)\.x:([a-z][a-z0-9-]*)$/.exec(channel);
  if (series) {
    const major = Number(series[1]);
    const matching = published.filter((version) => {
      const parsed = parseSemver(version);
      return parsed !== null && parsed.core[0] === major && parsed.pre.length === 2 && parsed.pre[0] === series[2] && /^\d+$/.test(parsed.pre[1]);
    }).sort(compareSemver);
    const highest = matching.at(-1);
    if (highest === undefined) throw new RangeError(`no ${major}.x ${series[2]} prerelease is published`);
    return highest;
  }
  throw new RangeError(`unsupported channel: ${channel}`);
}

/** @param {DependencyManifest} manifest @param {string} host @returns {string} */
export function peerForHost(manifest, host) {
  const peer = manifest.hosts[host] ?? manifest.hosts.default;
  if (peer === undefined || !(peer in manifest.ecosystems)) throw new RangeError(`no required peer for host ${host}`);
  return peer;
}

/** Build the printed, never-executed install step for one host. @param {DependencyManifest} manifest @param {string} host @param {string} version */
export function installPlan(manifest, host, version) {
  if (!/^[a-z][a-z0-9-]*$/.test(host)) throw new RangeError(`invalid host: ${host}`);
  const peer = peerForHost(manifest, host);
  const record = manifest.ecosystems[/** @type {"omo" | "omh" | "gsd"} */ (peer)];
  const escaped = record.package.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const command = record.install_hint
    .replace(new RegExp(`${escaped}@(?:latest|<[^>]*>)`, "g"), `${record.package}@${version}`)
    .replace(/--<runtime>/g, `--${host}`);
  return { host, peer, package: record.package, channel: record.channel, version, command };
}

/** @param {string[]} argv @returns {{command: "deps", json: boolean} | {command: "peers", host: string, json: boolean} | {command: "install" | "list" | "version" | "help"}} */
export function parseArgs(argv) {
  switch (argv[0]) {
    case "deps": {
      const options = argv.slice(1);
      const unknown = options.find((option) => option !== "--json");
      if (unknown !== undefined) throw new RangeError(`unknown option for deps: ${unknown}`);
      return { command: "deps", json: options.includes("--json") };
    }
    case "peers": {
      const options = argv.slice(1);
      let host = "", json = false;
      for (let i = 0; i < options.length; i++) {
        if (options[i] === "--json") json = true;
        else if (options[i] === "--host" && i + 1 < options.length) host = options[++i];
        else throw new RangeError(`unknown option for peers: ${options[i]}`);
      }
      if (!/^[a-z][a-z0-9-]*$/.test(host)) throw new RangeError("peers requires --host <name>");
      return { command: "peers", host, json };
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
    npx thunderkit peers --host <name> [--json]
                               Resolve the host's required peer and print its install command
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
    case "peers":
      try {
        const manifest = JSON.parse(readFileSync(process.env.THUNDERKIT_DEPS_MANIFEST || new URL("../skills/references/dependencies.json", import.meta.url), "utf8"));
        const peer = peerForHost(manifest, options.host);
        const record = manifest.ecosystems[peer];
        // THUNDERKIT_PEER_REGISTRY is a test-only offline override: {package: {versions, "dist-tags"}}.
        const override = process.env.THUNDERKIT_PEER_REGISTRY;
        let facts;
        if (override) {
          facts = JSON.parse(readFileSync(override, "utf8"))[record.package];
        } else {
          const view = spawnSync("npm", ["view", record.package, "versions", "dist-tags", "--json"], { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8", timeout: 60_000 });
          if (view.error || view.status !== 0) throw new Error(`npm view ${record.package} failed`);
          facts = JSON.parse(view.stdout);
        }
        if (!facts || !Array.isArray(facts.versions) || typeof facts["dist-tags"] !== "object") throw new Error("malformed registry facts");
        const plan = installPlan(manifest, options.host, resolveChannel(record.channel, facts.versions, facts["dist-tags"]));
        process.stdout.write(options.json ? JSON.stringify(plan, null, 2) + "\n"
          : `${plan.host}: ${plan.peer} ${plan.package}@${plan.version} (channel ${plan.channel})\n  run: ${plan.command}\n  Thunderkit never runs this command.\n`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        process.stderr.write(`thunderkit: peers: ${message.replace(/[\r\n]+/g, " ")}\n`);
        process.exitCode = error instanceof RangeError ? 2 : 1;
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
