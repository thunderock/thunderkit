"""H15 cooperative lifecycle regression: real work, isolated real alarms.

No production hooks or fixed source line numbers. The cleanup scheduler observes
initial generator entry (before ANY guard body statement), its real caller code
identity, and successful publication state. This is intentionally sensitive to
these private cleanup/function identities; a redesigned cleanup architecture
must port the scheduler, not silently skip it. Budget probes observe actual
public H calls, unchanged local fixture transport, and S subprocess waits.

Every child restores its own signals/context BEFORE fixture.doCleanups(), checks
root removal afterward, and returns bounded JSON. Assertions in this parent are
correct-behavior assertions, never assertions that a known defect occurred.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace
import gc
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import unittest
from unittest.mock import patch

import test_checkpoint_git_runtime as git_fixtures
import test_checkpoint_runtime as fixtures

SOURCE = Path(__file__).resolve().parents[1]
CHILD_LIMIT_SECONDS = 45
JSON_LIMIT_BYTES = 256 * 1024
OPERATION_SECONDS = 10.0


def descriptors():
    result = {}
    for name in os.listdir('/proc/self/fd'):
        try:
            result[int(name)] = os.readlink('/proc/self/fd/' + name)
        except FileNotFoundError:
            pass
    return result


def signal_state():
    return {
        'handler': str(signal.getsignal(signal.SIGALRM)),
        'timer': signal.getitimer(signal.ITIMER_REAL),
        'mask': sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, set())),
        'pending': sorted(int(s) for s in signal.sigpending()),
    }


def restore_signals(handler, timer, mask):
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    signal.setitimer(signal.ITIMER_REAL, 0.0)
    if signal.SIGALRM in signal.sigpending():
        signal.sigwait({signal.SIGALRM})
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, *timer)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def non_git_operation(f, data, case):
    api = f.runtime_api('h15_cooperative_' + case)
    original_handler = signal.getsignal(signal.SIGALRM)
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    hits = []

    def caller_handler(signum, frame):
        hits.append(signum)

    signal.signal(signal.SIGALRM, caller_handler)
    baseline = f.s._cleanup_guard.get()
    token = f.s._cleanup_guard.set(baseline)
    prior_locator = f.locator.read_bytes()
    prior_tree = f.tree(f.control.snapshot_path)
    project_tree = f.tree(f.project)
    fixture_fds = descriptors()
    reached = []
    guard_code = api._Timer.guard.__wrapped__.__code__
    owners = {
        'nested_s_lock': f.s._resources.__wrapped__.__code__,
        'runtime_lock': api.checkpoint.__code__,
        'cleanup_scope_reset': f.s.cleanup_scope.__wrapped__.__code__,
        'caller_signal_restore': api._deadline.__wrapped__.__code__,
    }

    def trace(frame, event, arg):
        if reached or event != 'call' or frame.f_code is not guard_code:
            return trace
        enter = frame.f_back
        if enter is None or enter.f_code is not contextlib._GeneratorContextManager.__enter__.__code__:
            return trace
        owner = enter.f_back
        if owner is None or owner.f_code is not owners.get(case):
            return trace
        if case == 'nested_s_lock':
            ancestor = owner.f_back
            while ancestor and ancestor.f_code is not f.s.create_snapshot.__code__:
                ancestor = ancestor.f_back
            if ancestor is None or owner.f_locals['stack'] is not ancestor.f_locals.get('stack'):
                return trace
        changed = f.locator.read_bytes() != prior_locator
        state_written = (f.state_dir / 'state.json').exists()
        if not changed or (case != 'nested_s_lock' and not state_written):
            return trace
        timer = frame.f_locals['self']
        f.assertEqual(timer._protected, 0, 'Must schedule before guard activation')
        f.assertFalse(timer.expired)
        f.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        chain = []
        ancestor = frame
        while ancestor:
            chain.append({'file': ancestor.f_code.co_filename,
                          'function': ancestor.f_code.co_name, 'line': ancestor.f_lineno})
            ancestor = ancestor.f_back
        reached.append({'event': event, 'protected_before': timer._protected,
                        'locator_changed': changed, 'state_written': state_written,
                        'timer_before': signal.getitimer(signal.ITIMER_REAL), 'stack': chain})
        # Actual one-shot expiration, not os.kill and not a synthetic exception.
        signal.setitimer(signal.ITIMER_REAL, 0.005)
        time.sleep(0.025)
        return trace

    try:
        if case != 'normal_non_git':
            sys.settrace(trace)
        try:
            result = api.checkpoint(f.request(api), clock=f.clock)
        finally:
            sys.settrace(None)
        data['trace'] = reached
        data['result'] = {'status': result.status, 'warnings': list(result.warnings),
                          'snapshot_status': None if result.snapshot is None else result.snapshot.status,
                          'push_status': None if result.push is None else result.push.status}
        data['post_operation_signal'] = signal_state()
        data['checks'].update({
            'handler_restored': signal.getsignal(signal.SIGALRM) is caller_handler,
            'timer_restored': signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            'mask_restored': signal.pthread_sigmask(signal.SIG_BLOCK, set()) == original_mask,
            'no_pending_alarm': signal.SIGALRM not in signal.sigpending(),
            'context_reset': f.s._cleanup_guard.get() is baseline,
            'runtime_lock_removed': not (f.state_dir / '.checkpoint.lock').exists(),
            's_lock_removed': not (f.locator.parent / '.snapshot.lock').exists(),
            'caller_handler_not_invoked': hits == [],
        })
        # Preserve the diagnostic's real standalone-S followup: an expired H
        # ContextVar must not poison a later independent capture. Record failure
        # here, then let the outer correct-behavior assertion report it.
        restore_signals(original_handler, (0.0, 0.0), original_mask)
        if case == 'cleanup_scope_reset':
            try:
                followup = f.s.create_snapshot(f.snapshot_request)
            except BaseException as error:
                data['standalone_s_after_h'] = type(error).__name__
                data['checks']['standalone_s_usable'] = False
            else:
                data['standalone_s_after_h'] = followup.status
                data['checks']['standalone_s_usable'] = followup.status == 'unchanged'
        # Integrity verification must not inherit a reproduced expired context.
        f.s._cleanup_guard.reset(token)
        token = None
        gc.collect()
        current = Path(json.loads(f.locator.read_text())['path'])
        manifest = f.s.verify_snapshot(current, f.identity, f.settings.limits)
        prior = f.s.verify_snapshot(f.control.snapshot_path, f.identity, f.settings.limits)
        data['checks'].update({
            'current_snapshot_complete': manifest.completion == 'complete',
            'current_payload_exact': f.s.read_payload(current, 'notes.md', f.identity,
                                                      f.settings.limits) == fixtures.CONTEXT,
            'current_distinct_from_prior': current != f.control.snapshot_path / 'manifest.json',
            'prior_snapshot_unchanged': f.tree(f.control.snapshot_path) == prior_tree,
            'prior_snapshot_complete': prior.completion == 'complete',
            'prior_payload_exact': f.s.read_payload(f.control.snapshot_path, 'notes.md', f.identity,
                                                    f.settings.limits) == fixtures.INITIAL_CONTEXT,
            'source_project_unchanged': f.tree(f.project) == project_tree,
            'fds_restored_after_gc': descriptors() == fixture_fds,
            'no_stage': list((f.locator.parent / '.staging').iterdir()) == [],
            'no_scratch': not any(p.name.startswith(('thunderkit-S-git-', '.checkpoint-state-', '.locator-'))
                                  for p in f.base.rglob('*')),
        })
        data['assertions_reached'] = sorted(data['checks'])
    finally:
        sys.settrace(None)
        restore_signals(original_handler, (0.0, 0.0), original_mask)
        if token is not None:
            f.s._cleanup_guard.reset(token)


def git_operation(case, data):
    f = case.fixture
    case.assertNotEqual(f.old, f.oid)
    case.assertEqual(f.remote_oid(), f.old)
    case.assertEqual(f.remote_oid('refs/heads/main'), f.old)
    case.assertEqual(f.raw('merge-base', '--is-ancestor', f.old, f.oid).returncode, 0)
    before_project = fixtures.RuntimeFixture().tree(f.repo)
    data['fixture_prevalidated'] = True
    budgets = {'authority': [], 'transport': [], 'local_wait': []}
    samples = {}
    deadline = []
    references = str(SOURCE / 'skills/references') + '/'

    def trace(frame, event, arg):
        if frame.f_code.co_filename.startswith(references):
            if event == 'line':
                # Timestamp the caller expression BEFORE argument evaluation.
                samples[frame.f_code] = time.monotonic()
                if frame.f_code is case.api.checkpoint.__code__:
                    timer = frame.f_locals.get('timer')
                    if timer is not None and not deadline:
                        deadline.append(timer.end)
        return trace

    def observe(kind, timeout, caller):
        case.assertTrue(deadline, 'Observe the actual H timer before callbacks/waits')
        at = samples[caller.f_code]
        budgets[kind].append({'timeout': timeout, 'original_end': deadline[0],
                              'sampled_before_call': at, 'remaining_ceiling': deadline[0] - at,
                              'caller': caller.f_code.co_name})

    def authority(request, *, timeout=None):
        # Missing keyword must reach the later behavioral assertion, not TypeError
        # swallowed by H's provider diagnostics handler.
        case.assertEqual((request.identity, request.repository, request.oid, request.destination_ref),
                         (f.identity, f.repo, f.oid, f.ref))
        observe('authority', timeout, sys._getframe(1))
        return f.grant

    def driver(argv, *, cwd, timeout):
        observe('transport', timeout, sys._getframe(1))
        return f.driver(argv, cwd=cwd, timeout=timeout)

    original_wait = git_fixtures.push_fixtures.POPEN.wait

    def wait(process, timeout=None):
        caller = sys._getframe(1)
        if caller.f_code is case.s._Git.run.__code__:
            observe('local_wait', timeout, caller)
        return original_wait(process, timeout=timeout)

    request = replace(case.request(), deadline_seconds=OPERATION_SECONDS)
    started = time.monotonic()
    with patch.object(git_fixtures.push_fixtures.POPEN, 'wait', wait):
        sys.settrace(trace)
        try:
            result = case.api.checkpoint(request, clock=case.clock, authority=authority, driver=driver)
        finally:
            sys.settrace(None)
    data['operation_seconds'] = time.monotonic() - started
    case.assert_history_saved(result)
    case.assertEqual(result.push.status, 'pushed')
    case.assertEqual(f.remote_oid(), f.oid)
    case.assertEqual(f.remote_oid('refs/heads/main'), f.old)
    case.assertEqual(f.remote_oid('refs/heads/lane'), f.old)
    case.assertEqual(f.driver.writes, 1)
    case.assertGreaterEqual(f.driver.reads, 3)
    case.assertEqual(f.driver.calls[-1], f.driver.read_argv())
    case.assertNotIn('authority_provider_failed', result.warnings)
    data['result'] = {'status': result.status, 'push_status': result.push.status,
                      'snapshot_status': result.snapshot.status, 'warnings': list(result.warnings),
                      'writes': f.driver.writes, 'reads': f.driver.reads,
                      'remote_oid': f.remote_oid(), 'expected_oid': f.oid}
    data['budgets'] = budgets
    locator = case.backups / 'projects' / f.identity.id / 'latest.json'
    data['checks'].update({
        'source_project_unchanged': fixtures.RuntimeFixture().tree(f.repo) == before_project,
        'runtime_lock_removed': not (case.state / '.checkpoint.lock').exists(),
        's_lock_removed': not (locator.parent / '.snapshot.lock').exists(),
        'no_stage': list((locator.parent / '.staging').iterdir()) == [],
        'no_scratch': not any(p.name.startswith(('thunderkit-S-git-', '.checkpoint-state-', '.locator-'))
                              for p in f.base.rglob('*')),
        'all_budget_boundaries_reached': all(budgets.values()),
    })
    data['assertions_reached'] = sorted(data['checks']) + ['real_snapshot', 'exact_remote_readback']


def child(case_name):
    data = {'case': case_name, 'checks': {}, 'assertions_reached': [], 'harness_error': None}
    before_fds = descriptors()
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    case = None
    fixture = None
    context = None
    token = None
    baseline = None
    started = time.monotonic()
    try:
        assert old_timer == (0.0, 0.0), 'Child must start with an idle timer'
        if case_name in ('authority_budget', 'transport_budget', 'local_wait_budget', 'normal_git'):
            case = git_fixtures.GitCheckpointRuntimeTests()
            case.setUp()
            fixture = case.fixture
            context = case.s._cleanup_guard
        else:
            case = fixture = fixtures.RuntimeFixture()
            case.setUp()
            context = fixture.s._cleanup_guard
        baseline = context.get()
        token = context.set(baseline)
        data['fixture_root'] = str(fixture.base)
        data['fixture_prevalidated'] = True
        if isinstance(case, git_fixtures.GitCheckpointRuntimeTests):
            git_operation(case, data)
        else:
            non_git_operation(fixture, data, case_name)
    except BaseException:
        data['harness_error'] = traceback.format_exc()
    finally:
        sys.settrace(None)
        restore_signals(old_handler, old_timer, old_mask)
        if token is not None:
            assert context is not None
            context.reset(token)
        context_restored = context is not None and context.get() is baseline
        cleanups_ok = case.doCleanups() if case is not None else False
        gc.collect()
        extras = {fd: name for fd, name in descriptors().items() if fd not in before_fds}
        for fd in extras:
            os.close(fd)
        data['cleanup_receipt'] = {
            'doCleanups': cleanups_ok,
            'fixture_removed': fixture is not None and not fixture.base.exists(),
            'context_restored_before_doCleanups': context_restored,
            'handler_restored': signal.getsignal(signal.SIGALRM) == old_handler,
            'timer_restored': signal.getitimer(signal.ITIMER_REAL) == old_timer,
            'mask_restored': signal.pthread_sigmask(signal.SIG_BLOCK, set()) == old_mask,
            'no_pending_alarm': signal.SIGALRM not in signal.sigpending(),
            'fds_restored': descriptors() == before_fds,
            'no_manual_fd_closes': not extras,
        }
        data['elapsed_seconds'] = time.monotonic() - started
    encoded = json.dumps(data, sort_keys=True)
    assert len(encoded.encode()) <= JSON_LIMIT_BYTES
    print(encoded, flush=True)
    # Child exit describes valid observation/teardown, not product correctness.
    return int(data['harness_error'] is not None or not all(data['cleanup_receipt'].values()))


class CooperativeDeadlineTests(unittest.TestCase):
    def observation(self, name):
        completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child', name],
                                   cwd=SOURCE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                   capture_output=True, text=True, timeout=CHILD_LIMIT_SECONDS)
        receipts = os.environ.get('H15_RECEIPTS_DIR')
        if receipts:
            target = Path(receipts)
            with (target / (name + '.log')).open('x') as stream:
                stream.write(completed.stdout + '\nSTDERR:\n' + completed.stderr)
        self.assertLessEqual(len(completed.stdout.encode()), JSON_LIMIT_BYTES)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        data = json.loads(completed.stdout)
        self.assertIsNone(data['harness_error'])
        self.assertTrue(data['fixture_prevalidated'])
        self.assertTrue(data['assertions_reached'], 'Behavioral assertions must actually be reached')
        self.assertTrue(all(data['cleanup_receipt'].values()), data['cleanup_receipt'])
        self.assertFalse(Path(data['fixture_root']).exists(), 'Parent verifies removal after doCleanups')
        self.assertEqual(completed.stderr, '')
        return data

    def correct_cleanup(self, name):
        data = self.observation(name)
        self.assertEqual(len(data['trace']), 1, 'Exact cleanup-entry scheduling seam must be reached')
        self.assertEqual(data['result']['status'], 'failed')
        self.assertIn('deadline_exceeded', data['result']['warnings'])
        self.assertIsNone(data['result']['push_status'])
        self.assertEqual([key for key, ok in data['checks'].items() if not ok], [],
                         'H15 cleanup-entry alarm must not strand owned resources or caller state')

    def correct_budget(self, name, kind):
        data = self.observation(name)
        self.assertTrue(all(data['checks'].values()), data['checks'])
        self.assertEqual(data['result']['push_status'], 'pushed', 'Prove real operation before budget assertion')
        values = data['budgets'][kind]
        self.assertTrue(values)
        bad = []
        for value in values:
            timeout = value['timeout']
            ceiling = min(OPERATION_SECONDS, value['remaining_ceiling'], 30.0)
            if not (type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= ceiling):
                bad.append(value)
        self.assertEqual(bad, [], 'H15 ' + kind + ' requires finite positive remaining original-deadline timeout; '
                         'unbudgeted fallback and restarted per-call budgets are forbidden')

    def test_00_normal_non_git_capture_control(self):
        data = self.observation('normal_non_git')
        self.assertEqual(data['trace'], [])
        self.assertEqual(data['result']['status'], 'captured')
        self.assertTrue(all(data['checks'].values()), data['checks'])

    def test_01_nested_snapshot_cleanup_entry_releases_s_lock(self):
        self.correct_cleanup('nested_s_lock')

    def test_02_runtime_cleanup_entry_releases_h_lock(self):
        self.correct_cleanup('runtime_lock')

    def test_03_cleanup_scope_entry_resets_context(self):
        self.correct_cleanup('cleanup_scope_reset')

    def test_04_deadline_cleanup_entry_restores_caller_signals(self):
        self.correct_cleanup('caller_signal_restore')

    def test_05_normal_git_capture_publication_control(self):
        data = self.observation('normal_git')
        self.assertTrue(all(data['checks'].values()), data['checks'])
        self.assertEqual(data['result']['push_status'], 'pushed')

    def test_06_fresh_authority_receives_remaining_deadline(self):
        self.correct_budget('authority_budget', 'authority')

    def test_07_publication_driver_receives_remaining_deadline(self):
        self.correct_budget('transport_budget', 'transport')

    def test_08_local_git_wait_receives_remaining_deadline(self):
        self.correct_budget('local_wait_budget', 'local_wait')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        raise SystemExit(child(sys.argv[2]))
    unittest.main()
