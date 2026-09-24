// @ts-check
import { parseSemver, validateNpmTag } from "./versions.mjs";

/** @typedef {"E_UNTRUSTED_CONTEXT"|"E_INVALID_VERSION"|"E_INVALID_NPM_TAG"|"E_NPM_TAG_WITHOUT_VERSION"|"E_VERSION_OVERFLOW"|"E_RECORD"|"E_STALE_SOURCE"|"E_VERSION_TAKEN"|"E_AMBIGUOUS_RESUME"|"E_TAG_NOT_ANCESTOR"|"E_RESUME_CHANNEL"|"E_NO_BASE"|"E_BASE_INCOMPLETE"|"E_ARTIFACT"|"E_CHANNEL_STATE"|"E_STALE_TARGET"|"E_CHANNEL_DRIFT"|"E_GH_CONFLICT"|"E_REGISTRY_INTEGRITY"|"E_STALE_PLAN"|"E_TOOLCHAIN"|"E_GIT"|"E_PACK"|"E_REGISTRY"|"E_GH"} ErrorCode */
/** @typedef {Readonly<{ok:false, error:Readonly<{code:ErrorCode, message:string}>}>} Failure */
/** @template T @typedef {Readonly<{ok:true, value:T}>|Failure} Result */
/** @typedef {Readonly<{repository:string, ref:string, event:"push"|"workflow_dispatch", sourceSha:string, runId:string, attempt:string, inputVersion:string, inputNpmTag:string}>} Request */

/** Freeze only owned data; callers retain ownership of their inputs. @param {unknown} value */
function freezeOwned(value) {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freezeOwned(child);
    Object.freeze(value);
  }
}

/** Copy a successful release value before recursively freezing it. @template T @param {T} value @returns {Result<T>} */
export function success(value) {
  const copy = structuredClone(value);
  freezeOwned(copy);
  return Object.freeze({ ok: true, value: copy });
}

/** Return bounded, nonsecret contract failure details. @param {ErrorCode} code @returns {Failure} */
export function failure(code) {
  return Object.freeze({ ok: false, error: Object.freeze({ code, message: "Release contract check failed" }) });
}

/** Check closed data records without invoking accessors. @template {string} K @param {unknown} value @param {readonly K[]} keys @returns {value is Record<K, unknown>} */
export function hasKeys(value, keys) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== null && prototype !== Object.prototype) return false;
  return Reflect.ownKeys(value).length === keys.length && keys.every((key) => {
    const field = Object.getOwnPropertyDescriptor(value, key);
    return field !== undefined && "value" in field && field.enumerable === true;
  });
}

/** @param {unknown} value @returns {value is string} */
export function isSha(value) {
  return typeof value === "string" && /^[a-f0-9]{40}$/.exec(value)?.[0] === value;
}

/** @param {unknown} value @returns {value is string} */
export function isPositiveDecimal(value) {
  return typeof value === "string" && /^[1-9][0-9]*$/.exec(value)?.[0] === value;
}

/** Decode a closed normalized request using the same input policy as workflow input. @param {unknown} value @returns {Result<Request>} */
export function decodeRequest(value) {
  if (!hasKeys(value, ["repository", "ref", "event", "sourceSha", "runId", "attempt", "inputVersion", "inputNpmTag"])) {
    return failure("E_UNTRUSTED_CONTEXT");
  }
  const { repository, ref, event, sourceSha, runId, attempt, inputVersion, inputNpmTag } = value;
  if (repository !== "thunderock/thunderkit" || ref !== "refs/heads/master"
      || (event !== "push" && event !== "workflow_dispatch")
      || !isSha(sourceSha) || !isPositiveDecimal(runId) || !isPositiveDecimal(attempt)) {
    return failure("E_UNTRUSTED_CONTEXT");
  }
  if (typeof inputVersion !== "string") return failure("E_INVALID_VERSION");
  if (typeof inputNpmTag !== "string") return failure("E_INVALID_NPM_TAG");
  const version = parseSemver(inputVersion);
  if (inputVersion !== "" && !version) return failure("E_INVALID_VERSION");
  if (inputVersion === "" && inputNpmTag !== "") return failure("E_NPM_TAG_WITHOUT_VERSION");
  if (inputNpmTag !== "" && !validateNpmTag(inputNpmTag).ok) return failure("E_INVALID_NPM_TAG");
  if (version?.prerelease.length && inputNpmTag === "latest") return failure("E_INVALID_NPM_TAG");
  switch (event) {
    case "push":
      if (inputVersion !== "" || inputNpmTag !== "") return failure("E_UNTRUSTED_CONTEXT");
      break;
    case "workflow_dispatch":
      break;
    default:
      return rejectVariant(event);
  }
  return success({ repository, ref, event, sourceSha, runId, attempt, inputVersion, inputNpmTag });
}

/** Read only workflow input keys; never enumerate or retain the environment. @param {Readonly<Record<string, string|undefined>>} raw @returns {Result<Request>} */
export function parseRequest(raw) {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)
      || raw.GITHUB_ACTIONS !== "true"
      || raw.GITHUB_WORKFLOW_REF !== "thunderock/thunderkit/.github/workflows/release-please.yml@refs/heads/master") {
    return failure("E_UNTRUSTED_CONTEXT");
  }
  return decodeRequest({
    repository: raw.GITHUB_REPOSITORY, ref: raw.GITHUB_REF, event: raw.GITHUB_EVENT_NAME,
    sourceSha: raw.GITHUB_SHA, runId: raw.GITHUB_RUN_ID, attempt: raw.GITHUB_RUN_ATTEMPT,
    inputVersion: raw.RELEASE_VERSION_INPUT === undefined ? "" : raw.RELEASE_VERSION_INPUT,
    inputNpmTag: raw.RELEASE_NPM_TAG_INPUT === undefined ? "" : raw.RELEASE_NPM_TAG_INPUT,
  });
}

/** Exhaustiveness guard that also fails closed for untyped callers. @param {never} value @returns {Failure} */
export function rejectVariant(value) {
  return failure("E_RECORD");
}
