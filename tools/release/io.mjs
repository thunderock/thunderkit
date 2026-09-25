// @ts-check
import { spawnSync } from "node:child_process";
import { request as httpsRequest } from "node:https";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { decodeRequest, failure, success, isSha } from "./request.mjs";
import { decodePlan, encodeReservation } from "./record.mjs";
import { highestStable } from "./policy.mjs";
import { parseSemver } from "./versions.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").ErrorCode} ErrorCode */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {import("./record.mjs").Prepared} Prepared */
/** @typedef {import("./record.mjs").Tag} Tag */
/** @typedef {import("./policy.mjs").GitFacts} GitFacts */
/** @typedef {import("./policy.mjs").Registry} Registry */
/** @typedef {import("./policy.mjs").NpmEntry} NpmEntry */
/** @typedef {import("./policy.mjs").Target} Target */
/** @typedef {Readonly<{cwd:string, env?:Readonly<Record<string,string>>, input?:string, timeout?:number, maxBuffer?:number}>} ExecOptions */
/** @typedef {Readonly<{status:number|null, signal:string|null, stdout:string, stderr:string}>} ExecResult */
/** @typedef {Readonly<{exec:(program:string, argv:readonly string[], options:ExecOptions)=>ExecResult, get:(url:string)=>Promise<Readonly<{status:number, body:string}>>}>} Drivers */
/** @typedef {ReturnType<typeof createSystemTransport>} Transport */

