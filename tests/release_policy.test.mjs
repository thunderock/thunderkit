import { test } from "node:test";
import assert from "node:assert/strict";
import * as policy from "../tools/release/policy.mjs";
import { parseSemver } from "../tools/release/versions.mjs";
import { parseRequest } from "../tools/release/request.mjs";
import { releaseFacts, reservedFacts, publishedFacts, manualFacts, twoTagFacts, historicalFacts, managedBaseFacts } from "./helpers/release_facts.mjs";
import { bootstrapFacts, prereleaseFacts, maintenanceFacts, wrongSourceFacts } from "./helpers/release_facts.mjs";

test("policy re-exports the shared raw request decoder", () => {
  const expected = parseRequest; // Given
  const result = policy.parseRequest; // When
  assert.equal(result, expected); // Then
});

for (const [subject, body, before, after] of [
  ["feat: introduce option", "", "patch", "minor"], ["fix: correct option", "", "patch", "patch"],
  ["docs: describe option", "", "patch", "patch"], ["chore: maintain", "", "patch", "patch"],
  ["test: add case", "", "patch", "patch"], ["ci: adjust runner", "", "patch", "patch"],
  ["feat!: remove option", "", "minor", "major"], ["fix(api)!: remove option", "", "minor", "major"],
  ["chore: revise API", "details\n\nBREAKING CHANGE: new API", "minor", "major"],
  ["fix: revise API", "BREAKING-CHANGE: new API", "minor", "major"],
  ["fix: revise API", "BREAKING CHANGE:\nnew API", "minor", "major"],
  ["unclassified commit", "", "patch", "patch"],
]) {
  for (const [major, expected] of [[0, before], [1, after]]) {
    test(`bumpLevel handles ${subject} at major ${major}`, () => {
      const commits = [{ sha: "a".repeat(40), subject, body }]; // Given
      const result = policy.bumpLevel(commits, major); // When
      assert.equal(result, expected); // Then
    });
  }
}
test("bumpLevel keeps the strongest commit regardless of order", () => {
  const commits = ["feat: feature", "fix!: breaking", "docs: text"].map((subject) => ({ sha: "a".repeat(40), subject, body: "" })); // Given
  const result = policy.bumpLevel(commits, 1); // When
  assert.equal(result, "major"); // Then
});

for (const [base, level, expected] of [["0.1.1", "patch", "0.1.2"], ["0.1.1", "minor", "0.2.0"], ["1.2.3", "major", "2.0.0"], ["1.2.3", "minor", "1.3.0"], ["1.2.3", "patch", "1.2.4"]]) {
  test(`nextVersion applies ${level} to parsed ${base}`, () => {
    const parsed = parseSemver(base); // Given
    const result = policy.nextVersion(parsed, level); // When
    assert.deepEqual(result, { ok: true, value: expected }); // Then
  });
}
for (const [base, level] of [["9007199254740991.0.0", "major"], ["0.9007199254740991.0", "minor"], ["0.0.9007199254740991", "patch"]]) {
  test(`nextVersion rejects ${level} overflow`, () => {
    const parsed = parseSemver(base); // Given
    const result = policy.nextVersion(parsed, level); // When
    assert.equal(result.error?.code, "E_VERSION_OVERFLOW"); // Then
  });
}

for (const reverse of [false, true]) {
  test(`highestStable ignores prereleases and tag enumeration order (${reverse})`, () => {
    const { baseTag } = releaseFacts(); // Given
    const higher = { ...baseTag, name: "v0.10.0", version: "0.10.0" };
    const tags = [higher, { ...baseTag, name: "v9.0.0-beta", version: "9.0.0-beta" }, baseTag, { ...baseTag, name: "notes", version: "unknown" }];
    if (reverse) tags.reverse();
    const result = policy.highestStable(Object.freeze(tags)); // When
    assert.deepEqual(result, higher); // Then
    assert.ok(Object.isFrozen(result));
  });
}

