// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { planRelease, cliDirectory } from "../tools/release/plan.mjs";
import { createSystemTransport } from "../tools/release/io.mjs";
import { decodePlan, encodeReservation } from "../tools/release/record.mjs";
import { releaseFacts } from "./helpers/release_facts.mjs";
import { child, createFixture, destroyFixture, git, isolated, journalOf, manual, moveSource, commit, tag, systemDrivers, saveEvidence } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
const planCli = fileURLToPath(new URL("../tools/release/plan.mjs", import.meta.url));
/** @type {Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));
function fixture() { const created = createFixture(); fixtures.push(created); return created; }
/** @param {Fixture} f @param {ReturnType<typeof systemDrivers>} [system] */
function plan(f, system = systemDrivers()) {
  const transport = createSystemTransport(f.checkoutDir, system.drivers);
  return isolated(f, () => planRelease(f.request, f.workspace, transport)).then((result) => ({ result, calls: system.calls, hooks: system.hooks }));
}
/** @param {Readonly<Record<string, string>>} distTags @param {Readonly<Record<string, string|null>>} versions */
function packument(distTags, versions) {
  const entries = Object.entries(versions).map(([version, integrity]) => [version, { name: "thunderkit", version, dist: integrity === null ? {} : { integrity } }]);
  return async () => ({ status: 200, body: JSON.stringify({ name: "thunderkit", "dist-tags": distTags, versions: Object.fromEntries(entries) }) });
}
/** @param {Fixture} f */
function recordOf(f) {
  const bytes = readFileSync(join(f.workspace.bundleDir, "release-plan.json"));
  return { bytes, decoded: decodePlan(bytes.toString("utf8"), f.request), sha256: createHash("sha256").update(bytes).digest("hex") };
}
/** @param {ReturnType<typeof systemDrivers>["calls"]} calls */
const remoteReads = (calls) => calls.filter((call) => call.program === "https" || call.program === "gh").map((call) => `${call.program}:${call.argv.at(-1)}`);

test("an automatic push after a legacy stable tag prepares the patch release with facts read in the fixed order", async () => {
  const f = fixture();
  tag(f, "v0.1.1", null);
  commit(f, "fix: handle empty input");
  const system = systemDrivers();
  system.hooks.get = packument({ latest: "0.1.1" }, { "0.1.1": null });
  const { result, calls } = await plan(f, system);
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(result.value.action, "publish");
  assert.ok(result.value.release !== null);
  const { release } = result.value;
  assert.deepEqual([release.version, release.tag, release.npmTag, release.origin.mode, release.bump, release.commitCount], ["0.1.2", "v0.1.2", "latest", "auto", "patch", 1]);
  assert.deepEqual(release.base, { tag: "v0.1.1", version: "0.1.1", sourceSha: git(f.checkoutDir, ["rev-parse", "v0.1.1^{commit}"]) });
  const kinds = calls.map((call) => (call.program === "git" && call.argv[0] === "fetch" ? "fetch" : call.program === "https" ? "registry" : call.program === "gh" ? "gh" : null)).filter(Boolean);
  assert.deepEqual(kinds, ["fetch", "registry", "gh"]);
  assert.deepEqual(remoteReads(calls), ["https:https://registry.npmjs.org/thunderkit", "gh:repos/thunderock/thunderkit/releases/tags/v0.1.2"]);
  const { decoded, sha256 } = recordOf(f);
  assert.ok(decoded.ok && JSON.stringify(decoded.value) === JSON.stringify(result.value));
  assert.equal(readFileSync(join(f.workspace.bundleDir, "package.tgz")).length, release.tarball.size);
  assert.ok(journalOf(f).every((entry) => entry.allowed));
  saveEvidence("plan-auto-patch", { release, recordSha256: sha256 });
});

test("bootstrap uses the committed package version and an occupied registry entry is not bumped past", async () => {
  const f = fixture();
  const first = await plan(f);
  assert.ok(first.result.ok && first.result.value.release !== null);
  assert.deepEqual([first.result.value.release.version, first.result.value.release.base, first.result.value.release.bump], ["0.1.1", null, null]);
  const occupied = fixture();
  const system = systemDrivers();
  system.hooks.get = packument({ latest: "0.1.1" }, { "0.1.1": "sha512-foreign" });
  const { result } = await plan(occupied, system);
  assert.equal(result.ok ? null : result.error.code, "E_NO_BASE");
});

test("no commits since the stable tag at HEAD and a stale detached source skip without remote lookups or packing", async () => {
  const f = fixture();
  tag(f, "v0.1.1", null);
  const { result, calls } = await plan(f);
  assert.ok(result.ok && result.value.action === "skip" && result.value.reason === "no_commits" && result.value.release === null);
  assert.deepEqual(remoteReads(calls), []);
  assert.equal(existsSync(join(f.workspace.bundleDir, "package.tgz")), false);
  assert.ok(recordOf(f).decoded.ok);
  const stale = fixture();
  const older = stale.sha;
  commit(stale, "fix: newer master");
  git(stale.checkoutDir, ["checkout", "--quiet", older]);
  moveSource(stale, older);
  const detached = await plan(stale);
  assert.ok(detached.result.ok && detached.result.value.reason === "stale_source");
  assert.deepEqual(remoteReads(detached.calls), []);
  assert.equal(journalOf(stale).filter((entry) => entry.name === "npm").length, 0);
});

