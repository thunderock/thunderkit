// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { createSystemTransport, exec, get, repositoryUrl, registryUrl } from "../tools/release/io.mjs";
import { encodeReservation } from "../tools/release/record.mjs";
import { releaseFacts } from "./helpers/release_facts.mjs";
import { createFixture, destroyFixture, git, isolated, journalOf, manual, moveSource, commit, tag, systemDrivers } from "./helpers/release_workspace.mjs";

/** @typedef {import("./helpers/release_workspace.mjs").Fixture} Fixture */
/** @type {Fixture[]} */ const fixtures = [];
after(() => fixtures.forEach(destroyFixture));
function fixture() { const created = createFixture(); fixtures.push(created); return created; }
/** @param {Fixture} f */
function transportFor(f) {
  const system = systemDrivers();
  return { ...system, transport: createSystemTransport(f.checkoutDir, system.drivers) };
}
/** A schema-2 record bound to the fixture commit; the tarball fields are shape-valid placeholders for write-path tests. @param {Fixture} f @param {string} version @param {string} channel */
function preparedFor(f, version, channel) {
  manual(f, version, channel);
  const { release } = releaseFacts();
  const candidate = { version, tag: `v${version}`, npmTag: channel, origin: { mode: /** @type {const} */ ("manual"), runId: f.request.runId }, base: null, bump: null, commitCount: 0 };
  return { schema: /** @type {const} */ (2), action: /** @type {const} */ ("publish"), reason: /** @type {const} */ ("ready"), request: { ...f.request }, release: { ...candidate, toolchain: release.toolchain, tarball: release.tarball } };
}
/** @param {string} status @param {string} body */
const ghResponse = (status, body) => ({ status: status === "200" ? 0 : 1, signal: null, stdout: `HTTP/2.0 ${status} X\r\nX-Header: 1\r\n\r\n${body}`, stderr: "" });

test("readGit reports peeled tags, the SemVer-highest base, merge-free commits and the committed package", async () => {
  const f = fixture();
  const first = f.sha;
  const reservation = JSON.stringify({ schema: "thunderkit.release/v1", note: "not a real record" });
  tag(f, "v0.1.0", "legacy annotated", first);
  const second = commit(f, "feat: add feature", "BREAKING CHANGE: api");
  tag(f, "v0.2.0", reservation, second);
  tag(f, "v0.1.5", null, second);
  tag(f, "v1.0.0-beta.1", null, second);
  tag(f, "unrelated", null, second);
  git(f.checkoutDir, ["checkout", "--quiet", "-b", "side"]);
  writeFileSync(join(f.checkoutDir, "NORTH_STAR.md"), "side\n");
  commit(f, "chore: side work");
  git(f.checkoutDir, ["checkout", "--quiet", "master"]);
  git(f.checkoutDir, ["merge", "--quiet", "--no-ff", "-m", "Merge side", "side"]);
  const head = moveSource(f, git(f.checkoutDir, ["rev-parse", "HEAD"]));
  writeFileSync(join(f.checkoutDir, "package.json"), "{\"name\":\"dirty\"}");
  const { transport, calls } = transportFor(f);
  const facts = await isolated(f, () => transport.readGit(f.request));
  assert.ok(facts.ok, JSON.stringify(facts));
  assert.equal(facts.value.headSha, head);
  assert.equal(facts.value.masterSha, head);
  assert.equal(facts.value.sourceOnMaster, true);
  assert.deepEqual(facts.value.sourcePackage, { name: "thunderkit", version: "0.1.1", repositoryUrl: "git+https://github.com/thunderock/thunderkit.git" });
  assert.deepEqual(facts.value.tags.map((entry) => entry.name).sort(), ["v0.1.0", "v0.1.5", "v0.2.0", "v1.0.0-beta.1"]);
  const base = facts.value.base;
  assert.ok(base !== null && base.name === "v0.2.0" && base.sha === second && base.objectSha !== second && base.annotation === reservation);
  assert.equal(facts.value.baseRelation, "ancestor");
  const lightweight = facts.value.tags.find((entry) => entry.name === "v0.1.5");
  assert.ok(lightweight !== undefined && lightweight.objectSha === lightweight.sha && lightweight.annotation === "");
  assert.deepEqual(facts.value.commits.map((entry) => entry.subject), ["chore: side work"]);
  assert.ok(facts.value.commits.every((entry) => !entry.subject.startsWith("Merge")));
  assert.equal(git(f.checkoutDir, ["for-each-ref", "refs/release-read"]), "");
  const fetch = calls.find((call) => call.program === "git" && call.argv[0] === "fetch");
  assert.ok(fetch !== undefined && fetch.argv.includes(repositoryUrl) && fetch.argv.includes("--no-tags"));
  assert.ok(journalOf(f).every((entry) => entry.allowed));
});

