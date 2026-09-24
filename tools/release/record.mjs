// @ts-check
import { parseSemver, compareSemver, validateNpmTag } from "./versions.mjs";
import { decodeRequest, failure, success, hasKeys, isSha, isPositiveDecimal, rejectVariant } from "./request.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {"major"|"minor"|"patch"} Level */
/** @typedef {Readonly<{name:string, version:string, sha:string, objectSha:string, annotation:string}>} Tag */
/** @typedef {Readonly<{tag:string, version:string, sourceSha:string}>} Base */
/** @typedef {Readonly<{version:string, tag:string, npmTag:string, origin:Readonly<{mode:"auto"|"manual", runId:string}>, base:Base|null, bump:Level|null, commitCount:number}>} Candidate */
/** @typedef {Candidate & Readonly<{toolchain:Readonly<{nodeMajor:24, npm:"11.19.1", pythonMinor:"3.12"}>, tarball:Readonly<{file:"package.tgz", size:number, integrity:string}>}>} Release */
/** @typedef {Readonly<{schema:2, request:Request, release:Release}> & (Readonly<{action:"publish", reason:"ready"}>|Readonly<{action:"skip", reason:"already_released"}>)} Prepared */
/** @typedef {Readonly<{schema:2, action:"skip", reason:"no_commits"|"stale_source", request:Request, release:null}>} Skip */
/** @typedef {Readonly<{schema:"thunderkit.release/v1", repository:string, sourceSha:string, release:Release}>} Reservation */

/** @param {unknown} text @returns {Result<unknown>} */
function parseJson(text) {
  if (typeof text !== "string") return failure("E_RECORD");
  try {
    /** @type {unknown} */
    const value = JSON.parse(text);
    return Object.freeze({ ok: true, value });
  } catch (error) {
    if (error instanceof SyntaxError) return failure("E_RECORD");
    throw error;
  }
}

/** @param {unknown} value @returns {Result<Base|null>} */
function decodeBase(value) {
  if (value === null) return success(null);
  if (!hasKeys(value, ["tag", "version", "sourceSha"])) return failure("E_RECORD");
  const version = parseSemver(value.version);
  if (!version) return failure("E_RECORD");
  if (version.prerelease.length || value.tag !== `v${version.raw}` || !isSha(value.sourceSha)) return failure("E_RECORD");
  return success({ tag: `v${version.raw}`, version: version.raw, sourceSha: value.sourceSha });
}

/** @param {unknown} value @param {string} sourceSha @returns {Result<Release>} */
function decodeRelease(value, sourceSha) {
  if (!hasKeys(value, ["version", "tag", "npmTag", "origin", "base", "bump", "commitCount", "toolchain", "tarball"])) return failure("E_RECORD");
  const version = parseSemver(value.version);
  const channel = validateNpmTag(value.npmTag);
  const baseResult = decodeBase(value.base);
  if (!version || !channel.ok || !baseResult.ok || value.tag !== `v${version.raw}`) return failure("E_RECORD");
  const { origin, bump, commitCount, toolchain, tarball } = value;
  if (!hasKeys(origin, ["mode", "runId"]) || !isPositiveDecimal(origin.runId)) return failure("E_RECORD");
  if ((origin.mode !== "auto" && origin.mode !== "manual") || typeof commitCount !== "number"
      || !Number.isSafeInteger(commitCount) || commitCount < 0) return failure("E_RECORD");
  if (bump !== null && bump !== "major" && bump !== "minor" && bump !== "patch") return failure("E_RECORD");
  if (!hasKeys(toolchain, ["nodeMajor", "npm", "pythonMinor"]) || toolchain.nodeMajor !== 24
      || toolchain.npm !== "11.19.1" || toolchain.pythonMinor !== "3.12") return failure("E_RECORD");
  if (!hasKeys(tarball, ["file", "size", "integrity"]) || tarball.file !== "package.tgz"
      || typeof tarball.size !== "number" || !Number.isSafeInteger(tarball.size) || tarball.size <= 0
      || typeof tarball.integrity !== "string" || /^sha512-[A-Za-z0-9+/]{86}==$/.exec(tarball.integrity)?.[0] !== tarball.integrity) return failure("E_RECORD");
  const digest = tarball.integrity.slice(7);
  const bytes = atob(digest);
  if (bytes.length !== 64 || btoa(bytes) !== digest) return failure("E_RECORD");
  if (version.prerelease.length && channel.value === "latest") return failure("E_RECORD");
  const base = baseResult.value;
  if (base?.tag === value.tag) return failure("E_RECORD");
  const prior = base === null ? null : parseSemver(base.version);
  switch (origin.mode) {
    case "manual":
      if (bump !== null || commitCount !== 0 || (prior && compareSemver(version, prior) <= 0 && channel.value === "latest")) return failure("E_RECORD");
      break;
    case "auto":
      if (version.prerelease.length || channel.value !== "latest") return failure("E_RECORD");
      if (base === null) {
        if (bump !== null || commitCount !== 0) return failure("E_RECORD");
        break;
      }
      if (!prior || commitCount === 0 || base.sourceSha === sourceSha) return failure("E_RECORD");
      switch (bump) {
        case "major":
          if (prior.major === 0 || version.raw !== `${prior.major + 1}.0.0`) return failure("E_RECORD");
          break;
        case "minor":
          if (version.raw !== `${prior.major}.${prior.minor + 1}.0`) return failure("E_RECORD");
          break;
        case "patch":
          if (version.raw !== `${prior.major}.${prior.minor}.${prior.patch + 1}`) return failure("E_RECORD");
          break;
        case null:
          return failure("E_RECORD");
        default:
          return rejectVariant(bump);
      }
      break;
    default:
      return rejectVariant(origin.mode);
  }
  return success({
    version: version.raw, tag: `v${version.raw}`, npmTag: channel.value,
    origin: { mode: origin.mode, runId: origin.runId }, base, bump, commitCount,
    toolchain: { nodeMajor: 24, npm: "11.19.1", pythonMinor: "3.12" },
    tarball: { file: "package.tgz", size: tarball.size, integrity: tarball.integrity },
  });
}

