"""G tests-only contract: exact-ref push authority, not a delivery engine.

API: checkpoint_push(PushRequest, grant: PushGrant | None = None, *,
                     driver=production_driver) -> PushResult (frozen C types).
Driver: callable(argv: tuple[str, ...], *, cwd: Path, timeout: float)
        -> subprocess.CompletedProcess[str]; argv excludes the git executable.
It returns raw stdout/stderr/returncode, or raises subprocess.TimeoutExpired.
Every Git invocation goes through this seam, including local preflight reads.
No environment flag enables filesystem transport in the production driver.

Allowed argv are enumerated by FixtureDriver below. In particular, publication
uses one explicit approved URL and OID:full-ref, never a remote's push defaults.
The driver maps ONLY the approved GitHub URL in exact ls-remote/push argv to a
local bare fixture. Everything else is rejected before subprocess execution.
The helper must inspect raw config (including include directives), effective
push URLs, hooks and unsafe transport overrides before the first transport;
then recheck authority/HEAD/ref/config/remote immediately before publication.

RED currently proves the missing entrypoint, not that each planned behavioral
assertion was reached. test_00 exercises the real fixture driver independently.
All other assertions become behavioral regression gates in the parent's GREEN.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from collections.abc import Callable
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "skills/references"
MODULE = REFERENCES / "checkpoint_push.py"
REF = "refs/heads/work"
HTTPS = "https://github.com/fixture/checkpoints.git"
SSH = "git@github.com:fixture/checkpoints.git"
SECRET = "G_TEST_ONLY_SECRET_DO_NOT_EMIT"
MISSING = "Missing scoped checkpoint push behavior: skills/references/checkpoint_push.py"
POPEN = subprocess.Popen


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FixtureDriver:
    """Real Git, strict command firewall, deterministic transport fault injection."""

    def __init__(self, fixture):
        self.fixture: Any = fixture
        self.calls = []
        self.reads = 0
        self.writes = 0
        self.after_first_read: Callable[[], Any] | None = None
        self.read_fault: Callable[[FixtureDriver], tuple[int, str, str] | str | None] | None = None
        self.push_fault: str | None = None
        self.after_push: Callable[[], Any] | None = None

    def read_argv(self):
        f = self.fixture
        return ("ls-remote", "--exit-code", "--refs", "--", f.url, f.ref)

    def push_argv(self):
        f = self.fixture
        return ("-c", "push.followTags=false", "-c", "push.recurseSubmodules=no",
                "push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                "--", f.url, f"{f.oid}:{f.ref}")

    def __call__(self, argv, *, cwd, timeout):
        f = self.fixture
        args = tuple(argv)
        if not Path(cwd).is_relative_to(f.repo) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise AssertionError("Driver scope or bounded timeout violated")
        local_reads = {
            ("rev-parse", "--show-toplevel"),
            ("rev-parse", "--absolute-git-dir"),
            ("rev-parse", "--git-path", "hooks"),
            ("rev-parse", "--verify", "HEAD"),
            ("symbolic-ref", "--quiet", "HEAD"),
            ("config", "--null", "--show-origin", "--no-includes", "--list"),
            ("remote", "get-url", "--push", "--all", "origin"),
            ("remote", "get-url", "--push", "--all", "unconfigured"),
        }
        is_ancestor = (
            len(args) == 4 and args[:2] == ("merge-base", "--is-ancestor")
            and args[2] in f.known_oids and args[3] == f.oid
        )
        is_read = args == self.read_argv()
        is_push = args == self.push_argv()
        if not (args in local_reads or is_ancestor or is_read or is_push):
            # Do not echo an unapproved argv: it might contain a credential URL.
            raise AssertionError("Unapproved Git argv; see FixtureDriver contract")
        self.calls.append(args)
        if is_read:
            self.reads += 1
            fault = self.read_fault(self) if self.read_fault else None
            if fault == "timeout":
                raise subprocess.TimeoutExpired(args, timeout, stderr="fixture read timeout")
            if fault is not None:
                assert isinstance(fault, tuple)
                code, out, err = fault
                return subprocess.CompletedProcess(args, code, out, err)
            mapped = (*args[:-2], str(f.bare), args[-1])
            result = f.raw(*mapped, check=False)
            if self.reads == 1 and self.after_first_read:
                self.after_first_read()
            return subprocess.CompletedProcess(args, result.returncode, result.stdout,
                                               "" if result.returncode in (0, 2) else "fixture read failed")
        if is_push:
            self.writes += 1
            if self.push_fault in ("timeout-before", "reject"):
                if self.push_fault == "timeout-before":
                    raise subprocess.TimeoutExpired(args, timeout, stderr="fixture push timeout")
                return subprocess.CompletedProcess(args, 1, "", "fixture push rejected")
            mapped = (*args[:-2], str(f.bare), args[-1])
            result = f.raw(*mapped, check=False)
            if self.after_push:
                self.after_push()
            if self.push_fault == "timeout-after":
                raise subprocess.TimeoutExpired(args, timeout, stderr="fixture push timeout")
            if self.push_fault == "nonzero-after":
                return subprocess.CompletedProcess(args, 1, "", "fixture transport closed")
            return subprocess.CompletedProcess(args, result.returncode, result.stdout,
                                               "" if result.returncode == 0 else "fixture push rejected")
        return f.raw(*args, cwd=cwd, check=False)


class CheckpointPushTests(unittest.TestCase):
    def setUp(self):
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        self.temp = tempfile.TemporaryDirectory(prefix="thunderkit-G-red-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "project"
        self.bare = self.base / "remote.git"
        self.home = self.base / "home"
        self.hooks = self.base / "hooks"
        self.template = self.base / "empty-template"
        for path in (self.home, self.hooks, self.template):
            path.mkdir()
        global_config = self.base / "empty-global"
        system_config = self.base / "empty-system"
        global_config.write_text("", encoding="utf-8")
        system_config.write_text("", encoding="utf-8")
        self.git = shutil.which("git", path="/usr/bin:/bin")
        self.assertIsNotNone(self.git, "Fixture Git is required, not skipped")
        assert self.git is not None
        # Replace, never overlay, the environment; no credential or auth inheritance.
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"), "TMPDIR": str(self.base),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": str(system_config),
            "GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_ATTR_NOSYSTEM": "1", "LC_ALL": "C",
            "GIT_AUTHOR_DATE": "2025-01-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": "2025-01-01T00:00:00+0000",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        environment = patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        previous_cwd = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous_cwd)
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        paths = patch.object(sys, "path", [str(REFERENCES), *sys.path])
        paths.start()
        self.addCleanup(paths.stop)
        bytecode = patch.object(sys, "dont_write_bytecode", True)
        bytecode.start()
        self.addCleanup(bytecode.stop)
        self.c = load_module("continuity_config", REFERENCES / "continuity_config.py")
        self.api = None
        # Direct subprocess use bypasses the driver contract and fails safely.
        guard = patch("subprocess.Popen", side_effect=AssertionError("Use injected Git driver"))
        guard.start()
        self.addCleanup(guard.stop)
        self.ref = REF
        self.url = HTTPS
        self.raw("init", "--bare", f"--template={self.template}", "--initial-branch=main",
                 str(self.bare), cwd=self.base)
        self.raw("init", f"--template={self.template}", "--initial-branch=work",
                 str(self.repo), cwd=self.base)
        self.config("core.hooksPath", str(self.hooks))
        self.config("user.name", "Fixture")
        self.config("user.email", "fixture@example.invalid")
        self.config("credential.helper", "")
        self.config("remote.origin.url", self.url)
        self.raw("commit", "--allow-empty", "-m", "seed")
        self.old = self.head()
        self.raw("push", "--", str(self.bare), f"{self.old}:{REF}")
        self.raw("update-ref", "refs/heads/main", self.old, cwd=self.bare)
        self.raw("update-ref", "refs/heads/lane", self.old, cwd=self.bare)
        self.raw("commit", "--allow-empty", "-m", "captured")
        self.oid = self.head()
        self.known_oids = {self.old, self.oid}
        self.identity = self.c.ScopeIdentity("project", "fixture-project")
        self.request = self.c.PushRequest(self.identity, self.repo, "origin", REF, self.oid)
        self.grant = self.c.PushGrant(self.identity, self.repo, self.url, REF,
                                     default_branch="refs/heads/main", branch_safety_verified=True)
        self.driver = FixtureDriver(self)

    def raw(self, *args, cwd=None, check=True):
        assert self.git is not None
        target = Path(cwd) if cwd else self.repo
        self.assertTrue(target.is_relative_to(self.base), "Git escaped the fixture")
        process = POPEN([self.git, *args], cwd=target, env=self.env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
        try:
            out, err = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError("Fixture Git exceeded its bound") from None
        if check and process.returncode != 0:
            self.fail("Fixture Git setup failed (raw diagnostics withheld)")
        return subprocess.CompletedProcess(args, process.returncode, out, err)

    def config(self, key, value, *, add=False):
        self.raw("config", "--add" if add else "--replace-all", key, value)

    def head(self):
        return self.raw("rev-parse", "--verify", "HEAD").stdout.strip()

    def remote_oid(self, ref=None):
        result = self.raw("show-ref", "--verify", "--hash", ref or self.ref,
                          cwd=self.bare, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def remote_refs(self):
        return self.raw("for-each-ref", "--format=%(refname) %(objectname)",
                        cwd=self.bare).stdout

    def push(self, request=None, grant: Any = "current", *, production=False) -> Any:
        self.assertTrue(MODULE.is_file(), MISSING)
        if self.api is None:
            self.api = load_module("checkpoint_push", MODULE)
        fn = getattr(self.api, "checkpoint_push", None)
        self.assertTrue(callable(fn), "Missing checkpoint_push callable")
        assert callable(fn)
        supplied = self.grant if grant == "current" else grant
        selected = request or self.request
        result: Any = fn(selected, supplied) if production else fn(selected, supplied, driver=self.driver)
        self.assertIsInstance(result, self.c.PushResult)
        self.assertEqual((result.oid, result.destination_ref), (selected.oid, selected.destination_ref))
        return result

    def denied(self, request=None, grant: Any = "current", *, zero_calls=False):
        before = self.remote_refs()
        result = self.push(request, grant)
        self.assertEqual(result.status, "denied")
        self.assertEqual(self.driver.writes, 0)
        if zero_calls:
            self.assertEqual(self.driver.calls, [])
        self.assertEqual(self.remote_refs(), before)
        return result

    def fresh_driver(self):
        self.driver = FixtureDriver(self)

    def test_00_real_fixture_driver_and_firewall(self):
        self.assertNotEqual(self.old, self.oid)
        self.assertEqual(self.raw("merge-base", "--is-ancestor", self.old, self.oid).returncode, 0)
        result = self.driver(self.driver.read_argv(), cwd=self.repo, timeout=5)
        self.assertEqual(result.stdout, f"{self.old}\t{REF}\n")
        with self.assertRaisesRegex(AssertionError, "Unapproved Git argv"):
            self.driver(("push", "--mirror", self.url), cwd=self.repo, timeout=5)
        result = self.driver(self.driver.push_argv(), cwd=self.repo, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.remote_oid(), self.oid)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.old)
        self.assertEqual(self.driver.writes, 1)
        self.raw("update-ref", REF, self.old, cwd=self.bare)
        self.driver.push_fault = "timeout-before"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.driver(self.driver.push_argv(), cwd=self.repo, timeout=5)
        self.assertEqual(self.remote_oid(), self.old)
        self.driver.push_fault = "timeout-after"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.driver(self.driver.push_argv(), cwd=self.repo, timeout=5)
        self.assertEqual(self.remote_oid(), self.oid)

    def test_01_default_on_without_fresh_grant_has_zero_calls(self):
        self.assertTrue(self.request.policy.push_after_commit)
        saved = self.repo / ".thunderkit"
        saved.mkdir()
        # Inert settings/backup bytes cannot resurrect current caller authority.
        for name in ("continuity.json", "continuity.local.json", "backup-manifest.json"):
            (saved / name).write_text(json.dumps({"schema": 1, "git": {"push_after_commit": True},
                "grant": {"remote_url": HTTPS, "destination_ref": REF}}), encoding="utf-8")
        self.denied(grant=None, zero_calls=True)

    def test_02_explicit_off_has_zero_calls_even_with_grant(self):
        result = self.push(replace(self.request, policy=self.c.GitPolicy(False)))
        self.assertEqual(result.status, "disabled")
        self.assertEqual(self.driver.calls, [])
        self.assertEqual(self.remote_oid(), self.old)

    def test_03_unknown_branch_safety_has_zero_calls(self):
        self.denied(grant=replace(self.grant, branch_safety_verified=False), zero_calls=True)

    def test_04_every_grant_binding_must_match(self):
        for change in ({"identity": self.c.ScopeIdentity("project", "other")},
                       {"repository": self.base / "other"},
                       {"destination_ref": "refs/heads/lane"},
                       {"remote_url": "https://github.com/fixture/other.git"}):
            with self.subTest(binding=next(iter(change))):
                self.fresh_driver()
                self.denied(grant=replace(self.grant, **change))
                self.assertEqual(self.driver.reads, 0, "Mismatched authority must not reach transport")

    def test_05_detached_wrong_branch_stale_oid_and_nonroot_are_denied(self):
        for state in ("detached", "branch", "stale", "subdirectory", "remote-name"):
            with self.subTest(state=state):
                self.raw("symbolic-ref", "HEAD", REF)
                self.fresh_driver()
                request, grant = self.request, self.grant
                if state == "detached":
                    self.raw("checkout", "--detach", self.oid)
                elif state == "branch":
                    self.raw("update-ref", "refs/heads/lane", self.oid)
                    self.raw("symbolic-ref", "HEAD", "refs/heads/lane")
                elif state == "stale":
                    request = replace(request, oid=self.old)
                elif state == "subdirectory":
                    subdir = self.repo / "subdir"
                    subdir.mkdir()
                    request = replace(request, repository=subdir)
                    grant = replace(grant, repository=subdir)
                else:
                    request = replace(request, remote="unconfigured")
                self.denied(request, grant)

    def test_06_default_protected_and_ungranted_lane_refs_are_denied(self):
        for ref in ("refs/heads/main", "refs/heads/master", "refs/heads/trunk",
                    "refs/heads/protected", "refs/heads/lane"):
            with self.subTest(ref=ref):
                self.fresh_driver()
                self.raw("update-ref", ref, self.oid)
                self.raw("symbolic-ref", "HEAD", ref)
                request = replace(self.request, destination_ref=ref)
                grant = replace(self.grant, destination_ref=ref, default_branch="refs/heads/trunk",
                                protected_refs=("refs/heads/protected",))
                if ref.endswith("/lane"):
                    grant = self.grant
                self.denied(request, grant)

    def test_07_only_canonical_credential_free_github_endpoints(self):
        endpoints = (str(self.bare), self.bare.as_uri(), "http://github.com/fixture/repo.git",
                     "https://github.com.evil.invalid/fixture/repo.git",
                     f"https://user:{SECRET}@github.com/fixture/repo.git",
                     "https://github.com/fixture/repo.git?token=fixture",
                     "root@github.com:fixture/repo.git", "git@evil.invalid:fixture/repo.git",
                     "ext::fixture-command")
        for url in endpoints:
            with self.subTest(endpoint_kind=endpoints.index(url)):
                self.fresh_driver()
                self.config("remote.origin.url", url)
                self.denied(grant=replace(self.grant, remote_url=url))
                self.assertEqual(self.driver.reads, 0)

    def test_08_ambiguous_rewritten_mirror_and_included_destinations(self):
        original = (self.repo / ".git/config").read_bytes()
        include = self.base / "included-config"
        include.write_text("[remote \"origin\"]\n\tpushurl = https://github.com/fixture/other.git\n")
        for case in ("multiple-url", "multiple-pushurl", "insteadOf", "pushInsteadOf",
                     "mirror", "include", "pushspec"):
            with self.subTest(config=case):
                (self.repo / ".git/config").write_bytes(original)
                self.fresh_driver()
                if case == "multiple-url":
                    self.config("remote.origin.url", HTTPS, add=True)
                elif case == "multiple-pushurl":
                    self.config("remote.origin.pushurl", HTTPS)
                    self.config("remote.origin.pushurl", "https://github.com/fixture/other.git", add=True)
                elif case in ("insteadOf", "pushInsteadOf"):
                    self.config(f"url.https://github.com/fixture/other.git.{case}", HTTPS)
                elif case == "mirror":
                    self.config("remote.origin.mirror", "true")
                elif case == "include":
                    self.config("include.path", str(include))
                else:
                    self.config("remote.origin.push", "refs/heads/lane:refs/heads/lane")
                self.denied()
                self.assertEqual(self.driver.reads, 0, "Inspect configuration before transport")

    def test_09_success_is_exact_oid_one_ref_no_tags_or_submodules(self):
        self.raw("tag", "-a", "fixture-tag", "-m", "must stay local", self.oid)
        self.config("push.followTags", "true")
        self.config("push.recurseSubmodules", "on-demand")
        for url in (HTTPS, SSH):
            with self.subTest(transport=url.split(":", 1)[0]):
                self.raw("update-ref", REF, self.old, cwd=self.bare)
                self.url = url
                self.config("remote.origin.url", url)
                self.grant = replace(self.grant, remote_url=url)
                self.fresh_driver()
                before_config = (self.repo / ".git/config").read_bytes()
                result = self.push()
                self.assertEqual((result.status, result.remote_oid), ("pushed", self.oid))
                self.assertEqual(self.remote_oid(), self.oid)
                self.assertEqual(self.remote_oid("refs/heads/main"), self.old)
                self.assertEqual(self.remote_oid("refs/heads/lane"), self.old)
                self.assertIsNone(self.remote_oid("refs/tags/fixture-tag"))
                self.assertEqual(self.driver.writes, 1)
                self.assertIn(self.driver.push_argv(), self.driver.calls)
                self.assertEqual(self.driver.calls[-1], self.driver.read_argv())
                pretransport = self.driver.calls[:self.driver.calls.index(self.driver.read_argv())]
                self.assertIn(("config", "--null", "--show-origin", "--no-includes", "--list"), pretransport)
                self.assertIn(("remote", "get-url", "--push", "--all", "origin"), pretransport)
                self.assertIn(("rev-parse", "--git-path", "hooks"), pretransport)
                self.assertGreaterEqual(self.driver.reads, 3, "Initial, prewrite and readback required")
                self.assertEqual((self.repo / ".git/config").read_bytes(), before_config)
                self.assertEqual(self.head(), self.oid)

    def test_10_already_pushed_deduplicates_without_write(self):
        self.raw("push", "--", str(self.bare), f"{self.oid}:{REF}")
        result = self.push()
        self.assertEqual((result.status, result.remote_oid), ("already-present", self.oid))
        self.assertEqual(self.driver.writes, 0)
        self.assertGreaterEqual(self.driver.reads, 1)

    def test_11_non_fast_forward_is_denied_and_local_commit_survives(self):
        self.raw("commit", "--allow-empty", "-m", "divergent remote")
        other = self.head()
        self.known_oids.add(other)
        self.raw("push", "--", str(self.bare), f"{other}:{REF}")
        self.raw("update-ref", REF, self.oid)
        self.denied()
        self.assertEqual(self.remote_oid(), other)
        self.assertEqual(self.head(), self.oid)

    def test_12_confirmed_missing_remote_ref_can_be_created(self):
        self.raw("update-ref", "-d", REF, cwd=self.bare)
        result = self.push()
        self.assertEqual((result.status, result.remote_oid), ("pushed", self.oid))
        self.assertEqual(self.remote_oid(), self.oid)
        self.assertEqual(self.driver.writes, 1)

    def test_13_failed_or_malformed_remote_read_is_not_absence(self):
        faults = ((128, "", "fixture auth failure"), "timeout",
                  (0, "not-an-oid\t" + REF + "\n", ""),
                  (0, f"{self.old}\trefs/heads/other\n", ""),
                  (0, f"{self.old}\t{REF}\n{self.oid}\t{REF}\n", ""))
        for fault in faults:
            with self.subTest(read_fault=faults.index(fault)):
                self.fresh_driver()
                self.driver.read_fault = lambda driver, value=fault: value
                result = self.push()
                self.assertIn(result.status, ("denied", "unknown", "failed"))
                self.assertIsNone(result.remote_oid)
                self.assertEqual(self.driver.writes, 0)
                self.assertEqual(self.remote_oid(), self.old)

    def test_14_timeout_before_acceptance_reads_back_once_without_retry(self):
        self.driver.push_fault = "timeout-before"
        result = self.push()
        self.assertEqual((result.status, result.remote_oid), ("failed", self.old))
        self.assert_one_postwrite_read()
        self.assertEqual(self.head(), self.oid)

    def test_15_timeout_after_acceptance_is_verified_pushed(self):
        self.driver.push_fault = "timeout-after"
        result = self.push()
        self.assertEqual((result.status, result.remote_oid), ("pushed", self.oid))
        self.assertEqual(self.remote_oid(), self.oid)
        self.assert_one_postwrite_read()

    def assert_one_postwrite_read(self):
        self.assertEqual(self.driver.writes, 1)
        position = self.driver.calls.index(self.driver.push_argv())
        self.assertEqual(self.driver.calls[position + 1:], [self.driver.read_argv()])

    def test_16_nonzero_push_reconciles_actual_acceptance(self):
        for fault, status, expected in (("reject", "failed", self.old),
                                        ("nonzero-after", "pushed", self.oid)):
            with self.subTest(fault=fault):
                self.raw("update-ref", REF, self.old, cwd=self.bare)
                self.fresh_driver()
                self.driver.push_fault = fault
                result = self.push()
                self.assertEqual((result.status, result.remote_oid), (status, expected))
                self.assertEqual(self.remote_oid(), expected)
                self.assert_one_postwrite_read()
                self.assertEqual(self.head(), self.oid)

    def test_17_unreadable_postwrite_state_remains_unknown(self):
        for push_fault in (None, "timeout-before", "timeout-after", "reject"):
            with self.subTest(push_fault=push_fault):
                self.raw("update-ref", REF, self.old, cwd=self.bare)
                self.fresh_driver()
                self.driver.push_fault = push_fault
                self.driver.read_fault = lambda d: (128, "", "fixture read failure") if d.writes else None
                result = self.push()
                self.assertEqual(result.status, "unknown")
                self.assertIsNone(result.remote_oid)
                self.assert_one_postwrite_read()
                self.assertEqual(self.head(), self.oid)

    def test_18_exit_zero_without_matching_readback_is_not_success(self):
        self.driver.after_push = lambda: self.raw("update-ref", REF, self.old, cwd=self.bare)
        result = self.push()
        self.assertIn(result.status, ("failed", "unknown"))
        self.assertNotEqual(result.remote_oid, self.oid)
        self.assert_one_postwrite_read()

    def test_19_changed_head_branch_or_grant_before_write_stops(self):
        for race in ("head", "branch", "grant-identity", "grant-ref"):
            with self.subTest(race=race):
                self.raw("symbolic-ref", "HEAD", REF)
                self.raw("update-ref", REF, self.oid)
                self.grant = replace(self.grant, identity=self.identity, destination_ref=REF)
                self.fresh_driver()
                def mutate():
                    if race == "head":
                        self.raw("commit", "--allow-empty", "-m", "concurrent")
                    elif race == "branch":
                        self.raw("update-ref", "refs/heads/lane", self.oid)
                        self.raw("symbolic-ref", "HEAD", "refs/heads/lane")
                    elif race == "grant-identity":
                        object.__setattr__(self.grant, "identity", self.c.ScopeIdentity("project", "other"))
                    else:
                        object.__setattr__(self.grant, "destination_ref", "refs/heads/lane")
                self.driver.after_first_read = mutate
                result = self.push()
                self.assertIn(result.status, ("denied", "unknown"))
                self.assertEqual(self.driver.writes, 0)
                self.assertEqual(self.remote_oid(), self.old)

    def test_20_changed_remote_or_url_before_write_stops(self):
        for race in ("remote-oid", "remote-url"):
            with self.subTest(race=race):
                self.config("remote.origin.url", HTTPS)
                self.raw("update-ref", REF, self.old, cwd=self.bare)
                self.fresh_driver()
                def mutate():
                    if race == "remote-oid":
                        self.raw("update-ref", "-d", REF, cwd=self.bare)
                    else:
                        self.config("remote.origin.url", "https://github.com/fixture/other.git")
                self.driver.after_first_read = mutate
                result = self.push()
                self.assertIn(result.status, ("denied", "unknown"))
                self.assertEqual(self.driver.writes, 0)

    def test_21_existing_hooks_and_configuration_are_never_replaced_or_bypassed(self):
        hook = self.hooks / "pre-push"
        for exit_status in (0, 1):
            with self.subTest(hook_exit=exit_status):
                self.raw("update-ref", REF, self.old, cwd=self.bare)
                self.fresh_driver()
                hook.write_text(f"#!/bin/sh\nexit {exit_status}\n", encoding="utf-8")
                hook.chmod(0o755)
                config_before = (self.repo / ".git/config").read_bytes()
                hooks_before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in self.hooks.iterdir()}
                result = self.push()
                if exit_status == 0:
                    self.assertEqual(result.status, "pushed")
                else:
                    self.assertIn(result.status, ("denied", "failed"))
                    self.assertEqual(self.remote_oid(), self.old)
                self.assertEqual((self.repo / ".git/config").read_bytes(), config_before)
                self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mode) for p in self.hooks.iterdir()}, hooks_before)
                self.assertEqual(self.head(), self.oid)

    def test_22_unsafe_ssh_overrides_are_refused_not_reproduced(self):
        self.url = SSH
        self.config("remote.origin.url", SSH)
        self.grant = replace(self.grant, remote_url=SSH)
        for source in ("environment", "config"):
            for option in ("StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null"):
                with self.subTest(source=source, option=option):
                    self.fresh_driver()
                    command = "ssh -o " + option
                    if source == "config":
                        self.config("core.sshCommand", command)
                    with patch.dict(os.environ, {"GIT_SSH_COMMAND": command} if source == "environment" else {}):
                        self.denied()
                    self.assertEqual(self.driver.reads, 0)

    def test_23_raw_error_diagnostics_and_credentials_never_escape(self):
        self.driver.read_fault = lambda d: (128, "", f"fixture denied https://user:{SECRET}@github.com/x/y")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = self.push()
        self.assertIn(result.status, ("denied", "failed", "unknown"))
        for public in (repr(result), out.getvalue(), err.getvalue(), repr(self.driver.calls)):
            self.assertNotIn(SECRET, public)
            self.assertNotIn("https://user:", public)
        self.assertEqual(self.driver.writes, 0)

    def test_24_production_entrypoint_never_accepts_fixture_transport(self):
        self.config("remote.origin.url", str(self.bare))
        grant = replace(self.grant, remote_url=str(self.bare))
        # The direct-process guard proves this cannot fall into real transport.
        with patch.dict(os.environ, {"THUNDERKIT_TEST_ALLOW_FILE_TRANSPORT": "1"}):
            result = self.push(grant=grant, production=True)
        self.assertEqual(result.status, "denied")
        self.assertEqual(self.remote_oid(), self.old)
        self.assertEqual(self.driver.calls, [])


if __name__ == "__main__":
    unittest.main()
