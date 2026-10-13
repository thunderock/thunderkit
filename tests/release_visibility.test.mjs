// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { planRelease } from "../tools/release/plan.mjs";
import { publishRelease } from "../tools/release/publish.mjs";
import { Remote } from "./helpers/release_remote.mjs";
import { bundleOf, createFixture, destroyFixture, isolated } from "./helpers/release_workspace.mjs";

/** @type {import("./helpers/release_workspace.mjs").Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));

/** A successful npm upload whose exact metadata remains hidden during scanning. */
async function pendingUpload() {
  const f = createFixture();
  fixtures.push(f);
  const remote = new Remote(f.sha, "0.1.1");
  const plan = await isolated(f, () => planRelease(f.request, f.workspace, remote));
  assert.ok(plan.ok && plan.value.action === "publish" && plan.value.release !== null);
  const original = structuredClone(remote.registry);
  let accepted = structuredClone(original);
  remote.onAccept = (step) => {
    if (step === "npm") {
      accepted = structuredClone(remote.registry);
      remote.registry = structuredClone(original);
    }
  };
  let elapsed = 0;
  /** @type {number[]} */ const delays = [];
  /** @type {(attempt:number) => void} */ let onWait = () => {};
  const clock = {
    now: () => elapsed,
    /** @param {number} ms */
    sleep: async (ms) => { delays.push(ms); elapsed += ms; onWait(delays.length); },
  };
  return {
    f, remote, delays, clock, prepared: plan.value,
    reveal: () => { remote.registry = structuredClone(accepted); },
    /** @param {(attempt:number) => void} callback */
    onWait: (callback) => { onWait = callback; },
    publish: () => isolated(f, () => publishRelease(f.request, bundleOf(f), remote, clock)),
  };
}

test("an accepted npm upload waits for scan visibility without republishing or creating GitHub early", async () => {
  const run = await pendingUpload();
  run.onWait((attempt) => {
    assert.deepEqual(run.remote.accepted, ["tag", "npm"]);
    assert.equal(run.remote.releases["v0.1.1"], undefined);
    if (attempt === 3) run.reveal();
  });
  assert.deepEqual(await run.publish(), { ok: true, value: { status: "completed", performedSteps: ["tag", "npm", "github"] } });
  assert.deepEqual(run.delays, [30_000, 30_000, 30_000]);
  assert.deepEqual(run.remote.calls.filter((call) => call.startsWith("write:")), ["write:tag", "write:npm", "write:github"]);
  assert.equal(run.remote.registry.versions["0.1.1"]?.integrity, run.prepared.release.tarball.integrity);
});

test("a scan that never becomes visible stops after a bounded wait with a distinct pending error", async () => {
  const run = await pendingUpload();
  const result = await run.publish();
  assert.equal(result.ok ? null : result.error.code, "E_REGISTRY_PENDING");
  assert.equal(run.clock.now(), 30 * 60_000);
  assert.equal(run.delays.length, 60);
  assert.deepEqual(run.remote.calls.filter((call) => call.startsWith("write:")), ["write:tag", "write:npm"]);
  run.reveal();
  assert.deepEqual(await run.publish(), { ok: true, value: { status: "completed", performedSteps: ["github"] } });
  assert.equal(run.delays.length, 60, "visible retries do not wait or upload again");
});

/** @type {ReadonlyArray<[string, (run:Awaited<ReturnType<typeof pendingUpload>>)=>void, string]>} */
const conflicts = [
  ["foreign integrity after visibility", (run) => {
    run.reveal();
    run.remote.registry.versions["0.1.1"].integrity = "sha512-foreign";
  }, "E_REGISTRY_INTEGRITY"],
  ["missing integrity after visibility", (run) => {
    run.reveal();
    run.remote.registry.versions["0.1.1"].integrity = null;
  }, "E_REGISTRY_INTEGRITY"],
  ["registry read failure", (run) => { run.remote.readError = "E_REGISTRY"; }, "E_REGISTRY"],
  ["missing latest after visibility", (run) => { run.reveal(); delete run.remote.registry.distTags.latest; }, "E_CHANNEL_DRIFT"],
  ["a replaced reservation", (run) => {
    const tag = run.remote.git.tags[0] ?? assert.fail("Expected reserved tag");
    tag.annotation = tag.annotation.replace(run.prepared.release.origin.runId, "7");
  }, "E_VERSION_TAKEN"],
  ["a newer stable tag", (run) => {
    const sha = "f".repeat(40);
    run.remote.git.tags.push({ name: "v0.2.0", version: "0.2.0", sha, objectSha: sha, annotation: "" });
    run.remote.refreshBase();
    run.remote.git.baseRelation = "descendant";
  }, "E_STALE_TARGET"],
];
for (const [name, arrange, code] of conflicts) {
  test(`availability waiting stops immediately on ${name}`, async () => {
    const run = await pendingUpload();
    run.onWait(() => arrange(run));
    const result = await run.publish();
    assert.equal(result.ok ? null : result.error.code, code);
    assert.equal(run.delays.length, 1);
    assert.deepEqual(run.remote.calls.filter((call) => call.startsWith("write:")), ["write:tag", "write:npm"]);
  });
}

test("a rejected npm upload is not turned into a visibility wait or repeated write", async () => {
  const run = await pendingUpload();
  run.remote.fault = { step: "npm", phase: "before" };
  const result = await run.publish();
  assert.equal(result.ok ? null : result.error.code, "E_REGISTRY");
  assert.deepEqual(run.delays, []);
  assert.deepEqual(run.remote.accepted, ["tag"]);
});