test("readGit distinguishes a diverged base from a stale detached source, and refuses a foreign HEAD", async () => {
  const f = fixture();
  const older = f.sha;
  tag(f, "v0.1.1", null, older);
  git(f.checkoutDir, ["checkout", "--quiet", "-b", "side"]);
  const side = commit(f, "feat: divergent");
  tag(f, "v0.2.0", null, side);
  git(f.checkoutDir, ["checkout", "--quiet", "master"]);
  const newer = commit(f, "fix: master advance");
  const { transport } = transportFor(f);
  const diverged = await isolated(f, () => transport.readGit(f.request));
  assert.ok(diverged.ok);
  assert.equal(diverged.value.baseRelation, "diverged");
  assert.equal(diverged.value.base?.name, "v0.2.0");
  git(f.checkoutDir, ["checkout", "--quiet", older]);
  moveSource(f, older);
  const stale = await isolated(f, () => transport.readGit(f.request));
  assert.ok(stale.ok);
  assert.equal(stale.value.headSha, older);
  assert.equal(stale.value.masterSha, newer);
  assert.equal(stale.value.sourceOnMaster, true);
  assert.equal(stale.value.baseRelation, "descendant");
  moveSource(f, newer);
  const foreign = await isolated(f, () => transport.readGit(f.request));
  assert.equal(foreign.ok ? null : foreign.error.code, "E_UNTRUSTED_CONTEXT");
  const invalid = await isolated(f, () => transport.readGit({ ...f.request, repository: "someone/else" }));
  assert.equal(invalid.ok ? null : invalid.error.code, "E_UNTRUSTED_CONTEXT");
});