for (const [version, inputTag, expectedTag, code] of [
  ["1.0.0", "", "latest"], ["3.7.4", "", "latest"], ["1.0.0-beta.1", "", "next"],
  ["1.0.0-beta.1", "latest", null, "E_INVALID_NPM_TAG"], ["0.0.9", "maintenance-0", "maintenance-0"],
  ["0.0.9", "", null, "E_INVALID_NPM_TAG"], ["0.0.9", "latest", null, "E_INVALID_NPM_TAG"],
  ["0.1.1-beta.1", "", null, "E_INVALID_NPM_TAG"], ["0.1.1-beta.1", "beta", "beta"],
]) {
  test(`selectCandidate honors exact manual ${version} with channel ${inputTag}`, () => {
    const { request, git } = manualFacts(version, inputTag); // Given
    const result = policy.selectCandidate(request, git); // When
    if (code) assert.equal(result.error?.code, code); // Then
    else assert.deepEqual([result.value.kind, result.value.candidate.version, result.value.candidate.npmTag, result.value.candidate.bump, result.value.candidate.commitCount], ["new", version, expectedTag, null, 0]);
  });
}

for (const reverse of [false, true]) {
  test(`selectCandidate ignores unrelated malformed prerelease reservations (${reverse})`, () => {
    const { request, git, baseTag, candidate } = releaseFacts(); // Given
    git.tags.push({ ...baseTag, sha: request.sourceSha, name: "v1.0.0-beta", version: "1.0.0-beta", annotation: "thunderkit.release broken" });
    git.tags.push({ ...baseTag, sha: request.sourceSha, name: "v2.0.0-alpha", version: "2.0.0-alpha" });
    if (reverse) git.tags.reverse();
    const result = policy.selectCandidate(request, git); // When
    assert.deepEqual(result, { ok: true, value: { kind: "new", candidate } }); // Then
    assert.ok(Object.isFrozen(result.value.candidate.origin));
  });
  for (const [runId, version] of [["9007199254740993", "0.1.2"], ["999", "0.2.0"]]) {
    test(`selectCandidate deterministically resumes ${version} for run ${runId} (${reverse})`, () => {
      const { request, git } = twoTagFacts(); // Given
      if (reverse) git.tags.reverse();
      const result = policy.selectCandidate({ ...request, runId }, git); // When
      assert.deepEqual([result.value.kind, result.value.reservation.release.version], ["resume", version]); // Then
    });
  }
  test(`selectCandidate selects the exact manual tag among unrelated tags (${reverse})`, () => {
    const { request, git, tag, release } = manualFacts("1.0.0-beta.1", "beta"); // Given
    const other = twoTagFacts();
    git.tags.push(tag, ...other.git.tags.slice(1));
    git.base = other.second;
    git.baseRelation = "equal";
    if (reverse) git.tags.reverse();
    const result = policy.selectCandidate({ ...request, inputNpmTag: "" }, git); // When
    assert.deepEqual([result.value.kind, result.value.reservation.release], ["resume", release]); // Then
  });
}

