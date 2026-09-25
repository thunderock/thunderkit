// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { verifyArtifact } from "../tools/release/artifact.mjs";
import { planRelease } from "../tools/release/plan.mjs";
import { publishRelease } from "../tools/release/publish.mjs";
import { artifactFixture, prepare, readObject, repack, unpack } from "./helpers/release_artifact.mjs";
import { Remote } from "./helpers/release_remote.mjs";
import { bundleOf, commit, createFixture, destroyFixture, git, isolated, journalOf, saveEvidence } from "./helpers/release_workspace.mjs";

const rootAllowlist = `# A package.json files list bypasses root exclusions; keep the allowlist here.
/*
!/skills/
!/bin/
!/NORTH_STAR.md
!/DEPENDENCIES.md
.github/
.thunderkit/
.omo/
.omo-tmp/
.omc/
.omh/
site/
tests/
Makefile
CHANGELOG.md
.gitignore
.npmignore
**/__pycache__/
**/*.pyc
**/.thunderkit/
**/.omo/
**/.omo-tmp/
**/.omh/
**/.omc/
**/node_modules/
**/.ruff_cache/
**/.pytest_cache/
**/.npm/
**/*.tgz
`;
const documentBytes = Buffer.from("# Dependencies\n\nPublic installation requirements.\n");

/** @param {import("node:test").TestContext} t */
function allowlistFixture(t) {
  const f = artifactFixture(t);
  const path = join(f.checkoutDir, "package.json");
  const pkg = readObject(path);
  delete pkg.files;
  writeFileSync(path, `${JSON.stringify(pkg, null, 2)}\n`);
  writeFileSync(join(f.checkoutDir, ".npmignore"), rootAllowlist);
  writeFileSync(join(f.checkoutDir, "DEPENDENCIES.md"), documentBytes);
  commit(f);
  return f;
}

test("fixture copying preserves an ignore-only policy from already stamped source", (t) => {
  // Given
  const source = allowlistFixture(t);
  const path = join(source.checkoutDir, "package.json");
  const pkg = { ...readObject(path), version: "7.6.5" };
  writeFileSync(path, JSON.stringify(pkg));
  // When
  const copied = createFixture(source.checkoutDir);
  t.after(() => destroyFixture(copied));
  // Then
  assert.deepEqual(readObject(join(copied.checkoutDir, "package.json")), { ...pkg, version: "0.1.1" });
  assert.equal(git(copied.checkoutDir, ["show", "HEAD:.npmignore"]), rootAllowlist.trimEnd());
  assert.deepEqual(readFileSync(join(copied.checkoutDir, "DEPENDENCIES.md")), documentBytes);
  assert.deepEqual(readObject(path), pkg);
});

test("preparation verifies the public guide selected only by the root allowlist", async (t) => {
  // Given
  const f = allowlistFixture(t);
  const expected = git(f.checkoutDir, ["ls-files"]).split("\n").filter((path) => /^(skills\/|bin\/|NORTH_STAR\.md$|DEPENDENCIES\.md$|package\.json$|README\.md$|LICENSE$)/.test(path));
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  const root = unpack(f);
  const actual = readdirSync(root, { recursive: true, withFileTypes: true }).filter((entry) => entry.isFile()).map((entry) => relative(root, join(entry.parentPath, entry.name)));
  assert.deepEqual(actual.sort(), expected.sort());
  assert.deepEqual(readFileSync(join(root, "DEPENDENCIES.md")), documentBytes);
  assert.equal(Object.hasOwn(readObject(join(root, "package.json")), "files"), false);
  assert.equal(readObject(join(root, "package.json")).version, "0.1.2");
  assert.equal(git(f.checkoutDir, ["diff", "--exit-code"]), "");
  saveEvidence("ignore-artifact", { sourceSha: f.sha, tarball: result.value.release.tarball, members: actual });
});

test("preparation rejects an allowlisted guide missing from committed source", async (t) => {
  // Given
  const f = allowlistFixture(t);
  renameSync(join(f.checkoutDir, "DEPENDENCIES.md"), join(f.root, "saved-guide.md"));
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.equal(existsSync(join(unpack(f), "DEPENDENCIES.md")), false);
});

for (const mutation of /** @type {const} */ (["changed", "missing"])) {
  test(`verification rejects a ${mutation} allowlisted guide with a rebound digest`, async (t) => {
    // Given
    const f = allowlistFixture(t);
    const prepared = await prepare(f);
    assert.ok(prepared.ok, JSON.stringify(prepared));
    const path = join(f.workspace.stageDir, "DEPENDENCIES.md");
    switch (mutation) {
      case "changed": writeFileSync(path, Buffer.alloc(documentBytes.length, 0x78)); break;
      case "missing": renameSync(path, join(f.root, "saved-packed-guide.md")); break;
      default: assert.fail(/** @satisfies {never} */ (mutation));
    }
    const rebound = await repack(f, prepared.value);
    // When
    const result = await isolated(f, () => verifyArtifact(rebound, f.workspace));
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.deepEqual(readFileSync(join(f.checkoutDir, "DEPENDENCIES.md")), documentBytes);
  });
}

