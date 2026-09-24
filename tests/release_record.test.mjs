import { test } from "node:test";
import assert from "node:assert/strict";
import * as records from "../tools/release/record.mjs";
import { releaseFacts, manualFacts } from "./helpers/release_facts.mjs";

test("decodePlan accepts the exact schema2 artifact record", () => {
  const { prepared, request } = releaseFacts(); // Given
  const result = records.decodePlan(JSON.stringify(prepared), request); // When
  assert.deepEqual(result, { ok: true, value: prepared }); // Then
  assert.ok(Object.isFrozen(result.value.release.tarball) && Object.isFrozen(result.value.request));
  assert.equal(Object.isFrozen(prepared.release), false);
});

for (const [attempt, allowed] of [["1", true], ["2", true], ["9007199254740993", true], ["0", false]]) {
  test(`decodePlan checks the current attempt ${attempt}`, () => {
    const { prepared, request } = releaseFacts(); // Given
    const result = records.decodePlan(JSON.stringify(prepared), { ...request, attempt }); // When
    assert.equal(result.ok, allowed); // Then
  });
}

test("decodePlan compares attempts above max-safe without rounding", () => {
  const { prepared, request } = releaseFacts(); // Given
  const current = { ...request, attempt: "9007199254740992" };
  prepared.request.attempt = "9007199254740993";
  const result = records.decodePlan(JSON.stringify(prepared), current); // When
  assert.equal(result.error?.code, "E_RECORD"); // Then
});

