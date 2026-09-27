// @ts-check
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { child, programs } from "./helpers/release_workspace.mjs";

const archiveTool = fileURLToPath(new URL("../tools/release/archive.py", import.meta.url));
const root = mkdtempSync(join(tmpdir(), "release-archive-"));
after(() => rmSync(root, { recursive: true, force: true }));
let counter = 0;

/** Build a tarball with an independent Python script so adversarial members never pass through the tool under test. @param {string} script */
function buildTar(script) {
  const path = join(root, `case-${counter += 1}.tgz`);
  const result = child(programs.python3, ["-c", `import tarfile, io, sys\nt = tarfile.open(sys.argv[1], "w:gz")\ndef add(name, data=b"", **attrs):\n    info = tarfile.TarInfo(name)\n    info.size = len(data)\n    info.mode = 0o644\n    for key, value in attrs.items(): setattr(info, key, value)\n    t.addfile(info, io.BytesIO(data))\n${script}\nt.close()\n`, path], root);
  assert.equal(result.status, 0, result.stderr);
  return path;
}
/** @param {string} tarball */
function inspect(tarball) {
  const destination = join(root, `out-${counter += 1}`);
  mkdirSync(destination);
  const result = child(programs.python3, [archiveTool, tarball, destination], root);
  return { result, destination };
}
const manifest = Buffer.from(JSON.stringify({ name: "thunderkit", version: "1.2.3", bin: { thunderkit: "bin/thunderkit.js" } }));
const valid = `add("package/package.json", ${JSON.stringify(manifest.toString())}.encode())\nadd("package/bin/thunderkit.js", b"#!/usr/bin/env node\\n", mode=0o755)\nadd("package/skills/", type=tarfile.DIRTYPE, mode=0o755)\nadd("package/skills/a.md", b"hello")`;

test("a plain archive extracts and reports sizes, modes and SHA-256 per regular file", () => {
  const { result, destination } = inspect(buildTar(valid));
  assert.equal(result.status, 0, result.stderr);
  const evidence = JSON.parse(result.stdout);
  assert.equal(evidence.name, "thunderkit");
  assert.equal(evidence.version, "1.2.3");
  assert.deepEqual(evidence.bin, { thunderkit: "bin/thunderkit.js" });
  assert.deepEqual(evidence.files.map((/** @type {{path:string}} */ file) => file.path), ["package/bin/thunderkit.js", "package/package.json", "package/skills/a.md"]);
  const skill = evidence.files[2];
  assert.equal(skill.size, 5);
  assert.equal(skill.sha256, createHash("sha256").update("hello").digest("hex"));
  assert.equal(evidence.files[0].mode & 0o111, 0o111);
  assert.equal(readFileSync(join(destination, "package/skills/a.md"), "utf8"), "hello");
});

test("a source-style archive without a package/ prefix is described from its root manifest", () => {
  const { result } = inspect(buildTar(`add("package.json", ${JSON.stringify(manifest.toString())}.encode())\nadd("NORTH_STAR.md", b"x")`));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).version, "1.2.3");
});

/** @type {ReadonlyArray<[string, string]>} */
const adversaries = [
  ["parent traversal", `${valid}\nadd("package/../escape", b"x")`],
  ["absolute path", `${valid}\nadd("/tmp/escape", b"x")`],
  ["backslash path", `${valid}\nadd("package\\\\evil", b"x")`],
  ["empty component", `${valid}\nadd("package//evil", b"x")`],
  ["control character", `${valid}\nadd("package/ev\\nil", b"x")`],
  ["symbolic link", `${valid}\nadd("package/link", type=tarfile.SYMTYPE, linkname="../../etc/passwd")`],
  ["in-tree symbolic link", `${valid}\nadd("package/link", type=tarfile.SYMTYPE, linkname="package.json")`],
  ["hard link", `${valid}\nadd("package/hard", type=tarfile.LNKTYPE, linkname="package/package.json")`],
  ["character device", `${valid}\nadd("package/dev", type=tarfile.CHRTYPE)`],
  ["fifo", `${valid}\nadd("package/pipe", type=tarfile.FIFOTYPE)`],
  ["duplicate member", `${valid}\nadd("package/skills/a.md", b"again")`],
  ["duplicate after normalization", `${valid}\nadd("package/skills/a.md/", b"again")`],
  ["setuid mode", `${valid}\nadd("package/suid", b"x", mode=0o4755)`],
  ["sticky directory", `${valid}\nadd("package/sticky/", type=tarfile.DIRTYPE, mode=0o1755)`],
  ["file used as directory", `${valid}\nadd("package/skills/a.md/inner", b"x")`],
  ["pax path traversal", `${valid}\nadd("package/ok", b"x", pax_headers={"path": "../pax-escape"})`],
  ["pax linkpath", `${valid}\nadd("package/ok", b"x", pax_headers={"linkpath": "somewhere"})`],
  ["sparse extended header", `${valid}\nadd("package/ok", b"x", pax_headers={"GNU.sparse.size": "1"})`],
  ["missing manifest", `add("package/skills/a.md", b"x")`],
  ["non-object manifest", `add("package/package.json", b"[]")`],
];
for (const [name, script] of adversaries) {
  test(`rejects ${name} before extracting anything`, () => {
    const { result, destination } = inspect(buildTar(script));
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.equal(result.stderr, "E_ARTIFACT: archive verification failed\n");
    assert.deepEqual(readdirSync(destination), []);
  });
}

test("corrupt gzip bytes and a truncated archive fail with the coded message", () => {
  const good = buildTar(valid);
  const bytes = readFileSync(good);
  const corrupt = join(root, "corrupt.tgz");
  const middle = Math.floor(bytes.length / 2);
  writeFileSync(corrupt, Buffer.concat([bytes.subarray(0, middle), Buffer.from(bytes.subarray(middle, middle + 16).map((byte) => byte ^ 0xff)), bytes.subarray(middle + 16)]));
  const truncated = join(root, "truncated.tgz");
  writeFileSync(truncated, bytes.subarray(0, Math.floor(bytes.length / 2)));
  for (const tarball of [corrupt, truncated]) {
    const { result, destination } = inspect(tarball);
    assert.equal(result.status, 1);
    assert.equal(result.stderr, "E_ARTIFACT: archive verification failed\n");
    assert.deepEqual(readdirSync(destination), []);
  }
});

test("a non-empty destination, a missing archive and wrong argument counts fail", () => {
  const tarball = buildTar(valid);
  const occupied = join(root, "occupied");
  mkdirSync(occupied);
  writeFileSync(join(occupied, "stale"), "");
  for (const args of [[tarball, occupied], [join(root, "missing.tgz"), join(root, "fresh")], [tarball], [tarball, occupied, "extra"]]) {
    mkdirSync(join(root, "fresh"), { recursive: true });
    const result = child(programs.python3, [archiveTool, ...args], root);
    assert.equal(result.status, 1, args.join(" "));
    assert.equal(result.stderr, "E_ARTIFACT: archive verification failed\n");
  }
  assert.deepEqual(readdirSync(occupied), ["stale"]);
});

test("archives beyond the member or byte limits are rejected", () => {
  const { result } = inspect(buildTar(`${valid}\nfor i in range(10000): add("package/f%d" % i, b"")`));
  assert.equal(result.status, 1);
  const big = inspect(buildTar(`${valid}\nadd("package/big", b"\\0" * (64 * 1024 * 1024 + 1))`));
  assert.equal(big.result.status, 1);
  assert.deepEqual(readdirSync(big.destination), []);
});
