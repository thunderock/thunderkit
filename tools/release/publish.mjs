// @ts-check
import { lstatSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { decodeRequest, parseRequest, failure, success, rejectVariant } from "./request.mjs";
import { decodePlan } from "./record.mjs";
import { reconcile } from "./policy.mjs";
import { parseSemver, compareSemver } from "./versions.mjs";
import { verifyArtifact, hashTarball } from "./artifact.mjs";
import { readLive, cliDirectory } from "./plan.mjs";
import { createSystemTransport, exec, get } from "./io.mjs";

/** @template T @typedef {import("./request.mjs").Result<T>} Result */
/** @typedef {import("./request.mjs").Request} Request */
/** @typedef {import("./record.mjs").Prepared} Prepared */
/** @typedef {import("./policy.mjs").Step} Step */
/** @typedef {import("./policy.mjs").Decision} Decision */
/** @typedef {import("./policy.mjs").LiveFacts} LiveFacts */
/** @typedef {import("./io.mjs").Transport} Transport */
/** @typedef {Readonly<{directory:string, recordSha256:string}>} Bundle */
/** @typedef {Readonly<{status:"completed"|"already_released"|"stale_source", performedSteps:readonly Step[]}>} PublishOutcome */

/** The downloaded record must hash to the gate output before it is decoded against the current request. @param {Bundle} bundle @param {Request} request @returns {Result<Prepared>} */
function readBundle(bundle, request) {
  if (!/^[a-f0-9]{64}$/.test(bundle.recordSha256)) return failure("E_RECORD");
  const file = join(resolve(bundle.directory), "release-plan.json");
  try {
    const stat = lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1_048_576) return failure("E_RECORD");
    const bytes = readFileSync(file);
    if (createHash("sha256").update(bytes).digest("hex") !== bundle.recordSha256) return failure("E_RECORD");
    const record = decodePlan(bytes.toString("utf8"), request);
    if (!record.ok) return record;
    if (record.value.action !== "publish") return failure("E_RECORD");
    return success(record.value);
  } catch (error) {
    if (error instanceof Error) return failure("E_RECORD");
    throw error;
  }
}

/** Refresh every source-bound fact and reconcile this exact prepared target again. @param {Prepared} prepared @param {Transport} transport @returns {Promise<Result<Readonly<{decision:Decision, live:LiveFacts}>>>} */
async function observe(prepared, transport) {
  const git = await transport.readGit(prepared.request);
  if (!git.ok) return git;
  const live = await readLive(prepared.release, git.value, transport);
  if (!live.ok) return live;
  const decision = reconcile(prepared, live.value);
  if (!decision.ok) return decision;
  return success({ decision: decision.value, live: live.value });
}

/** True only for a stable latest-channel target that the registry already shows as latest and that trails no stable Git tag. @param {Prepared} prepared @param {Decision} decision @param {LiveFacts} live */
function makeLatest(prepared, decision, live) {
  const version = parseSemver(prepared.release.version);
  const base = live.git.base === null ? null : parseSemver(live.git.base.version);
  return version !== null && version.prerelease.length === 0 && prepared.release.npmTag === "latest" && decision.channel === "current"
    && live.registry.distTags.latest === version.raw && (base === null || compareSemver(version, base) >= 0);
}

/** @param {Prepared} prepared @param {Step} step @param {Bundle} bundle @param {Transport} transport @param {Decision} decision @param {LiveFacts} live @returns {Promise<Result<null>>} */
async function perform(prepared, step, bundle, transport, decision, live) {
  switch (step) {
    case "tag":
      return transport.pushTag(prepared);
    case "npm": {
      const digest = hashTarball(join(resolve(bundle.directory), "package.tgz"));
      if (digest.size !== prepared.release.tarball.size || digest.integrity !== prepared.release.tarball.integrity) return failure("E_ARTIFACT");
      return transport.publishTarball(prepared, resolve(bundle.directory));
    }
    case "github":
      return transport.createRelease(prepared, makeLatest(prepared, decision, live));
    default:
      return rejectVariant(step);
  }
}

/** Run the finite tag → npm → GitHub lifecycle once, refreshing facts after each accepted mutation and stopping at the first error. @param {Request} request @param {Bundle} bundle @param {Transport} transport @returns {Promise<Result<PublishOutcome>>} */
export async function publishRelease(request, bundle, transport) {
  const input = decodeRequest(request);
  if (!input.ok) return input;
  const record = readBundle(bundle, input.value);
  if (!record.ok) return record;
  const prepared = record.value;
  const scratch = mkdtempSync(join(tmpdir(), "release-publish-"));
  try {
    const artifact = await verifyArtifact(prepared, { checkoutDir: process.cwd(), stageDir: join(scratch, "source"), bundleDir: resolve(bundle.directory) });
    if (!artifact.ok) return artifact;
  } catch (error) {
    if (error instanceof Error) return failure("E_ARTIFACT");
    throw error;
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
  /** @type {Step[]} */ const performedSteps = [];
  let observed = await observe(prepared, transport);
  if (!observed.ok) return observed;
  while (observed.value.decision.action === "publish") {
    const step = observed.value.decision.steps[0];
    if (step === undefined) return failure("E_RECORD");
    let result;
    try {
      result = await perform(prepared, step, bundle, transport, observed.value.decision, observed.value.live);
    } catch (error) {
      if (error instanceof Error) return failure("E_ARTIFACT");
      throw error;
    }
    if (!result.ok) return result;
    performedSteps.push(step);
    observed = await observe(prepared, transport);
    if (!observed.ok) return observed;
    if (observed.value.decision.steps.some((remaining) => remaining === step)) return failure(step === "tag" ? "E_GIT" : step === "npm" ? "E_REGISTRY_INTEGRITY" : "E_GH");
  }
  const outcome = observed.value.decision;
  switch (outcome.reason) {
    case "already_released":
      return success({ status: performedSteps.length === 0 ? "already_released" : "completed", performedSteps });
    case "stale_source":
      return performedSteps.length === 0 ? success({ status: "stale_source", performedSteps }) : failure("E_STALE_SOURCE");
    default:
      return rejectVariant(outcome);
  }
}

/** @returns {Promise<Result<PublishOutcome>>} */
async function main() {
  const request = parseRequest(process.env);
  if (!request.ok) return request;
  const directory = cliDirectory(process.argv.slice(2), "--bundle");
  if (!directory.ok) return directory;
  const bundle = { directory: directory.value, recordSha256: process.env.RELEASE_RECORD_SHA256 ?? "" };
  return publishRelease(request.value, bundle, createSystemTransport(process.cwd(), { exec, get }));
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await main();
  if (result.ok) {
    process.stdout.write(`${JSON.stringify(result.value)}\n`);
  } else {
    process.stderr.write(`${result.error.code}: release publication failed\n`);
    process.exitCode = 1;
  }
}
