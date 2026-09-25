import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const root = new URL('../', import.meta.url);
const original = readFileSync(new URL('.github/workflows/release-please.yml', root), 'utf8');
const sandbox = mkdtempSync(join(tmpdir(), 'release-workflow-'));
after(() => rmSync(sandbox, { recursive: true, force: true }));
const context = "github.repository == 'thunderock/thunderkit' && github.ref == 'refs/heads/master' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch')";
const candidate = "${{ success() && steps.plan.outputs.action == 'publish' }}";
const pins = {
  checkout: 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
  node: 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
  python: 'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
  upload: 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
  download: 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
};

// Only this workflow's indentation maps, step lists and literal blocks are supported.
function extract(text) {
  assert.ok(text.length < 40_000 && !text.includes('\t'), 'bounded workflow subset');
  const lines = text.split('\n');
  assert.ok(lines.length < 600, 'bounded line count');
  let cursor = 0;
  const indent = (line) => line.length - line.trimStart().length;
  function skip() { while (cursor < lines.length && /^\s*(#.*)?$/.test(lines[cursor])) cursor++; }
  function map(depth) {
    assert.ok(depth <= 12, 'bounded indentation');
    const result = {};
    skip();
    while (cursor < lines.length && indent(lines[cursor]) === depth) {
      const match = lines[cursor++].trim().match(/^([\w-]+|"on"): *(.*)$/);
      assert.ok(match, 'mapping entry required');
      const key = match[1].replaceAll('"', '');
      assert.ok(!Object.hasOwn(result, key), `duplicate ${key}`);
      const value = match[2].replace(/ +#.*$/, '');
      if (value === '|') {
        const block = [];
        while (cursor < lines.length && (indent(lines[cursor]) > depth || !lines[cursor].trim())) {
          block.push(lines[cursor++].slice(depth + 2));
        }
        result[key] = block.join('\n').trimEnd();
      } else if (value) {
        result[key] = value === '{}' ? {} : value.replace(/^(['"])(.*)\1$/, '$2');
      } else {
        skip();
        if (lines[cursor]?.trimStart().startsWith('- id: ')) {
          result[key] = [];
          while (lines[cursor]?.startsWith(' '.repeat(depth + 2) + '- id: ')) {
            lines[cursor] = lines[cursor].replace('- id: ', '  id: ');
            result[key].push(map(depth + 4));
            skip();
          }
        } else result[key] = map(depth + 2);
      }
      skip();
    }
    return result;
  }
  const result = map(0);
  assert.equal(cursor, lines.length, 'entire workflow consumed');
  return result;
}

function step(workflow, job, id) {
  const matches = workflow.jobs[job].steps.filter((entry) => entry.id === id);
  assert.equal(matches.length, 1, `${job}.${id} occurs once`);
  return matches[0];
}
function guards(w) {
  assert.equal(w.name, 'Release');
  assert.deepEqual(w.on, { push: { branches: '[master]' }, workflow_dispatch: { inputs: {
    version: { description: 'Exact version; leave empty for automatic stable versioning', required: 'false', type: 'string' },
    npm_tag: { description: 'Optional channel for an exact version', required: 'false', type: 'string' },
  } } });
  assert.deepEqual(w.concurrency, { group: 'npm-release', 'cancel-in-progress': 'false' });
  assert.deepEqual(Object.keys(w.jobs), ['gate', 'publish']);
  assert.equal(w.jobs.gate.if, '${{ ' + context + ' }}');
  assert.equal(w.jobs.publish.if, '${{ ' + context + " && needs.gate.result == 'success' && needs.gate.outputs.action == 'publish' }}");
  assert.equal(w.jobs.publish.needs, 'gate');
}
function permissions(w) {
  assert.deepEqual(w.permissions, {});
  assert.deepEqual(w.jobs.gate.permissions, { contents: 'read' });
  assert.deepEqual(w.jobs.publish.permissions, { contents: 'write', 'id-token': 'write' });
  assert.deepEqual(w.env, {
    RELEASE_VERSION_INPUT: '${{ inputs.version }}', RELEASE_NPM_TAG_INPUT: '${{ inputs.npm_tag }}',
    npm_config_registry: 'https://registry.npmjs.org', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1',
  });
  for (const [name, job] of Object.entries(w.jobs)) {
    assert.deepEqual(job.env, {
      npm_config_userconfig: '${{ runner.temp }}/npm-userconfig', npm_config_globalconfig: '${{ runner.temp }}/npm-globalconfig',
      npm_config_cache: '${{ runner.temp }}/npm-cache', GH_CONFIG_DIR: '${{ runner.temp }}/gh-config',
    });
    for (const s of job.steps) {
      assert.notEqual(Boolean(s.uses), Boolean(s.run), `${name}.${s.id} has one executor`);
      for (const key of Object.keys(s)) assert.ok(['id', 'name', 'if', ...(s.uses ? ['uses', 'with'] : ['run', 'env'])].includes(key), key);
      assert.equal(s.permissions, undefined);
      assert.equal(s['continue-on-error'], undefined);
      if (!(name === 'gate' && ['tests', 'verify', 'upload'].includes(s.id))) assert.equal(s.if, undefined);
      const authenticated = (name === 'gate' && s.id === 'plan') || (name === 'publish' && s.id === 'publish');
      assert.equal(s.env?.GH_TOKEN, authenticated ? '${{ github.token }}' : undefined, `${name}.${s.id} token scope`);
      if (!['plan', 'verify', 'handoff', 'publish'].includes(s.id)) assert.equal(s.env, undefined);
      assert.doesNotMatch(JSON.stringify(s), /NODE_AUTH_TOKEN|NPM_TOKEN|secrets\.|--force|"overwrite":"true"/);
      if (s.run) assert.doesNotMatch(s.run, /\$\{\{|npm (?:publish|pack|version)|git (?:push|tag|config)|\beval\b/);
    }
  }
}
function tools(w) {
  assert.deepEqual(w.defaults, { run: { shell: 'bash' } });
  for (const job of ['gate', 'publish']) {
    assert.equal(w.jobs[job]['runs-on'], 'ubuntu-24.04');
    assert.ok(Number(w.jobs[job]['timeout-minutes']) <= 30);
    for (const id of ['checkout', 'node', 'python']) assert.equal(step(w, job, id).uses, pins[id]);
    assert.deepEqual(step(w, job, 'checkout').with, {
      ref: '${{ github.sha }}', 'fetch-depth': '0', 'fetch-tags': 'true', 'persist-credentials': 'false', 'set-safe-directory': 'false',
    });
    assert.deepEqual(step(w, job, 'node').with, { 'node-version': '24', 'package-manager-cache': 'false' });
    assert.deepEqual(step(w, job, 'python').with, { 'python-version': '3.12' });
    const npm = step(w, job, 'npm').run;
    for (const line of ['umask 077', ': > "$npm_config_userconfig"', ': > "$npm_config_globalconfig"',
      'prefix="$(mktemp -d "$RUNNER_TEMP/npm-cli.XXXXXX")"', 'cd "$RUNNER_TEMP"',
      'npm install --prefix "$prefix" --ignore-scripts --no-audit --no-fund --package-lock=false npm@11.19.1',
      'printf \'%s\\n\' "$prefix/node_modules/.bin" >> "$GITHUB_PATH"']) assert.ok(npm.split('\n').includes(line), line);
    const checks = step(w, job, 'tools').run;
    for (const line of ['test "$(npm --version)" = \'11.19.1\'', 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
      'node -e \'if (process.versions.node.split(".")[0] !== "24") process.exit(1)\'',
      "python3 -c 'import sys; assert sys.version_info[:2] == (3, 12)'",
      'for tool in git gh make; do command -v "$tool" > /dev/null; done']) assert.ok(checks.split('\n').includes(line), line);
    assert.match(checks, /^api_help="\$\(gh api --help\)"$/m);
    assert.match(checks, /^release_help="\$\(gh release create --help\)"$/m);
    assert.match(checks, /^for flag in --include --method; do \[\[ "\$api_help" == \*"\$flag"\* \]\]; done$/m);
    assert.match(checks, /^for flag in --repo --verify-tag --target --title --generate-notes --prerelease --latest; do \[\[ "\$release_help" == \*"\$flag"\* \]\]; done$/m);
  }
}
function handoff(w) {
  assert.deepEqual(w.jobs.gate.steps.map((s) => s.id), ['checkout', 'node', 'python', 'npm', 'tools', 'plan', 'tests', 'verify', 'upload']);
  assert.deepEqual(w.jobs.publish.steps.map((s) => s.id), ['checkout', 'node', 'python', 'npm', 'tools', 'handoff', 'download', 'publish']);
  assert.deepEqual(w.jobs.gate.outputs, { action: '${{ steps.plan.outputs.action }}', record_sha256: '${{ steps.plan.outputs.record_sha256 }}', artifact_id: '${{ steps.upload.outputs.artifact-id }}' });
  assert.equal(step(w, 'gate', 'plan').run, 'node tools/release/plan.mjs --workspace "$RUNNER_TEMP/release"');
  assert.deepEqual(step(w, 'gate', 'plan').env, { GH_TOKEN: '${{ github.token }}' });
  assert.equal(step(w, 'publish', 'publish').run, 'node tools/release/publish.mjs --bundle "$RUNNER_TEMP/release-bundle"');
  assert.deepEqual(step(w, 'publish', 'publish').env, { GH_TOKEN: '${{ github.token }}', RELEASE_RECORD_SHA256: '${{ needs.gate.outputs.record_sha256 }}' });
  for (const id of ['tests', 'verify', 'upload']) assert.equal(step(w, 'gate', id).if, candidate);
  assert.equal(step(w, 'gate', 'tests').run, 'cd "$RUNNER_TEMP/release/source"\nmake run_tests && make lint && npm test\nnode --test tests/release_*.test.mjs\nmake site');
  const verify = step(w, 'gate', 'verify');
  assert.deepEqual(verify.env, { RELEASE_RECORD_SHA256: '${{ steps.plan.outputs.record_sha256 }}' });
  for (const line of ["const bytes = readFileSync(join(bundle, 'release-plan.json'));",
    "assert.match(process.env.RELEASE_RECORD_SHA256, /^[a-f0-9]{64}$/);",
    "assert.equal(createHash('sha256').update(bytes).digest('hex'), process.env.RELEASE_RECORD_SHA256);",
    "assert.equal(record.action, 'publish');", "assert.equal(record.release.tarball.file, 'package.tgz');",
    "const tarball = readFileSync(join(bundle, 'package.tgz'));", 'assert.equal(tarball.length, record.release.tarball.size);',
    "assert.equal('sha512-' + createHash('sha512').update(tarball).digest('base64'), record.release.tarball.integrity);"])
    assert.ok(verify.run.split('\n').includes(line), line);
  assert.equal(step(w, 'gate', 'upload').uses, pins.upload);
  assert.deepEqual(step(w, 'gate', 'upload').with, {
    name: 'release-${{ github.run_id }}-${{ github.run_attempt }}',
    path: '${{ runner.temp }}/release/bundle/release-plan.json\n${{ runner.temp }}/release/bundle/package.tgz',
    'if-no-files-found': 'error', overwrite: 'false', archive: 'true',
  });
  const identity = step(w, 'publish', 'handoff');
  assert.deepEqual(identity.env, { RELEASE_ARTIFACT_ID: '${{ needs.gate.outputs.artifact_id }}', RELEASE_RECORD_SHA256: '${{ needs.gate.outputs.record_sha256 }}' });
  assert.ok(identity.run.split('\n').includes('assert.match(process.env.RELEASE_ARTIFACT_ID, /^[1-9][0-9]*$/);'));
  assert.ok(identity.run.split('\n').includes('assert.match(process.env.RELEASE_RECORD_SHA256, /^[a-f0-9]{64}$/);'));
  assert.equal(step(w, 'publish', 'download').uses, pins.download);
  assert.deepEqual(step(w, 'publish', 'download').with, { 'artifact-ids': '${{ needs.gate.outputs.artifact_id }}', path: '${{ runner.temp }}/release-bundle', 'merge-multiple': 'true', 'digest-mismatch': 'error' });
}

for (const check of [guards, permissions, tools, handoff]) {
  test(`Given the release workflow, when checking ${check.name}, then its scoped contract holds`, () => check(extract(original)));
}
test('Given the replacement, when checking legacy routes, then all three retired files are absent', () => {
  for (const path of ['.github/workflows/publish.yml', '.release-please-config.json', '.release-please-manifest.json']) assert.equal(existsSync(new URL(path, root)), false);
});

function mutation(name, check, change) {
  test(`Given ${name}, when checking ${check.name}, then the mutated workflow is rejected`, () => {
    const changed = change(original);
    assert.notEqual(changed, original, 'mutation applied');
    const path = join(sandbox, `${name.replaceAll(/[^a-z0-9]/gi, '-')}.yml`);
    writeFileSync(path, changed, { mode: 0o600 });
    assert.throws(() => check(extract(readFileSync(path, 'utf8'))), assert.AssertionError);
  });
}
function inJob(text, job, change) {
  const start = text.indexOf(`\n  ${job}:\n`);
  const end = job === 'gate' ? text.indexOf('\n  publish:\n') : text.length;
  return text.slice(0, start) + change(text.slice(start, end)) + text.slice(end);
}
for (const job of ['gate', 'publish']) {
  const clauses = ["github.repository == 'thunderock/thunderkit'", "github.ref == 'refs/heads/master'", "github.event_name == 'push'", "github.event_name == 'workflow_dispatch'"];
  if (job === 'publish') clauses.push("needs.gate.result == 'success'", "needs.gate.outputs.action == 'publish'");
  for (const clause of clauses) for (const replacement of ['true', clause.replace(/'[^']+'/g, "'other'")])
    mutation(`${job} ${clause} becomes ${replacement}`, guards, (s) => inJob(s, job, (j) => j.replace(clause, replacement)));
  mutation(`${job} checkout changes source`, tools, (s) => inJob(s, job, (j) => j.replace('ref: ${{ github.sha }}', 'ref: master')));
  mutation(`${job} HEAD check becomes a comment`, tools, (s) => inJob(s, job, (j) => j.replace('test "$(git rev-parse HEAD)"', '# test "$(git rev-parse HEAD)"')));
  mutation(`${job} token moves to another step`, permissions, (s) => inJob(s, job, (j) => j.replace('          GH_TOKEN: ${{ github.token }}\n', '').replace('      - id: tools\n', '      - id: tools\n        env:\n          GH_TOKEN: ${{ github.token }}\n')));
}
mutation('OIDC permission moves to gate', permissions, (s) => s.replace('      id-token: write\n', '').replace('      contents: read\n', '      contents: read\n      id-token: write\n'));
mutation('OIDC permission moves to workflow', permissions, (s) => s.replace('      id-token: write\n', '').replace('permissions: {}', 'permissions:\n  id-token: write'));
mutation('token moves to workflow environment', permissions, (s) => s.replace('          GH_TOKEN: ${{ github.token }}\n', '').replace('\nenv:\n', '\nenv:\n  GH_TOKEN: ${{ github.token }}\n'));
for (const id of ['tests', 'verify', 'upload']) mutation(`${id} loses success guard`, handoff, (s) => s.replace(new RegExp(`(- id: ${id}[\\s\\S]*?if: )[^\\n]+`), '$1${{ always() }}'));
for (const [from, to] of [['overwrite: false', 'overwrite: true'], ['artifact-ids:', 'name:'], ['steps.plan.outputs.record_sha256', 'steps.other.outputs.record_sha256'], ['steps.upload.outputs.artifact-id', 'steps.other.outputs.artifact-id'], ['make site', '# make site'], ["assert.equal(tarball.length, record.release.tarball.size);", '// removed']])
  mutation(`handoff alters ${from}`, handoff, (s) => s.replace(from, to));
for (const [id, pin] of Object.entries(pins)) mutation(`${id} loses immutable action pin`, ['upload', 'download'].includes(id) ? handoff : tools, (s) => s.replace(pin, pin.split('@')[0] + '@main'));
mutation('input expression enters a command', permissions, (s) => s.replace('--workspace "$RUNNER_TEMP/release"', '--workspace "${{ inputs.version }}"'));
