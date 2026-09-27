// @ts-check
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { parseSemver, compareSemver } from "../../tools/release/versions.mjs";

/** @template T @typedef {import("./release_facts.mjs").Mutable<T>} Mutable */
/** @typedef {import("../../tools/release/request.mjs").Request} Request */
/** @typedef {import("../../tools/release/request.mjs").ErrorCode} ErrorCode */
/** @typedef {import("../../tools/release/record.mjs").Prepared} Prepared */
/** @typedef {import("../../tools/release/record.mjs").Tag} Tag */
/** @typedef {import("../../tools/release/policy.mjs").Step} Step */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").GitFacts>} GitFacts */
/** @typedef {Mutable<import("../../tools/release/policy.mjs").Registry>} Registry */
/** @typedef {NonNullable<Mutable<import("../../tools/release/policy.mjs").Target>["github"]>} GithubRelease */
/** @typedef {{step:Step, phase:"before"|"after"}} Fault */

/** @template T @param {T} value @returns {import("../../tools/release/request.mjs").Result<T>} */
const ok = (value) => ({ ok: true, value: structuredClone(value) });
/** @param {ErrorCode} code @returns {import("../../tools/release/request.mjs").Failure} */
const fail = (code) => ({ ok: false, error: { code, message: "Remote unavailable" } });
const failureCodes = /** @type {Readonly<Record<Step, ErrorCode>>} */ ({ tag: "E_GIT", npm: "E_REGISTRY", github: "E_GH" });

/** @param {readonly Tag[]} tags */
function highestStableTag(tags) {
  /** @type {Tag|null} */ let highest = null;
  for (const entry of tags) {
    const version = parseSemver(entry.version);
    if (version === null || version.prerelease.length !== 0) continue;
    const current = highest === null ? null : parseSemver(highest.version);
    if (current === null || compareSemver(version, current) === 1) highest = entry;
  }
  return highest;
}

/** Stateful, immutable remote model: Git tags, an npm registry that hashes what it receives, and GitHub releases. */
export class Remote {
  /** @type {GitFacts} */ git;
  /** @type {Registry} */ registry = { exists: false, versions: {}, distTags: {} };
  /** @type {Record<string, GithubRelease>} */ releases = {};
  /** @type {Record<string, Buffer>} */ packages = {};
  /** @type {string[]} */ calls = [];
  /** @type {Step[]} */ accepted = [];
  /** @type {boolean[]} */ latestFlags = [];
  /** @type {Fault|null} */ fault = null;
  /** @type {ErrorCode|null} */ readError = null;
  /** @type {((name:string) => void)|null} */ onRead = null;
  /** @type {((step:Step) => void)|null} */ onAccept = null;
  /** @param {string} sourceSha @param {string} packageVersion */
  constructor(sourceSha, packageVersion) {
    this.git = {
      headSha: sourceSha, masterSha: sourceSha, sourceOnMaster: true,
      sourcePackage: { name: "thunderkit", version: packageVersion, repositoryUrl: "git+https://github.com/thunderock/thunderkit.git" },
      tags: [], base: null, baseRelation: "none", commits: [{ sha: sourceSha, subject: "feat: initial package", body: "" }],
    };
  }
  /** Recompute derived base facts from the tag list, as the concrete reader would. */
  refreshBase() {
    const base = highestStableTag(this.git.tags);
    this.git.base = base;
    this.git.baseRelation = base === null ? "none" : base.sha === this.git.headSha ? "equal" : "ancestor";
    if (base !== null && base.sha === this.git.headSha) this.git.commits = [];
  }
  /** @param {string} name */
  read(name) {
    this.calls.push(`read:${name}`);
    this.onRead?.(name);
    return this.readError;
  }
  /** @param {Request} request */
  async readGit(request) {
    const error = this.read("git");
    if (error !== null) return fail(error);
    return request.sourceSha === this.git.headSha ? ok(this.git) : fail("E_UNTRUSTED_CONTEXT");
  }
  async readRegistry() {
    const error = this.read("registry");
    return error === null ? ok(this.registry) : fail(error);
  }
  /** @param {string} tagName */
  async readRelease(tagName) {
    const error = this.read(`github:${tagName}`);
    return error === null ? ok(this.releases[tagName] ?? null) : fail(error);
  }
  /** A fault before acceptance leaves state untouched; a fault after acceptance mutates state but reports failure (lost acknowledgement). @param {Step} step @param {() => void} accept */
  mutate(step, accept) {
    this.calls.push(`write:${step}`);
    const fault = this.fault?.step === step ? this.fault : null;
    if (fault !== null) this.fault = null;
    if (fault?.phase === "before") return fail(failureCodes[step]);
    accept();
    this.accepted.push(step);
    this.onAccept?.(step);
    return fault?.phase === "after" ? fail(failureCodes[step]) : ok(null);
  }
  /** @param {Prepared} prepared */
  async pushTag(prepared) {
    const { release, request } = prepared;
    const annotation = JSON.stringify({ schema: "thunderkit.release/v1", repository: request.repository, sourceSha: request.sourceSha, release });
    const existing = this.git.tags.find((entry) => entry.name === release.tag);
    if (existing !== undefined) return existing.sha === request.sourceSha && existing.annotation === annotation ? ok(null) : fail("E_GIT");
    return this.mutate("tag", () => {
      this.git.tags.push({ name: release.tag, version: release.version, sha: request.sourceSha, objectSha: createHash("sha256").update(annotation).digest("hex").slice(0, 40), annotation });
      this.refreshBase();
    });
  }
  /** @param {Prepared} prepared @param {string} directory */
  async publishTarball(prepared, directory) {
    const { version, npmTag } = prepared.release;
    if (Object.hasOwn(this.registry.versions, version)) return fail("E_REGISTRY");
    const bytes = readFileSync(join(directory, "package.tgz"));
    return this.mutate("npm", () => {
      this.packages[version] = Buffer.from(bytes);
      this.registry.exists = true;
      this.registry.versions[version] = { name: "thunderkit", version, integrity: `sha512-${createHash("sha512").update(bytes).digest("base64")}` };
      this.registry.distTags[npmTag] = version;
    });
  }
  /** @param {Prepared} prepared @param {boolean} latest */
  async createRelease(prepared, latest) {
    const { version, tag } = prepared.release;
    if (Object.hasOwn(this.releases, tag)) return fail("E_GH");
    if (!this.git.tags.some((entry) => entry.name === tag)) return fail("E_GH");
    return this.mutate("github", () => {
      this.latestFlags.push(latest);
      this.releases[tag] = { tagName: tag, draft: false, prerelease: (parseSemver(version)?.prerelease.length ?? 0) !== 0 };
    });
  }
  snapshot() {
    return { calls: [...this.calls], accepted: [...this.accepted], latestFlags: [...this.latestFlags], registry: structuredClone(this.registry), tags: structuredClone(this.git.tags), releases: structuredClone(this.releases) };
  }
}
