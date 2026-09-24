// @ts-check
import { parseSemver, compareSemver } from "./versions.mjs";
import { decodeRequest, failure, success, hasKeys, isSha, rejectVariant } from "./request.mjs";
import { decodeReservation, decodePlan } from "./record.mjs";
export { parseRequest } from "./request.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {import("./versions.mjs").Semver} Semver */
/** @typedef {import("./record.mjs").Tag} Tag */
/** @typedef {import("./record.mjs").Level} Level */
/** @typedef {import("./record.mjs").Release} Release */
/** @typedef {import("./record.mjs").Prepared} Prepared */
/** @typedef {import("./record.mjs").Reservation} Reservation */
/** @typedef {Readonly<{sha:string, subject:string, body:string}>} Commit */
/** @typedef {Readonly<{headSha:string, masterSha:string, sourceOnMaster:boolean, sourcePackage:Readonly<{name:string, version:string, repositoryUrl:string}>, tags:readonly Tag[], base:Tag|null, baseRelation:"none"|"equal"|"ancestor"|"descendant"|"diverged", commits:readonly Commit[]}>} GitFacts */
/** @typedef {Readonly<{name:string, version:string, integrity:string|null}>} NpmEntry */
/** @typedef {Readonly<{exists:boolean, versions:Readonly<Record<string,NpmEntry>>, distTags:Readonly<Record<string,string>>}>} Registry */
/** @typedef {Readonly<{version:string, tag:string, gitTag:Tag|null, npm:NpmEntry|null, github:Readonly<{tagName:string, draft:boolean, prerelease:boolean}>|null}>} Target */
/** @typedef {Readonly<{git:GitFacts, registry:Registry, target:Target, baseTarget:Target|null}>} LiveFacts */
/** @typedef {Readonly<{kind:"new", candidate:import("./record.mjs").Candidate}>|Readonly<{kind:"resume", reservation:Reservation}>|Readonly<{kind:"skip", reason:"no_commits"|"stale_source"}>} Selection */
/** @typedef {"tag"|"npm"|"github"} Step */
/** @typedef {"pending"|"current"|"superseded"} Channel */
/** @typedef {Readonly<{action:"publish", reason:"ready", steps:readonly Step[], channel:Channel}>|Readonly<{action:"skip", reason:"already_released", steps:readonly [], channel:Channel}>|Readonly<{action:"skip", reason:"stale_source", steps:readonly [], channel:null}>} Decision */
/** @typedef {"fresh"|"reserved"|"published"|"complete"} TargetState */

/** @param {string} left @param {string} right @returns {-1|0|1|null} */
function order(left, right) {
  const a = parseSemver(left);
  const b = parseSemver(right);
  return a && b ? compareSemver(a, b) : null;
}
/** @param {Tag} tag */
function stableTag(tag) {
  const version = parseSemver(tag.version);
  return version !== null && version.prerelease.length === 0 && tag.name === `v${version.raw}`;
}
/** @param {Tag|null} a @param {Tag|null} b */
function sameTag(a, b) {
  const keys = /** @type {const} */ (["name", "version", "sha", "objectSha", "annotation"]);
  return a === null ? b === null : hasKeys(b, keys) && keys.every((key) => a[key] === b[key]);
}

/** Select by canonical stable SemVer, never enumeration date or order. @param {readonly Tag[]} tags @returns {Tag|null} */
export function highestStable(tags) {
  /** @type {Tag|null} */
  let highest = null;
  for (const tag of tags) {
    if (stableTag(tag) && (highest === null || order(tag.version, highest.version) === 1)) highest = tag;
  }
  return highest === null ? null : Object.freeze({ ...highest });
}

/** The caller supplies the non-merge base range. @param {readonly Commit[]} commits @param {number} baseMajor @returns {Level} */
export function bumpLevel(commits, baseMajor) {
  const breaking = commits.some(({ subject, body }) => /^[a-z]+(?:\([^\r\n)]+\))?!: /i.test(subject) || /^BREAKING(?: CHANGE|-CHANGE):\s+\S/m.test(body));
  if (breaking) return baseMajor === 0 ? "minor" : "major";
  return baseMajor > 0 && commits.some(({ subject }) => /^feat(?:\([^\r\n)]+\))?: /i.test(subject)) ? "minor" : "patch";
}

