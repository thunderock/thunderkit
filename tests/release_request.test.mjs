import { test } from "node:test";
import assert from "node:assert/strict";
import * as requests from "../tools/release/request.mjs";
import { requestFacts } from "./helpers/release_facts.mjs";

test("parseRequest decodes trusted input without rounding run identity", () => {
  const { raw, request } = requestFacts(); // Given
  const result = requests.parseRequest(raw); // When
  assert.deepEqual(result, { ok: true, value: request }); // Then
});

test("parseRequest reads only whitelisted environment keys", () => {
  const { raw, request } = requestFacts(); // Given
  const input = new Proxy(raw, {
    ownKeys: () => assert.fail("environment enumeration"),
    get: (target, key) => {
      assert.ok(Object.hasOwn(target, key) || key === "RELEASE_VERSION_INPUT" || key === "RELEASE_NPM_TAG_INPUT");
      return Reflect.get(target, key);
    },
  });
  const result = requests.parseRequest(input); // When
  assert.deepEqual(result, { ok: true, value: request }); // Then
});

for (const [key, value] of [
  ["GITHUB_ACTIONS", undefined], ["GITHUB_ACTIONS", "false"], ["GITHUB_ACTIONS", true],
  ["GITHUB_REPOSITORY", "other/thunderkit"], ["GITHUB_REF", "refs/tags/v1.0.0"],
  ["GITHUB_REF", "refs/heads/feature"], ["GITHUB_EVENT_NAME", "pull_request"],
  ["GITHUB_WORKFLOW_REF", "thunderock/thunderkit/.github/workflows/publish.yml@refs/heads/master"],
  ["GITHUB_WORKFLOW_REF", "thunderock/thunderkit/.github/workflows/release-please.yml@refs/heads/topic"],
  ["GITHUB_SHA", "A".repeat(40)], ["GITHUB_SHA", "a".repeat(39)], ["GITHUB_SHA", "a".repeat(40) + "\n"],
  ["GITHUB_RUN_ID", 123], ["GITHUB_RUN_ID", "0"], ["GITHUB_RUN_ID", "01"], ["GITHUB_RUN_ID", "1e3"],
  ["GITHUB_RUN_ID", "1\n"], ["GITHUB_RUN_ATTEMPT", "-1"], ["GITHUB_RUN_ATTEMPT", "1.0"],
  ["GITHUB_RUN_ATTEMPT", "01"], ["GITHUB_RUN_ATTEMPT", ""], ["GITHUB_RUN_ATTEMPT", " 1"],
  ["GITHUB_RUN_ATTEMPT", "1\n"], ["GITHUB_RUN_ATTEMPT", "1e2"], ["GITHUB_RUN_ID", "+1"],
  ["GITHUB_RUN_ID", "١"], ["GITHUB_SHA", "a".repeat(41)],
]) {
  test(`parseRequest rejects untrusted ${key}=${JSON.stringify(value)}`, () => {
    const { raw } = requestFacts(); // Given
    const result = requests.parseRequest({ ...raw, [key]: value }); // When
    assert.equal(result.error?.code, "E_UNTRUSTED_CONTEXT"); // Then
  });
}

for (const [version, channel, code] of [
  ["", "next", "E_NPM_TAG_WITHOUT_VERSION"], [" ", "", "E_INVALID_VERSION"],
  ["1.0.0+build", "", "E_INVALID_VERSION"], [null, "", "E_INVALID_VERSION"],
  ["1.0.0", null, "E_INVALID_NPM_TAG"], ["1.0.0", "1.x", "E_INVALID_NPM_TAG"],
  ["1.0.0-rc.1", "latest", "E_INVALID_NPM_TAG"], [12, "", "E_INVALID_VERSION"],
]) {
  test(`parseRequest rejects manual inputs ${JSON.stringify([version, channel])}`, () => {
    const { raw } = requestFacts(); // Given
    const input = { ...raw, GITHUB_EVENT_NAME: "workflow_dispatch", RELEASE_VERSION_INPUT: version, RELEASE_NPM_TAG_INPUT: channel };
    const result = requests.parseRequest(input); // When
    assert.equal(result.error?.code, code); // Then
  });
}

