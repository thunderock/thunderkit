// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, readdirSync, readFileSync, readlinkSync, symlinkSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { verifyArtifact } from "../tools/release/artifact.mjs";
import { planRelease } from "../tools/release/plan.mjs";
import { createSystemTransport } from "../tools/release/io.mjs";
import { artifactFixture, prepare } from "./helpers/release_artifact.mjs";
import { createFixture, destroyFixture, isolated, journalOf, systemDrivers, tag } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
/** @typedef {import("../tools/release/artifact.mjs").Workspace} Workspace */
/** Include files, links and empty directories so partial writes cannot hide behind unchanged tracked files. @param {string} root */
function snapshot(root) {
  const directories = [root];
  /** @type {string[]} */ const entries = [];
  for (const directory of directories) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      const content = entry.isSymbolicLink() ? readlinkSync(path) : entry.isDirectory() ? "directory" : createHash("sha256").update(readFileSync(path)).digest("hex");
      entries.push(`${relative(root, path)}:${content}`);
      if (entry.isDirectory()) directories.push(path);
    }
  }
  return createHash("sha256").update(JSON.stringify(entries.sort())).digest("hex");
}

/** @type {ReadonlyArray<readonly [string, (f:Fixture)=>Workspace]>} */
const overlaps = [
  ["a ..stage child of checkout", (f) => ({ ...f.workspace, stageDir: join(f.checkoutDir, "..stage") })],
  ["a ..bundle child of checkout", (f) => ({ ...f.workspace, bundleDir: join(f.checkoutDir, "..bundle") })],
  ["a stage below a symlink to checkout", (f) => {
    const alias = join(f.root, "checkout-alias");
    symlinkSync(f.checkoutDir, alias, "dir");
    return { ...f.workspace, stageDir: join(alias, "missing-parent", "stage") };
  }],
  ["a bundle below a symlink to checkout", (f) => {
    const alias = join(f.root, "checkout-alias");
    symlinkSync(f.checkoutDir, alias, "dir");
    return { ...f.workspace, bundleDir: join(alias, "missing-parent", "bundle") };
  }],
  ["bundle nested in stage through a parent alias", (f) => {
    const alias = join(f.root, "outputs-alias");
    symlinkSync(f.root, alias, "dir");
    return { ...f.workspace, bundleDir: join(alias, "stage", "bundle") };
  }],
  ["stage and bundle naming the same directory through aliases", (f) => {
    const alias = join(f.root, "outputs-alias");
    symlinkSync(f.root, alias, "dir");
    return { ...f.workspace, bundleDir: join(alias, "stage") };
  }],
  ["checkout itself reached through an alias", (f) => {
    const alias = join(f.root, "checkout-alias");
    symlinkSync(f.checkoutDir, alias, "dir");
    return { ...f.workspace, checkoutDir: alias, stageDir: join(f.checkoutDir, "missing-parent", "stage") };
  }],
  ["a parent traversal after a symlink into checkout", (f) => {
    const anchor = join(f.checkoutDir, "anchor");
    mkdirSync(anchor);
    const alias = join(f.root, "anchor-alias");
    symlinkSync(anchor, alias, "dir");
    return { ...f.workspace, stageDir: `${alias}/../..stage` };
  }],
];

for (const [name, workspaceFor] of overlaps) {
  test(`preparation rejects ${name} before writing any workspace bytes`, async (t) => {
    // Given
    const f = artifactFixture(t);
    const workspace = workspaceFor(f);
    writeFileSync(join(f.checkoutDir, "untracked-user-file"), "preserve me\n");
    const checkout = snapshot(f.checkoutDir);
    const entireFixture = snapshot(f.root);
    // When
    const result = await prepare(f, workspace);
    // Then
    assert.equal(snapshot(f.checkoutDir), checkout, "checkout bytes changed");
    assert.equal(snapshot(f.root), entireFixture, "workspace bytes changed");
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.deepEqual(journalOf(f), []);
  });
}

test("preparation accepts distinct sibling outputs whose names start with two dots or the checkout name", async (t) => {
  // Given
  const f = artifactFixture(t);
  const workspace = { ...f.workspace, stageDir: join(f.root, "..stage"), bundleDir: join(f.root, "checkout-bundle") };
  const before = snapshot(f.checkoutDir);
  // When
  const result = await prepare(f, workspace);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(snapshot(f.checkoutDir), before);
});

test("preparation accepts an external symlink parent when the resolved outputs remain distinct siblings", async (t) => {
  // Given
  const f = artifactFixture(t);
  const outputs = join(f.root, "outputs");
  mkdirSync(outputs);
  const alias = join(f.root, "outputs-alias");
  symlinkSync(outputs, alias, "dir");
  const workspace = { ...f.workspace, stageDir: join(alias, "source"), bundleDir: join(outputs, "bundle") };
  const before = snapshot(f.checkoutDir);
  // When
  const result = await prepare(f, workspace);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(snapshot(f.checkoutDir), before);
});

test("verification rejects aliased stage containment before reading any artifact", async (t) => {
  // Given
  const f = artifactFixture(t);
  const prepared = await prepare(f);
  assert.ok(prepared.ok);
  const alias = join(f.root, "bundle-alias");
  symlinkSync(f.workspace.bundleDir, alias, "dir");
  const workspace = { ...f.workspace, stageDir: join(alias, "..stage") };
  const before = snapshot(f.root);
  const calls = journalOf(f).length;
  // When
  const result = await isolated(f, () => verifyArtifact(prepared.value, workspace));
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.equal(snapshot(f.root), before);
  assert.equal(journalOf(f).length, calls);
});

test("a no-commits plan rejects a nested bundle before touching the checkout", async (t) => {
  // Given
  const f = createFixture();
  t.after(() => destroyFixture(f));
  tag(f, "v0.1.1", null);
  const workspace = { ...f.workspace, bundleDir: join(f.checkoutDir, "..bundle") };
  const system = systemDrivers();
  const transport = createSystemTransport(f.checkoutDir, system.drivers);
  const before = snapshot(f.checkoutDir);
  // When
  const result = await isolated(f, () => planRelease(f.request, workspace, transport));
  // Then
  assert.equal(snapshot(f.checkoutDir), before);
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.deepEqual(system.calls, []);
});
