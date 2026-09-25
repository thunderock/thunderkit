// @ts-check
import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { exec } from "./io.mjs";
import { decodeRequest, failure, success } from "./request.mjs";
import { decodePlan } from "./record.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").ErrorCode} ErrorCode */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {import("./record.mjs").Prepared} Prepared */
/** @typedef {import("./record.mjs").Release} Release */
/** @typedef {import("./policy.mjs").Selection} Selection */
/** @typedef {Readonly<{checkoutDir:string, stageDir:string, bundleDir:string}>} Workspace */
/** @typedef {Readonly<{path:string, size:number, mode:number, sha256:string}>} Member */
/** @typedef {Readonly<{name:string, version:string, files:readonly Member[]}>} ArchiveEvidence */
/** @typedef {Readonly<{size:number, integrity:string, files:readonly Member[]}>} ArtifactEvidence */
/** @typedef {Readonly<{size:number, integrity:string}>} Digest */

const archiveTool = fileURLToPath(new URL("archive.py", import.meta.url));
const metadataFiles = new Set(["package.json", "package-lock.json", "npm-shrinkwrap.json"]);
export const toolchain = Object.freeze({ nodeMajor: /** @type {const} */ (24), npm: /** @type {const} */ ("11.19.1"), pythonMinor: /** @type {const} */ ("3.12") });
const placeholderIntegrity = `sha512-${Buffer.alloc(64).toString("base64")}`;

