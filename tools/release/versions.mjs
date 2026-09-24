// @ts-check
/** @typedef {Readonly<{raw:string, major:number, minor:number, patch:number, prerelease:readonly string[]}>} Semver */
/** @typedef {Readonly<{code:"E_INVALID_NPM_TAG", message:string}>} TagError */

/** Parse only canonical npm versions; numeric prerelease components remain strings. @param {unknown} text @returns {Semver|null} */
export function parseSemver(text) {
  if (typeof text !== "string" || text.length > 256) return null;
  const match = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(text);
  if (!match || match[0] !== text) return null;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  const patch = Number(match[3]);
  if (![major, minor, patch].every(Number.isSafeInteger)) return null;
  const prerelease = match[4] === undefined ? [] : match[4].split(".");
  if (prerelease.some((part) => /^0[0-9]+$/.test(part))) return null;
  return Object.freeze({ raw: text, major, minor, patch, prerelease: Object.freeze(prerelease) });
}

/** Compare parsed versions without rounding numeric prerelease identifiers. @param {Semver} a @param {Semver} b @returns {-1|0|1} */
export function compareSemver(a, b) {
  for (const key of /** @type {const} */ (["major", "minor", "patch"])) {
    if (a[key] !== b[key]) return a[key] < b[key] ? -1 : 1;
  }
  if (a.prerelease.length === 0 && b.prerelease.length !== 0) return 1;
  if (b.prerelease.length === 0 && a.prerelease.length !== 0) return -1;
  for (let i = 0; i < Math.max(a.prerelease.length, b.prerelease.length); i++) {
    const left = a.prerelease[i];
    const right = b.prerelease[i];
    if (left === right) continue;
    if (left === undefined) return -1;
    if (right === undefined) return 1;
    const leftNumeric = /^[0-9]+$/.test(left);
    const rightNumeric = /^[0-9]+$/.test(right);
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
    if (leftNumeric && left.length !== right.length) return left.length < right.length ? -1 : 1;
    return left < right ? -1 : 1;
  }
  return 0;
}

/** Validate the conservative project channel grammar, not npm's full tag grammar.
 * @param {unknown} text @returns {Readonly<{ok:true, value:string}>|Readonly<{ok:false, error:TagError}>}
 */
export function validateNpmTag(text) {
  if (typeof text === "string" && /^[a-uwyz][a-z0-9-]{0,63}$/.exec(text)?.[0] === text) {
    return Object.freeze({ ok: true, value: text });
  }
  return Object.freeze({ ok: false, error: Object.freeze({ code: "E_INVALID_NPM_TAG", message: "Invalid npm channel" }) });
}