/** Increment parsed core components without exceeding npm's integer bounds. @param {Semver} base @param {Level} level @returns {Result<string>} */
export function nextVersion(base, level) {
  switch (level) {
    case "major":
      return base.major === Number.MAX_SAFE_INTEGER ? failure("E_VERSION_OVERFLOW") : success(`${base.major + 1}.0.0`);
    case "minor":
      return base.minor === Number.MAX_SAFE_INTEGER ? failure("E_VERSION_OVERFLOW") : success(`${base.major}.${base.minor + 1}.0`);
    case "patch":
      return base.patch === Number.MAX_SAFE_INTEGER ? failure("E_VERSION_OVERFLOW") : success(`${base.major}.${base.minor}.${base.patch + 1}`);
    default:
      return rejectVariant(level);
  }
}

/** @param {Request} request @param {GitFacts} git @returns {Result<Tag|null>} */
function gitBase(request, git) {
  if (!hasKeys(git, ["headSha", "masterSha", "sourceOnMaster", "sourcePackage", "tags", "base", "baseRelation", "commits"])) return failure("E_GIT");
  if (git.headSha !== request.sourceSha || !isSha(git.masterSha)) return failure("E_UNTRUSTED_CONTEXT");
  if (git.sourceOnMaster !== true) return failure("E_STALE_SOURCE");
  if (!hasKeys(git.sourcePackage, ["name", "version", "repositoryUrl"]) || git.sourcePackage.name !== "thunderkit"
      || git.sourcePackage.repositoryUrl !== "git+https://github.com/thunderock/thunderkit.git" || typeof git.sourcePackage.version !== "string") return failure("E_GIT");
  if (!Array.isArray(git.tags) || !Array.isArray(git.commits)
      || [...git.tags].some((tag) => !hasKeys(tag, ["name", "version", "sha", "objectSha", "annotation"]) || !isSha(tag.sha) || !isSha(tag.objectSha)
        || typeof tag.name !== "string" || typeof tag.version !== "string" || typeof tag.annotation !== "string")
      || [...git.commits].some((commit) => !hasKeys(commit, ["sha", "subject", "body"]) || !isSha(commit.sha) || typeof commit.subject !== "string" || typeof commit.body !== "string")) return failure("E_GIT");
  const base = highestStable(git.tags);
  if (new Set(git.tags.map((tag) => tag.name)).size !== git.tags.length || !sameTag(base, git.base)
      || !["none", "equal", "ancestor", "descendant", "diverged"].includes(git.baseRelation)
      || (base === null) !== (git.baseRelation === "none") || (base !== null && (base.sha === request.sourceSha) !== (git.baseRelation === "equal"))) return failure("E_GIT");
  return success(base);
}

/** @param {Request} request @param {Tag} tag @returns {Result<Selection>} */
function resume(request, tag) {
  if (tag.sha !== request.sourceSha) return failure("E_VERSION_TAKEN");
  const decoded = decodeReservation(tag.annotation, tag);
  if (!decoded.ok) return decoded;
  if (decoded.value === null) return request.inputVersion === "" ? success({ kind: "skip", reason: "no_commits" }) : failure("E_VERSION_TAKEN");
  if (request.inputNpmTag !== "" && request.inputNpmTag !== decoded.value.release.npmTag) return failure("E_RESUME_CHANNEL");
  return success({ kind: "resume", reservation: decoded.value });
}

/** Validate before manual precedence or automatic resume selection. @param {Request} input @param {GitFacts} git @returns {Result<Selection>} */
export function selectCandidate(input, git) {
  const decoded = decodeRequest(input);
  if (!decoded.ok) return decoded;
  const request = decoded.value;
  const checked = gitBase(request, git);
  if (!checked.ok) return checked;
  const base = checked.value;
  const manual = request.inputVersion !== "";
  /** @type {Level|null} */
  let bump = null;
  let commitCount = 0;
  let version = request.inputVersion;
  if (manual) {
    const exact = git.tags.find((tag) => tag.name === `v${version}`);
    if (exact) return resume(request, exact);
    if (request.sourceSha !== git.masterSha) return failure("E_STALE_SOURCE");
  } else {
    /** @type {{tag:Tag, reservation:Reservation}[]} */
    const matches = [];
    for (const tag of git.tags.filter(stableTag)) {
      const record = decodeReservation(tag.annotation, tag);
      if (!record.ok) return record;
      if (record.value?.release.origin.runId === request.runId) matches.push({ tag, reservation: record.value });
    }
    if (matches.length > 1) return failure("E_AMBIGUOUS_RESUME");
    const prior = matches[0];
    if (prior) {
      if (prior.reservation.release.origin.mode !== "auto" || prior.tag.sha !== request.sourceSha) return failure("E_VERSION_TAKEN");
      return resume(request, prior.tag);
    }
    if (base?.sha === request.sourceSha) return resume(request, base);
    if (request.sourceSha !== git.masterSha) return success({ kind: "skip", reason: "stale_source" });
    if (base !== null && git.baseRelation !== "ancestor") return failure("E_TAG_NOT_ANCESTOR");
    if (git.commits.length === 0) return success({ kind: "skip", reason: "no_commits" });
    const parsed = parseSemver(base === null ? git.sourcePackage.version : base.version);
    if (!parsed || parsed.prerelease.length) return failure("E_INVALID_VERSION");
    version = parsed.raw;
    if (base !== null) {
      bump = bumpLevel(git.commits, parsed.major);
      const next = nextVersion(parsed, bump);
      if (!next.ok) return next;
      version = next.value;
      commitCount = git.commits.length;
    }
  }
  if (manual && base !== null && order(version, base.version) === -1 && (request.inputNpmTag === "" || request.inputNpmTag === "latest")) return failure("E_INVALID_NPM_TAG");
  return success({ kind: "new", candidate: {
    version, tag: `v${version}`, npmTag: request.inputNpmTag || (parseSemver(version)?.prerelease.length ? "next" : "latest"),
    origin: { mode: manual ? "manual" : "auto", runId: request.runId },
    base: base === null ? null : { tag: base.name, version: base.version, sourceSha: base.sha }, bump, commitCount,
  } });
}