test("a manual exact version queries only its own tag even when HEAD carries another released tag", async () => {
  const f = fixture();
  tag(f, "v0.5.0", null);
  manual(f, "0.6.0", "");
  const system = systemDrivers();
  system.hooks.get = packument({ latest: "0.5.0" }, { "0.5.0": null });
  system.hooks.gh = (call) => (call.argv.at(-1)?.endsWith("v0.5.0") ? { status: 0, signal: null, stdout: `HTTP/2.0 200 OK\r\n\r\n${JSON.stringify({ tag_name: "v0.5.0", draft: false, prerelease: false })}`, stderr: "" }
    : { status: 1, signal: null, stdout: "HTTP/2.0 404 Not Found\r\n\r\n{}", stderr: "" });
  const { result, calls } = await plan(f, system);
  assert.ok(result.ok && result.value.action === "publish" && result.value.release?.version === "0.6.0" && result.value.release.npmTag === "latest");
  assert.deepEqual(remoteReads(calls).filter((read) => read.startsWith("gh")), ["gh:repos/thunderock/thunderkit/releases/tags/v0.6.0"]);
  const taken = fixture();
  const older = taken.sha;
  commit(taken, "fix: later");
  tag(taken, "v0.6.0", null, older);
  manual(taken, "0.6.0", "");
  const conflict = await plan(taken);
  assert.equal(conflict.result.ok ? null : conflict.result.error.code, "E_VERSION_TAKEN");
  assert.deepEqual(remoteReads(conflict.calls), []);
});

test("prerelease tags at HEAD do not suppress the automatic stable bump", async () => {
  const f = fixture();
  tag(f, "v0.1.1", null);
  commit(f, "feat: something new");
  tag(f, "v0.2.0-rc.1", null);
  tag(f, "v1.0.0-beta.2", null);
  const { result } = await plan(f);
  assert.ok(result.ok && result.value.release?.version === "0.1.2");
});

test("registry state vetoes candidates: latest ahead of Git, an unfinished managed base and a failed read", async () => {
  const ahead = fixture();
  tag(ahead, "v0.1.1", null);
  commit(ahead, "fix: patch");
  const aheadSystem = systemDrivers();
  aheadSystem.hooks.get = packument({ latest: "0.3.0" }, { "0.1.1": null, "0.3.0": null });
  const vetoed = await plan(ahead, aheadSystem);
  assert.equal(vetoed.result.ok ? null : vetoed.result.error.code, "E_STALE_TARGET");
  const managed = fixture();
  const { release, tag: reserved } = releaseFacts();
  const reservation = { schema: "thunderkit.release/v1", repository: "thunderock/thunderkit", sourceSha: managed.sha, release: { ...release, version: "0.1.1", tag: "v0.1.1", base: null, bump: null, commitCount: 0, origin: { mode: "auto", runId: "40" } } };
  tag(managed, "v0.1.1", JSON.stringify(reservation));
  commit(managed, "fix: after reserved base");
  const incomplete = await plan(managed);
  assert.equal(incomplete.result.ok ? null : incomplete.result.error.code, "E_BASE_INCOMPLETE");
  assert.deepEqual(remoteReads(incomplete.calls).filter((read) => read.startsWith("gh")).sort(), ["gh:repos/thunderock/thunderkit/releases/tags/v0.1.1", "gh:repos/thunderock/thunderkit/releases/tags/v0.1.2"]);
  assert.ok(reserved.name === "v0.1.2");
  const failing = fixture();
  tag(failing, "v0.1.1", null);
  commit(failing, "fix: patch");
  const failingSystem = systemDrivers();
  failingSystem.hooks.get = async () => ({ status: 500, body: "" });
  const unavailable = await plan(failing, failingSystem);
  assert.equal(unavailable.result.ok ? null : unavailable.result.error.code, "E_REGISTRY");
  assert.equal(journalOf(failing).filter((entry) => entry.name === "npm" && entry.args[0] === "pack").length, 0);
  assert.equal(existsSync(join(failing.workspace.bundleDir, "release-plan.json")), false);
});

