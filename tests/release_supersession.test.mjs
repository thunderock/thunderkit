// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { reconcile } from "../tools/release/policy.mjs";
import { planRelease } from "../tools/release/plan.mjs";
import { publishRelease } from "../tools/release/publish.mjs";
import { managedBaseFacts, historicalFacts } from "./helpers/release_facts.mjs";
import { Remote } from "./helpers/release_remote.mjs";
import { createFixture, destroyFixture, commit, git, isolated, bundleOf } from "./helpers/release_workspace.mjs";

/** @type {import("./helpers/release_workspace.mjs").Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));

/** A valid older reservation, absent from both publication services. */
function unpublishedBase() {
  const facts = managedBaseFacts();
  assert.ok(facts.live.baseTarget !== null);
  facts.live.baseTarget.npm = null;
  facts.live.baseTarget.github = null;
  delete facts.registry.versions["0.1.1"];
  facts.registry.versions["0.1.0"] = { name: "thunderkit", version: "0.1.0", integrity: null };
  facts.registry.distTags.latest = "0.1.0";
  return facts;
}

test("a newer automatic release supersedes a wholly unpublished ancestor without changing its reservation", () => {
  const facts = unpublishedBase();
  const before = JSON.stringify(facts);
  const result = reconcile(facts.prepared, facts.live);
  assert.deepEqual(result, { ok: true, value: { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" } });
  assert.equal(JSON.stringify(facts), before);
});

/** @type {ReadonlyArray<[string, (f:ReturnType<typeof unpublishedBase>)=>void, string]>} */
const conflicts = [
  ["npm already published but GitHub missing", (f) => {
    const npm = { name: "thunderkit", version: "0.1.1", integrity: JSON.parse(f.baseTag.annotation).release.tarball.integrity };
    f.registry.versions["0.1.1"] = npm;
    f.registry.distTags.latest = "0.1.1";
    if (f.live.baseTarget !== null) f.live.baseTarget.npm = npm;
  }, "E_BASE_INCOMPLETE"],
  ["GitHub present without npm", (f) => {
    if (f.live.baseTarget !== null) f.live.baseTarget.github = { tagName: "v0.1.1", draft: false, prerelease: false };
  }, "E_BASE_INCOMPLETE"],
  ["missing base lookup", (f) => { f.live.baseTarget = null; }, "E_BASE_INCOMPLETE"],
  ["registry and base lookup disagree", (f) => {
    f.registry.versions["0.1.1"] = { name: "thunderkit", version: "0.1.1", integrity: "sha512-foreign" };
  }, "E_BASE_INCOMPLETE"],
  ["base tag identity differs", (f) => {
    if (f.live.baseTarget !== null) f.live.baseTarget.gitTag = { ...f.baseTag, objectSha: "d".repeat(40) };
  }, "E_BASE_INCOMPLETE"],
  ["base release lookup names a different tag", (f) => {
    if (f.live.baseTarget !== null) f.live.baseTarget.tag = "v0.0.1";
  }, "E_BASE_INCOMPLETE"],
  ["malformed reservation", (f) => { f.baseTag.annotation = "thunderkit.release invalid"; }, "E_RECORD"],
  ["diverged source", (f) => { f.git.baseRelation = "diverged"; }, "E_TAG_NOT_ANCESTOR"],
  ["latest advanced above target", (f) => {
    f.registry.versions["9.0.0"] = { name: "thunderkit", version: "9.0.0", integrity: null };
    f.registry.distTags.latest = "9.0.0";
  }, "E_STALE_TARGET"],
];
for (const [name, change, code] of conflicts) {
  test(`automatic supersession refuses ${name}`, () => {
    const facts = unpublishedBase();
    change(facts);
    const result = reconcile(facts.prepared, facts.live);
    assert.equal(result.ok ? null : result.error.code, code);
  });
}

test("a late retry cannot publish a missing older version after its successor exists", () => {
  const facts = historicalFacts();
  facts.target.npm = null;
  delete facts.registry.versions["0.1.2"];
  const result = reconcile(facts.prepared, facts.live);
  assert.equal(result.ok ? null : result.error.code, "E_STALE_TARGET");
});

/** Real local artifacts with a modeled tag-only npm failure followed by a new source. */
async function strandedRelease() {
  const f = createFixture();
  fixtures.push(f);
  const originalSha = f.sha;
  const oldRequest = { ...f.request };
  const oldWorkspace = { ...f.workspace };
  const remote = new Remote(f.sha, "0.1.1");
  const first = await isolated(f, () => planRelease(f.request, f.workspace, remote));
  assert.ok(first.ok && first.value.action === "publish" && first.value.release !== null);
  remote.fault = { step: "npm", phase: "before" };
  const failed = await isolated(f, () => publishRelease(f.request, bundleOf(f), remote));
  assert.equal(failed.ok ? null : failed.error.code, "E_REGISTRY");
  assert.deepEqual(remote.accepted, ["tag"]);
  const reservation = JSON.stringify(remote.git.tags[0]);
  commit(f, "fix: repair publication transport");
  f.request.runId = "9007199254740995";
  f.raw.GITHUB_RUN_ID = f.request.runId;
  remote.git.headSha = f.sha;
  remote.git.masterSha = f.sha;
  remote.refreshBase();
  remote.git.commits = [{ sha: f.sha, subject: "fix: repair publication transport", body: "" }];
  f.workspace = { ...f.workspace, stageDir: `${f.workspace.stageDir}-new`, bundleDir: `${f.workspace.bundleDir}-new` };
  const next = await isolated(f, () => planRelease(f.request, f.workspace, remote));
  assert.ok(next.ok && next.value.action === "publish" && next.value.release !== null, JSON.stringify(next));
  assert.equal(next.value.release.version, "0.1.2");
  assert.equal(next.value.release.base?.version, "0.1.1");
  remote.calls.length = 0;
  remote.accepted.length = 0;
  return { f, remote, reservation, originalSha, oldRequest, oldWorkspace, prepared: next.value };
}

test("new master source completes the next release after tag-only failure and keeps the old tag immutable", async () => {
  const { f, remote, reservation, originalSha, oldRequest, oldWorkspace, prepared } = await strandedRelease();
  const result = await isolated(f, () => publishRelease(f.request, bundleOf(f), remote));
  assert.deepEqual(result, { ok: true, value: { status: "completed", performedSteps: ["tag", "npm", "github"] } });
  assert.equal(JSON.stringify(remote.git.tags[0]), reservation);
  assert.equal(remote.registry.versions["0.1.1"], undefined);
  assert.equal(remote.registry.versions["0.1.2"]?.integrity, prepared.release?.tarball.integrity);
  assert.equal(remote.registry.distTags.latest, "0.1.2");
  assert.deepEqual(remote.releases["v0.1.2"], { tagName: "v0.1.2", draft: false, prerelease: false });
  assert.deepEqual(await isolated(f, () => publishRelease(f.request, bundleOf(f), remote)), { ok: true, value: { status: "already_released", performedSteps: [] } });
  git(f.checkoutDir, ["checkout", "--quiet", originalSha]);
  remote.git.headSha = originalSha;
  remote.git.baseRelation = "descendant";
  remote.calls.length = 0;
  const retry = await isolated(f, () => publishRelease(oldRequest, bundleOf({ ...f, workspace: oldWorkspace }), remote));
  assert.equal(retry.ok ? null : retry.error.code, "E_STALE_TARGET");
  assert.deepEqual(remote.calls.filter((call) => call.startsWith("write:")), []);
});

test("a base published between gate and publisher blocks supersession before any new remote write", async () => {
  const { f, remote } = await strandedRelease();
  const base = remote.git.base ?? assert.fail("Expected reserved base");
  const reservation = JSON.parse(base.annotation);
  remote.registry = { exists: true, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", integrity: reservation.release.tarball.integrity } }, distTags: { latest: "0.1.1" } };
  const result = await isolated(f, () => publishRelease(f.request, bundleOf(f), remote));
  assert.equal(result.ok ? null : result.error.code, "E_BASE_INCOMPLETE");
  assert.deepEqual(remote.calls.filter((call) => call.startsWith("write:")), []);
});