/** @param {Reservation} expected @param {Target} target @returns {Result<TargetState>} */
function targetState(expected, target) {
  const release = expected.release;
  if (!hasKeys(target, ["version", "tag", "gitTag", "npm", "github"]) || target.version !== release.version || target.tag !== release.tag) return failure("E_RECORD");
  const gh = target.github;
  if (gh !== null && (!hasKeys(gh, ["tagName", "draft", "prerelease"]) || gh.tagName !== release.tag || gh.draft !== false
      || gh.prerelease !== Boolean(parseSemver(release.version)?.prerelease.length) || target.gitTag === null || target.npm === null)) return failure("E_GH_CONFLICT");
  if (target.gitTag === null) return target.npm === null ? success("fresh") : failure("E_VERSION_TAKEN");
  if (target.gitTag.sha !== expected.sourceSha) return failure("E_VERSION_TAKEN");
  const record = decodeReservation(target.gitTag.annotation, target.gitTag);
  if (!record.ok) return record;
  if (record.value === null) return failure("E_VERSION_TAKEN");
  if (record.value.release.tarball.integrity !== release.tarball.integrity || record.value.release.tarball.size !== release.tarball.size) return failure("E_ARTIFACT");
  if (JSON.stringify(record.value) !== JSON.stringify(expected)) return failure("E_VERSION_TAKEN");
  if (target.npm === null) return success("reserved");
  if (target.npm.name !== "thunderkit" || target.npm.version !== release.version) return failure("E_REGISTRY");
  if (target.npm.integrity !== release.tarball.integrity) return failure("E_REGISTRY_INTEGRITY");
  return success(gh === null ? "published" : "complete");
}

/** @param {NpmEntry|null} entry @param {NpmEntry|null} target @returns {boolean} */
function sameNpm(entry, target) {
  return entry === null ? target === null : hasKeys(target, ["name", "version", "integrity"])
    && entry.name === target.name && entry.version === target.version && entry.integrity === target.integrity;
}

/** @param {Release} release @param {Registry} registry @param {boolean} published @returns {Result<Channel>} */
function channelState(release, registry, published) {
  const latest = registry.distTags.latest;
  const latestVersion = parseSemver(latest);
  if (latest !== undefined && (!latestVersion || latestVersion.prerelease.length || !Object.hasOwn(registry.versions, latest))) return failure("E_CHANNEL_STATE");
  if (latest === undefined && Object.keys(registry.versions).some((version) => parseSemver(version)?.prerelease.length === 0)
      && !(published && release.npmTag === "latest")) return failure("E_CHANNEL_STATE");
  if (!published) return success("pending");
  const pointer = registry.distTags[release.npmTag];
  const parsed = parseSemver(pointer);
  if (!parsed || !Object.hasOwn(registry.versions, parsed.raw)) return failure("E_CHANNEL_DRIFT");
  const comparison = order(parsed.raw, release.version);
  if (comparison !== 0 && comparison !== 1) return failure("E_CHANNEL_DRIFT");
  return success(comparison === 0 ? "current" : "superseded");
}