test("a completed release at HEAD is recognized as already released through the concrete reader", async () => {
  const f = fixture();
  tag(f, "v0.1.1", null);
  commit(f, "fix: patch");
  const first = await plan(f);
  assert.ok(first.result.ok && first.result.value.action === "publish" && first.result.value.release !== null);
  const prepared = { ...first.result.value, action: /** @type {const} */ ("publish"), reason: /** @type {const} */ ("ready"), release: first.result.value.release };
  tag(f, "v0.1.2", encodeReservation(prepared));
  const system = systemDrivers();
  system.hooks.get = packument({ latest: "0.1.2" }, { "0.1.1": null, "0.1.2": prepared.release.tarball.integrity });
  system.hooks.gh = () => ({ status: 0, signal: null, stdout: `HTTP/2.0 200 OK\r\n\r\n${JSON.stringify({ tag_name: "v0.1.2", draft: false, prerelease: false })}`, stderr: "" });
  f.workspace = { ...f.workspace, stageDir: `${f.workspace.stageDir}-2`, bundleDir: `${f.workspace.bundleDir}-2` };
  const { result } = await plan(f, system);
  assert.ok(result.ok, JSON.stringify(result));
  assert.equal(result.value.action, "skip");
  assert.equal(result.value.reason, "already_released");
  assert.deepEqual(result.value.release, prepared.release);
  system.hooks.get = packument({ latest: "0.1.2" }, { "0.1.1": null, "0.1.2": "sha512-foreign" });
  f.workspace = { ...f.workspace, stageDir: `${f.workspace.stageDir}-3`, bundleDir: `${f.workspace.bundleDir}-3` };
  const foreign = await plan(f, system);
  assert.equal(foreign.result.ok ? null : foreign.result.error.code, "E_REGISTRY_INTEGRITY");
});

test("invalid or untrusted requests fail before any child process or lookup", async () => {
  const f = fixture();
  /** @type {ReadonlyArray<[Partial<typeof f.request>, string]>} */ const cases = [
    [{ inputVersion: "1.0.0" }, "E_UNTRUSTED_CONTEXT"], [{ repository: "someone/else" }, "E_UNTRUSTED_CONTEXT"], [{ ref: "refs/heads/dev" }, "E_UNTRUSTED_CONTEXT"],
    [{ event: "workflow_dispatch", inputVersion: "v1.0.0" }, "E_INVALID_VERSION"], [{ event: "workflow_dispatch", inputNpmTag: "next" }, "E_NPM_TAG_WITHOUT_VERSION"],
    [{ event: "workflow_dispatch", inputVersion: "1.0.0", inputNpmTag: "1.x" }, "E_INVALID_NPM_TAG"], [{ event: "workflow_dispatch", inputVersion: "1.0.0-rc.1", inputNpmTag: "latest" }, "E_INVALID_NPM_TAG"],
  ];
  for (const [patch, code] of cases) {
    const system = systemDrivers();
    const transport = createSystemTransport(f.checkoutDir, system.drivers);
    const result = await isolated(f, () => planRelease({ ...f.request, ...patch }, f.workspace, transport));
    assert.equal(result.ok ? null : result.error.code, code, JSON.stringify(patch));
    assert.equal(system.calls.length, 0);
  }
  assert.equal(journalOf(f).length, 0);
});

test("the planner CLI validates context, writes only action and record hash, and never emits success on failure", async () => {
  const f = fixture();
  tag(f, "v0.1.1", null);
  const output = join(f.root, "github-output");
  writeFileSync(output, "");
  const env = { ...f.raw, GITHUB_OUTPUT: output, PATH: f.bin, HOME: f.root, GH_TOKEN: "ghs_fixture" };
  const run = (/** @type {string[]} */ args, /** @type {Record<string,string>} */ extra = {}) => child(process.execPath, [planCli, ...args], f.checkoutDir, { ...env, ...extra });
  const skip = run(["--workspace", join(f.root, "cli-ws")]);
  assert.equal(skip.status, 0, skip.stderr);
  assert.equal(skip.stdout, "");
  const written = readFileSync(join(f.root, "cli-ws", "bundle", "release-plan.json"));
  assert.equal(readFileSync(output, "utf8"), `action=skip\nrecord_sha256=${createHash("sha256").update(written).digest("hex")}\n`);
  writeFileSync(output, "");
  for (const [args, extra, code] of /** @type {ReadonlyArray<[string[], Record<string,string>, string]>} */ ([
    [["--workspace", join(f.root, "cli-ws2")], { GITHUB_REPOSITORY: "someone/else" }, "E_UNTRUSTED_CONTEXT"],
    [["--workspace", join(f.root, "cli-ws3")], { GITHUB_EVENT_NAME: "workflow_dispatch", RELEASE_VERSION_INPUT: " 1.0.0" }, "E_INVALID_VERSION"],
    [["--bundle", join(f.root, "cli-ws4")], {}, "E_RECORD"], [["--workspace"], {}, "E_RECORD"], [["--workspace", join(f.root, "cli-ws5"), "extra"], {}, "E_RECORD"],
  ])) {
    const result = run(args, extra);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.equal(result.stderr, `${code}: release planning failed\n`);
  }
  assert.equal(readFileSync(output, "utf8"), "");
  assert.deepEqual(cliDirectory(["--workspace", "-x"], "--workspace").ok, false);
  assert.ok(journalOf(f).every((entry) => entry.allowed));
});