for (const [name, factory, change, code] of [
  ["wrong HEAD", releaseFacts, (f) => { f.git.headSha = f.baseTag.sha; }, "E_UNTRUSTED_CONTEXT"],
  ["wrong same-run source", wrongSourceFacts, () => {}, "E_VERSION_TAKEN"],
  ["manual taken elsewhere", wrongSourceFacts, (f) => { Object.assign(f.request, { event: "workflow_dispatch", inputVersion: "0.1.2" }); }, "E_VERSION_TAKEN"],
  ["unsafe resume channel", reservedFacts, (f) => { Object.assign(f.request, { event: "workflow_dispatch", inputVersion: "0.1.2", inputNpmTag: "x" }); }, "E_INVALID_NPM_TAG"],
  ["outside master", reservedFacts, (f) => { f.git.sourceOnMaster = false; }, "E_STALE_SOURCE"],
  ["divergent base", releaseFacts, (f) => { f.git.baseRelation = "diverged"; }, "E_TAG_NOT_ANCESTOR"],
  ["descendant base", releaseFacts, (f) => { f.git.baseRelation = "descendant"; }, "E_TAG_NOT_ANCESTOR"],
  ["incorrect base", releaseFacts, (f) => { f.git.base = null; }, "E_GIT"],
  ["prerelease bootstrap", releaseFacts, (f) => { f.git.tags = []; f.git.base = null; f.git.baseRelation = "none"; f.git.sourcePackage.version = "0.1.1-beta"; }, "E_INVALID_VERSION"],
  ["duplicate run", twoTagFacts, (f) => { const r = JSON.parse(f.second.annotation); r.release.origin.runId = f.request.runId; f.second.annotation = JSON.stringify(r); }, "E_AMBIGUOUS_RESUME"],
  ["wrong original mode", reservedFacts, (f) => { f.reservation.release.origin.mode = "manual"; f.reservation.release.bump = null; f.reservation.release.commitCount = 0; f.tag.annotation = JSON.stringify(f.reservation); }, "E_VERSION_TAKEN"],
  ["invalid input on resume", reservedFacts, (f) => { f.request.inputVersion = " v0.1.2"; }, "E_INVALID_VERSION"],
  ["channel without version on resume", reservedFacts, (f) => { f.request.inputNpmTag = "beta"; }, "E_NPM_TAG_WITHOUT_VERSION"],
  ["channel change", reservedFacts, (f) => { Object.assign(f.request, { event: "workflow_dispatch", inputVersion: "0.1.2", inputNpmTag: "beta" }); }, "E_RESUME_CHANNEL"],
  ["unowned manual target", reservedFacts, (f) => { f.tag.annotation = ""; Object.assign(f.request, { event: "workflow_dispatch", inputVersion: "0.1.2" }); }, "E_VERSION_TAKEN"],
  ["stale manual", releaseFacts, (f) => { f.git.masterSha = "e".repeat(40); Object.assign(f.request, { event: "workflow_dispatch", inputVersion: "1.0.0" }); }, "E_STALE_SOURCE"],
  ["non-string tag name", releaseFacts, (f) => { f.git.tags.push({ ...f.baseTag, name: 12 }); }, "E_GIT"],
  ["non-string tag version", releaseFacts, (f) => { f.git.tags.push({ ...f.baseTag, name: "notes", version: 12 }); }, "E_GIT"],
  ["sparse commit range", releaseFacts, (f) => { f.git.commits = Array(1); }, "E_GIT"],
]) {
  test(`selectCandidate rejects ${name}`, () => {
    const facts = factory(); // Given
    change(facts);
    const result = policy.selectCandidate(facts.request, facts.git); // When
    assert.equal(result.error?.code, code); // Then
  });
}
for (const [name, change, reason] of [
  ["empty range", (f) => { f.git.commits = []; }, "no_commits"],
  ["stale source", (f) => { f.git.masterSha = "e".repeat(40); }, "stale_source"],
  ["legacy tagged HEAD", (f) => { f.baseTag.sha = f.request.sourceSha; f.git.baseRelation = "equal"; }, "no_commits"],
]) {
  test(`selectCandidate skips ${name}`, () => {
    const facts = releaseFacts(); // Given
    change(facts);
    const result = policy.selectCandidate(facts.request, facts.git); // When
    assert.deepEqual(result, { ok: true, value: { kind: "skip", reason } }); // Then
  });
}
test("selectCandidate bootstraps from the source package rather than incrementing it", () => {
  const { request, git } = releaseFacts(); // Given
  Object.assign(git, { tags: [], base: null, baseRelation: "none" });
  const result = policy.selectCandidate(request, git); // When
  assert.deepEqual([result.value.candidate.version, result.value.candidate.bump, result.value.candidate.commitCount], ["0.1.1", null, 0]); // Then
});

