// @ts-check
/** @template T @typedef {T extends readonly (infer V)[] ? Mutable<V>[] : T extends object ? {-readonly [K in keyof T]: Mutable<T[K]>} : T} Mutable */
/** @typedef {Mutable<import("../../tools/release/request.mjs").Request>} Request */
/** @typedef {Mutable<import("../../tools/release/record.mjs").Tag>} Tag */
/** @typedef {Mutable<import("../../tools/release/record.mjs").Candidate>} Candidate */
/** @typedef {Mutable<import("../../tools/release/record.mjs").Release>} Release */
/** @typedef {Mutable<import("../../tools/release/record.mjs").Prepared>} Prepared */
/** @typedef {Mutable<import("../../tools/release/record.mjs").Reservation>} Reservation */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").GitFacts>} GitFacts */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").Registry>} Registry */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").NpmEntry>} NpmEntry */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").Target>} Target */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").LiveFacts>} LiveFacts */
/** @typedef {{raw:Record<string,string|undefined>, request:Request, baseTag:Tag, candidate:Candidate, release:Release, prepared:Prepared, reservation:Reservation, tag:Tag, git:GitFacts, npm:NpmEntry, github:NonNullable<Target["github"]>, registry:Registry, target:Target, live:LiveFacts}} Facts */

/** Independent workflow input. @returns {Pick<Facts,"raw"|"request">} */
export function requestFacts() {
  return {
    raw: {
      GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: "thunderock/thunderkit", GITHUB_REF: "refs/heads/master",
      GITHUB_EVENT_NAME: "push", GITHUB_SHA: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      GITHUB_RUN_ID: "9007199254740993", GITHUB_RUN_ATTEMPT: "1",
      GITHUB_WORKFLOW_REF: "thunderock/thunderkit/.github/workflows/release-please.yml@refs/heads/master",
    },
    request: {
      repository: "thunderock/thunderkit", ref: "refs/heads/master", event: "push",
      sourceSha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", runId: "9007199254740993", attempt: "1",
      inputVersion: "", inputNpmTag: "",
    },
  };
}

