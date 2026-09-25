// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, cpSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { prepareArtifact, verifyArtifact, checkToolchain, hashTarball } from "../tools/release/artifact.mjs";
import { readObject } from "./helpers/release_artifact.mjs";
import { child, createFixture, destroyFixture, git, isolated, journalOf, manual, moveSource, programs, saveEvidence, commit } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
/** @typedef {import("../tools/release/policy.mjs").Selection} Selection */
/** @type {Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));
function fixture() { const created = createFixture(); fixtures.push(created); return created; }
/** @param {Fixture} f @param {string} version @param {string} [channel] @returns {Selection} */
function manualSelection(f, version, channel) {
  manual(f, version, channel ?? (version.includes("-") ? "next" : "latest"));
  return { kind: "new", candidate: { version, tag: `v${version}`, npmTag: f.request.inputNpmTag, origin: { mode: "manual", runId: f.request.runId }, base: null, bump: null, commitCount: 0 } };
}
/** @param {Fixture} f @param {string} version @param {string} [suffix] */
function prepare(f, version, suffix = "") {
  const workspace = suffix === "" ? f.workspace : { ...f.workspace, stageDir: `${f.workspace.stageDir}${suffix}`, bundleDir: `${f.workspace.bundleDir}${suffix}` };
  return isolated(f, () => prepareArtifact(f.request, manualSelection(f, version), workspace));
}
/** @param {string} tarball */
function memberNames(tarball) {
  const result = child(programs.python3, ["-c", "import tarfile, sys, json; print(json.dumps(sorted(m.name for m in tarfile.open(sys.argv[1]))))", tarball], process.cwd());
  assert.equal(result.status, 0, result.stderr);
  return /** @type {string[]} */ (JSON.parse(result.stdout));
}
/** @param {string} path */
const sha512 = (path) => `sha512-${createHash("sha512").update(readFileSync(path)).digest("base64")}`;

test("preparing a manual candidate packs exactly the tracked payload at the stamped version without touching the checkout", async () => {
  const f = fixture();
  const result = await prepare(f, "0.1.2");
  assert.ok(result.ok, JSON.stringify(result));
  const { release } = result.value;
  const tarball = join(f.workspace.bundleDir, "package.tgz");
  assert.deepEqual(readdirSync(f.workspace.bundleDir), ["package.tgz"]);
  assert.equal(release.tarball.integrity, sha512(tarball));
  assert.equal(release.tarball.size, readFileSync(tarball).length);
  assert.deepEqual(release.toolchain, { nodeMajor: 24, npm: "11.19.1", pythonMinor: "3.12" });
  const selected = readObject(join(f.checkoutDir, "package.json")).files;
  const tracked = git(f.checkoutDir, ["ls-files"]).split("\n").filter((path) => /^(skills\/|bin\/|NORTH_STAR\.md$|package\.json$|README\.md$|LICENSE$)/.test(path)
    || (path === "DEPENDENCIES.md" && Array.isArray(selected) && selected.includes(path)));
  assert.deepEqual(memberNames(tarball), tracked.map((path) => `package/${path}`).sort());
  assert.equal(JSON.parse(readFileSync(join(f.workspace.stageDir, "package.json"), "utf8")).version, "0.1.2");
  assert.equal(JSON.parse(readFileSync(join(f.checkoutDir, "package.json"), "utf8")).version, "0.1.1");
  assert.equal(git(f.checkoutDir, ["rev-parse", "HEAD"]), f.sha);
  assert.equal(git(f.checkoutDir, ["status", "--porcelain"]), "");
  assert.ok(journalOf(f).every((entry) => entry.allowed), "only allowlisted offline commands ran");
  saveEvidence("artifact-prepared", { integrity: release.tarball.integrity, size: release.tarball.size, members: memberNames(tarball) });
});

test("a second clean preparation reproduces identical bytes and a different version changes them", async () => {
  const f = fixture();
  const first = await prepare(f, "0.1.2");
  const second = await prepare(f, "0.1.2", "-again");
  const third = await prepare(f, "0.1.3", "-other");
  assert.ok(first.ok && second.ok && third.ok);
  assert.equal(first.value.release.tarball.integrity, second.value.release.tarball.integrity);
  assert.ok(readFileSync(join(f.workspace.bundleDir, "package.tgz")).equals(readFileSync(join(`${f.workspace.bundleDir}-again`, "package.tgz"))));
  assert.notEqual(first.value.release.tarball.integrity, third.value.release.tarball.integrity);
  saveEvidence("artifact-reproducibility", { first: first.value.release.tarball, second: second.value.release.tarball, third: third.value.release.tarball });
});

