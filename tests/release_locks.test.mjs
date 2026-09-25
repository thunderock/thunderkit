// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { verifyArtifact } from "../tools/release/artifact.mjs";
import { artifactFixture, prepare, readObject, repack, unpack } from "./helpers/release_artifact.mjs";
import { commit, git, isolated, journalOf, programs } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
/** @param {Fixture} f @param {string} [version] */
function lockFor(f, version = "0.1.1") {
  const pkg = readObject(join(f.checkoutDir, "package.json"));
  return { name: "thunderkit", version, lockfileVersion: 3, requires: true, packages: { "": { name: "thunderkit", version, license: pkg.license, bin: pkg.bin, engines: pkg.engines } } };
}

/** @param {Fixture} f */
function selectShrinkwrap(f) {
  const path = join(f.checkoutDir, "package.json");
  const pkg = readObject(path);
  assert.ok(Array.isArray(pkg.files));
  writeFileSync(path, JSON.stringify({ ...pkg, files: [...pkg.files, "npm-shrinkwrap.json"] }));
}

/** Wrap the allowlisted npm stub and corrupt only its stamped fixture output. @param {Fixture} f @param {string} file @param {boolean} rootVersion */
function corruptStamp(f, file, rootVersion) {
  const original = join(f.root, "npm-original");
  renameSync(join(f.bin, "npm"), original);
  writeFileSync(join(f.bin, "npm"), `#!${programs.node}
const { spawnSync } = require("node:child_process");
const { readFileSync, writeFileSync } = require("node:fs");
const args = process.argv.slice(2);
const result = spawnSync(${JSON.stringify(programs.node)}, [${JSON.stringify(original)}, ...args], { stdio: "inherit", timeout: 110000 });
if (result.error || result.signal || result.status !== 0) process.exit(result.status || 98);
if (args[0] === "version") {
  const file = ${JSON.stringify(file)};
  const lock = JSON.parse(readFileSync(file, "utf8"));
  ${rootVersion ? "lock" : 'lock.packages[""]'}.version = "9.9.9";
  writeFileSync(file, JSON.stringify(lock));
}
`, { mode: 0o755 });
}

test("preparation creates no lock or shrinkwrap when neither exists in source", async (t) => {
  // Given
  const f = artifactFixture(t);
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  const packed = unpack(f);
  for (const file of ["package-lock.json", "npm-shrinkwrap.json"]) {
    for (const directory of [f.checkoutDir, f.workspace.stageDir, packed]) assert.equal(existsSync(join(directory, file)), false);
  }
});

for (const file of ["package-lock.json", "npm-shrinkwrap.json"]) {
  for (const format of [1, 3]) {
    test(`preparation stamps ${file} format ${format} consistently without changing source`, async (t) => {
      // Given
      const f = artifactFixture(t);
      const modern = lockFor(f);
      const lock = format === 1 ? { name: "thunderkit", version: "0.1.1", lockfileVersion: 1, requires: true } : modern;
      const bytes = Buffer.from(`${JSON.stringify(lock, null, 2)}\n`);
      writeFileSync(join(f.checkoutDir, file), bytes);
      if (file === "npm-shrinkwrap.json") selectShrinkwrap(f);
      commit(f);
      // When
      const result = await prepare(f);
      // Then
      assert.ok(result.ok, JSON.stringify(result));
      const expected = format === 1 ? { ...lock, version: "0.1.2" } : lockFor(f, "0.1.2");
      assert.deepEqual(readObject(join(f.workspace.stageDir, file)), expected);
      assert.equal(readObject(join(f.workspace.stageDir, "package.json")).version, "0.1.2");
      const packed = unpack(f);
      if (file === "npm-shrinkwrap.json") assert.deepEqual(readObject(join(packed, file)), expected);
      else assert.equal(existsSync(join(packed, file)), false);
      assert.deepEqual(readFileSync(join(f.checkoutDir, file)), bytes);
      assert.equal(git(f.checkoutDir, ["diff", "--exit-code"]), "");
    });
  }

  for (const rootVersion of [true, false]) {
    test(`preparation rejects ${file} when npm leaves its ${rootVersion ? "root" : "packages root"} version inconsistent`, async (t) => {
      // Given
      const f = artifactFixture(t);
      const bytes = Buffer.from(JSON.stringify(lockFor(f)));
      writeFileSync(join(f.checkoutDir, file), bytes);
      if (file === "npm-shrinkwrap.json") selectShrinkwrap(f);
      commit(f);
      corruptStamp(f, file, rootVersion);
      // When
      const result = await prepare(f);
      // Then
      assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
      assert.equal(journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 0);
      assert.deepEqual(readFileSync(join(f.checkoutDir, file)), bytes);
    });
  }
}

test("preparation rejects a tracked shrinkwrap when the package file list omits it", async (t) => {
  // Given
  const f = artifactFixture(t);
  writeFileSync(join(f.checkoutDir, "npm-shrinkwrap.json"), JSON.stringify(lockFor(f)));
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.equal(existsSync(join(unpack(f), "npm-shrinkwrap.json")), false);
});

for (const rootVersion of [true, false]) {
  test(`verification rejects a packed shrinkwrap with an inconsistent ${rootVersion ? "root" : "packages root"} version`, async (t) => {
    // Given
    const f = artifactFixture(t);
    writeFileSync(join(f.checkoutDir, "npm-shrinkwrap.json"), JSON.stringify(lockFor(f)));
    selectShrinkwrap(f);
    commit(f);
    const prepared = await prepare(f);
    assert.ok(prepared.ok, JSON.stringify(prepared));
    const lock = lockFor(f, "0.1.2");
    if (rootVersion) lock.version = "9.9.9";
    else lock.packages[""].version = "9.9.9";
    writeFileSync(join(f.workspace.stageDir, "npm-shrinkwrap.json"), JSON.stringify(lock));
    const rebound = await repack(f, prepared.value);
    // When
    const result = await isolated(f, () => verifyArtifact(rebound, f.workspace));
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  });
}
