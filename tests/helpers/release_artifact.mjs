// @ts-check
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, renameSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { hashTarball, prepareArtifact } from "../../tools/release/artifact.mjs";
import { exec } from "../../tools/release/io.mjs";
import { child, createFixture, destroyFixture, isolated, manual, programs } from "./release_workspace.mjs";

/** @typedef {import("./release_workspace.mjs").Fixture} Fixture */
/** @typedef {import("../../tools/release/record.mjs").Prepared} Prepared */
const archiveTool = fileURLToPath(new URL("../../tools/release/archive.py", import.meta.url));

/** @param {import("node:test").TestContext} context */
export function artifactFixture(context) {
  const f = createFixture();
  context.after(() => destroyFixture(f));
  manual(f, "0.1.2", "latest");
  return f;
}

/** @param {Fixture} f @param {import("../../tools/release/artifact.mjs").Workspace} [workspace] */
export function prepare(f, workspace = f.workspace) {
  const candidate = { version: "0.1.2", tag: "v0.1.2", npmTag: "latest", origin: { mode: /** @type {const} */ ("manual"), runId: f.request.runId }, base: null, bump: null, commitCount: 0 };
  return isolated(f, () => prepareArtifact(f.request, { kind: "new", candidate }, workspace));
}

/** @param {string} path @returns {Record<string, unknown>} */
export function readObject(path) {
  /** @type {unknown} */ const value = JSON.parse(readFileSync(path, "utf8"));
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}

/** Inspect a real pack's extracted contents, without deriving the expected inventory from it. @param {Fixture} f */
export function unpack(f) {
  const destination = mkdtempSync(join(f.root, "inspection-"));
  const result = child(programs.python3, [archiveTool, join(f.workspace.bundleDir, "package.tgz"), destination], f.root);
  assert.equal(result.status, 0, result.stderr);
  return join(destination, "package");
}

/** Rebind the digest after a fixture mutation so verification must check content, not just the old hash. @param {Fixture} f @param {Prepared} prepared */
export function repack(f, prepared) {
  return isolated(f, async () => {
    const result = exec("npm", ["pack", "--offline", "--ignore-scripts", "--json", "--pack-destination", f.workspace.bundleDir], { cwd: f.workspace.stageDir });
    assert.equal(result.status, 0, result.stderr);
    const tarball = join(f.workspace.bundleDir, "package.tgz");
    renameSync(join(f.workspace.bundleDir, `thunderkit-${prepared.release.version}.tgz`), tarball);
    return { ...prepared, release: { ...prepared.release, tarball: { ...prepared.release.tarball, ...hashTarball(tarball) } } };
  });
}
