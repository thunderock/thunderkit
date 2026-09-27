// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { verifyArtifact } from "../tools/release/artifact.mjs";
import { artifactFixture, prepare, readObject, repack, unpack } from "./helpers/release_artifact.mjs";
import { commit, git, isolated, journalOf } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
const payloadRoots = ["skills/", "bin/", "NORTH_STAR.md"];
const documentBytes = Buffer.from("# Dependencies\n\nPublic installation requirements.\n");
/** @param {Fixture} f @param {readonly string[]} files */
function packageFiles(f, files) {
  const path = join(f.checkoutDir, "package.json");
  writeFileSync(path, `${JSON.stringify({ ...readObject(path), files }, null, 2)}\n`);
}

test("preparation includes the exact public document when package metadata selects it", async (t) => {
  // Given
  const f = artifactFixture(t);
  packageFiles(f, [...payloadRoots, "DEPENDENCIES.md"]);
  writeFileSync(join(f.checkoutDir, "DEPENDENCIES.md"), documentBytes);
  commit(f);
  const tracked = git(f.checkoutDir, ["ls-files"]).split("\n").filter((path) => /^(skills\/|bin\/|NORTH_STAR\.md$|DEPENDENCIES\.md$|package\.json$|README\.md$|LICENSE$)/.test(path));
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  const root = unpack(f);
  const actual = readdirSync(root, { recursive: true, withFileTypes: true }).filter((entry) => entry.isFile()).map((entry) => relative(root, join(entry.parentPath, entry.name)));
  assert.deepEqual(actual.sort(), tracked.sort());
  assert.deepEqual(readFileSync(join(root, "DEPENDENCIES.md")), documentBytes);
  assert.deepEqual(readFileSync(join(f.checkoutDir, "DEPENDENCIES.md")), documentBytes);
  assert.equal(git(f.checkoutDir, ["diff", "--exit-code"]), "");
  assert.ok(journalOf(f).every((entry) => entry.allowed));
});

test("preparation omits a tracked public document when package metadata does not select it", async (t) => {
  // Given
  const f = artifactFixture(t);
  packageFiles(f, payloadRoots);
  writeFileSync(join(f.checkoutDir, "DEPENDENCIES.md"), documentBytes);
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(existsSync(join(unpack(f), "DEPENDENCIES.md")), false);
  assert.deepEqual(readFileSync(join(f.checkoutDir, "DEPENDENCIES.md")), documentBytes);
});

test("preparation rejects a selected public document missing from committed source", async (t) => {
  // Given
  const f = artifactFixture(t);
  packageFiles(f, [...payloadRoots, "DEPENDENCIES.md"]);
  const path = join(f.checkoutDir, "DEPENDENCIES.md");
  if (existsSync(path)) renameSync(path, join(f.root, "saved-dependencies.md"));
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
});

for (const mutation of ["changed", "missing"]) {
  test(`verification rejects a ${mutation} packaged public document even with a matching tarball digest`, async (t) => {
    // Given
    const f = artifactFixture(t);
    packageFiles(f, [...payloadRoots, "DEPENDENCIES.md"]);
    writeFileSync(join(f.checkoutDir, "DEPENDENCIES.md"), documentBytes);
    commit(f);
    const prepared = await prepare(f);
    assert.ok(prepared.ok, JSON.stringify(prepared));
    const path = join(f.workspace.stageDir, "DEPENDENCIES.md");
    if (mutation === "missing") renameSync(path, join(f.root, "removed-from-pack.md"));
    else writeFileSync(path, Buffer.alloc(documentBytes.length, 0x78));
    const rebound = await repack(f, prepared.value);
    // When
    const result = await isolated(f, () => verifyArtifact(rebound, f.workspace));
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.deepEqual(readFileSync(join(f.checkoutDir, "DEPENDENCIES.md")), documentBytes);
  });
}

for (const name of ["PRIVATE.md", ".private-notes", "DEPENDENCIES-private.md"]) {
  test(`preparation rejects arbitrary root ${name} even when metadata selects it`, async (t) => {
    // Given
    const f = artifactFixture(t);
    packageFiles(f, [...payloadRoots, name]);
    writeFileSync(join(f.checkoutDir, name), "private bytes\n");
    commit(f);
    // When
    const result = await prepare(f);
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.equal(journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 1);
  });
}
