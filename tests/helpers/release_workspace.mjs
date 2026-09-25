// @ts-check
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { basename, delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import { exec } from "../../tools/release/io.mjs";
import { requestFacts } from "./release_facts.mjs";

/** @typedef {import("../../tools/release/io.mjs").ExecResult} ExecResult */
/** @typedef {import("../../tools/release/io.mjs").ExecOptions} ExecOptions */
/** @typedef {import("../../tools/release/io.mjs").Drivers} Drivers */
/** @typedef {import("../../tools/release/artifact.mjs").Workspace} Workspace */
/** @typedef {ReturnType<typeof createFixture>} Fixture */
/** @typedef {{program:string, argv:readonly string[], env:Readonly<Record<string,string>>|undefined}} Call */

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const evidenceRoot = process.env.RELEASE_TEST_EVIDENCE ?? "";
const devCaches = new Set(["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"]);
const secretKeys = ["GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN", "NPM_CONFIG_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"];
/** @param {string} name */
function binary(name) {
  const found = (process.env.PATH ?? "").split(delimiter).map((directory) => join(directory, name)).find(existsSync);
  assert.ok(found, `missing ${name} on PATH`);
  return realpathSync(found);
}
export const programs = Object.freeze({ git: binary("git"), npm: binary("npm"), python3: binary("python3"), node: process.execPath });

/** Every fixture child runs with an isolated HOME and no inherited credentials beyond explicit test values. @param {string} program @param {readonly string[]} args @param {string} cwd @param {Readonly<Record<string,string>>} [env] */
export function child(program, args, cwd, env = {}) {
  const result = spawnSync(program, [...args], {
    cwd, encoding: "utf8", timeout: 120_000, maxBuffer: 16_777_216,
    env: { PATH: process.env.PATH ?? "", HOME: tmpdir(), TMPDIR: tmpdir(), LC_ALL: "C.UTF-8", GIT_MASTER: "1", GIT_AUTOPUSH_DISABLE: "1", GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0", ...env },
  });
  assert.ifError(result.error);
  return result;
}
/** @param {string} cwd @param {readonly string[]} args */
export function git(cwd, args) {
  const result = child(programs.git, ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], cwd);
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}
/** Wrapper scripts journal every child invocation and reject anything outside the offline allowlist; the fixed remote URL resolves to the fixture itself. @param {string} directory @param {string} journal @param {string} checkoutDir */
function writeStubs(directory, journal, checkoutDir) {
  for (const [name, target] of Object.entries(programs)) {
    const prefix = name === "npm" ? [programs.node, target] : [target];
    writeFileSync(join(directory, name), `#!${programs.node}
const fs = require("node:fs"); const cp = require("node:child_process");
const args = process.argv.slice(2); const name = ${JSON.stringify(name)};
const gitCommand = args.filter((arg, index, all) => !(arg === "-c" || all[index - 1] === "-c"))[0];
const localArgs = gitCommand === "fetch" ? args.map((arg) => (arg === "https://github.com/thunderock/thunderkit.git" ? ${JSON.stringify(checkoutDir)} : arg)) : args;
const remoteArg = localArgs.some((arg) => /^(https?:|git@|ssh:)/.test(arg));
const gitAllowed = ["init", "add", "commit", "tag", "rev-parse", "check-ref-format", "archive", "rev-list", "log", "show", "cat-file", "merge-base", "for-each-ref", "update-ref", "checkout", "branch", "fetch", "update-index"].includes(gitCommand) && !remoteArg;
const npmAllowed = args[0] === "--version" || (args[0] === "version" && args.includes("--no-git-tag-version") && args.includes("--ignore-scripts")) || (args[0] === "pack" && args.includes("--offline") && args.includes("--ignore-scripts"));
const allowed = name === "git" ? gitAllowed : name === "npm" ? npmAllowed : true;
fs.appendFileSync(${JSON.stringify(journal)}, JSON.stringify({ name, args, allowed }) + "\\n");
if (!allowed) { process.stderr.write("fixture: rejected " + name + " invocation\\n"); process.exit(97); }
const result = cp.spawnSync(${JSON.stringify(prefix[0])}, [...${JSON.stringify(prefix.slice(1))}, ...(name === "git" ? localArgs : args)], { env: process.env, stdio: "inherit", timeout: 110000 });
process.exit(result.error || result.signal || result.status === null ? 98 : result.status);
`, { mode: 0o755 });
  }
  writeFileSync(join(directory, "gh"), `#!${programs.node}
require("node:fs").appendFileSync(${JSON.stringify(journal)}, JSON.stringify({ name: "gh", args: process.argv.slice(2), allowed: false }) + "\\n");
process.stderr.write("fixture: rejected gh invocation\\n"); process.exit(97);
`, { mode: 0o755 });
}
/** A fresh committed copy of the distributable source with an isolated stub PATH. @param {string} [sourceDir] */
export function createFixture(sourceDir = repoRoot) {
  const root = mkdtempSync(join(tmpdir(), "release-case-"));
  chmodSync(root, 0o700);
  const checkoutDir = join(root, "checkout");
  mkdirSync(checkoutDir);
  const names = ["bin", "skills", "package.json", ".npmignore", "NORTH_STAR.md", "README.md", "LICENSE", "CHANGELOG.md"];
  if (existsSync(join(sourceDir, "DEPENDENCIES.md"))) names.push("DEPENDENCIES.md");
  for (const name of names) {
    cpSync(join(sourceDir, name), join(checkoutDir, name), {
      recursive: true,
      filter: (path) => !devCaches.has(basename(path)) && !/\.py[co]$/.test(path),
    });
  }
  const manifest = join(checkoutDir, "package.json");
  /** @type {unknown} */ const pkg = JSON.parse(readFileSync(manifest, "utf8"));
  assert.ok(pkg !== null && typeof pkg === "object" && !Array.isArray(pkg));
  writeFileSync(manifest, `${JSON.stringify({ ...pkg, version: "0.1.1" }, null, 2)}\n`);
  git(checkoutDir, ["init", "--quiet", "--initial-branch=master"]);
  git(checkoutDir, ["add", "--", "."]);
  git(checkoutDir, ["commit", "--quiet", "-m", "feat: initial package"]);
  const bin = join(root, "allowed-bin");
  mkdirSync(bin);
  const journal = join(root, "children.jsonl");
  writeStubs(bin, journal, checkoutDir);
  const { raw, request } = requestFacts();
  const sha = git(checkoutDir, ["rev-parse", "HEAD"]);
  raw.GITHUB_SHA = sha;
  request.sourceSha = sha;
  /** @type {Workspace} */ const workspace = { checkoutDir, stageDir: join(root, "stage"), bundleDir: join(root, "bundle") };
  return { root, checkoutDir, workspace, request, raw, bin, journal, sha };
}
/** @param {Fixture} fixture */
export function destroyFixture(fixture) { rmSync(fixture.root, { recursive: true, force: true }); }
/** Run with the fixture checkout as cwd, only stub programs on PATH and no inherited credential environment beyond the explicit test values. @template T @param {Fixture} fixture @param {() => Promise<T>} operation @param {Readonly<Record<string,string>>} [env] @returns {Promise<T>} */
export async function isolated(fixture, operation, env = {}) {
  const previous = { cwd: process.cwd(), env: { ...process.env } };
  process.chdir(fixture.checkoutDir);
  process.env.PATH = fixture.bin;
  for (const key of secretKeys) delete process.env[key];
  Object.assign(process.env, env);
  try {
    return await operation();
  } finally {
    process.chdir(previous.cwd);
    for (const key of Object.keys(process.env)) if (!(key in previous.env)) delete process.env[key];
    Object.assign(process.env, previous.env);
  }
}
/** @param {Fixture} fixture @param {string} version @param {string} channel */
export function manual(fixture, version, channel = "") {
  Object.assign(fixture.request, { event: "workflow_dispatch", inputVersion: version, inputNpmTag: channel });
  Object.assign(fixture.raw, { GITHUB_EVENT_NAME: "workflow_dispatch", RELEASE_VERSION_INPUT: version, RELEASE_NPM_TAG_INPUT: channel });
}
/** Commit tracked changes (or an empty commit) and move the request to the new HEAD. @param {Fixture} fixture @param {string} subject @param {string} [body] */
export function commit(fixture, subject = "fix: revise package", body) {
  git(fixture.checkoutDir, ["add", "--", "."]);
  git(fixture.checkoutDir, ["commit", "--quiet", "--allow-empty", "-m", subject, ...(body === undefined ? [] : ["-m", body])]);
  return moveSource(fixture, git(fixture.checkoutDir, ["rev-parse", "HEAD"]));
}
/** @param {Fixture} fixture @param {string} sha */
export function moveSource(fixture, sha) {
  fixture.sha = sha;
  fixture.request.sourceSha = sha;
  fixture.raw.GITHUB_SHA = sha;
  return sha;
}
/** Fixture-only tag: annotated when a message is given, lightweight otherwise. @param {Fixture} fixture @param {string} name @param {string|null} message @param {string} [sha] */
export function tag(fixture, name, message, sha = fixture.sha) {
  if (message === null) {
    git(fixture.checkoutDir, ["tag", name, sha]);
  } else {
    const file = join(fixture.root, `${createHash("sha256").update(name).digest("hex").slice(0, 12)}.msg`);
    writeFileSync(file, message);
    git(fixture.checkoutDir, ["tag", "-a", name, "--cleanup=verbatim", "-F", file, sha]);
  }
  return git(fixture.checkoutDir, ["rev-parse", `refs/tags/${name}`]);
}
/** @param {Fixture} fixture */
export function bundleOf(fixture) {
  const bytes = readFileSync(join(fixture.workspace.bundleDir, "release-plan.json"));
  return { directory: fixture.workspace.bundleDir, recordSha256: createHash("sha256").update(bytes).digest("hex") };
}
/** @param {Fixture} fixture */
export function journalOf(fixture) {
  return existsSync(fixture.journal) ? readFileSync(fixture.journal, "utf8").trim().split("\n").filter(Boolean).map((line) => /** @type {{name:string, args:string[], allowed:boolean}} */ (JSON.parse(line))) : [];
}
/** @param {string} name @param {unknown} value */
export function saveEvidence(name, value) {
  if (evidenceRoot === "") return;
  mkdirSync(evidenceRoot, { recursive: true });
  writeFileSync(join(evidenceRoot, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`);
}
/** Real local git/npm/python through the stubs; remote-touching commands are recorded and answered by test hooks. */
export function systemDrivers() {
  /** @type {Call[]} */ const calls = [];
  const hooks = {
    /** @type {(call:Call) => ExecResult} */ push: () => ({ status: 0, signal: null, stdout: "", stderr: "" }),
    /** @type {(call:Call) => ExecResult} */ gh: () => ({ status: 1, signal: null, stdout: "HTTP/2.0 404 Not Found\r\nContent-Type: application/json\r\n\r\n{\"message\":\"Not Found\"}\n", stderr: "gh: Not Found (HTTP 404)\n" }),
    /** @type {(call:Call) => ExecResult} */ publish: () => ({ status: 0, signal: null, stdout: "", stderr: "" }),
    /** @type {(url:string) => Promise<{status:number, body:string}>} */ get: async () => ({ status: 404, body: "{\"error\":\"Not found\"}" }),
  };
  /** @type {Drivers} */ const drivers = {
    exec(program, argv, options) {
      const call = { program, argv: [...argv], env: options.env };
      calls.push(call);
      if (program === "git" && argv[0] === "push") return hooks.push(call);
      if (program === "gh") return hooks.gh(call);
      if (program === "npm" && argv[0] === "publish") return hooks.publish(call);
      return exec(program, argv, options);
    },
    get(url) { calls.push({ program: "https", argv: [url], env: undefined }); return hooks.get(url); },
  };
  return { drivers, calls, hooks };
}
