// @ts-check
import { appendFileSync, lstatSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { decodeRequest, parseRequest, failure, success } from "./request.mjs";
import { selectCandidate, reconcile } from "./policy.mjs";
import { decodeReservation, decodePlan } from "./record.mjs";
import { checkWorkspace, prepareArtifact } from "./artifact.mjs";
import { createSystemTransport, exec, get } from "./io.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {import("./record.mjs").Candidate} Candidate */
/** @typedef {import("./record.mjs").Prepared} Prepared */
/** @typedef {import("./record.mjs").Skip} Skip */
/** @typedef {import("./policy.mjs").GitFacts} GitFacts */
/** @typedef {import("./policy.mjs").LiveFacts} LiveFacts */
/** @typedef {import("./policy.mjs").Target} Target */
/** @typedef {import("./artifact.mjs").Workspace} Workspace */
/** @typedef {import("./io.mjs").Transport} Transport */

/** Read the exact selected target, then its managed automatic base, from one registry snapshot. @param {Candidate} candidate @param {GitFacts} git @param {Transport} transport @returns {Promise<Result<LiveFacts>>} */
export async function readLive(candidate, git, transport) {
  const registry = await transport.readRegistry();
  if (!registry.ok) return registry;
  /** @param {string} version @param {string} tag @returns {Promise<Result<Target>>} */
  const readTarget = async (version, tag) => {
    const github = await transport.readRelease(tag);
    if (!github.ok) return github;
    return success({ version, tag, gitTag: git.tags.find((entry) => entry.name === tag) ?? null, npm: registry.value.versions[version] ?? null, github: github.value });
  };
  const target = await readTarget(candidate.version, candidate.tag);
  if (!target.ok) return target;
  /** @type {Target|null} */ let baseTarget = null;
  if (candidate.origin.mode === "auto" && target.value.gitTag === null && candidate.base !== null) {
    const baseTag = candidate.base.tag;
    const base = git.tags.find((entry) => entry.name === baseTag);
    if (base === undefined) return failure("E_STALE_PLAN");
    const reservation = decodeReservation(base.annotation, base);
    if (!reservation.ok) return reservation;
    if (reservation.value !== null) {
      const read = await readTarget(base.version, base.name);
      if (!read.ok) return read;
      baseTarget = read.value;
    }
  }
  return success({ git, registry: registry.value, target: target.value, baseTarget });
}

/** @param {Request} request @param {"no_commits"|"stale_source"} reason @returns {Skip} */
function skip(request, reason) { return { schema: 2, action: "skip", reason, request, release: null }; }

/** Plan one source-bound release: validate, read facts, select, prepare, reconcile, serialize; no remote writes. @param {Request} request @param {Workspace} workspace @param {Transport} transport @returns {Promise<Result<Prepared|Skip>>} */
export async function planRelease(request, workspace, transport) {
  const input = decodeRequest(request);
  if (!input.ok) return input;
  const paths = checkWorkspace(workspace);
  if (!paths.ok) return paths;
  const git = await transport.readGit(input.value);
  if (!git.ok) return git;
  const selection = selectCandidate(input.value, git.value);
  if (!selection.ok) return selection;
  /** @type {Prepared|Skip} */ let record;
  if (selection.value.kind === "skip") {
    record = skip(input.value, selection.value.reason);
  } else {
    const candidate = selection.value.kind === "new" ? selection.value.candidate : selection.value.reservation.release;
    const live = await readLive(candidate, git.value, transport);
    if (!live.ok) return live;
    const artifact = await prepareArtifact(input.value, selection.value, workspace);
    if (!artifact.ok) return artifact;
    const decision = reconcile(artifact.value, live.value);
    if (!decision.ok) return decision;
    switch (decision.value.reason) {
      case "ready":
        record = artifact.value;
        break;
      case "already_released":
        record = { ...artifact.value, action: "skip", reason: "already_released" };
        break;
      case "stale_source":
        record = skip(input.value, "stale_source");
        break;
    }
  }
  const decoded = decodePlan(JSON.stringify(record), input.value);
  if (!decoded.ok) return decoded;
  try {
    mkdirSync(workspace.bundleDir, { recursive: true, mode: 0o700 });
    if (lstatSync(workspace.bundleDir).isSymbolicLink()) return failure("E_ARTIFACT");
    writeFileSync(join(workspace.bundleDir, "release-plan.json"), `${JSON.stringify(decoded.value)}\n`, { flag: "wx", mode: 0o600 });
  } catch (error) {
    if (error instanceof Error) return failure("E_ARTIFACT");
    throw error;
  }
  return decoded;
}

/** Accept exactly one directory flag; record content never becomes a path or command. @param {readonly string[]} argv @param {string} flag @returns {Result<string>} */
export function cliDirectory(argv, flag) {
  const [name, path, ...rest] = argv;
  if (name !== flag || path === undefined || path === "" || path.startsWith("-") || rest.length !== 0) return failure("E_RECORD");
  return success(resolve(path));
}

/** @returns {Promise<Result<null>>} */
async function main() {
  const request = parseRequest(process.env);
  if (!request.ok) return request;
  const directory = cliDirectory(process.argv.slice(2), "--workspace");
  if (!directory.ok) return directory;
  const workspace = { checkoutDir: process.cwd(), stageDir: join(directory.value, "source"), bundleDir: join(directory.value, "bundle") };
  const result = await planRelease(request.value, workspace, createSystemTransport(workspace.checkoutDir, { exec, get }));
  if (!result.ok) return result;
  const recordSha256 = createHash("sha256").update(`${JSON.stringify(result.value)}\n`).digest("hex");
  const output = process.env.GITHUB_OUTPUT;
  if (output !== undefined && output !== "") appendFileSync(output, `action=${result.value.action}\nrecord_sha256=${recordSha256}\n`);
  return success(null);
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await main();
  if (!result.ok) {
    process.stderr.write(`${result.error.code}: release planning failed\n`);
    process.exitCode = 1;
  }
}
