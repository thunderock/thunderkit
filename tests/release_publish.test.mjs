// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { publishRelease } from "../tools/release/publish.mjs";
import { planRelease } from "../tools/release/plan.mjs";
import { Remote } from "./helpers/release_remote.mjs";
import { bundleOf, child, createFixture, destroyFixture, isolated, journalOf, manual, saveEvidence } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
/** @typedef {import("../tools/release/policy.mjs").Step} Step */
/** @typedef {import("../tools/release/record.mjs").Prepared} Prepared */
const publishCli = fileURLToPath(new URL("../tools/release/publish.mjs", import.meta.url));
const steps = /** @type {readonly Step[]} */ (["tag", "npm", "github"]);
/** @type {Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));
const otherSha = "b".repeat(40);
/** @param {string} path */
const sha512 = (path) => `sha512-${createHash("sha512").update(readFileSync(path)).digest("base64")}`;

/** A fixture with a legacy stable base, one fix commit and a gated automatic 0.1.2 plan. @param {(f:Fixture, remote:Remote) => void} [arrange] */
async function gated(arrange) {
  const f = createFixture();
  fixtures.push(f);
  const remote = new Remote(f.sha, "0.1.1");
  remote.git.tags.push({ name: "v0.1.1", version: "0.1.1", sha: otherSha, objectSha: otherSha, annotation: "" });
  remote.registry = { exists: true, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", integrity: null } }, distTags: { latest: "0.1.1" } };
  remote.refreshBase();
  remote.git.commits = [{ sha: f.sha, subject: "fix: handle empty input", body: "" }];
  arrange?.(f, remote);
  const planned = await isolated(f, () => planRelease(f.request, f.workspace, remote));
  assert.ok(planned.ok && planned.value.action === "publish" && planned.value.release !== null, JSON.stringify(planned));
  const prepared = /** @type {Prepared} */ (planned.value);
  remote.calls.length = 0;
  return { f, remote, prepared, bundle: bundleOf(f), publish: () => isolated(f, () => publishRelease(f.request, bundleOf(f), remote)) };
}
/** @param {Remote} remote @param {Prepared} prepared @param {string|null} [integrity] */
function reserve(remote, prepared, integrity = prepared.release.tarball.integrity) {
  const annotation = JSON.stringify({ schema: "thunderkit.release/v1", repository: prepared.request.repository, sourceSha: prepared.request.sourceSha, release: prepared.release });
  remote.git.tags.push({ name: "v0.1.2", version: "0.1.2", sha: prepared.request.sourceSha, objectSha: "d".repeat(40), annotation });
  remote.refreshBase();
  remote.registry.versions["0.1.2"] = { name: "thunderkit", version: "0.1.2", integrity };
  remote.registry.distTags.latest = "0.1.2";
}
/** @param {Remote} remote */
const writes = (remote) => remote.calls.filter((call) => call.startsWith("write:"));

test("an empty remote receives tag, npm and GitHub once; the registry stores the exact gated bytes; a repeat run mutates nothing", async () => {
  const { f, remote, prepared, publish } = await gated();
  const first = await publish();
  assert.deepEqual(first, { ok: true, value: { status: "completed", performedSteps: ["tag", "npm", "github"] } });
  assert.deepEqual(remote.accepted, ["tag", "npm", "github"]);
  const stored = remote.registry.versions["0.1.2"];
  assert.equal(stored?.integrity, prepared.release.tarball.integrity);
  assert.equal(stored?.integrity, sha512(join(f.workspace.bundleDir, "package.tgz")));
  assert.equal(remote.packages["0.1.2"]?.length, prepared.release.tarball.size);
  assert.equal(remote.registry.distTags.latest, "0.1.2");
  assert.deepEqual(remote.releases["v0.1.2"], { tagName: "v0.1.2", draft: false, prerelease: false });
  assert.deepEqual(remote.latestFlags, [true]);
  assert.equal(remote.calls.indexOf("write:npm") > remote.calls.indexOf("write:tag") && remote.calls.indexOf("write:github") > remote.calls.indexOf("write:npm"), true);
  const before = remote.snapshot();
  const second = await publish();
  assert.deepEqual(second, { ok: true, value: { status: "already_released", performedSteps: [] } });
  assert.deepEqual(remote.accepted, before.accepted);
  assert.deepEqual(writes(remote).length, 3);
  const replanned = await isolated(f, () => planRelease(f.request, { ...f.workspace, stageDir: `${f.workspace.stageDir}-2`, bundleDir: `${f.workspace.bundleDir}-2` }, remote));
  assert.ok(replanned.ok && replanned.value.action === "skip" && replanned.value.reason === "already_released");
  assert.ok(journalOf(f).every((entry) => entry.allowed && entry.name !== "gh"));
  saveEvidence("publish-lifecycle", { prepared: prepared.release, remote: remote.snapshot(), tarballSha512: sha512(join(f.workspace.bundleDir, "package.tgz")) });
});

for (const step of steps) {
  for (const phase of /** @type {const} */ (["before", "after"])) {
    test(`a ${step} failure ${phase} acceptance stops the run, and the explicit rerun performs only the genuinely missing steps`, async () => {
      const { remote, publish } = await gated();
      remote.fault = { step, phase };
      const failed = await publish();
      assert.equal(failed.ok ? null : failed.error.code, { tag: "E_GIT", npm: "E_REGISTRY", github: "E_GH" }[step]);
      const earlier = steps.slice(0, steps.indexOf(step));
      assert.deepEqual(remote.accepted, phase === "after" ? [...earlier, step] : earlier);
      assert.deepEqual(writes(remote).at(-1), `write:${step}`);
      const remaining = steps.slice(steps.indexOf(step) + (phase === "after" ? 1 : 0));
      const retried = await publish();
      assert.deepEqual(retried, { ok: true, value: remaining.length === 0 ? { status: "already_released", performedSteps: [] } : { status: "completed", performedSteps: remaining } });
      assert.deepEqual(remote.accepted, ["tag", "npm", "github"]);
      assert.equal(remote.registry.versions["0.1.2"]?.integrity, sha512(join(fixtures.at(-1)?.workspace.bundleDir ?? "", "package.tgz")));
      saveEvidence(`publish-fault-${step}-${phase}`, remote.snapshot());
    });
  }
}

test("a tarball mutated after the tag reservation never reaches npm", async () => {
  const { f, remote, publish } = await gated();
  const tarball = join(f.workspace.bundleDir, "package.tgz");
  remote.onAccept = (step) => { if (step === "tag") writeFileSync(tarball, Buffer.concat([readFileSync(tarball), Buffer.from([0])])); };
  const result = await publish();
  assert.equal(result.ok ? null : result.error.code, "E_ARTIFACT");
  assert.deepEqual(remote.accepted, ["tag"]);
  assert.deepEqual(Object.keys(remote.registry.versions), ["0.1.1"]);
});

/** @type {ReadonlyArray<[string, (remote:Remote, prepared:Prepared) => void, string]>} */
const adversaries = [
  ["foreign npm bytes at the target version without a reservation", (remote) => { remote.registry.versions["0.1.2"] = { name: "thunderkit", version: "0.1.2", integrity: "sha512-foreign" }; }, "E_VERSION_TAKEN"],
  ["a reservation whose npm integrity differs", (remote, prepared) => reserve(remote, prepared, "sha512-foreign"), "E_REGISTRY_INTEGRITY"],
  ["a reservation whose npm entry lacks SHA-512 evidence", (remote, prepared) => reserve(remote, prepared, null), "E_REGISTRY_INTEGRITY"],
  ["the target tag moved to another commit", (remote, prepared) => { reserve(remote, prepared); const moved = remote.git.tags.find((entry) => entry.name === "v0.1.2") ?? assert.fail(); moved.sha = otherSha; remote.git.baseRelation = "ancestor"; }, "E_VERSION_TAKEN"],
  ["a reservation with changed fields", (remote, prepared) => { reserve(remote, prepared); const changed = remote.git.tags.find((entry) => entry.name === "v0.1.2") ?? assert.fail(); changed.annotation = changed.annotation.replace(prepared.release.origin.runId, "7"); }, "E_VERSION_TAKEN"],
  ["a GitHub release without tag or npm", (remote) => { remote.releases["v0.1.2"] = { tagName: "v0.1.2", draft: false, prerelease: false }; }, "E_GH_CONFLICT"],
  ["a published target whose latest pointer is missing", (remote, prepared) => { reserve(remote, prepared); delete remote.registry.distTags.latest; }, "E_CHANNEL_DRIFT"],
  ["a published target whose latest pointer stayed behind", (remote, prepared) => { reserve(remote, prepared); remote.registry.distTags.latest = "0.1.1"; }, "E_CHANNEL_DRIFT"],
  ["a prerelease registry latest", (remote) => { remote.registry.versions["0.2.0-rc.1"] = { name: "thunderkit", version: "0.2.0-rc.1", integrity: null }; remote.registry.distTags.latest = "0.2.0-rc.1"; }, "E_CHANNEL_STATE"],
  ["a registry latest that advanced past the gated candidate", (remote) => { remote.registry.versions["0.5.0"] = { name: "thunderkit", version: "0.5.0", integrity: null }; remote.registry.distTags.latest = "0.5.0"; }, "E_STALE_TARGET"],
  ["a newer stable base tagged after gating", (remote) => { remote.git.tags.push({ name: "v0.1.5", version: "0.1.5", sha: "c".repeat(40), objectSha: "c".repeat(40), annotation: "" }); remote.refreshBase(); }, "E_STALE_PLAN"],
  ["an old reservation whose npm is missing after a newer stable release", (remote, prepared) => { reserve(remote, prepared); delete remote.registry.versions["0.1.2"]; remote.registry.versions["0.2.0"] = { name: "thunderkit", version: "0.2.0", integrity: null }; remote.registry.distTags.latest = "0.2.0"; remote.git.tags.push({ name: "v0.2.0", version: "0.2.0", sha: "c".repeat(40), objectSha: "c".repeat(40), annotation: "" }); remote.refreshBase(); remote.git.baseRelation = "descendant"; }, "E_STALE_TARGET"],
];
for (const [name, arrange, code] of adversaries) {
  test(`${name} fails with ${code} and performs no mutation, channel repair or repack`, async () => {
    const { remote, prepared, publish } = await gated();
    arrange(remote, prepared);
    const result = await publish();
    assert.equal(result.ok ? null : result.error.code, code, JSON.stringify(result));
    assert.deepEqual(remote.accepted, []);
    assert.deepEqual(writes(remote), []);
    assert.equal(journalOf(fixtures.at(-1) ?? assert.fail()).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 1, "packed once at gate only");
  });
}

test("a matching historical package completes GitHub with latest=false and no npm write", async () => {
  const { remote, prepared, publish } = await gated();
  reserve(remote, prepared);
  remote.registry.versions["0.2.0"] = { name: "thunderkit", version: "0.2.0", integrity: null };
  remote.registry.distTags.latest = "0.2.0";
  remote.git.tags.push({ name: "v0.2.0", version: "0.2.0", sha: "c".repeat(40), objectSha: "c".repeat(40), annotation: "" });
  remote.refreshBase();
  remote.git.baseRelation = "descendant";
  assert.deepEqual(await publish(), { ok: true, value: { status: "completed", performedSteps: ["github"] } });
  assert.deepEqual(remote.latestFlags, [false]);
  assert.deepEqual(remote.accepted, ["github"]);
});

test("a source that stopped being master before the first write skips automatically and fails a manual plan", async () => {
  const auto = await gated();
  auto.remote.git.masterSha = "c".repeat(40);
  assert.deepEqual(await auto.publish(), { ok: true, value: { status: "stale_source", performedSteps: [] } });
  assert.deepEqual(auto.remote.accepted, []);
  const manualRun = await gated((f) => manual(f, "0.1.2", "latest"));
  manualRun.remote.git.masterSha = "c".repeat(40);
  const result = await manualRun.publish();
  assert.equal(result.ok ? null : result.error.code, "E_STALE_SOURCE");
  assert.deepEqual(manualRun.remote.accepted, []);
});

test("record hash, run identity and attempt ordering gate the bundle before any remote read", async () => {
  const { f, remote, bundle, publish } = await gated();
  const tampered = await isolated(f, () => publishRelease(f.request, { ...bundle, recordSha256: "0".repeat(64) }, remote));
  assert.equal(tampered.ok ? null : tampered.error.code, "E_RECORD");
  const swapped = await isolated(f, () => publishRelease({ ...f.request, runId: "42" }, bundle, remote));
  assert.equal(swapped.ok ? null : swapped.error.code, "E_RECORD");
  const future = await isolated(f, () => publishRelease({ ...f.request, attempt: "0" }, bundle, remote));
  assert.equal(future.ok, false);
  assert.deepEqual(remote.calls, []);
  const other = await gated((g) => manual(g, "0.1.3", "latest"));
  writeFileSync(join(f.workspace.bundleDir, "package.tgz"), readFileSync(join(other.f.workspace.bundleDir, "package.tgz")));
  const replaced = await publish();
  assert.equal(replaced.ok ? null : replaced.error.code, "E_ARTIFACT");
  assert.deepEqual(remote.calls, []);
  const later = await isolated(other.f, () => publishRelease({ ...other.f.request, attempt: "3" }, other.bundle, other.remote));
  assert.deepEqual(later, { ok: true, value: { status: "completed", performedSteps: ["tag", "npm", "github"] } });
});

test("the publisher CLI fails closed on bundle problems without any remote call", async () => {
  const { f, bundle } = await gated();
  const env = { ...f.raw, PATH: f.bin, RELEASE_RECORD_SHA256: bundle.recordSha256 };
  for (const [extra, code] of /** @type {ReadonlyArray<[Record<string,string>, string]>} */ ([[{ RELEASE_RECORD_SHA256: "f".repeat(64) }, "E_RECORD"], [{ RELEASE_RECORD_SHA256: "" }, "E_RECORD"], [{ GITHUB_REF: "refs/heads/dev" }, "E_UNTRUSTED_CONTEXT"]])) {
    const result = child(process.execPath, [publishCli, "--bundle", f.workspace.bundleDir], f.checkoutDir, { ...env, ...extra });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.equal(result.stderr, `${code}: release publication failed\n`);
  }
  const wrongFlag = child(process.execPath, [publishCli, "--workspace", f.workspace.bundleDir], f.checkoutDir, env);
  assert.equal(wrongFlag.stderr, "E_RECORD: release publication failed\n");
  assert.ok(journalOf(f).every((entry) => entry.allowed && entry.name !== "gh"));
});