/** Reconcile only this prepared target against current source-bound evidence. @param {Prepared} prepared @param {LiveFacts} live @returns {Result<Decision>} */
export function reconcile(prepared, live) {
  const decoded = decodePlan(JSON.stringify(prepared), prepared.request);
  if (!decoded.ok) return decoded;
  if (decoded.value.release === null) return failure("E_RECORD");
  if (!hasKeys(live, ["git", "registry", "target", "baseTarget"]) || !hasKeys(live.target, ["version", "tag", "gitTag", "npm", "github"])) return failure("E_RECORD");
  const plan = decoded.value;
  const { request, release } = plan;
  const checked = gitBase(request, live.git);
  if (!checked.ok) return checked;
  const base = checked.value;
  const target = live.target;
  const registry = live.registry;
  if (!hasKeys(registry, ["exists", "versions", "distTags"]) || typeof registry.exists !== "boolean") return failure("E_REGISTRY");
  for (const map of [registry.versions, registry.distTags]) {
    if (map === null || typeof map !== "object" || !hasKeys(map, Object.keys(map))) return failure("E_REGISTRY");
  }
  if (!registry.exists && (Object.keys(registry.versions).length || Object.keys(registry.distTags).length)) return failure("E_REGISTRY");
  if (Object.values(registry.distTags).some((value) => typeof value !== "string")) return failure("E_REGISTRY");
  for (const [version, entry] of Object.entries(registry.versions)) {
    if (!parseSemver(version) || !hasKeys(entry, ["name", "version", "integrity"]) || entry.name !== "thunderkit" || entry.version !== version
        || (entry.integrity !== null && typeof entry.integrity !== "string")) return failure("E_REGISTRY");
  }
  const originalBase = release.base;
  if (originalBase !== null && !live.git.tags.some((tag) => tag.name === originalBase.tag
      && tag.version === originalBase.version && tag.sha === originalBase.sourceSha)) return failure("E_STALE_PLAN");
  if (!sameTag(live.git.tags.find((tag) => tag.name === release.tag) ?? null, target.gitTag)) return failure("E_GIT");
  const entry = registry.versions[release.version] ?? null;
  if (!sameNpm(entry, target.npm)) return failure("E_REGISTRY");
  if (target.gitTag === null && target.npm !== null && release.origin.mode === "auto" && release.base === null) return failure("E_NO_BASE");
  const expected = { schema: /** @type {const} */ ("thunderkit.release/v1"), repository: request.repository, sourceSha: request.sourceSha, release };
  const state = targetState(expected, target);
  if (!state.ok) return state;
  const channel = channelState(release, registry, target.npm !== null);
  if (!channel.ok) return channel;
  if (target.gitTag === null) {
    if (release.origin.mode === "auto" && (release.base?.tag !== base?.name || release.base?.sourceSha !== base?.sha)) return failure("E_STALE_PLAN");
    const selected = selectCandidate(request, live.git);
    if (!selected.ok) return selected;
    switch (selected.value.kind) {
      case "skip":
        return selected.value.reason === "stale_source" ? success({ action: "skip", reason: "stale_source", steps: [], channel: null }) : failure("E_STALE_PLAN");
      case "resume":
        return failure("E_STALE_PLAN");
      case "new": {
        const { toolchain, tarball, ...candidate } = release;
        const current = { ...selected.value.candidate, base: release.origin.mode === "manual" ? release.base : selected.value.candidate.base };
        if (JSON.stringify(candidate) !== JSON.stringify(current)) return failure("E_STALE_PLAN");
        break;
      }
      default:
        return rejectVariant(selected.value);
    }
    if (release.origin.mode === "auto" && base !== null) {
      const reservation = decodeReservation(base.annotation, base);
      if (!reservation.ok) return reservation;
      if (reservation.value !== null) {
        const completion = live.baseTarget === null ? failure("E_BASE_INCOMPLETE") : targetState(reservation.value, live.baseTarget);
        if (!completion.ok || completion.value !== "complete" || !sameTag(base, live.baseTarget?.gitTag ?? null)
            || !sameNpm(registry.versions[base.version] ?? null, live.baseTarget?.npm ?? null)
            || !channelState(reservation.value.release, registry, true).ok) return failure("E_BASE_INCOMPLETE");
      }
    }
  }
  if (target.npm === null && release.npmTag === "latest" && ((base !== null && order(release.version, base.version) === -1)
      || (registry.distTags.latest !== undefined && order(release.version, registry.distTags.latest) !== 1))) return failure("E_STALE_TARGET");
  /** @type {Readonly<Record<TargetState, readonly Step[]>>} */
  const steps = { fresh: ["tag", "npm", "github"], reserved: ["npm", "github"], published: ["github"], complete: [] };
  switch (state.value) {
    case "complete":
      return success({ action: "skip", reason: "already_released", steps: [], channel: channel.value });
    case "fresh":
    case "reserved":
    case "published":
      return success({ action: "publish", reason: "ready", steps: steps[state.value], channel: channel.value });
    default:
      return rejectVariant(state.value);
  }
}
