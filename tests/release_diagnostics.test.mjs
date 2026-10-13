// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { createSystemTransport } from "../tools/release/io.mjs";
import * as publisher from "../tools/release/publish.mjs";
import { releaseFacts } from "./helpers/release_facts.mjs";

/** @param {Partial<import("../tools/release/io.mjs").ExecResult>} overrides */
async function rejectedPublish(overrides) {
  const transport = createSystemTransport(process.cwd(), {
    get: async () => { throw new Error("Network forbidden in diagnostic tests"); },
    exec: () => ({ status: 1, signal: null, stdout: "", stderr: "", ...overrides }),
  });
  return transport.publishTarball(releaseFacts().prepared, process.cwd());
}

for (const code of ["E422", "ENEEDAUTH", "E403"]) {
  test(`failed npm publication retains its ${code} diagnostic without raw output`, async () => {
    const result = await rejectedPublish({ stderr: `npm error code ${code}\nnpm error Authorization: Bearer fixture-private-value\n` });
    assert.equal(result.ok, false);
    if (result.ok) assert.fail("Expected publication failure");
    assert.equal(result.error.code, "E_REGISTRY");
    assert.equal(result.error.message, `npm publish failed (exit=1, signal=none, npm_code=${code})`);
    assert.ok(Object.isFrozen(result.error));
    assert.ok(!JSON.stringify(result).includes("fixture-private-value"));
  });
}

/** @type {ReadonlyArray<[string, Partial<import("../tools/release/io.mjs").ExecResult>, string]>} */
const diagnosticCases = [
  ["legacy CRLF npm errors", { stderr: "npm ERR! code E422\r\n" }, "exit=1, signal=none, npm_code=E422"],
  ["unknown secret-like code", { stderr: "npm error code EPRIVATE_VALUE\n" }, "exit=1, signal=none, npm_code=unknown"],
  ["error code prefix with secret suffix", { stderr: "npm error code E422_PRIVATE_VALUE\n" }, "exit=1, signal=none, npm_code=unknown"],
  ["ambiguous error codes", { stderr: "npm error code E422\nnpm error code E403\n" }, "exit=1, signal=none, npm_code=unknown"],
  ["raw message without npm code", { stderr: "https://example.invalid/?token=private\n::error::injected\n" }, "exit=1, signal=none, npm_code=unknown"],
  ["error code only in stdout", { stdout: "npm error code E422\n", stderr: "private" }, "exit=1, signal=none, npm_code=unknown"],
  ["terminated child", { status: null, signal: "SIGKILL" }, "exit=unknown, signal=SIGKILL, npm_code=unknown"],
  ["unexpected signal", { status: null, signal: "private\n::error::injected" }, "exit=unknown, signal=unknown, npm_code=unknown"],
  ["invalid exit status", { status: 999 }, "exit=unknown, signal=none, npm_code=unknown"],
  ["code outside bounded diagnostic tail", { stderr: `npm error code E403\n${"x".repeat(20_000)}` }, "exit=1, signal=none, npm_code=unknown"],
];
for (const [name, overrides, expected] of diagnosticCases) {
  test(`npm diagnostic handles ${name} without emitting raw text`, async () => {
    const result = await rejectedPublish(overrides);
    if (result.ok) assert.fail("Expected failure");
    assert.equal(result.error.message, `npm publish failed (${expected})`);
    assert.ok(result.error.message.length < 160);
    assert.doesNotMatch(result.error.message, /private|::|https:|[\r\n]/);
  });
}

test("publisher error output includes the safe npm diagnostic while preserving its nonzero status", async () => {
  const result = await rejectedPublish({ stderr: "npm error code E422\nprivate diagnostic\n" });
  if (result.ok) assert.fail("Expected failure");
  assert.equal(typeof publisher.reportOutcome, "function");
  const output = { stdout: "", stderr: "" };
  const status = publisher.reportOutcome(result, {
    stdout: (text) => { output.stdout += text; },
    stderr: (text) => { output.stderr += text; },
  });
  assert.equal(status, 1);
  assert.equal(output.stdout, "");
  assert.equal(output.stderr, "E_REGISTRY: release publication failed: npm publish failed (exit=1, signal=none, npm_code=E422)\n");
});

for (const message of [
  "unexpected private diagnostic\n::error::injected",
  "npm publish failed (exit=1, signal=none, npm_code=EPRIVATE_VALUE)",
  "npm publish failed (exit=1, signal=none, npm_code=E422)\n",
]) {
  test(`publisher suppresses an unrecognized diagnostic ${JSON.stringify(message)}`, () => {
    const output = { stdout: "", stderr: "" };
    const status = publisher.reportOutcome({ ok: false, error: { code: "E_REGISTRY", message } }, {
      stdout: (text) => { output.stdout += text; },
      stderr: (text) => { output.stderr += text; },
    });
    assert.equal(status, 1);
    assert.equal(output.stdout, "");
    assert.equal(output.stderr, "E_REGISTRY: release publication failed\n");
  });
}
