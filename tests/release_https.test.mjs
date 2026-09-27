// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import https from "node:https";
import { IncomingMessage } from "node:http";
import { syncBuiltinESMExports } from "node:module";
import { Socket } from "node:net";
import { PassThrough } from "node:stream";
import { createSystemTransport, get, registryUrl } from "../tools/release/io.mjs";

/** @typedef {Readonly<{status:number, location?:string, body?:string}>} Response */
const document = JSON.stringify({ name: "thunderkit", versions: {}, "dist-tags": {} });
const present = { ok: true, value: { exists: true, versions: {}, distTags: {} } };

/** Stub only HTTPS delivery; exercise the real URL, redirect, body and Result boundaries. @param {import("node:test").TestContext} t @param {readonly Response[]} responses */
function offline(t, responses) {
  /** @type {{url:string, options:https.RequestOptions}[]} */ const calls = [];
  const network = t.mock.method(Socket.prototype, "connect", () => assert.fail("live network is forbidden"));
  const stub = t.mock.method(https, "request", (/** @type {URL} */ url, /** @type {https.RequestOptions} */ options, /** @type {(response:IncomingMessage)=>void} */ callback) => {
    calls.push({ url: url.href, options });
    const reply = responses[calls.length - 1];
    assert.ok(reply, "unexpected HTTPS request");
    const request = new PassThrough();
    queueMicrotask(() => {
      const response = new IncomingMessage(new Socket());
      response.statusCode = reply.status;
      response.headers = reply.location === undefined ? {} : { location: reply.location };
      try {
        callback(response);
        response.push(reply.body ?? "");
        response.push(null);
      } finally {
        request.destroy();
      }
    });
    return request;
  });
  syncBuiltinESMExports();
  t.after(() => { stub.mock.restore(); network.mock.restore(); syncBuiltinESMExports(); });
  const transport = createSystemTransport(process.cwd(), { exec: () => assert.fail("unexpected subprocess"), get });
  return { calls, transport };
}

for (const location of ["https://[", "https://registry.npmjs.org:invalid/"]) {
  test(`readRegistry returns a failure rather than crashing when redirect Location is ${location}`, { timeout: 2_000 }, async (t) => {
    // Given
    const { transport, calls } = offline(t, [{ status: 302, location }]);
    // When
    const result = await transport.readRegistry();
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_REGISTRY");
    assert.deepEqual(calls.map((call) => call.url), [registryUrl]);
  });
}

for (const location of ["/thunderkit?fresh=1", "https://registry.npmjs.org/thunderkit?fresh=2"]) {
  test(`readRegistry follows a valid same-host redirect to ${location} with verified TLS and no credentials`, async (t) => {
    // Given
    const { transport, calls } = offline(t, [{ status: 302, location }, { status: 200, body: document }]);
    // When
    const result = await transport.readRegistry();
    // Then
    assert.deepEqual(result, present);
    assert.deepEqual(calls.map((call) => call.url), [registryUrl, new URL(location, registryUrl).href]);
    for (const call of calls) assert.deepEqual(call.options, { method: "GET", rejectUnauthorized: true, headers: { accept: "application/json" } });
  });
}

for (const location of ["https://example.invalid/", "http://registry.npmjs.org/thunderkit", "https://user:password@registry.npmjs.org/thunderkit", "https://registry.npmjs.org:8443/thunderkit"]) {
  test(`readRegistry rejects a forbidden redirect destination ${location} before another request`, async (t) => {
    // Given
    const { transport, calls } = offline(t, [{ status: 307, location }]);
    // When
    const result = await transport.readRegistry();
    // Then
    assert.equal(result.ok ? null : result.error.code, "E_REGISTRY");
    assert.equal(calls.length, 1);
  });
}

test("readRegistry rejects a redirect with no Location header", async (t) => {
  // Given
  const { transport, calls } = offline(t, [{ status: 301 }]);
  // When
  const result = await transport.readRegistry();
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_REGISTRY");
  assert.equal(calls.length, 1);
});

test("readRegistry accepts exactly three redirect hops", async (t) => {
  // Given
  const { transport, calls } = offline(t, [{ status: 301, location: "/one" }, { status: 303, location: "/two" }, { status: 308, location: "/three" }, { status: 200, body: document }]);
  // When
  const result = await transport.readRegistry();
  // Then
  assert.deepEqual(result, present);
  assert.equal(calls.length, 4);
});

test("readRegistry rejects a fourth redirect hop without requesting it", async (t) => {
  // Given
  const { transport, calls } = offline(t, Array.from({ length: 4 }, () => ({ status: 302, location: "/again" })));
  // When
  const result = await transport.readRegistry();
  // Then
  assert.equal(result.ok ? null : result.error.code, "E_REGISTRY");
  assert.equal(calls.length, 4);
});