test("uncommitted working-copy edits never reach the tarball", async () => {
  const f = fixture();
  const clean = await prepare(f, "0.1.2");
  writeFileSync(join(f.checkoutDir, "NORTH_STAR.md"), "dirty\n");
  const dirty = await prepare(f, "0.1.2", "-dirty");
  assert.ok(clean.ok && dirty.ok);
  assert.equal(dirty.value.release.tarball.integrity, clean.value.release.tarball.integrity);
});

test("verifyArtifact accepts the prepared record and rejects mutated, truncated or misdescribed bytes", async () => {
  const f = fixture();
  const prepared = await prepare(f, "0.1.2");
  assert.ok(prepared.ok);
  const tarball = join(f.workspace.bundleDir, "package.tgz");
  const original = readFileSync(tarball);
  const verified = await isolated(f, () => verifyArtifact(prepared.value, f.workspace));
  assert.ok(verified.ok && verified.value.integrity === prepared.value.release.tarball.integrity);
  assert.equal(verified.value.files.length, memberNames(tarball).length);
  const mismatched = { ...prepared.value, release: { ...prepared.value.release, version: "0.1.3", tag: "v0.1.3" } };
  assert.equal((await isolated(f, () => verifyArtifact({ ...mismatched, request: { ...f.request, inputVersion: "0.1.3" } }, f.workspace))).ok, false);
  writeFileSync(tarball, Buffer.concat([original.subarray(0, 200), Buffer.from([(original[200] ?? 0) ^ 0xff]), original.subarray(201)]));
  const mutated = await isolated(f, () => verifyArtifact(prepared.value, f.workspace));
  assert.equal(mutated.ok ? null : mutated.error.code, "E_ARTIFACT");
  writeFileSync(tarball, original.subarray(0, 100));
  const truncated = await isolated(f, () => verifyArtifact(prepared.value, f.workspace));
  assert.equal(truncated.ok ? null : truncated.error.code, "E_ARTIFACT");
});

test("verifyArtifact fails when the checkout no longer sits at the recorded source", async () => {
  const f = fixture();
  const prepared = await prepare(f, "0.1.2");
  assert.ok(prepared.ok);
  commit(f, "chore: unrelated");
  const result = await isolated(f, () => verifyArtifact(prepared.value, f.workspace));
  assert.equal(result.ok ? null : result.error.code, "E_GIT");
});

test("a request bound to a commit other than HEAD cannot prepare", async () => {
  const f = fixture();
  const head = f.sha;
  commit(f, "chore: later");
  moveSource(f, head);
  const result = await prepare(f, "0.1.2");
  assert.equal(result.ok ? null : result.error.code, "E_GIT");
});

test("skip selections, nested workspaces and occupied bundle directories are rejected before any tool runs", async () => {
  const f = fixture();
  const skipped = await isolated(f, () => prepareArtifact(f.request, { kind: "skip", reason: "no_commits" }, f.workspace));
  assert.equal(skipped.ok ? null : skipped.error.code, "E_RECORD");
  const nested = await isolated(f, () => prepareArtifact(f.request, manualSelection(f, "0.1.2"), { ...f.workspace, stageDir: join(f.checkoutDir, "stage") }));
  assert.equal(nested.ok ? null : nested.error.code, "E_ARTIFACT");
  mkdirSync(f.workspace.bundleDir);
  writeFileSync(join(f.workspace.bundleDir, "stale"), "");
  const occupied = await prepare(f, "0.1.2");
  assert.equal(occupied.ok ? null : occupied.error.code, "E_ARTIFACT");
  assert.equal(journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 0);
});

test("toolchain drift and pack failures are reported with their own codes", async () => {
  const f = fixture();
  const altered = join(f.root, "altered-bin");
  cpSync(f.bin, altered, { recursive: true });
  writeFileSync(join(altered, "npm"), `#!${programs.node}\nconst a = process.argv.slice(2); if (a[0] === "--version") { console.log("11.0.0"); process.exit(0); } process.exit(1);\n`, { mode: 0o755 });
  const previous = f.bin;
  f.bin = altered;
  assert.equal(checkToolchain(f.checkoutDir).ok, true);
  const drift = await prepare(f, "0.1.2");
  assert.equal(drift.ok ? null : drift.error.code, "E_TOOLCHAIN");
  writeFileSync(join(altered, "npm"), `#!${programs.node}\nconst a = process.argv.slice(2); if (a[0] === "--version") { console.log("11.19.1"); process.exit(0); } if (a[0] === "pack") { process.stderr.write("disk full\\n"); process.exit(3); }\nconst r = require("node:child_process").spawnSync(${JSON.stringify(programs.node)}, [${JSON.stringify(programs.npm)}, ...a], { stdio: "inherit" }); process.exit(r.status ?? 1);\n`, { mode: 0o755 });
  const pack = await prepare(f, "0.1.2", "-pack");
  assert.equal(pack.ok ? null : pack.error.code, "E_PACK");
  f.bin = previous;
});

