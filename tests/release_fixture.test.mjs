// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { planRelease } from "../tools/release/plan.mjs";
import { createSystemTransport } from "../tools/release/io.mjs";
import { readObject } from "./helpers/release_artifact.mjs";
import { child, commit, createFixture, destroyFixture, git, isolated, journalOf, manual, programs, systemDrivers } from "./helpers/release_workspace.mjs";

/** @type {import("./helpers/release_workspace.mjs").Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));
/** @param {string} [sourceDir] */
function fixture(sourceDir) {
  const f = createFixture(sourceDir);
  fixtures.push(f);
  return f;
}
/** @param {import("./helpers/release_workspace.mjs").Fixture} f */
function plan(f) {
  manual(f, "0.1.2", "latest");
  return isolated(f, () => planRelease(f.request, f.workspace, createSystemTransport(f.checkoutDir, systemDrivers().drivers)));
}

test("fixture copying omits disposable caches while retaining other source files", () => {
  // Given
  const source = fixture();
  const caches = ["__pycache__/fixture.pyc", ".pytest_cache/state", ".mypy_cache/state", ".ruff_cache/state", "fixture.pyc", "fixture.pyo"];
  const retained = [".env", "cache-guide.md", "cache_impl.py"];
  const scripts = join(source.checkoutDir, "skills/tk-ask/scripts");
  for (const name of [...caches, ...retained]) {
    mkdirSync(dirname(join(scripts, name)), { recursive: true });
    writeFileSync(join(scripts, name), `fixture:${name}\n`);
  }
  const before = [...caches, ...retained].map((name) => readFileSync(join(scripts, name)));
  // When
  const copied = fixture(source.checkoutDir);
  // Then
  const files = git(copied.checkoutDir, ["ls-files"]).split("\n");
  assert.deepEqual(files.filter((name) => /(?:__pycache__|\.(?:pytest|mypy|ruff)_cache|\.py[co]$)/.test(name)), []);
  for (const name of retained) assert.deepEqual(readFileSync(join(copied.checkoutDir, "skills/tk-ask/scripts", name)), readFileSync(join(scripts, name)));
  assert.deepEqual([...caches, ...retained].map((name) => readFileSync(join(scripts, name))), before);
});

test("fixture version is 0.1.1 when the source package has already been stamped", () => {
  // Given
  const source = fixture();
  const manifest = join(source.checkoutDir, "package.json");
  const stamped = { ...JSON.parse(readFileSync(manifest, "utf8")), version: "7.6.5" };
  const bytes = Buffer.from(`${JSON.stringify(stamped, null, 2)}\n`);
  writeFileSync(manifest, bytes);
  // When
  const copied = fixture(source.checkoutDir);
  // Then
  assert.deepEqual(JSON.parse(readFileSync(join(copied.checkoutDir, "package.json"), "utf8")), { ...stamped, version: "0.1.1" });
  assert.deepEqual(readFileSync(manifest), bytes);
});

test("fixture copying preserves the public dependency document when present in source", () => {
  // Given
  const source = fixture();
  const document = Buffer.from("public dependency requirements\n");
  writeFileSync(join(source.checkoutDir, "DEPENDENCIES.md"), document);
  // When
  const copied = fixture(source.checkoutDir);
  // Then
  assert.deepEqual(readFileSync(join(copied.checkoutDir, "DEPENDENCIES.md")), document);
  assert.deepEqual(readFileSync(join(source.checkoutDir, "DEPENDENCIES.md")), document);
});

test("planning packs successfully when fixture source contains newly compiled Python caches", async () => {
  // Given
  const source = fixture();
  const scripts = join(source.checkoutDir, "skills/tk-ask/scripts");
  const compiled = child(programs.python3, ["-m", "py_compile", join(scripts, "tk-resolve.py")], source.checkoutDir);
  assert.equal(compiled.status, 0, compiled.stderr);
  const cache = join(scripts, "__pycache__");
  const entries = readdirSync(cache);
  assert.ok(entries.length > 0);
  const before = entries.map((name) => readFileSync(join(cache, name)));
  const f = fixture(source.checkoutDir);
  // When
  const result = await plan(f);
  // Then
  assert.ok(result.ok && result.value.release !== null, JSON.stringify(result));
  assert.equal(result.value.release.version, "0.1.2");
  assert.deepEqual(entries.map((name) => readFileSync(join(cache, name))), before);
  assert.ok(journalOf(f).every((entry) => entry.allowed));
});

for (const file of ["skills/tk-ask/scripts/__pycache__/packaged.pyc", "skills/tk-ask/scripts/packaged.pyc"]) {
  test(`production rejects forbidden bytes when ${file} is deliberately tracked`, async () => {
    // Given
    const f = fixture();
    const manifest = join(f.checkoutDir, "package.json");
    writeFileSync(manifest, JSON.stringify({ ...readObject(manifest), files: ["skills/", "bin/", "NORTH_STAR.md"] }));
    mkdirSync(dirname(join(f.checkoutDir, file)), { recursive: true });
    writeFileSync(join(f.checkoutDir, file), "forbidden package bytes");
    commit(f);
    // When
    const result = await plan(f);
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
    assert.equal(journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 1);
    assert.equal(readFileSync(join(f.checkoutDir, file), "utf8"), "forbidden package bytes");
  });
}