for (const factory of [reservedFacts, publishedFacts, historicalFacts]) {
  test(`selectCandidate pins the original version when retrying ${factory.name}`, () => {
    const { request, git } = factory(); // Given
    const result = policy.selectCandidate(request, git); // When
    assert.equal(result.value.reservation.release.version, "0.1.2"); // Then
  });
}

for (const [name, factory, change, expected] of [
  ["fresh", releaseFacts, () => {}, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
  ["reserved", reservedFacts, () => {}, { action: "publish", reason: "ready", steps: ["npm", "github"], channel: "pending" }],
  ["published", publishedFacts, () => {}, { action: "publish", reason: "ready", steps: ["github"], channel: "current" }],
  ["completed", publishedFacts, (f) => { f.target.github = f.github; }, { action: "skip", reason: "already_released", steps: [], channel: "current" }],
  ["historical", historicalFacts, () => {}, { action: "publish", reason: "ready", steps: ["github"], channel: "superseded" }],
  ["historical complete", historicalFacts, (f) => { f.target.github = f.github; }, { action: "skip", reason: "already_released", steps: [], channel: "superseded" }],
  ["stale", releaseFacts, (f) => { f.git.masterSha = "e".repeat(40); }, { action: "skip", reason: "stale_source", steps: [], channel: null }],
  ["managed base", managedBaseFacts, () => {}, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
  ["bootstrap", bootstrapFacts, () => {}, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
  ["prerelease-only registry", bootstrapFacts, (f) => { Object.assign(f.registry, { exists: true, versions: { "1.0.0-beta.1": { ...f.npm, version: "1.0.0-beta.1" } } }); }, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
  ["completed prerelease", prereleaseFacts, () => {}, { action: "skip", reason: "already_released", steps: [], channel: "current" }],
  ["explicit backward alias", () => manualFacts("0.0.9", "maintenance-0"), (f) => { f.registry.distTags["maintenance-0"] = "0.1.1"; }, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
  ["manual original base", () => manualFacts("0.0.9", "maintenance-0"), (f) => { const higher = { ...f.baseTag, name: "v2.0.0", version: "2.0.0" }; f.git.tags.push(higher); f.git.base = higher; }, { action: "publish", reason: "ready", steps: ["tag", "npm", "github"], channel: "pending" }],
]) {
  test(`reconcile returns only the permitted steps when ${name}`, () => {
    const facts = factory(); // Given
    change(facts);
    const before = JSON.stringify(facts);
    const result = policy.reconcile(facts.prepared, facts.live); // When
    assert.deepEqual(result, { ok: true, value: expected }); // Then
    assert.ok(Object.isFrozen(result.value.steps));
    assert.equal(JSON.stringify(facts), before);
  });
}

for (const [name, factory, change, code] of [
  ["unowned npm", releaseFacts, (f) => { f.target.npm = f.npm; f.registry.versions["0.1.2"] = f.npm; }, "E_VERSION_TAKEN"],
  ["foreign bytes", publishedFacts, (f) => { f.npm.integrity = "sha512-" + "B".repeat(85) + "A=="; }, "E_REGISTRY_INTEGRITY"],
  ["missing integrity", publishedFacts, (f) => { f.npm.integrity = null; }, "E_REGISTRY_INTEGRITY"],
  ["ambiguous integrity", publishedFacts, (f) => { f.npm.integrity += " " + f.npm.integrity; }, "E_REGISTRY_INTEGRITY"],
  ["moved tag", reservedFacts, (f) => { f.tag.sha = "e".repeat(40); f.git.baseRelation = "diverged"; }, "E_VERSION_TAKEN"],
  ["legacy tag", reservedFacts, (f) => { f.tag.annotation = "historical"; }, "E_VERSION_TAKEN"],
  ["changed bytes", reservedFacts, (f) => { const r = JSON.parse(f.tag.annotation); r.release.tarball.size = 999; f.tag.annotation = JSON.stringify(r); }, "E_ARTIFACT"],
  ["changed origin", reservedFacts, (f) => { const r = JSON.parse(f.tag.annotation); r.release.origin.runId = "77"; f.tag.annotation = JSON.stringify(r); }, "E_VERSION_TAKEN"],
  ["orphan GH", releaseFacts, (f) => { f.target.github = f.github; }, "E_GH_CONFLICT"],
  ["npm and GH without reservation", releaseFacts, (f) => {
    f.target.npm = f.npm;
    f.registry.versions["0.1.2"] = f.npm;
    f.target.github = f.github;
  }, "E_GH_CONFLICT"],
  ["GH before npm", reservedFacts, (f) => { f.target.github = f.github; }, "E_GH_CONFLICT"],
  ["wrong GH tag", publishedFacts, (f) => { f.target.github = { ...f.github, tagName: "v8.0.0" }; }, "E_GH_CONFLICT"],
  ["draft GH", publishedFacts, (f) => { f.target.github = { ...f.github, draft: true }; }, "E_GH_CONFLICT"],
  ["wrong GH prerelease", publishedFacts, (f) => { f.target.github = { ...f.github, prerelease: true }; }, "E_GH_CONFLICT"],
  ["missing latest", publishedFacts, (f) => { delete f.registry.distTags.latest; }, "E_CHANNEL_DRIFT"],
  ["older latest", publishedFacts, (f) => { f.registry.distTags.latest = "0.1.1"; }, "E_CHANNEL_DRIFT"],
  ["dangling latest", releaseFacts, (f) => { f.registry.distTags.latest = "9.0.0"; }, "E_CHANNEL_STATE"],
  ["noncanonical latest", releaseFacts, (f) => { f.registry.distTags.latest = "v0.1.1"; }, "E_CHANNEL_STATE"],
  ["missing stable latest", releaseFacts, (f) => { delete f.registry.distTags.latest; }, "E_CHANNEL_STATE"],
  ["latest ahead of Git", releaseFacts, (f) => { f.registry.versions["9.0.0"] = { ...f.npm, version: "9.0.0" }; f.registry.distTags.latest = "9.0.0"; }, "E_STALE_TARGET"],
  ["obsolete missing npm", historicalFacts, (f) => { f.target.npm = null; delete f.registry.versions["0.1.2"]; }, "E_STALE_TARGET"],
  ["base missing npm", managedBaseFacts, (f) => { f.live.baseTarget.npm = null; }, "E_BASE_INCOMPLETE"],
  ["base missing GH", managedBaseFacts, (f) => { f.live.baseTarget.github = null; }, "E_BASE_INCOMPLETE"],
  ["base wrong identity", managedBaseFacts, (f) => { f.live.baseTarget.npm.integrity = null; }, "E_BASE_INCOMPLETE"],
  ["base missing lookup", managedBaseFacts, (f) => { f.live.baseTarget = null; }, "E_BASE_INCOMPLETE"],
  ["wrong lookup", releaseFacts, (f) => { f.target.version = "0.1.1"; }, "E_RECORD"],
  ["wrong tag lookup", releaseFacts, (f) => { f.target.tag = "v0.1.1"; }, "E_RECORD"],
  ["occupied bootstrap", bootstrapFacts, (f) => { f.registry.exists = true; f.registry.versions["0.1.1"] = f.npm; f.registry.distTags.latest = "0.1.1"; f.target.npm = f.npm; }, "E_NO_BASE"],
  ["prerelease latest", releaseFacts, (f) => { f.registry.versions["2.0.0-beta"] = { ...f.npm, version: "2.0.0-beta" }; f.registry.distTags.latest = "2.0.0-beta"; }, "E_CHANNEL_STATE"],
  ["foreign registry metadata", releaseFacts, (f) => { f.registry.versions["0.1.1"].name = "foreign"; }, "E_REGISTRY"],
  ["registry version binding", releaseFacts, (f) => { f.registry.versions["0.1.1"].version = "0.1.0"; }, "E_REGISTRY"],
  ["registry absence contradiction", releaseFacts, (f) => { f.registry.exists = false; }, "E_REGISTRY"],
  ["registry shape", releaseFacts, (f) => { f.registry.distTags = null; }, "E_REGISTRY"],
  ["registry unknown key", releaseFacts, (f) => { f.registry.extra = true; }, "E_REGISTRY"],
  ["registry array map", bootstrapFacts, (f) => { f.registry.versions = []; }, "E_REGISTRY"],
  ["registry null entry", releaseFacts, (f) => { f.registry.versions["0.1.1"] = null; }, "E_REGISTRY"],
  ["registry lookup mismatch", releaseFacts, (f) => { f.registry.versions["0.1.2"] = f.npm; }, "E_REGISTRY"],
  ["missing alias", maintenanceFacts, (f) => { delete f.registry.distTags["maintenance-0"]; }, "E_CHANNEL_DRIFT"],
  ["invalid alias", maintenanceFacts, (f) => { f.registry.distTags["maintenance-0"] = "x"; }, "E_CHANNEL_DRIFT"],
  ["dangling alias", maintenanceFacts, (f) => { f.registry.distTags["maintenance-0"] = "9.0.0"; }, "E_CHANNEL_DRIFT"],
  ["displaced higher alias", maintenanceFacts, (f) => { f.registry.distTags["maintenance-0"] = "0.1.1"; }, "E_CHANNEL_DRIFT"],
  ["base channel drift", managedBaseFacts, (f) => { f.registry.versions["0.1.0"] = { ...f.npm, version: "0.1.0" }; f.registry.distTags.latest = "0.1.0"; }, "E_BASE_INCOMPLETE"],
  ["base lookup mismatch", managedBaseFacts, (f) => { f.live.baseTarget.tag = "v0.1.0"; }, "E_BASE_INCOMPLETE"],
  ["base snapshot disagreement", managedBaseFacts, (f) => { f.registry.versions["0.1.1"] = { ...f.npm, version: "0.1.1", integrity: null }; }, "E_BASE_INCOMPLETE"],
  ["missing original base", reservedFacts, (f) => { f.git.tags = [f.tag]; }, "E_STALE_PLAN"],
  ["changed reserved base binding", reservedFacts, (f) => {
    f.release.base.sourceSha = "e".repeat(40);
    f.tag.annotation = JSON.stringify(f.reservation);
  }, "E_STALE_PLAN"],
  ["foreign fresh origin", releaseFacts, (f) => { f.release.origin.runId = "77"; }, "E_STALE_PLAN"],
  ["changed live base", releaseFacts, (f) => { const higher = { ...f.baseTag, name: "v2.0.0", version: "2.0.0" }; f.git.tags.push(higher); f.git.base = higher; }, "E_STALE_PLAN"],
  ["stale prepared manual", () => manualFacts("1.0.0", "latest"), (f) => { f.git.masterSha = "e".repeat(40); }, "E_STALE_SOURCE"],
  ["invalid completed prerelease reservation", prereleaseFacts, (f) => { const r = JSON.parse(f.tag.annotation); r.release.npmTag = "latest"; f.tag.annotation = JSON.stringify(r); }, "E_RECORD"],
  ["untrusted completed context", publishedFacts, (f) => { f.target.github = f.github; f.request.ref = "refs/heads/topic"; }, "E_UNTRUSTED_CONTEXT"],
  ["reserved source outside master", publishedFacts, (f) => { f.git.sourceOnMaster = false; }, "E_STALE_SOURCE"],
]) {
  test(`reconcile fails closed for ${name}`, () => {
    const facts = factory(); // Given
    change(facts);
    const result = policy.reconcile(facts.prepared, facts.live); // When
    assert.equal(result.error?.code, code); // Then
  });
}