/** Literal release facts, independent of production behavior. @returns {Facts} */
export function releaseFacts() {
  const { request, raw } = requestFacts();
  /** @type {Tag} */
  const baseTag = {
    name: "v0.1.1", version: "0.1.1", sha: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    objectSha: "cccccccccccccccccccccccccccccccccccccccc", annotation: "",
  };
  /** @type {Candidate} */
  const candidate = {
    version: "0.1.2", tag: "v0.1.2", npmTag: "latest", origin: { mode: "auto", runId: "9007199254740993" },
    base: { tag: "v0.1.1", version: "0.1.1", sourceSha: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
    bump: "patch", commitCount: 1,
  };
  /** @type {Release} */
  const release = {
    ...candidate, toolchain: { nodeMajor: 24, npm: "11.19.1", pythonMinor: "3.12" },
    tarball: {
      file: "package.tgz", size: 512,
      integrity: "sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
    },
  };
  /** @type {Prepared} */
  const prepared = { schema: 2, action: "publish", reason: "ready", request, release };
  /** @type {Reservation} */
  const reservation = {
    schema: "thunderkit.release/v1", repository: "thunderock/thunderkit",
    sourceSha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", release,
  };
  /** @type {Tag} */
  const tag = {
    name: "v0.1.2", version: "0.1.2", sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    objectSha: "dddddddddddddddddddddddddddddddddddddddd", annotation: JSON.stringify(reservation),
  };
  /** @type {GitFacts} */
  const git = {
    headSha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", masterSha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    sourceOnMaster: true,
    sourcePackage: { name: "thunderkit", version: "0.1.1", repositoryUrl: "git+https://github.com/thunderock/thunderkit.git" },
    tags: [baseTag], base: baseTag, baseRelation: "ancestor",
    commits: [{ sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", subject: "fix: handle empty input", body: "" }],
  };
  const npm = { name: "thunderkit", version: "0.1.2", integrity: release.tarball.integrity };
  const github = { tagName: "v0.1.2", draft: false, prerelease: false };
  /** @type {Registry} */
  const registry = {
    exists: true, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", integrity: null } },
    distTags: { latest: "0.1.1" },
  };
  /** @type {Target} */
  const target = { version: "0.1.2", tag: "v0.1.2", gitTag: null, npm: null, github: null };
  /** @type {LiveFacts} */
  const live = { git, registry, target, baseTarget: null };
  return { raw, request, baseTag, candidate, release, prepared, reservation, tag, git, npm, github, registry, target, live };
}

/** A reserved target with no npm write yet. @returns {Facts} */
export function reservedFacts() {
  const facts = releaseFacts();
  facts.git.tags.push(facts.tag);
  facts.git.base = facts.tag;
  facts.git.baseRelation = "equal";
  facts.target.gitTag = facts.tag;
  return facts;
}

/** A published target on its desired channel. @returns {Facts} */
export function publishedFacts() {
  const facts = reservedFacts();
  facts.target.npm = facts.npm;
  facts.registry.versions["0.1.2"] = facts.npm;
  facts.registry.distTags.latest = "0.1.2";
  return facts;
}

/** Explicit intent without deriving channel defaults. @param {string} version @param {string} channel @returns {Facts} */
export function manualFacts(version, channel) {
  const facts = releaseFacts();
  Object.assign(facts.request, { event: "workflow_dispatch", inputVersion: version, inputNpmTag: channel });
  Object.assign(facts.candidate, { version, tag: `v${version}`, npmTag: channel, origin: { mode: "manual", runId: facts.request.runId }, bump: null, commitCount: 0 });
  Object.assign(facts.release, facts.candidate);
  Object.assign(facts.tag, { name: `v${version}`, version, annotation: JSON.stringify(facts.reservation) });
  Object.assign(facts.target, { version, tag: `v${version}` });
  Object.assign(facts.npm, { version });
  Object.assign(facts.github, { tagName: `v${version}`, prerelease: version.includes("-") });
  return facts;
}

/** Two stable reservations with distinct origin runs. @returns {Facts & {second:Tag}} */
export function twoTagFacts() {
  const facts = reservedFacts();
  const release = { ...facts.release, version: "0.2.0", tag: "v0.2.0", bump: "minor", origin: { mode: "auto", runId: "500" } };
  const second = { ...facts.tag, name: "v0.2.0", version: "0.2.0", objectSha: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", annotation: JSON.stringify({ ...facts.reservation, release }) };
  facts.git.tags.push(second);
  facts.git.base = second;
  return { ...facts, second };
}

/** A stable release supersedes this reservation. @returns {Facts} */
export function historicalFacts() {
  const facts = publishedFacts();
  const newest = {
    name: "v0.2.0", version: "0.2.0", sha: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    objectSha: "ffffffffffffffffffffffffffffffffffffffff", annotation: "",
  };
  facts.git.tags.push(newest);
  facts.git.base = newest;
  facts.git.masterSha = newest.sha;
  facts.git.baseRelation = "descendant";
  facts.registry.versions["0.2.0"] = { name: "thunderkit", version: "0.2.0", integrity: null };
  facts.registry.distTags.latest = "0.2.0";
  return facts;
}

/** A completed base keyed independently of the target. @returns {Facts} */
export function managedBaseFacts() {
  const facts = releaseFacts();
  const release = {
    ...facts.release, version: "0.1.1", tag: "v0.1.1", origin: { mode: "auto", runId: "40" },
    base: null, bump: null, commitCount: 0,
  };
  facts.baseTag.annotation = JSON.stringify({ ...facts.reservation, sourceSha: facts.baseTag.sha, release });
  const npm = { name: "thunderkit", version: "0.1.1", integrity: release.tarball.integrity };
  facts.registry.versions["0.1.1"] = npm;
  facts.live.baseTarget = {
    version: "0.1.1", tag: "v0.1.1", gitTag: facts.baseTag, npm,
    github: { tagName: "v0.1.1", draft: false, prerelease: false },
  };
  return facts;
}

/** First stable publication into an absent package. @returns {Facts} */
export function bootstrapFacts() {
  const facts = releaseFacts();
  Object.assign(facts.git, { tags: [], base: null, baseRelation: "none" });
  Object.assign(facts.candidate, { version: "0.1.1", tag: "v0.1.1", base: null, bump: null, commitCount: 0 });
  Object.assign(facts.release, facts.candidate);
  Object.assign(facts.target, { version: "0.1.1", tag: "v0.1.1" });
  Object.assign(facts.npm, { version: "0.1.1" });
  Object.assign(facts.registry, { exists: false, versions: {}, distTags: {} });
  return facts;
}

/** A completed explicit prerelease on next. @returns {Facts} */
export function prereleaseFacts() {
  const facts = manualFacts("1.0.0-beta.1", "next");
  facts.git.tags.push(facts.tag);
  facts.target.gitTag = facts.tag;
  facts.target.npm = facts.npm;
  facts.target.github = facts.github;
  facts.registry.versions["1.0.0-beta.1"] = facts.npm;
  facts.registry.distTags.next = "1.0.0-beta.1";
  return facts;
}

/** A published stable version on a non-latest channel. @returns {Facts} */
export function maintenanceFacts() {
  const facts = manualFacts("1.0.0", "maintenance-0");
  facts.git.tags.push(facts.tag);
  facts.git.base = facts.tag;
  facts.git.baseRelation = "equal";
  facts.target.gitTag = facts.tag;
  facts.target.npm = facts.npm;
  facts.registry.versions["1.0.0"] = facts.npm;
  facts.registry.distTags["maintenance-0"] = "1.0.0";
  return facts;
}

/** Same-run reservation bound to another commit. @returns {Facts} */
export function wrongSourceFacts() {
  const facts = reservedFacts();
  const reservation = {
    ...facts.reservation, sourceSha: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    release: { ...facts.release, base: null, bump: null, commitCount: 0 },
  };
  facts.tag.sha = reservation.sourceSha;
  facts.tag.annotation = JSON.stringify(reservation);
  facts.git.baseRelation = "diverged";
  return facts;
}