export const repositoryUrl = "https://github.com/thunderock/thunderkit.git";
export const registryUrl = "https://registry.npmjs.org/thunderkit";
export const botIdentity = Object.freeze(["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]);
const redirects = new Set([301, 302, 303, 307, 308]);

class BoundaryError extends Error {
  /** @param {ErrorCode} code */
  constructor(code) {
    super("Release boundary check failed");
    /** @readonly */ this.code = code;
  }
}
/** @param {ErrorCode} code @returns {never} */
function fail(code) { throw new BoundaryError(code); }
/** @param {unknown} error @param {ErrorCode} fallback */
function caught(error, fallback) {
  if (error instanceof BoundaryError) return failure(error.code);
  if (error instanceof Error) return failure(fallback);
  throw error;
}
/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }

/** Run argv with a throwaway HOME, so children see no inherited credentials or configuration. @param {string} program @param {readonly string[]} argv @param {ExecOptions} options @returns {ExecResult} */
export function exec(program, argv, options) {
  const home = mkdtempSync(join(tmpdir(), "release-child-"));
  try {
    writeFileSync(join(home, "user.npmrc"), "");
    writeFileSync(join(home, "global.npmrc"), "");
    const env = {
      PATH: process.env.PATH ?? "", HOME: home, TMPDIR: tmpdir(), LC_ALL: "C.UTF-8", TZ: "UTC",
      GIT_MASTER: "1", GIT_AUTOPUSH_DISABLE: "1", GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: join(home, "gitconfig"), GIT_TERMINAL_PROMPT: "0",
      NPM_CONFIG_USERCONFIG: join(home, "user.npmrc"), NPM_CONFIG_GLOBALCONFIG: join(home, "global.npmrc"),
      NPM_CONFIG_CACHE: join(home, "npm-cache"), NPM_CONFIG_REGISTRY: "https://registry.npmjs.org", NPM_CONFIG_STRICT_SSL: "true",
      NPM_CONFIG_AUDIT: "false", NPM_CONFIG_FUND: "false", NPM_CONFIG_UPDATE_NOTIFIER: "false",
      PYTHONSAFEPATH: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1", ...options.env,
    };
    const result = spawnSync(program, [...argv], {
      cwd: resolve(options.cwd), env, encoding: "utf8", shell: false, killSignal: "SIGKILL", ...(options.input === undefined ? {} : { input: options.input }),
      timeout: Math.min(options.timeout ?? 120_000, 600_000), maxBuffer: Math.min(options.maxBuffer ?? 16_777_216, 67_108_864),
    });
    if (result.error) return { status: null, signal: result.signal, stdout: result.stdout ?? "", stderr: "Child process unavailable" };
    return { status: result.status, signal: result.signal, stdout: result.stdout, stderr: result.stderr };
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

/** Read public registry metadata over verified TLS with a total deadline, size bound and host-bound redirects. @param {string} url @returns {Promise<Readonly<{status:number, body:string}>>} */
export async function get(url) {
  const deadline = Date.now() + 20_000;
  /** @param {string} address @param {number} hops @returns {Promise<Readonly<{status:number, body:string}>>} */
  const visit = (address, hops) => new Promise((accept, reject) => {
    const target = new URL(address);
    if (target.protocol !== "https:" || target.host !== "registry.npmjs.org" || target.username || target.password || hops > 3) return reject(new Error("Registry destination rejected"));
    const remaining = deadline - Date.now();
    if (remaining <= 0) return reject(new Error("Registry deadline exceeded"));
    const request = httpsRequest(target, { method: "GET", rejectUnauthorized: true, headers: { accept: "application/json" } }, (response) => {
      const status = response.statusCode ?? 0;
      if (redirects.has(status)) {
        response.resume();
        const location = response.headers.location;
        return location === undefined ? reject(new Error("Redirect without location")) : accept(visit(new URL(location, target).href, hops + 1));
      }
      let size = 0;
      /** @type {Buffer[]} */ const chunks = [];
      response.on("data", (chunk) => { size += chunk.length; if (size > 16_777_216) request.destroy(new Error("Registry response too large")); else chunks.push(chunk); });
      response.on("error", reject);
      response.on("end", () => accept({ status, body: Buffer.concat(chunks).toString("utf8") }));
    });
    const timer = setTimeout(() => request.destroy(new Error("Registry deadline exceeded")), remaining);
    request.on("close", () => clearTimeout(timer));
    request.on("error", reject);
    request.end();
  });
  return visit(url, 0);
}

/** Construct the source-bound transport; every remote or child-process effect goes through the supplied drivers. @param {string} checkoutDir @param {Drivers} drivers */
export function createSystemTransport(checkoutDir, drivers) {
  const cwd = resolve(checkoutDir);
  /** @param {readonly string[]} argv @param {ErrorCode} code @param {Readonly<Record<string,string>>} [env] */
  const git = (argv, code = "E_GIT", env) => {
    const result = drivers.exec("git", argv, env === undefined ? { cwd } : { cwd, env });
    if (result.status !== 0 || result.signal !== null) fail(code);
    return result.stdout;
  };
  /** @param {string} ancestor @param {string} descendant */
  const isAncestor = (ancestor, descendant) => {
    const result = drivers.exec("git", ["merge-base", "--is-ancestor", ancestor, descendant], { cwd });
    if (result.signal !== null || (result.status !== 0 && result.status !== 1)) fail("E_GIT");
    return result.status === 0;
  };
  /** @param {string} namespace @returns {Tag[]} */
  const readTags = (namespace) => {
    /** @type {Tag[]} */ const tags = [];
    for (const line of git(["for-each-ref", "--format=%(objectname) %(objecttype) %(*objecttype) %(refname)", `${namespace}/tags/`]).split("\n").filter(Boolean)) {
      const [objectSha, objectType, peeledType, ref] = line.split(" ");
      if (!objectSha || !ref || !ref.startsWith(`${namespace}/tags/`)) fail("E_GIT");
      const name = ref.slice(`${namespace}/tags/`.length);
      const version = name.startsWith("v") ? parseSemver(name.slice(1)) : null;
      if (version === null) continue;
      if (!isSha(objectSha) || (objectType === "tag" ? peeledType !== "commit" : objectType !== "commit")) fail("E_GIT");
      const sha = git(["rev-parse", "--verify", `${ref}^{commit}`]).trim();
      const raw = objectType === "tag" ? git(["cat-file", "tag", objectSha]) : "";
      const separator = raw.indexOf("\n\n");
      if (!isSha(sha) || (raw !== "" && separator === -1)) fail("E_GIT");
      tags.push({ name, version: version.raw, sha, objectSha, annotation: raw === "" ? "" : raw.slice(separator + 2) });
    }
    return tags;
  };
  /** @param {string} range */
  const readCommits = (range) => git(["log", "-z", "--no-merges", "--format=%H%n%s%n%b", range]).split("\0").filter(Boolean).map((record) => {
    const [sha, subject, ...body] = record.split("\n");
    if (!isSha(sha) || subject === undefined) return fail("E_GIT");
    return { sha, subject, body: body.join("\n").trimEnd() };
  });
  /** @param {string} sha */
  const readPackage = (sha) => {
    const pkg = JSON.parse(git(["show", `${sha}:package.json`]));
    if (!isObject(pkg) || typeof pkg.name !== "string" || typeof pkg.version !== "string" || !isObject(pkg.repository) || typeof pkg.repository.url !== "string") fail("E_GIT");
    return { name: pkg.name, version: pkg.version, repositoryUrl: pkg.repository.url };
  };
  /** @param {Prepared} prepared */
  const publishable = (prepared) => {
    const decoded = decodePlan(JSON.stringify(prepared), prepared.request);
    if (!decoded.ok || decoded.value.action !== "publish") fail("E_RECORD");
    return decoded.value;
  };
  /** Reuse an identical retained tag object; any other existing tag is a conflict. @param {string} tag @param {string} sourceSha @param {string} message */
  const ensureLocalTag = (tag, sourceSha, message) => {
    const existing = drivers.exec("git", ["rev-parse", "-q", "--verify", `refs/tags/${tag}`], { cwd });
    if (existing.signal !== null || (existing.status !== 0 && existing.status !== 1)) fail("E_GIT");
    if (existing.status === 0) {
      const objectSha = existing.stdout.trim();
      const raw = git(["cat-file", "-t", objectSha]).trim() === "tag" ? git(["cat-file", "tag", objectSha]) : "";
      const separator = raw.indexOf("\n\n");
      if (objectSha === sourceSha || separator === -1 || raw.slice(separator + 2) !== message || git(["rev-parse", "--verify", `refs/tags/${tag}^{commit}`]).trim() !== sourceSha) fail("E_GIT");
      return;
    }
    const scratch = mkdtempSync(join(tmpdir(), "release-tag-"));
    try {
      const file = join(scratch, "reservation.json");
      writeFileSync(file, message, { mode: 0o600 });
      git([...botIdentity, "tag", "-a", tag, "--cleanup=verbatim", "-F", file, sourceSha]);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  };
  return Object.freeze({
    /** @param {Request} request @returns {Promise<Result<GitFacts>>} */
    async readGit(request) {
      const decoded = decodeRequest(request);
      if (!decoded.ok) return decoded;
      const namespace = `refs/release-read/${randomUUID()}`;
      try {
        const headSha = git(["rev-parse", "--verify", "HEAD^{commit}"]).trim();
        if (headSha !== decoded.value.sourceSha) return failure("E_UNTRUSTED_CONTEXT");
        git(["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", repositoryUrl, `refs/heads/master:${namespace}/master`, `refs/tags/*:${namespace}/tags/*`]);
        const masterSha = git(["rev-parse", "--verify", `${namespace}/master^{commit}`]).trim();
        if (!isSha(masterSha)) return failure("E_GIT");
        const tags = readTags(namespace);
        const base = highestStable(tags);
        const baseRelation = base === null ? "none" : base.sha === headSha ? "equal" : isAncestor(base.sha, headSha) ? "ancestor" : isAncestor(headSha, base.sha) ? "descendant" : "diverged";
        const commits = readCommits(base === null ? headSha : `${base.sha}..${headSha}`);
        return success({ headSha, masterSha, sourceOnMaster: isAncestor(headSha, masterSha), sourcePackage: readPackage(headSha), tags, base, baseRelation, commits });
      } catch (error) {
        return caught(error, "E_GIT");
      } finally {
        const refs = drivers.exec("git", ["for-each-ref", "--format=%(refname)", namespace], { cwd }).stdout.split("\n").filter(Boolean);
        if (refs.length) drivers.exec("git", ["update-ref", "--stdin"], { cwd, input: refs.map((ref) => `delete ${ref}\n`).join("") });
      }
    },
    /** @returns {Promise<Result<Registry>>} */
    async readRegistry() {
      try {
        const response = await drivers.get(registryUrl);
        if (response.status === 404) return success({ exists: false, versions: {}, distTags: {} });
        if (response.status !== 200) return failure("E_REGISTRY");
        const document = JSON.parse(response.body);
        if (!isObject(document) || document.name !== "thunderkit" || !isObject(document.versions) || !isObject(document["dist-tags"])) return failure("E_REGISTRY");
        /** @type {Record<string, NpmEntry>} */ const versions = {};
        for (const [version, entry] of Object.entries(document.versions)) {
          if (!parseSemver(version) || !isObject(entry) || entry.name !== "thunderkit" || entry.version !== version) return failure("E_REGISTRY");
          const integrity = isObject(entry.dist) ? entry.dist.integrity : undefined;
          if (integrity !== undefined && typeof integrity !== "string") return failure("E_REGISTRY");
          versions[version] = { name: "thunderkit", version, integrity: integrity ?? null };
        }
        /** @type {Record<string, string>} */ const distTags = {};
        for (const [channel, version] of Object.entries(document["dist-tags"])) {
          if (typeof version !== "string") return failure("E_REGISTRY");
          if (!parseSemver(version) || !Object.hasOwn(versions, version)) return failure("E_CHANNEL_STATE");
          distTags[channel] = version;
        }
        return success({ exists: true, versions, distTags });
      } catch (error) {
        return caught(error, "E_REGISTRY");
      }
    },
    /** Only an actual HTTP 404 for the exact tag means absent. @param {string} tag @returns {Promise<Result<Target["github"]>>} */
    async readRelease(tag) {
      if (!tag.startsWith("v") || parseSemver(tag.slice(1)) === null) return failure("E_GH");
      try {
        const result = drivers.exec("gh", ["api", "--include", "--method", "GET", `repos/thunderock/thunderkit/releases/tags/${encodeURIComponent(tag)}`], { cwd, env: { GH_TOKEN: process.env.GH_TOKEN ?? "" } });
        if (result.signal !== null || (result.status !== 0 && result.status !== 1)) return failure("E_GH");
        const match = /^HTTP\/\S+ (\d{3})[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n([\s\S]*)$/.exec(result.stdout);
        if (match === null || match[1] === undefined) return failure("E_GH");
        if (match[1] === "404") return success(null);
        if (match[1] !== "200" || result.status !== 0) return failure("E_GH");
        const release = JSON.parse(match[2] ?? "");
        if (!isObject(release) || release.tag_name !== tag || typeof release.draft !== "boolean" || typeof release.prerelease !== "boolean") return failure("E_GH");
        return success({ tagName: tag, draft: release.draft, prerelease: release.prerelease });
      } catch (error) {
        return caught(error, "E_GH");
      }
    },
    /** Reserve the exact source with an annotated tag; push only that single ref, never forced. @param {Prepared} prepared @returns {Promise<Result<null>>} */
    async pushTag(prepared) {
      try {
        const plan = publishable(prepared);
        const token = process.env.GH_TOKEN;
        if (token === undefined || token === "") return failure("E_GIT");
        const { tag } = plan.release;
        ensureLocalTag(tag, plan.request.sourceSha, encodeReservation(prepared));
        const header = `AUTHORIZATION: basic ${Buffer.from(`x-access-token:${token}`).toString("base64")}`;
        git(["push", repositoryUrl, `refs/tags/${tag}:refs/tags/${tag}`], "E_GIT", { GIT_CONFIG_COUNT: "1", GIT_CONFIG_KEY_0: "http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0: header });
        return success(null);
      } catch (error) {
        return caught(error, "E_GIT");
      }
    },
    /** Publish the exact handed-off tarball file on its validated channel. @param {Prepared} prepared @param {string} bundleDir @returns {Promise<Result<null>>} */
    async publishTarball(prepared, bundleDir) {
      try {
        const plan = publishable(prepared);
        /** @type {Record<string, string>} */ const env = {};
        for (const key of ["ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW_REF", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SERVER_URL", "GITHUB_API_URL"]) {
          const value = process.env[key];
          if (value !== undefined) env[key] = value;
        }
        const directory = resolve(bundleDir);
        const result = drivers.exec("npm", ["publish", join(directory, "package.tgz"), "--ignore-scripts", "--provenance", "--access", "public", "--registry", "https://registry.npmjs.org", "--tag", plan.release.npmTag], { cwd: directory, env, timeout: 600_000 });
        if (result.status !== 0 || result.signal !== null) return failure("E_REGISTRY");
        return success(null);
      } catch (error) {
        return caught(error, "E_REGISTRY");
      }
    },
    /** @param {Prepared} prepared @param {boolean} makeLatest @returns {Promise<Result<null>>} */
    async createRelease(prepared, makeLatest) {
      try {
        const { tag, version } = publishable(prepared).release;
        const prerelease = parseSemver(version)?.prerelease.length ? ["--prerelease"] : [];
        const argv = ["release", "create", tag, "--repo", "thunderock/thunderkit", "--verify-tag", "--target", prepared.request.sourceSha, "--title", tag, "--generate-notes", ...prerelease, `--latest=${makeLatest}`];
        const result = drivers.exec("gh", argv, { cwd, env: { GH_TOKEN: process.env.GH_TOKEN ?? "" } });
        if (result.status !== 0 || result.signal !== null) return failure("E_GH");
        return success(null);
      } catch (error) {
        return caught(error, "E_GH");
      }
    },
  });
}