/** Decode only owned annotations; malformed marked data is never legacy. @param {unknown} text @param {Tag} tag @returns {Result<Reservation|null>} */
export function decodeReservation(text, tag) {
  if (typeof text !== "string" || !hasKeys(tag, ["name", "version", "sha", "objectSha", "annotation"])
      || !parseSemver(tag.version) || tag.name !== `v${tag.version}` || !isSha(tag.sha)
      || !isSha(tag.objectSha) || tag.annotation !== text) return failure("E_RECORD");
  // An escaped marker is still owned even when the surrounding JSON is malformed.
  const markerText = text.replace(/\\u[0-9a-f]{4}/gi, (token) => String.fromCharCode(Number.parseInt(token.slice(2), 16)));
  if (!markerText.includes("thunderkit.release")) return success(null);
  if (tag.objectSha === tag.sha) return failure("E_RECORD");
  const decoded = parseJson(text);
  if (!decoded.ok) return decoded;
  const value = decoded.value;
  if (!hasKeys(value, ["schema", "repository", "sourceSha", "release"]) || value.schema !== "thunderkit.release/v1"
      || value.repository !== "thunderock/thunderkit" || value.sourceSha !== tag.sha) return failure("E_RECORD");
  const release = decodeRelease(value.release, tag.sha);
  if (!release.ok) return release;
  if (release.value.version !== tag.version || release.value.tag !== tag.name) return failure("E_RECORD");
  return success({ schema: "thunderkit.release/v1", repository: value.repository, sourceSha: tag.sha, release: release.value });
}

/** Decode a same-run schema2 handoff; an older gate attempt may be reused. @param {unknown} text @param {Request} request @returns {Result<Prepared|Skip>} */
export function decodePlan(text, request) {
  const current = decodeRequest(request);
  if (!current.ok) return current;
  const decoded = parseJson(text);
  if (!decoded.ok) return decoded;
  const value = decoded.value;
  if (!hasKeys(value, ["schema", "action", "reason", "request", "release"]) || value.schema !== 2) return failure("E_RECORD");
  const stored = decodeRequest(value.request);
  if (!stored.ok) return failure("E_RECORD");
  for (const key of /** @type {const} */ (["repository", "ref", "event", "sourceSha", "runId", "inputVersion", "inputNpmTag"])) {
    if (stored.value[key] !== current.value[key]) return failure("E_RECORD");
  }
  const before = stored.value.attempt;
  const now = current.value.attempt;
  if (before.length > now.length || (before.length === now.length && before > now)) return failure("E_RECORD");
  switch (value.reason) {
    case "no_commits":
    case "stale_source":
      if (value.action !== "skip" || value.release !== null || stored.value.inputVersion !== "") return failure("E_RECORD");
      return success({ schema: 2, action: "skip", reason: value.reason, request: stored.value, release: null });
    case "ready":
    case "already_released":
      break;
    default:
      return failure("E_RECORD");
  }
  const release = decodeRelease(value.release, stored.value.sourceSha);
  if (!release.ok) return release;
  if (stored.value.inputVersion === "" && (release.value.version.includes("-")
      || (release.value.origin.runId === stored.value.runId && release.value.origin.mode !== "auto"))) return failure("E_RECORD");
  if (stored.value.inputVersion !== "" && stored.value.inputVersion !== release.value.version) return failure("E_RECORD");
  if (stored.value.inputNpmTag !== "" && stored.value.inputNpmTag !== release.value.npmTag) return failure("E_RECORD");
  switch (value.action) {
    case "publish":
      if (value.reason !== "ready") return failure("E_RECORD");
      return success({ schema: 2, action: "publish", reason: "ready", request: stored.value, release: release.value });
    case "skip":
      if (value.reason !== "already_released") return failure("E_RECORD");
      return success({ schema: 2, action: "skip", reason: "already_released", request: stored.value, release: release.value });
    default:
      return failure("E_RECORD");
  }
}

class RecordEncodingError extends TypeError {
  /** @readonly */ code = "E_RECORD";
  constructor() {
    super("Invalid prepared release");
  }
}

/** Encode the immutable tag reservation in canonical field order. @param {Prepared} prepared @returns {string} */
export function encodeReservation(prepared) {
  const decoded = decodePlan(JSON.stringify(prepared), prepared.request);
  if (!decoded.ok || decoded.value.release === null) throw new RecordEncodingError();
  const { request, release } = decoded.value;
  return JSON.stringify({ schema: "thunderkit.release/v1", repository: request.repository, sourceSha: request.sourceSha, release });
}
