import { after, test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import * as cli from "../bin/thunderkit.js";

const entry = new URL("../bin/thunderkit.js", import.meta.url);
const bin = fileURLToPath(entry);
const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const manifest = JSON.parse(readFileSync(new URL("../skills/references/dependencies.json", import.meta.url), "utf8"));
const evidenceRoot = process.env.THUNDERKIT_TEST_TMPDIR
  || fileURLToPath(new URL("../.omo/evidence/cli/", import.meta.url));
mkdirSync(evidenceRoot, { recursive: true });
const sandbox = mkdtempSync(join(evidenceRoot, "cli-"));
after(() => rmSync(sandbox, { recursive: true, force: true }));

/** @param {string[]} args @param {Record<string, string>} env */
function runNode(args, env = {}) {
  const result = spawnSync(process.execPath, args, {
    cwd: sandbox,
    env: { ...process.env, PATH: sandbox, THUNDERKIT_DEPS_MANIFEST: "", ...env },
    encoding: "utf8",
    timeout: 10_000,
  });
  assert.ifError(result.error);
  return result;
}

/** @param {string[]} args @param {Record<string, string>} env */
function runCli(args, env = {}) {
  return runNode([bin, ...args], env);
}

function npxStub() {
  const directory = mkdtempSync(join(sandbox, "npx-"));
  const log = join(directory, "argv.txt");
  writeFileSync(join(directory, "npx"), `#!/bin/sh
printf '%s\\n' "$@" > "$THUNDERKIT_ARGV_LOG"
if [ -n "\${THUNDERKIT_CHILD_SIGNAL:-}" ]; then
  kill -"$THUNDERKIT_CHILD_SIGNAL" "$$"
fi
exit "\${THUNDERKIT_CHILD_EXIT:-0}"
`, { mode: 0o755 });
  return { log, env: { PATH: directory, THUNDERKIT_ARGV_LOG: log } };
}

for (const [version, expected] of [
  ["22.20.0", true], ["22.19.9", false], ["18.20.4", false], ["23.0.0", true],
  ["22.3.0", false], ["9.99.99", false], ["22.20.1", true],
]) {
  test(`nodeSatisfies compares ${version} numerically`, () => {
    assert.equal(cli.nodeSatisfies(version, ">=22.20.0"), expected);
  });
}

test("nodeSatisfies compares patch floors and rejects incomplete versions", () => {
  assert.equal(cli.nodeSatisfies("22.20.0", ">=22.20.1"), false);
  assert.equal(cli.nodeSatisfies("22.20", ">=22.20.0"), false);
});

test("renderDeps returns only the dependency JSON contract", () => {
  const output = JSON.parse(cli.renderDeps(manifest, { json: true }));
  assert.deepEqual(Object.keys(output).sort(), ["distribution_cli", "ecosystems", "hosts", "note", "schema_version"]);
  assert.equal(output.schema_version, 2);
  assert.deepEqual(output.hosts, { hermes: "omh", opencode: "omo", default: "gsd" });
  assert.deepEqual(Object.keys(output.ecosystems), ["omo", "omh", "gsd"]);
  assert.deepEqual(Object.values(output.ecosystems).map(({ package: name, channel }) => `${name} ${channel}`), [
    "oh-my-openagent max-prerelease:5.x:beta", "oh-my-hermes dist-tag:latest", "get-shit-done-cc dist-tag:latest",
  ]);
  assert.ok(Object.values(output.ecosystems).every((peer) => !("version" in peer) && !("integrity" in peer)));
  assert.deepEqual(output.ecosystems, manifest.ecosystems);
  assert.deepEqual(output.distribution_cli, manifest.distribution_cli);
  assert.match(output.note, /Thunderkit never runs/);
});

test("renderDeps describes requirements and hints without executing them", () => {
  const output = cli.renderDeps(manifest, { json: false });
  for (const peer of Object.values(manifest.ecosystems)) {
    for (const value of [`${peer.package} (channel ${peer.channel})`, peer.license, ...peer.hosts, peer.install_hint, peer.doctor_hint ?? "none"]) {
      assert.ok(output.includes(value), `missing dependency detail: ${value}`);
    }
  }
  assert.match(output, /runtime:.*host-managed/i);
  assert.match(output, /node >=18/i);
  assert.match(output, /python >=3\.11/i);
  assert.match(output, /Thunderkit never runs[^\n]+\n(?:\n)?distribution_cli: skills@1\.7\.0.*Node >=22\.20\.0/);
});

test("parseArgs accepts deps and rejects every option except --json", () => {
  assert.deepEqual(cli.parseArgs(["deps"]), { command: "deps", json: false });
  assert.deepEqual(cli.parseArgs(["deps", "--json"]), { command: "deps", json: true });
  for (const option of ["--bogus", "--help", "--json=true", "extra"]) {
    assert.throws(() => cli.parseArgs(["deps", option]), RangeError);
    assert.throws(() => cli.parseArgs(["deps", "--json", option]), RangeError);
  }
});

test("importing the entry point does not run the CLI", () => {
  const result = runNode(["--input-type=module", "--eval", `await import(${JSON.stringify(entry.href)});`]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr, "");
});

test("deps --json emits exactly one object from outside the package directory", () => {
  const result = runCli(["deps", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  const output = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(output).sort(), ["distribution_cli", "ecosystems", "hosts", "note", "schema_version"]);
  assert.equal(output.schema_version, 2);
  assert.deepEqual(Object.keys(output.ecosystems), ["omo", "omh", "gsd"]);
  assert.equal(output.ecosystems.omo.channel, "max-prerelease:5.x:beta");
  assert.equal(output.ecosystems.omh.channel, "dist-tag:latest");
  assert.equal(output.ecosystems.gsd.channel, "dist-tag:latest");
  assert.equal(output.distribution_cli.version, "1.7.0");
});

test("deps prints every host peer with its release channel", () => {
  const result = runCli(["deps"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  assert.match(result.stdout, /oh-my-openagent \(channel max-prerelease:5\.x:beta\)/);
  assert.match(result.stdout, /oh-my-hermes \(channel dist-tag:latest\)/);
  assert.match(result.stdout, /get-shit-done-cc \(channel dist-tag:latest\)/);
});

test("deps rejects unknown options before reading the manifest", () => {
  const result = runCli(["deps", "--bogus"], { THUNDERKIT_DEPS_MANIFEST: join(sandbox, "missing.json") });
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /--bogus/);
});

for (const command of ["help", "--help", "-h"]) {
  test(`${command} includes dependency help without loading the manifest`, () => {
    const result = runCli([command], { THUNDERKIT_DEPS_MANIFEST: join(sandbox, "missing.json") });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /deps/);
    assert.match(result.stdout, /skills@1\.7\.0/);
  });
}

for (const command of ["--version", "-v"]) {
  test(`${command} matches package.json`, () => {
    const result = runCli([command]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, `${pkg.version}\n`);
  });
}

for (const [name, contents] of [["corrupt", "{broken"], ["invalid", "{}"], ["missing", null]]) {
  test(`deps reports a ${name} manifest without a stack trace`, () => {
    const path = join(sandbox, `${name}.json`);
    if (contents !== null) writeFileSync(path, contents);
    for (const args of [["deps"], ["deps", "--json"]]) {
      const result = runCli(args, { THUNDERKIT_DEPS_MANIFEST: path });
      assert.equal(result.status, 1);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /dependency manifest/i);
      assert.equal(result.stderr.trim().split("\n").length, 1);
      assert.doesNotMatch(result.stderr, /^\s+at\s|node:internal/m);
    }
  });
}

for (const [command, flag] of [["install", "--all"], ["add", "--all"], ["list", "-l"], ["ls", "-l"]]) {
  for (const exitCode of [0, 7]) {
    test(`${command} pins installer argv and propagates exit ${exitCode}`, () => {
      const stub = npxStub();
      const result = runCli([command], { ...stub.env, THUNDERKIT_CHILD_EXIT: String(exitCode) });
      if (cli.nodeSatisfies(process.versions.node, ">=22.20.0")) {
        assert.equal(result.status, exitCode, result.stderr);
        assert.deepEqual(readFileSync(stub.log, "utf8").trimEnd().split("\n"), [
          "-y", "skills@1.7.0", "add", "thunderock/thunderkit", flag,
        ]);
      } else {
        assert.equal(result.status, 2);
        assert.equal(existsSync(stub.log), false);
      }
    });
  }

  test(`${command} rejects an old Node runtime before spawning`, () => {
    const stub = npxStub();
    const result = runNode(["--input-type=module", "--eval", `
      Object.defineProperty(process.versions, "node", { value: "18.20.4" });
      process.argv = [process.execPath, ${JSON.stringify(bin)}, ${JSON.stringify(command)}];
      await import(${JSON.stringify(entry.href)});
    `], stub.env);
    assert.equal(result.status, 2);
    assert.equal(result.stdout, "");
    assert.equal(existsSync(stub.log), false);
    assert.equal(result.stderr, "install/list require Node >=22.20.0 for skills@1.7.0 (current 18.20.4); help/version/deps work on Node >=18\n");
  });
}

test("an absent npx cannot report a successful install", () => {
  const result = runCli(["install"]);
  const canInstall = cli.nodeSatisfies(process.versions.node, ">=22.20.0");
  assert.equal(result.status, canInstall ? 1 : 2);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, canInstall ? /npx.*ENOENT/ : /require Node >=22\.20\.0/);
  assert.doesNotMatch(result.stderr, /^\s+at\s|node:internal/m);
});

test("an installer terminated by a signal cannot report success", () => {
  const stub = npxStub();
  const result = runCli(["install"], { ...stub.env, THUNDERKIT_CHILD_SIGNAL: "TERM" });
  const canInstall = cli.nodeSatisfies(process.versions.node, ">=22.20.0");
  assert.equal(result.status, canInstall ? 1 : 2);
  assert.match(result.stderr, canInstall ? /SIGTERM/ : /require Node >=22\.20\.0/);
});