class ArtifactError extends Error {
  /** @param {ErrorCode} code */
  constructor(code) {
    super("Release artifact check failed");
    /** @readonly */ this.code = code;
  }
}
/** @param {ErrorCode} code @returns {never} */
function abort(code) { throw new ArtifactError(code); }
/** @param {unknown} error @param {ErrorCode} fallback */
function caught(error, fallback) {
  if (error instanceof ArtifactError) return failure(error.code);
  if (error instanceof Error) return failure(fallback);
  throw error;
}
/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
/** @param {string} program @param {readonly string[]} argv @param {string} cwd @param {ErrorCode} code */
function run(program, argv, cwd, code) {
  const result = exec(program, argv, { cwd });
  if (result.status !== 0 || result.signal !== null) abort(code);
  return result.stdout;
}
/** @param {string} path @returns {unknown} */
function readJson(path) { return JSON.parse(readFileSync(path, "utf8")); }
/** @param {string} path */
function emptyDirectory(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || readdirSync(path).length !== 0) abort("E_ARTIFACT");
}
/** Resolve existing ancestors before appending missing components, preserving symlink/parent traversal order. @param {string} path @returns {string} */
function physicalPath(path) {
  try {
    return realpathSync.native(path);
  } catch (error) {
    if (!(error instanceof Error) || !("code" in error) || error.code !== "ENOENT" || dirname(path) === path) throw error;
    if (lstatSync(path, { throwIfNoEntry: false })?.isSymbolicLink()) throw error;
    return join(physicalPath(dirname(path)), basename(path));
  }
}
/** Three distinct, non-nested directories, both lexically and through filesystem aliases. @param {Workspace} workspace @returns {Result<null>} */
export function checkWorkspace(workspace) {
  try {
    const paths = [workspace.checkoutDir, workspace.stageDir, workspace.bundleDir];
    for (const directories of [paths.map((path) => resolve(path)), paths.map(physicalPath)]) {
      for (const [index, left] of directories.entries()) {
        for (const right of directories.slice(index + 1)) {
          if ([relative(left, right), relative(right, left)].some((path) => !isAbsolute(path) && path.split(sep)[0] !== "..")) return failure("E_ARTIFACT");
        }
      }
    }
    return success(null);
  } catch (error) {
    return caught(error, "E_ARTIFACT");
  }
}
/** The package must publish as the public thunderkit CLI with no redirecting publication settings. @param {string} directory */
function packageIdentity(directory) {
  const pkg = readJson(join(directory, "package.json"));
  if (!isObject(pkg) || pkg.name !== "thunderkit" || typeof pkg.version !== "string" || pkg.private !== undefined
      || !isObject(pkg.repository) || pkg.repository.url !== "git+https://github.com/thunderock/thunderkit.git"
      || !isObject(pkg.bin) || Object.keys(pkg.bin).length !== 1 || pkg.bin.thunderkit !== "bin/thunderkit.js"
      || (pkg.publishConfig !== undefined && (!isObject(pkg.publishConfig) || Object.keys(pkg.publishConfig).length !== 0))) abort("E_ARTIFACT");
  return pkg;
}
/** Confirm the pinned release tools before any stamping or packing. @param {string} cwd @returns {Result<typeof toolchain>} */
export function checkToolchain(cwd) {
  try {
    if (process.versions.node.split(".")[0] !== "24" || run("npm", ["--version"], cwd, "E_TOOLCHAIN").trim() !== toolchain.npm
        || !/^Python 3\.12\.\d+\s*$/.test(run("python3", ["--version"], cwd, "E_TOOLCHAIN"))) return failure("E_TOOLCHAIN");
    return success(toolchain);
  } catch (error) {
    return caught(error, "E_TOOLCHAIN");
  }
}
/** @param {string} tarball @param {string} destination @returns {ArchiveEvidence} */
function extract(tarball, destination) {
  emptyDirectory(destination);
  const evidence = JSON.parse(run("python3", [archiveTool, tarball, destination], destination, "E_ARTIFACT"));
  if (!isObject(evidence) || typeof evidence.name !== "string" || typeof evidence.version !== "string" || !Array.isArray(evidence.files)) abort("E_ARTIFACT");
  /** @type {Member[]} */ const files = [];
  for (const file of evidence.files) {
    if (!isObject(file) || typeof file.path !== "string" || typeof file.size !== "number" || typeof file.mode !== "number" || typeof file.sha256 !== "string") abort("E_ARTIFACT");
    files.push({ path: file.path, size: file.size, mode: file.mode, sha256: file.sha256 });
  }
  return { name: evidence.name, version: evidence.version, files };
}
/** Materialize tracked source at exactly the requested commit, never the working copy. @param {Request} request @param {string} checkoutDir @param {string} destination */
function materializeSource(request, checkoutDir, destination) {
  if (run("git", ["rev-parse", "--verify", "HEAD^{commit}"], checkoutDir, "E_GIT").trim() !== request.sourceSha) abort("E_GIT");
  const scratch = mkdtempSync(join(tmpdir(), "release-source-"));
  try {
    const tar = join(scratch, "source.tar");
    run("git", ["archive", "--format=tar", `--output=${tar}`, request.sourceSha], checkoutDir, "E_GIT");
    const evidence = extract(tar, destination);
    packageIdentity(destination);
    if (evidence.files.some((file) => file.path === ".npmrc")) abort("E_ARTIFACT");
    return evidence;
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}
/** Independently derived payload allowlist over tracked source paths. @param {string} path @param {boolean} dependencies */
function payloadPath(path, dependencies) {
  if (/(^|\/)(\.[^/]*|node_modules|__pycache__)(\/|$)/.test(path) || /\.(?:pyc|pem|key|tgz|log)$/.test(path)) return false;
  return path.startsWith("skills/") || path.startsWith("bin/") || path === "NORTH_STAR.md" || path === "package.json"
    || (dependencies && path === "DEPENDENCIES.md") || path === "npm-shrinkwrap.json" || /^(?:readme|licen[cs]e)(?:\.[^/]*)?$/i.test(path);
}
/** Hash the exact bytes; npm output is never the only integrity proof. @param {string} path @returns {Digest} */
export function hashTarball(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size <= 0 || stat.size > 67_108_864) abort("E_ARTIFACT");
  const bytes = readFileSync(path);
  return Object.freeze({ size: bytes.length, integrity: `sha512-${createHash("sha512").update(bytes).digest("base64")}` });
}
/** @param {string} directory @param {string} version */
function checkStamped(directory, version) {
  if (packageIdentity(directory).version !== version) abort("E_ARTIFACT");
  for (const name of ["package-lock.json", "npm-shrinkwrap.json"]) {
    if (!readdirSync(directory).includes(name)) continue;
    const lock = readJson(join(directory, name));
    const root = isObject(lock) && isObject(lock.packages) && isObject(lock.packages[""]) ? lock.packages[""] : {};
    if (!isObject(lock) || lock.version !== version || (isObject(lock.packages) && root.version !== version)) abort("E_ARTIFACT");
  }
}
/** Only the stamped version may differ between tracked metadata and the packed copy. @param {string} sourceFile @param {string} packedFile @param {string} version */
function compareMetadata(sourceFile, packedFile, version) {
  const expected = readJson(sourceFile);
  if (!isObject(expected)) abort("E_ARTIFACT");
  expected.version = version;
  if (isObject(expected.packages) && isObject(expected.packages[""])) expected.packages[""].version = version;
  if (JSON.stringify(expected) !== JSON.stringify(readJson(packedFile))) abort("E_ARTIFACT");
}
/** Drive the extracted CLI outside the checkout with an empty PATH. @param {string} root @param {string} version @param {string} cwd */
function smoke(root, version, cwd) {
  /** @param {readonly string[]} args */
  const launch = (args) => exec(process.execPath, [join(root, "bin/thunderkit.js"), ...args], { cwd, env: { PATH: "" } });
  const shown = launch(["--version"]);
  if (shown.status !== 0 || shown.stdout !== `${version}\n` || shown.stderr !== "") abort("E_ARTIFACT");
  const help = launch(["help"]);
  if (help.status !== 0 || help.stderr !== "" || !help.stdout.startsWith(`thunderkit v${version} `) || !help.stdout.includes("thunderkit deps --json")) abort("E_ARTIFACT");
  const deps = launch(["deps", "--json"]);
  const manifest = readJson(join(root, "skills/references/dependencies.json"));
  const reported = deps.status === 0 && deps.stderr === "" ? JSON.parse(deps.stdout) : abort("E_ARTIFACT");
  if (!isObject(manifest) || !isObject(reported) || reported.schema_version !== 1 || JSON.stringify(reported.ecosystems) !== JSON.stringify(manifest.ecosystems)
      || JSON.stringify(reported.distribution_cli) !== JSON.stringify(manifest.distribution_cli)) abort("E_ARTIFACT");
  const invalid = launch(["deps", "--invalid"]);
  if (invalid.status !== 2 || invalid.stdout !== "" || invalid.stderr.trimEnd().split("\n").length !== 1 || invalid.stderr.trim() === "") abort("E_ARTIFACT");
}
/** Inspect handed-off bytes against exact committed source, without stamping or packing. @param {Prepared} prepared @param {Workspace} workspace @returns {Promise<Result<ArtifactEvidence>>} */
export async function verifyArtifact(prepared, workspace) {
  const scratch = mkdtempSync(join(tmpdir(), "release-verify-"));
  try {
    const decoded = decodePlan(JSON.stringify(prepared), prepared.request);
    if (!decoded.ok) return decoded;
    if (decoded.value.release === null) return failure("E_RECORD");
    const { request, release } = decoded.value;
    const paths = checkWorkspace(workspace);
    if (!paths.ok) return paths;
    const tools = checkToolchain(workspace.checkoutDir);
    if (!tools.ok) return tools;
    const tarball = join(resolve(workspace.bundleDir), "package.tgz");
    const digest = hashTarball(tarball);
    if (digest.size !== release.tarball.size || digest.integrity !== release.tarball.integrity) return failure("E_ARTIFACT");
    const original = join(scratch, "source");
    const inventory = materializeSource(request, workspace.checkoutDir, original);
    const unpacked = join(scratch, "unpacked");
    const evidence = extract(tarball, unpacked);
    const root = join(unpacked, "package");
    if (evidence.name !== "thunderkit" || evidence.version !== release.version || evidence.files.some((file) => !file.path.startsWith("package/"))) abort("E_ARTIFACT");
    checkStamped(root, release.version);
    const selected = packageIdentity(original).files;
    const dependencies = Array.isArray(selected) && selected.includes("DEPENDENCIES.md");
    const expected = inventory.files.filter((file) => payloadPath(file.path, dependencies));
    if (dependencies && !expected.some((file) => file.path === "DEPENDENCIES.md")) abort("E_ARTIFACT");
    if (JSON.stringify(expected.map((file) => `package/${file.path}`).sort()) !== JSON.stringify(evidence.files.map((file) => file.path).sort())) abort("E_ARTIFACT");
    for (const file of expected) {
      const actual = evidence.files.find((candidate) => candidate.path === `package/${file.path}`);
      if (actual === undefined || (file.path.startsWith("bin/") && (actual.mode & 0o111) !== (file.mode & 0o111))) abort("E_ARTIFACT");
      if (metadataFiles.has(file.path)) compareMetadata(join(original, file.path), join(root, file.path), release.version);
      else if (actual.sha256 !== file.sha256 || actual.size !== file.size) abort("E_ARTIFACT");
    }
    if (!expected.some((file) => file.path === "bin/thunderkit.js") || !expected.some((file) => file.path === "skills/references/dependencies.json")) abort("E_ARTIFACT");
    smoke(root, release.version, scratch);
    return success({ size: digest.size, integrity: digest.integrity, files: evidence.files });
  } catch (error) {
    return caught(error, "E_ARTIFACT");
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}
/** Real filesystem representability of the generated tag ref and npm filename, before any remote write. @param {string} tag @param {string} version */
function probeNames(tag, version) {
  const probe = mkdtempSync(join(tmpdir(), "release-name-"));
  try {
    for (const [name, code] of /** @type {const} */ ([[`${tag}.lock`, "E_GIT"], [`thunderkit-${version}.tgz`, "E_PACK"]])) {
      try {
        writeFileSync(join(probe, name), "", { flag: "wx" });
      } catch {
        abort(code);
      }
    }
  } finally {
    rmSync(probe, { recursive: true, force: true });
  }
}
/** Materialize, stamp and pack exact committed source into a fresh workspace, then verify the result. @param {Request} request @param {Selection} selection @param {Workspace} workspace @returns {Promise<Result<Prepared>>} */
export async function prepareArtifact(request, selection, workspace) {
  try {
    const input = decodeRequest(request);
    if (!input.ok) return input;
    if (selection.kind === "skip") return failure("E_RECORD");
    const candidate = selection.kind === "new" ? selection.candidate : selection.reservation.release;
    const provisional = { schema: 2, action: "publish", reason: "ready", request: input.value, release: { ...candidate, toolchain, tarball: { file: "package.tgz", size: 1, integrity: placeholderIntegrity } } };
    const validated = decodePlan(JSON.stringify(provisional), input.value);
    if (!validated.ok) return validated;
    const paths = checkWorkspace(workspace);
    if (!paths.ok) return paths;
    const tools = checkToolchain(workspace.checkoutDir);
    if (!tools.ok) return tools;
    emptyDirectory(workspace.bundleDir);
    run("git", ["check-ref-format", `refs/tags/${candidate.tag}`], workspace.checkoutDir, "E_GIT");
    probeNames(candidate.tag, candidate.version);
    materializeSource(input.value, workspace.checkoutDir, workspace.stageDir);
    run("npm", ["version", candidate.version, "--no-git-tag-version", "--ignore-scripts", "--allow-same-version"], workspace.stageDir, "E_PACK");
    checkStamped(workspace.stageDir, candidate.version);
    const packed = JSON.parse(run("npm", ["pack", "--offline", "--ignore-scripts", "--json", "--pack-destination", resolve(workspace.bundleDir)], workspace.stageDir, "E_PACK"));
    const filename = `thunderkit-${candidate.version}.tgz`;
    if (!Array.isArray(packed) || packed.length !== 1 || !isObject(packed[0]) || packed[0].filename !== filename) return failure("E_PACK");
    renameSync(join(workspace.bundleDir, filename), join(workspace.bundleDir, "package.tgz"));
    const digest = hashTarball(join(workspace.bundleDir, "package.tgz"));
    /** @type {Release} */ const release = selection.kind === "resume" ? selection.reservation.release : { ...candidate, toolchain: tools.value, tarball: { file: "package.tgz", ...digest } };
    /** @type {Prepared} */ const prepared = { schema: 2, action: "publish", reason: "ready", request: input.value, release };
    const verified = await verifyArtifact(prepared, workspace);
    return verified.ok ? success(prepared) : verified;
  } catch (error) {
    return caught(error, "E_ARTIFACT");
  }
}