test("preparation omits a tracked guide not selected by the root allowlist", async (t) => {
  // Given
  const f = allowlistFixture(t);
  writeFileSync(join(f.checkoutDir, ".npmignore"), rootAllowlist.replace("!/DEPENDENCIES.md\n", ""));
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(existsSync(join(unpack(f), "DEPENDENCIES.md")), false);
});

test("verification rejects a guide selected only in the packed staging policy", async (t) => {
  // Given
  const f = allowlistFixture(t);
  writeFileSync(join(f.checkoutDir, ".npmignore"), rootAllowlist.replace("!/DEPENDENCIES.md\n", ""));
  commit(f);
  const prepared = await prepare(f);
  assert.ok(prepared.ok, JSON.stringify(prepared));
  writeFileSync(join(f.workspace.stageDir, ".npmignore"), rootAllowlist);
  const rebound = await repack(f, prepared.value);
  assert.deepEqual(readFileSync(join(unpack(f), "DEPENDENCIES.md")), documentBytes);
  // When
  const result = await isolated(f, () => verifyArtifact(rebound, f.workspace));
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
});

test("explicit files selection takes precedence over an allowlisted guide", async (t) => {
  // Given
  const f = allowlistFixture(t);
  const path = join(f.checkoutDir, "package.json");
  writeFileSync(path, JSON.stringify({ ...readObject(path), files: ["skills/", "bin/", "NORTH_STAR.md"] }));
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(existsSync(join(unpack(f), "DEPENDENCIES.md")), false);
});

test("preparation fails closed when a later rule excludes the selected guide", async (t) => {
  // Given
  const f = allowlistFixture(t);
  writeFileSync(join(f.checkoutDir, ".npmignore"), `${rootAllowlist}/DEPENDENCIES.md\n`);
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.equal(existsSync(join(unpack(f), "DEPENDENCIES.md")), false);
});

for (const name of ["PRIVATE.md", ".private-notes", "DEPENDENCIES-private.md"]) {
  test(`preparation rejects private root ${name} selected by the ignore policy`, async (t) => {
    // Given
    const f = allowlistFixture(t);
    writeFileSync(join(f.checkoutDir, ".npmignore"), `${rootAllowlist}!/${name}\n`);
    writeFileSync(join(f.checkoutDir, name), "private bytes\n");
    commit(f);
    // When
    const result = await prepare(f);
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.equal(readFileSync(join(unpack(f), name), "utf8"), "private bytes\n");
  });
}

test("root exclusions preserve tracked cache bytes without packaging them", async (t) => {
  // Given
  const f = allowlistFixture(t);
  const files = ["skills/tk-ask/scripts/__pycache__/fixture.pyc", "skills/tk-ask/scripts/fixture.pyc", "skills/tk-ask/.omo/state", "skills/tk-ask/.pytest_cache/state", "skills/tk-ask/.ruff_cache/state", "skills/tk-ask/node_modules/fixture/index.js"];
  for (const file of files) {
    mkdirSync(dirname(join(f.checkoutDir, file)), { recursive: true });
    writeFileSync(join(f.checkoutDir, file), "retained cache\n");
  }
  commit(f);
  // When
  const result = await prepare(f);
  // Then
  assert.ok(result.ok, JSON.stringify(result));
  const root = unpack(f);
  for (const file of files) {
    assert.equal(existsSync(join(root, file)), false);
    assert.equal(readFileSync(join(f.checkoutDir, file), "utf8"), "retained cache\n");
  }
});

test("publication sends only prepared allowlist bytes after staging changes", async (t) => {
  // Given
  const f = allowlistFixture(t);
  const remote = new Remote(f.sha, "0.1.1");
  const planned = await isolated(f, () => planRelease(f.request, f.workspace, remote));
  assert.ok(planned.ok && planned.value.action === "publish", JSON.stringify(planned));
  const gated = readFileSync(join(f.workspace.bundleDir, "package.tgz"));
  writeFileSync(join(f.workspace.stageDir, "DEPENDENCIES.md"), "not prepared bytes\n");
  // When
  const result = await isolated(f, () => publishRelease(f.request, bundleOf(f), remote));
  // Then
  assert.deepEqual(result, { ok: true, value: { status: "completed", performedSteps: ["tag", "npm", "github"] } });
  assert.deepEqual(remote.packages["0.1.2"], gated);
  assert.equal(remote.registry.versions["0.1.2"]?.integrity, planned.value.release.tarball.integrity);
  assert.equal(journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 1);
  saveEvidence("ignore-publication", { tarball: planned.value.release.tarball, remote: remote.snapshot(), journal: journalOf(f) });
});