for (const [name, change] of [
  ["schema1", (p) => { p.schema = 1; }], ["root key", (p) => { p.steps = ["npm"]; }],
  ["request key", (p) => { p.request.extra = true; }], ["run number", (p) => { p.request.runId = 1; }],
  ["different run", (p) => { p.request.runId = "9007199254740992"; }],
  ["different source", (p) => { p.request.sourceSha = "c".repeat(40); }],
  ["bad source", (p) => { p.request.sourceSha = "A".repeat(40); }],
  ["future attempt", (p) => { p.request.attempt = "2"; }],
  ["noncanonical attempt", (p) => { p.request.attempt = "01"; }],
  ["input version", (p) => { p.request.inputVersion = " "; }],
  ["orphan channel", (p) => { p.request.inputNpmTag = "next"; }],
  ["action", (p) => { p.action = "execute"; }], ["reason", (p) => { p.reason = "done"; }],
  ["publish skip", (p) => { p.reason = "no_commits"; }], ["skip ready", (p) => { p.action = "skip"; }],
  ["missing artifact", (p) => { p.release = null; }], ["release key", (p) => { p.release.source = "today"; }],
  ["version normalization", (p) => { p.release.version = "v0.1.2"; }],
  ["tag binding", (p) => { p.release.tag = "v0.1.3"; }], ["unsafe channel", (p) => { p.release.npmTag = "1.x"; }],
  ["auto channel", (p) => { p.release.npmTag = "next"; }],
  ["origin key", (p) => { p.release.origin.attempt = "1"; }], ["origin mode", (p) => { p.release.origin.mode = "rerun"; }],
  ["origin run", (p) => { p.release.origin.runId = "01"; }],
  ["base tag", (p) => { p.release.base.tag = "v0.1.0"; }],
  ["base SHA", (p) => { p.release.base.sourceSha = "b".repeat(39); }],
  ["base key", (p) => { p.release.base.branch = "master"; }],
  ["prerelease base", (p) => { p.release.base.version = "0.1.1-rc.1"; p.release.base.tag = "v0.1.1-rc.1"; }],
  ["self base", (p) => { p.release.base.sourceSha = p.request.sourceSha; }],
  ["wrong increment", (p) => { p.release.version = "0.1.3"; p.release.tag = "v0.1.3"; }],
  ["pre1 major bump", (p) => { p.release.bump = "major"; p.release.version = "1.0.0"; p.release.tag = "v1.0.0"; }],
  ["bump enum", (p) => { p.release.bump = "feature"; }], ["missing bump", (p) => { p.release.bump = null; }],
  ["zero commits", (p) => { p.release.commitCount = 0; }], ["fractional commits", (p) => { p.release.commitCount = 1.5; }],
  ["unsafe count", (p) => { p.release.commitCount = 9007199254740992; }],
  ["toolchain key", (p) => { p.release.toolchain.flags = []; }], ["node family", (p) => { p.release.toolchain.nodeMajor = 26; }],
  ["npm pin", (p) => { p.release.toolchain.npm = "latest"; }], ["python pin", (p) => { p.release.toolchain.pythonMinor = "3.11"; }],
  ["path", (p) => { p.release.tarball.file = "../package.tgz"; }],
  ["tarball key", (p) => { p.release.tarball.command = "publish"; }],
  ["size type", (p) => { p.release.tarball.size = "512"; }], ["empty tarball", (p) => { p.release.tarball.size = 0; }],
  ["SHA1", (p) => { p.release.tarball.integrity = "sha1-AAAA"; }],
  ["short SHA512", (p) => { p.release.tarball.integrity = "sha512-AAAA"; }],
  ["ambiguous SRI", (p) => { p.release.tarball.integrity += " " + p.release.tarball.integrity; }],
  ["noncanonical base64", (p) => { p.release.tarball.integrity = "sha512-" + "A".repeat(85) + "B=="; }],
  ["automatic same-run manual origin", (p) => {
    Object.assign(p.release, { origin: { mode: "manual", runId: p.request.runId }, bump: null, commitCount: 0 });
  }],
  ["automatic prerelease recovery", (p) => {
    Object.assign(p.release, { version: "1.0.0-beta", tag: "v1.0.0-beta", npmTag: "next", origin: { mode: "manual", runId: "40" }, bump: null, commitCount: 0 });
  }],
]) {
  test(`decodePlan rejects ${name}`, () => {
    const { prepared, request } = releaseFacts(); // Given
    const current = structuredClone(request);
    change(prepared);
    const result = records.decodePlan(JSON.stringify(prepared), current); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

for (const reason of ["no_commits", "stale_source", "already_released"]) {
  test(`decodePlan validates a ${reason} skip`, () => {
    const { prepared, request } = releaseFacts(); // Given
    const input = { ...prepared, action: "skip", reason, release: reason === "already_released" ? prepared.release : null };
    const result = records.decodePlan(JSON.stringify(input), request); // When
    assert.deepEqual(result, { ok: true, value: input }); // Then
  });
}

for (const reason of ["no_commits", "stale_source", "already_released"]) {
  test(`decodePlan rejects invalid artifact presence for ${reason}`, () => {
    const { prepared, request } = releaseFacts(); // Given
    const input = { ...prepared, action: "skip", reason, release: reason === "already_released" ? null : prepared.release };
    const result = records.decodePlan(JSON.stringify(input), request); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

test("decodePlan validates current inputs before accepting a completed record", () => {
  const { prepared, request } = releaseFacts(); // Given
  const result = records.decodePlan(JSON.stringify({ ...prepared, action: "skip", reason: "already_released" }), { ...request, ref: "refs/heads/topic" }); // When
  assert.equal(result.error?.code, "E_UNTRUSTED_CONTEXT"); // Then
});

for (const text of ["{", "[]", "null", "{}", null, 2]) {
  test(`decodePlan rejects invalid serialized data ${JSON.stringify(text)}`, () => {
    const { request } = releaseFacts(); // Given
    const result = records.decodePlan(text, request); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

for (const text of ["", "historical tag", '{"note":"legacy"}']) {
  test(`decodeReservation recognizes unmarked legacy annotation ${text}`, () => {
    const { baseTag } = releaseFacts(); // Given
    const result = records.decodeReservation(text, { ...baseTag, annotation: text }); // When
    assert.deepEqual(result, { ok: true, value: null }); // Then
  });
}

test("decodeReservation binds a managed tag to its immutable source and release", () => {
  const { tag, reservation } = releaseFacts(); // Given
  const result = records.decodeReservation(tag.annotation, tag); // When
  assert.deepEqual(result, { ok: true, value: reservation }); // Then
  assert.ok(Object.isFrozen(result.value.release.base) && Object.isFrozen(result.value.release.origin));
});

for (const [name, change] of [
  ["schema", (r) => { r.schema = "thunderkit.release/v2"; }],
  ["unknown key", (r) => { r.updated = 1; }], ["repository", (r) => { r.repository = "foreign/thunderkit"; }],
  ["source binding", (r) => { r.sourceSha = "b".repeat(40); }],
  ["prerelease latest", (r) => { r.release.version = "1.0.0-beta"; r.release.tag = "v1.0.0-beta"; }],
  ["manual counts", (r) => { r.release.origin.mode = "manual"; }],
]) {
  test(`decodeReservation rejects ${name} rather than treating it as legacy`, () => {
    const { tag, reservation } = releaseFacts(); // Given
    change(reservation);
    const text = JSON.stringify(reservation);
    const result = records.decodeReservation(text, { ...tag, annotation: text }); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

for (const change of [{ name: "v0.1.3" }, { version: "0.1.3" }, { sha: "b".repeat(40) }, { objectSha: "bad" }, { objectSha: "a".repeat(40) }]) {
  test(`decodeReservation rejects altered Git identity ${JSON.stringify(change)}`, () => {
    const { tag } = releaseFacts(); // Given
    const result = records.decodeReservation(tag.annotation, { ...tag, ...change }); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

test("decodeReservation rejects a malformed marked record", () => {
  const { tag } = releaseFacts(); // Given
  const text = '{"schema":"thunderkit.release/v1",';
  const result = records.decodeReservation(text, { ...tag, annotation: text }); // When
  assert.equal(result.error?.code, "E_RECORD"); // Then
});

for (const complete of [true, false]) {
  test(`decodeReservation recognizes an escaped marker when complete=${complete}`, () => {
    const { tag, reservation } = releaseFacts(); // Given
    const escaped = tag.annotation.replace("thunderkit.release", "thunderkit\\u002erelease");
    const text = complete ? escaped : escaped.slice(0, -1);
    const result = records.decodeReservation(text, { ...tag, annotation: text }); // When
    if (complete) assert.deepEqual(result, { ok: true, value: reservation }); // Then
    else assert.equal(result.error?.code, "E_RECORD");
  });
}

test("decodePlan rejects an unknown nested value before recursively copying it", () => {
  const { prepared, request } = releaseFacts(); // Given
  const text = JSON.stringify(prepared).slice(0, -1) + ',"extra":' + "[".repeat(10000) + "0" + "]".repeat(10000) + "}";
  const result = records.decodePlan(text, request); // When
  assert.equal(result.error?.code, "E_RECORD"); // Then
});

for (const reason of ["no_commits", "stale_source"]) {
  test(`decodePlan rejects a manual ${reason} skip`, () => {
    const { prepared, request } = manualFacts("1.0.0", "latest"); // Given
    const text = JSON.stringify({ ...prepared, action: "skip", reason, release: null });
    const result = records.decodePlan(text, request); // When
    assert.equal(result.error?.code, "E_RECORD"); // Then
  });
}

test("decodePlan rejects a manual release that names itself as its base", () => {
  const { prepared, request } = manualFacts("0.1.1", "maintenance-0"); // Given
  const result = records.decodePlan(JSON.stringify(prepared), request); // When
  assert.equal(result.error?.code, "E_RECORD"); // Then
});

test("encodeReservation emits canonical field order regardless of insertion order", () => {
  const { prepared, reservation } = releaseFacts(); // Given
  const reordered = { ...prepared, release: Object.fromEntries(Object.entries(prepared.release).reverse()) };
  const result = records.encodeReservation(reordered); // When
  assert.equal(result, JSON.stringify(reservation)); // Then
});

test("encodeReservation rejects invalid prepared identity", () => {
  const { prepared } = releaseFacts(); // Given
  prepared.release.tarball.file = "/tmp/foreign.tgz";
  assert.throws(() => records.encodeReservation(prepared), { code: "E_RECORD" }); // When / Then
});

for (const mode of ["bootstrap", "maintenance", "prerelease"]) {
  test(`decodePlan accepts a valid ${mode} release`, () => {
    const { prepared, request } = releaseFacts(); // Given
    prepared.release.bump = null;
    prepared.release.commitCount = 0;
    if (mode === "bootstrap") prepared.release.base = null;
    else {
      request.event = "workflow_dispatch";
      request.inputVersion = mode === "maintenance" ? "0.0.9" : "1.0.0-beta.1";
      request.inputNpmTag = mode === "maintenance" ? "maintenance-0" : "";
      Object.assign(prepared.release, { version: request.inputVersion, tag: `v${request.inputVersion}`, npmTag: mode === "maintenance" ? "maintenance-0" : "next" });
      prepared.release.origin.mode = "manual";
    }
    const result = records.decodePlan(JSON.stringify(prepared), request); // When
    assert.deepEqual(result, { ok: true, value: prepared }); // Then
  });
}
