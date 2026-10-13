// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { createSystemTransport, exec } from "../tools/release/io.mjs";
import { releaseFacts } from "./helpers/release_facts.mjs";
import { programs } from "./helpers/release_workspace.mjs";

/** @typedef {import("../tools/release/io.mjs").ExecResult} ExecResult */

for (const event of ["push", "workflow_dispatch"]) {
  test(`npm provenance retains runner and GitHub identity for ${event} without leaking other credentials`, async () => {
    const { raw, prepared } = releaseFacts();
    prepared.request.event = /** @type {"push"|"workflow_dispatch"} */ (event);
    const environment = {
      ...raw, GITHUB_EVENT_NAME: event, GITHUB_SERVER_URL: "https://github.com", GITHUB_API_URL: "https://api.github.com",
      GITHUB_REPOSITORY_ID: "1355543838", GITHUB_REPOSITORY_OWNER_ID: "10261509", RUNNER_ENVIRONMENT: "github-hosted",
      ACTIONS_ID_TOKEN_REQUEST_URL: "https://example.invalid/oidc", ACTIONS_ID_TOKEN_REQUEST_TOKEN: "fixture-only-oidc",
    };
    const saved = { ...process.env };
    /** @type {ExecResult|undefined} */ let child;
    let calls = 0;
    try {
      Object.assign(process.env, environment, { GH_TOKEN: "fixture-must-not-leak", NPM_TOKEN: "fixture-must-not-leak", NODE_AUTH_TOKEN: "fixture-must-not-leak" });
      const transport = createSystemTransport(process.cwd(), {
        get: async () => { throw new Error("Network is forbidden in provenance tests"); },
        exec(program, argv, options) {
          assert.equal(program, "npm");
          assert.equal(argv[0], "publish");
          assert.ok(argv.includes("--provenance"));
          calls += 1;
          // Execute the pinned npm consumer through the actual isolated child environment.
          // Intercept only signing: no OIDC request, signature or registry response is fabricated.
          const script = `
            const { createRequire } = require('node:module');
            const npmRequire = createRequire(${JSON.stringify(programs.npm)});
            const publishRequire = createRequire(npmRequire.resolve('libnpmpublish'));
            const sigstorePath = publishRequire.resolve('sigstore');
            const sigstore = publishRequire('sigstore');
            publishRequire.cache[sigstorePath].exports = {
              ...sigstore,
              attest: async bytes => JSON.parse(bytes.toString('utf8'))
            };
            publishRequire('./provenance.js').generateProvenance([], {}).then(statement => {
              process.stdout.write(JSON.stringify({
                builder: statement.predicate.runDetails.builder.id,
                github: statement.predicate.buildDefinition.internalParameters.github,
                workflow: statement.predicate.buildDefinition.externalParameters.workflow,
                credentials: ['GH_TOKEN', 'NPM_TOKEN', 'NODE_AUTH_TOKEN'].map(key => process.env[key] ?? null),
                oidcAvailable: Boolean(process.env.ACTIONS_ID_TOKEN_REQUEST_URL && process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN)
              }));
            }).catch(error => { console.error(error); process.exitCode = 1; });
          `;
          child = exec(process.execPath, ["-e", script], options);
          return child;
        },
      });
      const result = await transport.publishTarball(prepared, process.cwd());
      assert.equal(child?.status, 0, child?.stderr);
      assert.deepEqual(result, { ok: true, value: null });
      assert.equal(calls, 1);
      const statement = JSON.parse(child?.stdout ?? "");
      assert.equal(statement.builder, "https://github.com/actions/runner/github-hosted");
      assert.deepEqual(statement.github, {
        event_name: event, repository_id: environment.GITHUB_REPOSITORY_ID, repository_owner_id: environment.GITHUB_REPOSITORY_OWNER_ID,
      });
      assert.deepEqual(statement.workflow, {
        ref: "refs/heads/master", repository: "https://github.com/thunderock/thunderkit", path: ".github/workflows/release-please.yml",
      });
      assert.deepEqual(statement.credentials, [null, null, null]);
      assert.equal(statement.oidcAvailable, true);
    } finally {
      for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
      Object.assign(process.env, saved);
    }
  });
}
