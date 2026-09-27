// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Remote } from "./helpers/release_remote.mjs";
import { releaseFacts, manualFacts } from "./helpers/release_facts.mjs";

/** @typedef {import("../tools/release/record.mjs").Prepared} Prepared */
const root = mkdtempSync(join(tmpdir(), "release-remote-"));
after(() => rmSync(root, { recursive: true, force: true }));
const sourceSha = "a".repeat(40);

/** The fake never reads the record's digest; the tarball name is fixed, so each case uses its own directory. @param {string} content */
function tarballDirectory(content) {
  const directory = mkdtempSync(join(root, "bundle-"));
  writeFileSync(join(directory, "package.tgz"), content);
  return directory;
}
/** @returns {Prepared} */
function preparedFixture() { return releaseFacts().prepared; }

test("publishTarball stores the digest of the bytes it receives, not the record's claimed integrity", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const prepared = preparedFixture();
  await remote.pushTag(prepared);
  const directory = tarballDirectory("real bytes");
  const result = await remote.publishTarball(prepared, directory);
  assert.deepEqual(result, { ok: true, value: null });
  assert.equal(remote.registry.versions["0.1.2"]?.integrity, `sha512-${createHash("sha512").update("real bytes").digest("base64")}`);
  assert.notEqual(remote.registry.versions["0.1.2"]?.integrity, prepared.release.tarball.integrity);
  assert.equal(remote.registry.distTags.latest, "0.1.2");
  assert.equal(remote.registry.exists, true);
});

test("published versions are immutable and a second publish leaves stored bytes untouched", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const prepared = preparedFixture();
  await remote.publishTarball(prepared, tarballDirectory("first"));
  const again = await remote.publishTarball(prepared, tarballDirectory("second"));
  assert.equal(again.ok ? null : again.error.code, "E_REGISTRY");
  assert.equal(remote.packages["0.1.2"]?.toString(), "first");
  assert.deepEqual(remote.accepted, ["npm"]);
});

test("pushTag accepts an identical retained reservation but rejects a changed one, and never moves a tag", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const prepared = preparedFixture();
  assert.deepEqual(await remote.pushTag(prepared), { ok: true, value: null });
  assert.deepEqual(await remote.pushTag(prepared), { ok: true, value: null });
  assert.deepEqual(remote.accepted, ["tag"]);
  const changed = { ...prepared, release: { ...prepared.release, npmTag: "next" } };
  const conflict = await remote.pushTag(changed);
  assert.equal(conflict.ok ? null : conflict.error.code, "E_GIT");
  assert.equal(remote.git.tags.length, 1);
  assert.equal(remote.git.tags[0]?.annotation.includes("\"npmTag\":\"latest\""), true);
  assert.equal(remote.git.base?.name, "v0.1.2");
  assert.equal(remote.git.baseRelation, "equal");
});

test("createRelease verifies the tag exists, refuses duplicates and records the latest flag", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const prepared = preparedFixture();
  const untagged = await remote.createRelease(prepared, true);
  assert.equal(untagged.ok ? null : untagged.error.code, "E_GH");
  await remote.pushTag(prepared);
  assert.deepEqual(await remote.createRelease(prepared, false), { ok: true, value: null });
  const duplicate = await remote.createRelease(prepared, true);
  assert.equal(duplicate.ok ? null : duplicate.error.code, "E_GH");
  assert.deepEqual(remote.latestFlags, [false]);
  assert.deepEqual(remote.releases["v0.1.2"], { tagName: "v0.1.2", draft: false, prerelease: false });
  const prerelease = manualFacts("1.0.0-beta.1", "next").prepared;
  await remote.pushTag(prerelease);
  await remote.createRelease(prerelease, false);
  assert.equal(remote.releases["v1.0.0-beta.1"]?.prerelease, true);
  assert.equal(remote.git.base?.name, "v0.1.2", "prerelease tags never become the stable base");
});

test("a fault before acceptance leaves state unchanged while a fault after acceptance mutates state and still reports failure", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const prepared = preparedFixture();
  remote.fault = { step: "tag", phase: "before" };
  const before = await remote.pushTag(prepared);
  assert.equal(before.ok ? null : before.error.code, "E_GIT");
  assert.deepEqual([remote.git.tags, remote.accepted, remote.fault], [[], [], null]);
  remote.fault = { step: "tag", phase: "after" };
  const after = await remote.pushTag(prepared);
  assert.equal(after.ok ? null : after.error.code, "E_GIT");
  assert.deepEqual(remote.accepted, ["tag"]);
  assert.equal(remote.git.tags.length, 1);
  assert.equal(remote.fault, null, "faults fire once so the explicit rerun observes real state");
  assert.deepEqual(remote.calls, ["write:tag", "write:tag"]);
});

test("reads are journaled, can be failed on demand and reject a request bound to another source", async () => {
  const remote = new Remote(sourceSha, "0.1.1");
  const { request } = releaseFacts();
  const git = await remote.readGit(request);
  assert.ok(git.ok && git.value.headSha === sourceSha);
  git.value.tags.push({ name: "v9.9.9", version: "9.9.9", sha: sourceSha, objectSha: sourceSha, annotation: "" });
  assert.equal(remote.git.tags.length, 0, "readers receive copies, not the live model");
  const foreign = await remote.readGit({ ...request, sourceSha: "b".repeat(40) });
  assert.equal(foreign.ok ? null : foreign.error.code, "E_UNTRUSTED_CONTEXT");
  assert.deepEqual(await remote.readRelease("v0.1.2"), { ok: true, value: null });
  remote.readError = "E_REGISTRY";
  const failed = await remote.readRegistry();
  assert.equal(failed.ok ? null : failed.error.code, "E_REGISTRY");
  assert.deepEqual(remote.calls, ["read:git", "read:git", "read:github:v0.1.2", "read:registry"]);
});