test("readRegistry decodes the packument by exact keys and fails on every non-404 anomaly", async () => {
  const f = fixture();
  const { transport, hooks, calls } = transportFor(f);
  const document = { name: "thunderkit", "dist-tags": { latest: "0.1.1", next: "0.2.0-rc.1" }, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", dist: { integrity: "sha512-x" } }, "0.2.0-rc.1": { name: "thunderkit", version: "0.2.0-rc.1", dist: {} } } };
  hooks.get = async () => ({ status: 200, body: JSON.stringify(document) });
  const present = await transport.readRegistry();
  assert.ok(present.ok);
  assert.deepEqual(present.value, { exists: true, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", integrity: "sha512-x" }, "0.2.0-rc.1": { name: "thunderkit", version: "0.2.0-rc.1", integrity: null } }, distTags: { latest: "0.1.1", next: "0.2.0-rc.1" } });
  assert.deepEqual(calls.map((call) => call.argv), [[registryUrl]]);
  hooks.get = async () => ({ status: 404, body: "{}" });
  assert.deepEqual(await transport.readRegistry(), { ok: true, value: { exists: false, versions: {}, distTags: {} } });
  /** @type {ReadonlyArray<[number, string, string]>} */ const anomalies = [
    [500, "{}", "E_REGISTRY"], [401, "{}", "E_REGISTRY"], [200, "{not json", "E_REGISTRY"], [200, JSON.stringify({ ...document, name: "other" }), "E_REGISTRY"],
    [200, JSON.stringify({ ...document, versions: { "0.1.1": { name: "thunderkit", version: "0.1.2" } } }), "E_REGISTRY"],
    [200, JSON.stringify({ ...document, versions: { "1.x": { name: "thunderkit", version: "1.x" } } }), "E_REGISTRY"],
    [200, JSON.stringify({ ...document, "dist-tags": { latest: 7 } }), "E_REGISTRY"], [200, JSON.stringify({ ...document, "dist-tags": { latest: "9.9.9" } }), "E_CHANNEL_STATE"],
    [200, JSON.stringify({ ...document, versions: { "0.1.1": { name: "thunderkit", version: "0.1.1", dist: { integrity: 5 } } } }), "E_REGISTRY"], [301, "", "E_REGISTRY"],
  ];
  for (const [status, body, code] of anomalies) {
    hooks.get = async () => ({ status, body });
    const result = await transport.readRegistry();
    assert.equal(result.ok ? null : result.error.code, code, body);
  }
  hooks.get = async () => { throw new Error("socket hang up"); };
  const thrown = await transport.readRegistry();
  assert.equal(thrown.ok ? null : thrown.error.code, "E_REGISTRY");
});

test("readRelease treats only a typed HTTP 404 as absence and scopes the ephemeral token to gh", async () => {
  const f = fixture();
  const { transport, hooks, calls } = transportFor(f);
  process.env.GH_TOKEN = "ghs_fixture";
  try {
    assert.deepEqual(await transport.readRelease("v0.1.2"), { ok: true, value: null });
    hooks.gh = () => ghResponse("200", JSON.stringify({ tag_name: "v0.1.2", draft: false, prerelease: false, target_commitish: "master" }));
    assert.deepEqual(await transport.readRelease("v0.1.2"), { ok: true, value: { tagName: "v0.1.2", draft: false, prerelease: false } });
    hooks.gh = () => ghResponse("200", JSON.stringify({ tag_name: "v0.1.2", draft: true, prerelease: true }));
    assert.deepEqual(await transport.readRelease("v0.1.2"), { ok: true, value: { tagName: "v0.1.2", draft: true, prerelease: true } });
    const call = calls.at(-1);
    assert.ok(call !== undefined);
    assert.deepEqual(call.argv, ["api", "--include", "--method", "GET", "repos/thunderock/thunderkit/releases/tags/v0.1.2"]);
    assert.deepEqual(call.env, { GH_TOKEN: "ghs_fixture" });
    /** @type {ReadonlyArray<[import("../tools/release/io.mjs").ExecResult, string]>} */ const failures = [
      [ghResponse("200", JSON.stringify({ tag_name: "v0.1.3", draft: false, prerelease: false })), "E_GH"], [ghResponse("500", "{\"message\":\"Not Found\"}"), "E_GH"], [ghResponse("401", "{}"), "E_GH"],
      [ghResponse("200", "{oops"), "E_GH"], [{ ...ghResponse("200", "{}"), status: 1 }, "E_GH"], [{ status: 1, signal: null, stdout: "gh: Not Found (HTTP 404)\n", stderr: "" }, "E_GH"],
      [{ status: null, signal: "SIGKILL", stdout: "", stderr: "" }, "E_GH"], [ghResponse("200", JSON.stringify({ tag_name: "v0.1.2", draft: "no", prerelease: false })), "E_GH"],
    ];
    for (const [response, code] of failures) {
      hooks.gh = () => response;
      const result = await transport.readRelease("v0.1.2");
      assert.equal(result.ok ? null : result.error.code, code, response.stdout);
    }
    assert.equal((await transport.readRelease("0.1.2")).ok, false);
  } finally {
    delete process.env.GH_TOKEN;
  }
});

test("pushTag writes the bot-identified reservation once, pushes only that ref with header-scoped auth and reuses an identical retained tag on retry", async () => {
  const f = fixture();
  const prepared = preparedFor(f, "0.1.2", "latest");
  const { transport, hooks, calls } = transportFor(f);
  delete process.env.GH_TOKEN;
  const unauthenticated = await isolated(f, () => transport.pushTag(prepared));
  assert.equal(unauthenticated.ok ? null : unauthenticated.error.code, "E_GIT");
  assert.equal(calls.length, 0);
  const token = { GH_TOKEN: "ghs_fixture" };
  {
    hooks.push = () => ({ status: 128, signal: null, stdout: "", stderr: "fatal: unable to access\n" });
    const interrupted = await isolated(f, () => transport.pushTag(prepared), token);
    assert.equal(interrupted.ok ? null : interrupted.error.code, "E_GIT");
    const object = git(f.checkoutDir, ["cat-file", "tag", "refs/tags/v0.1.2"]);
    assert.ok(object.includes("\ntagger github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com> "));
    assert.equal(object.slice(object.indexOf("\n\n") + 2), encodeReservation(prepared));
    assert.equal(git(f.checkoutDir, ["rev-parse", "refs/tags/v0.1.2^{commit}"]), f.sha);
    const pushes = () => calls.filter((call) => call.program === "git" && call.argv[0] === "push");
    const first = pushes()[0];
    assert.ok(first !== undefined);
    assert.deepEqual(first.argv, ["push", repositoryUrl, "refs/tags/v0.1.2:refs/tags/v0.1.2"]);
    assert.deepEqual(Object.keys(first.env ?? {}), ["GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"]);
    assert.equal(first.env?.GIT_CONFIG_KEY_0, "http.https://github.com/.extraheader");
    assert.equal(first.env?.GIT_CONFIG_VALUE_0, `AUTHORIZATION: basic ${Buffer.from("x-access-token:ghs_fixture").toString("base64")}`);
    assert.ok(!JSON.stringify(calls.map((call) => call.argv)).includes("ghs_fixture"));
    hooks.push = () => ({ status: 0, signal: null, stdout: "", stderr: "" });
    const retried = await isolated(f, () => transport.pushTag(prepared), token);
    assert.deepEqual(retried, { ok: true, value: null });
    assert.equal(calls.filter((call) => call.program === "git" && call.argv.includes("tag") && call.argv.includes("-a")).length, 1);
    assert.equal(pushes().length, 2);
    assert.ok(calls.every((call) => !call.argv.includes("--force") && !call.argv.some((arg) => arg.startsWith("+"))));
    const moved = preparedFor(f, "0.1.2", "next");
    const conflict = await isolated(f, () => transport.pushTag(moved), token);
    assert.equal(conflict.ok ? null : conflict.error.code, "E_GIT");
    assert.equal(pushes().length, 2);
    const skip = { ...prepared, action: /** @type {const} */ ("skip"), reason: /** @type {const} */ ("already_released") };
    assert.equal((await isolated(f, () => transport.pushTag(skip), token)).ok, false);
  }
});

test("publishTarball and createRelease use exact explicit argv with only the intended environment", async () => {
  const f = fixture();
  const { transport, hooks, calls } = transportFor(f);
  // A GitHub runner already exports provenance variables; hide them so the forwarded set is exact.
  const provenanceKeys = ["ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW_REF", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SERVER_URL", "GITHUB_API_URL"];
  const saved = Object.fromEntries(provenanceKeys.filter((key) => key in process.env).map((key) => [key, process.env[key]]));
  for (const key of provenanceKeys) delete process.env[key];
  process.env.GH_TOKEN = "ghs_fixture";
  process.env.NPM_TOKEN = "npm_secret";
  process.env.ACTIONS_ID_TOKEN_REQUEST_URL = "https://token.example";
  try {
    const stable = preparedFor(f, "0.1.2", "latest");
    assert.deepEqual(await transport.publishTarball(stable, f.workspace.bundleDir), { ok: true, value: null });
    const publish = calls.at(-1);
    assert.ok(publish !== undefined && publish.program === "npm");
    assert.deepEqual(publish.argv, ["publish", join(f.workspace.bundleDir, "package.tgz"), "--ignore-scripts", "--provenance", "--access", "public", "--registry", "https://registry.npmjs.org", "--tag", "latest"]);
    assert.deepEqual(publish.env, { ACTIONS_ID_TOKEN_REQUEST_URL: "https://token.example" });
    hooks.publish = () => ({ status: 1, signal: null, stdout: "", stderr: "E403\n" });
    const rejected = await transport.publishTarball(stable, f.workspace.bundleDir);
    assert.equal(rejected.ok ? null : rejected.error.code, "E_REGISTRY");
    hooks.gh = () => ({ status: 0, signal: null, stdout: "https://github.com/thunderock/thunderkit/releases/tag/v0.1.2\n", stderr: "" });
    assert.deepEqual(await transport.createRelease(stable, true), { ok: true, value: null });
    const release = calls.at(-1);
    assert.ok(release !== undefined);
    assert.deepEqual(release.argv, ["release", "create", "v0.1.2", "--repo", "thunderock/thunderkit", "--verify-tag", "--target", f.sha, "--title", "v0.1.2", "--generate-notes", "--latest=true"]);
    assert.deepEqual(release.env, { GH_TOKEN: "ghs_fixture" });
    await transport.createRelease(preparedFor(f, "1.0.0-beta.1", "next"), false);
    assert.deepEqual(calls.at(-1)?.argv.slice(-2), ["--prerelease", "--latest=false"]);
    hooks.gh = () => ({ status: 1, signal: null, stdout: "", stderr: "HTTP 422\n" });
    const failed = await transport.createRelease(stable, false);
    assert.equal(failed.ok ? null : failed.error.code, "E_GH");
  } finally {
    delete process.env.GH_TOKEN;
    delete process.env.NPM_TOKEN;
    delete process.env.ACTIONS_ID_TOKEN_REQUEST_URL;
    Object.assign(process.env, saved);
  }
});

test("exec isolates children from inherited secrets and configuration and bounds their runtime", async () => {
  process.env.GH_TOKEN = "leak";
  try {
    const result = exec(process.execPath, ["-e", "process.stdout.write(JSON.stringify({ token: process.env.GH_TOKEN ?? null, home: process.env.HOME, npmrc: process.env.NPM_CONFIG_USERCONFIG, node: process.env.NODE_OPTIONS ?? null }))"], { cwd: process.cwd() });
    assert.equal(result.status, 0, result.stderr);
    const seen = JSON.parse(result.stdout);
    assert.equal(seen.token, null);
    assert.equal(seen.node, null);
    assert.ok(seen.home !== process.env.HOME && seen.npmrc.startsWith(seen.home));
  } finally {
    delete process.env.GH_TOKEN;
  }
  const slow = exec(process.execPath, ["-e", "setTimeout(() => {}, 10000)"], { cwd: process.cwd(), timeout: 500 });
  assert.equal(slow.status, null);
  assert.equal(slow.signal, "SIGKILL");
  const missing = exec("release-program-that-does-not-exist", [], { cwd: process.cwd() });
  assert.equal(missing.status, null);
  await assert.rejects(get("https://example.com/thunderkit"), /rejected/);
  await assert.rejects(get("http://registry.npmjs.org/thunderkit"), /rejected/);
  await assert.rejects(get("https://user:pw@registry.npmjs.org/thunderkit"), /rejected/);
});