test("parseRequest accepts an exact manual version and preserves a large attempt string", () => {
  const { raw, request } = requestFacts(); // Given
  const input = { ...raw, GITHUB_EVENT_NAME: "workflow_dispatch", GITHUB_RUN_ATTEMPT: "9007199254740995", RELEASE_VERSION_INPUT: "3.4.5" };
  const result = requests.parseRequest(input); // When
  assert.deepEqual(result, { ok: true, value: { ...request, event: "workflow_dispatch", attempt: "9007199254740995", inputVersion: "3.4.5" } }); // Then
});

test("parseRequest rejects push inputs rather than interpreting them as manual intent", () => {
  const { raw } = requestFacts(); // Given
  const result = requests.parseRequest({ ...raw, RELEASE_VERSION_INPUT: "1.0.0" }); // When
  assert.equal(result.error?.code, "E_UNTRUSTED_CONTEXT"); // Then
});

for (const input of [null, undefined, 1, [], "environment"]) {
  test(`parseRequest rejects a non-environment ${JSON.stringify(input)}`, () => {
    // Given a non-map value.
    const result = requests.parseRequest(input); // When
    assert.equal(result.error?.code, "E_UNTRUSTED_CONTEXT"); // Then
  });
}

for (const change of [
  { extra: true }, { runId: 9007199254740993 }, { attempt: "0" }, { inputVersion: undefined },
  { sourceSha: "invalid" }, { event: "pull_request" }, { inputVersion: "1.0.0" }, { inputNpmTag: "next" },
]) {
  test(`decodeRequest rejects invalid normalized fields ${JSON.stringify(change)}`, () => {
    const { request } = requestFacts(); // Given
    const result = requests.decodeRequest({ ...request, ...change }); // When
    assert.equal(result.ok, false); // Then
  });
}

test("decodeRequest rejects accessors and hidden keys without reading them", () => {
  const { request } = requestFacts(); // Given
  Object.defineProperty(request, "runId", { get: () => assert.fail("accessor invoked") });
  const result = requests.decodeRequest(request); // When
  assert.equal(result.ok, false); // Then
});

test("decodeRequest returns a frozen copy without freezing caller data", () => {
  const { request } = requestFacts(); // Given
  const result = requests.decodeRequest(request); // When
  assert.deepEqual(result, { ok: true, value: request }); // Then
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.value));
  assert.equal(Object.isFrozen(request), false);
  assert.notEqual(result.value, request);
});

for (const optional of [undefined, ""]) {
  test(`parseRequest preserves automatic dispatch when optional inputs are ${String(optional)}`, () => {
    const { raw, request } = requestFacts(); // Given
    const result = requests.parseRequest({ ...raw, GITHUB_EVENT_NAME: "workflow_dispatch", RELEASE_VERSION_INPUT: optional, RELEASE_NPM_TAG_INPUT: optional }); // When
    assert.deepEqual(result, { ok: true, value: { ...request, event: "workflow_dispatch" } }); // Then
  });
}

for (const hidden of ["override", Symbol("override")]) {
  test(`decodeRequest rejects an undisclosed ${String(hidden)} key`, () => {
    const { request } = requestFacts(); // Given
    Object.defineProperty(request, hidden, { value: "foreign" });
    const result = requests.decodeRequest(request); // When
    assert.equal(result.ok, false); // Then
  });
}

test("success isolates nested returned collections from caller mutation", () => {
  const input = { steps: ["tag"], nested: { origin: ["42"] } }; // Given
  const result = requests.success(input); // When
  assert.deepEqual(result, { ok: true, value: input }); // Then
  assert.ok(Object.isFrozen(result.value.steps) && Object.isFrozen(result.value.nested.origin));
  assert.equal(Object.isFrozen(input.steps), false);
  assert.notEqual(result.value.steps, input.steps);
});