test("canonical long versions are probed against real filename and ref limits before packing", async () => {
  const f = fixture();
  const packable = `1.0.0-${"a".repeat(229)}`;
  const tooLongForTarball = `1.0.0-${"b".repeat(239)}`;
  const tooLongForRef = `1.0.0-${"c".repeat(250)}`;
  assert.deepEqual([packable.length, tooLongForTarball.length, tooLongForRef.length], [235, 245, 256]);
  const packed = await prepare(f, packable);
  assert.ok(packed.ok, JSON.stringify(packed));
  assert.equal(packed.value.release.version, packable);
  const tarballFailure = await prepare(f, tooLongForTarball, "-tgz");
  assert.equal(tarballFailure.ok ? null : tarballFailure.error.code, "E_PACK");
  const refFailure = await prepare(f, tooLongForRef, "-ref");
  assert.equal(refFailure.ok ? null : refFailure.error.code, "E_GIT");
  const packs = journalOf(f).filter((entry) => entry.name === "npm" && entry.args[0] === "pack");
  assert.equal(packs.length, 1, "only the representable version reached npm pack");
  saveEvidence("artifact-long-versions", { packable: { length: packable.length, tarball: packed.value.release.tarball }, tooLongForTarball: { length: 245, code: "E_PACK" }, tooLongForRef: { length: 256, code: "E_GIT" } });
});

test("resume reuses the reservation only when the rebuilt bytes match its integrity", async () => {
  const f = fixture();
  const first = await prepare(f, "0.1.2");
  assert.ok(first.ok);
  const reservation = { schema: /** @type {const} */ ("thunderkit.release/v1"), repository: f.request.repository, sourceSha: f.request.sourceSha, release: first.value.release };
  const same = await isolated(f, () => prepareArtifact(f.request, { kind: "resume", reservation }, { ...f.workspace, stageDir: `${f.workspace.stageDir}-r`, bundleDir: `${f.workspace.bundleDir}-r` }));
  assert.ok(same.ok);
  assert.deepEqual(same.value.release, first.value.release);
  const foreign = { ...reservation, release: { ...first.value.release, tarball: { ...first.value.release.tarball, size: first.value.release.tarball.size + 1 } } };
  const differs = await isolated(f, () => prepareArtifact(f.request, { kind: "resume", reservation: foreign }, { ...f.workspace, stageDir: `${f.workspace.stageDir}-f`, bundleDir: `${f.workspace.bundleDir}-f` }));
  assert.equal(differs.ok ? null : differs.error.code, "E_ARTIFACT");
});

test("tracked credential noise and a broken packed CLI fail the payload gate", async () => {
  const f = fixture();
  writeFileSync(join(f.checkoutDir, "skills", "tk-ask", ".env"), "TOKEN=x\n");
  commit(f, "chore: add noise");
  const noise = await prepare(f, "0.1.2");
  assert.equal(noise.ok ? null : noise.error.code, "E_ARTIFACT");
  git(f.checkoutDir, ["rm", "--quiet", "skills/tk-ask/.env"]);
  const cli = join(f.checkoutDir, "bin", "thunderkit.js");
  writeFileSync(cli, readFileSync(cli, "utf8").replace('process.stdout.write(pkg.version + "\\n");', 'process.stdout.write("0.0.0\\n");'));
  chmodSync(cli, 0o755);
  commit(f, "fix: break version output");
  const broken = await prepare(f, "0.1.2", "-cli");
  assert.equal(broken.ok ? null : broken.error.code, "E_ARTIFACT");
});

test("redirecting publish settings and a missing tarball are rejected", async () => {
  const f = fixture();
  const manifest = JSON.parse(readFileSync(join(f.checkoutDir, "package.json"), "utf8"));
  writeFileSync(join(f.checkoutDir, "package.json"), `${JSON.stringify({ ...manifest, publishConfig: { registry: "https://example.invalid" } }, null, 2)}\n`);
  commit(f, "chore: redirect");
  const redirected = await prepare(f, "0.1.2");
  assert.equal(redirected.ok ? null : redirected.error.code, "E_ARTIFACT");
  assert.throws(() => hashTarball(join(f.root, "absent.tgz")));
});
