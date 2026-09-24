import { test } from "node:test";
import assert from "node:assert/strict";
import * as versions from "../tools/release/versions.mjs";

for (const [text, expected] of [
  ["0.0.0", { raw: "0.0.0", major: 0, minor: 0, patch: 0, prerelease: [] }],
  ["1.20.3-rc.9007199254740993.01a", {
    raw: "1.20.3-rc.9007199254740993.01a", major: 1, minor: 20, patch: 3,
    prerelease: ["rc", "9007199254740993", "01a"],
  }],
  ["9007199254740991.9007199254740991.9007199254740991", {
    raw: "9007199254740991.9007199254740991.9007199254740991",
    major: 9007199254740991, minor: 9007199254740991, patch: 9007199254740991, prerelease: [],
  }],
]) {
  test(`parseSemver preserves components when given ${text}`, () => {
    // Given the canonical version and its independent component record above.
    const result = versions.parseSemver(text); // When
    assert.deepEqual(result, expected); // Then
  });
}

for (const length of [65, 256]) {
  test(`parseSemver accepts a canonical ${length}-character version`, () => {
    const text = `1.2.3-${"a".repeat(length - 6)}`; // Given
    const result = versions.parseSemver(text); // When
    assert.equal(result?.raw, text); // Then
  });
}

for (const text of [
  "", "1", "1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3-00", "1.2.3-rc.01",
  "1.2.3-", "1.2.3-a..b", "1.2.3-ä", "v1.2.3", "=1.2.3", " 1.2.3", "1.2.3 ",
  "1.2.3\n", "1.2.3\r", "1.2.3\t", "1.2.3\u0000", "1.2.3+build", "^1.2.3", "1.x",
  "1.2.3 || 2.0.0", "1.2.3;id", "$(id)", "9007199254740992.0.0", "0.9007199254740992.0",
  "0.0.9007199254740992", `1.2.3-${"a".repeat(251)}`, null, 123, {}, ["1.2.3"],
]) {
  test(`parseSemver rejects noncanonical input ${JSON.stringify(text)}`, () => {
    // Given an untrusted value, without normalization.
    const result = versions.parseSemver(text); // When
    assert.equal(result, null); // Then
  });
}

for (const [left, right, expected] of [
  ["0.9.9", "1.0.0", -1], ["1.20.0", "1.3.0", 1], ["1.2.10", "1.2.9", 1],
  ["1.0.0", "1.0.0", 0], ["1.0.0-rc.1", "1.0.0", -1], ["1.0.0", "1.0.0-rc.1", 1],
  ["1.0.0-alpha", "1.0.0-alpha.1", -1], ["1.0.0-alpha.1", "1.0.0-alpha.beta", -1],
  ["1.0.0-alpha.beta", "1.0.0-beta", -1], ["1.0.0-beta.2", "1.0.0-beta.11", -1],
  ["1.0.0-beta.11", "1.0.0-rc.1", -1], ["1.0.0-01a", "1.0.0-1", 1],
  ["1.0.0-9007199254740992", "1.0.0-9007199254740993", -1],
  ["1.0.0-99999999999999999", "1.0.0-100000000000000000", -1],
  ["1.0.0-9007199254740993", "1.0.0-9007199254740992", 1],
  ["1.0.0-9007199254740993", "1.0.0-9007199254740993", 0],
  ["1.0.0-a.2", "1.0.0-a", 1], ["1.0.0-Z", "1.0.0-a", -1],
  ["1.0.0-0", "1.0.0-00a", -1], ["1.0.0-1", "1.0.0--", -1],
  [`1.0.0-${"9".repeat(249)}`, `1.0.0-1${"0".repeat(249)}`, -1],
]) {
  test(`compareSemver orders ${left} against ${right}`, () => {
    const a = versions.parseSemver(left); // Given
    const b = versions.parseSemver(right);
    assert.ok(a && b);
    const result = versions.compareSemver(a, b); // When
    assert.equal(result, expected); // Then
  });
}

for (const text of ["latest", "next", "beta", "maintenance-0", "a", "w1", "y", "z", `b${"a".repeat(63)}`]) {
  test(`validateNpmTag accepts the safe channel ${text}`, () => {
    // Given a channel in the project-safe grammar.
    const result = versions.validateNpmTag(text); // When
    assert.deepEqual(result, { ok: true, value: text }); // Then
  });
}

for (const text of [
  "1.x", "x", "v1", "vx", "v1.4", "1.0.0", "*", "Latest", "-x", " beta", "beta ",
  "beta\n", "beta\r", "next\t", "next\u0000", "a_b", "a.b", "a;id", "$(id)", "", "b".repeat(65),
  undefined, null, 12, ["next"],
]) {
  test(`validateNpmTag rejects ${JSON.stringify(text)}`, () => {
    // Given an unsafe channel value.
    const result = versions.validateNpmTag(text); // When
    assert.equal(result.ok, false); // Then
    assert.equal(result.error.code, "E_INVALID_NPM_TAG");
  });
}

test("parseSemver returns an immutable component record", () => {
  const text = "1.2.3-beta.1"; // Given
  const result = versions.parseSemver(text); // When
  assert.ok(result); // Then
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.prerelease));
});

test("validateNpmTag returns immutable failure details", () => {
  const text = "x"; // Given
  const result = versions.validateNpmTag(text); // When
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.error)); // Then
});

for (const first of "abcdefghijklmnopqrstuvwxyz") {
  test(`validateNpmTag bounds the first-letter grammar when given ${first}`, () => {
    const text = `${first}vx09-`; // Given
    const result = versions.validateNpmTag(text); // When
    assert.equal(result.ok, first !== "v" && first !== "x"); // Then
  });
}

test("parseSemver retains a maximum-length numeric prerelease identifier", () => {
  const digits = "9".repeat(250); // Given
  const result = versions.parseSemver(`0.0.0-${digits}`); // When
  assert.deepEqual(result, { raw: `0.0.0-${digits}`, major: 0, minor: 0, patch: 0, prerelease: [digits] }); // Then
});
