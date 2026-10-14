"""Bounded supplemental authority/cleanup tests; never a real scheduler."""
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_checkpoint_global_schedule as F


class GlobalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = F.CheckpointGlobalScheduleTests()
        self.addCleanup(self.clean)
        self.f.setUp()
        self.signals = (signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL))
        self.a = self.f.api()

    def clean(self):
        ok = self.f.doCleanups()
        self.assertTrue(ok)
        self.assertFalse(self.f.base.exists())
        self.assertEqual((signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)), self.signals)

    def test_receipt_duplicate_foreign_and_replacement_refusal(self):
        installed = self.f.install(self.a)
        receipt = self.f.state / 'global-receipt.json'
        original = receipt.read_bytes()
        for content in (b'{"schema":1,"schema":1}', original[:-2] + b',"foreign":true}\n'):
            receipt.write_bytes(content)
            before = F.tree(self.f.base)
            with self.assertRaises(self.f.c.ContinuityError):
                self.a.load_global(self.f.state, identity=self.f.identity)
            self.assertEqual(self.a.remove_global(installed, self.f.driver, approval=True).status, 'blocked')
            self.assertEqual(F.tree(self.f.base), before)
        receipt.write_bytes(original)
        replacement = self.f.state / 'replacement'
        replacement.write_bytes(original)
        replacement.chmod(0o600)
        replacement.replace(receipt)
        before = F.tree(self.f.base)
        self.assertEqual(self.a.remove_global(installed, self.f.driver, approval=True).status, 'blocked')
        self.assertEqual(F.tree(self.f.base), before)

    def test_stale_pinned_runtime_has_no_side_effect(self):
        runtime = self.f.base / 'runtime'
        runtime.mkdir(mode=0o700)
        for name in self.a._MODULES:
            shutil.copyfile(F.REFERENCES / (name + '.py'), runtime / (name + '.py'))
        with patch.object(self.a, '__file__', str(runtime / 'checkpoint_global.py')):
            installed = self.f.install(self.a)
        (runtime / 'backup_store.py').write_bytes(b"raise RuntimeError('must not execute')\n")
        before = F.tree(self.f.base)
        code, out, err = self.f.tick(installed)
        self.assertEqual(code, 2)
        self.assertNotIn('Traceback', err)
        self.assertEqual(json.loads(err)['error']['code'], 'global_runtime_changed')
        self.assertEqual(F.tree(self.f.base), before)

    def test_removal_readback_unknown_fresh_retry(self):
        installed = self.f.install(self.a)
        owned = {p for p in self.f.state.rglob('*') if p.is_file()}
        self.f.driver.writes.clear()
        self.f.driver.unknown_after_write = True
        result = self.a.remove_global(installed, self.f.driver, approval=True)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)
        self.assertTrue(all(p.is_file() for p in owned))
        self.assertTrue(owned <= set(result.retained_paths))
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertTrue(all(not p.exists() for p in owned))

    def test_partial_cleanup_is_fresh_process_recoverable(self):
        installed = self.f.install(self.a)
        unlink = self.a.Host._unlink
        def interrupted(item):
            if item.path.name == 'global-binding.json':
                raise OSError('fixture cleanup interrupted')
            return unlink(item)
        with patch.object(self.a.Host, '_unlink', side_effect=interrupted):
            result = self.a.remove_global(installed, self.f.driver, approval=True)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)
        self.assertFalse(Path(shlex.split(installed.periodic_command)[0]).exists())
        self.assertTrue((self.f.state / 'global-binding.json').exists())
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertFalse((self.f.state / 'global-receipt.json').exists())

    def test_current_scope_change_refuses_without_writes(self):
        installed = self.f.install(self.a)
        for change in ('approved_sources', 'interval', 'limits', 'disabled', 'invalid'):
            document = json.loads(self.f.config.read_bytes())
            if change == 'approved_sources':
                document['global']['approved_sources'] = [str(self.f.notes)]
            elif change == 'interval':
                document['policy']['backup']['interval_minutes'] = 17
            elif change == 'limits':
                document['policy']['backup']['limits'] = {'max_files': 9999}
            elif change == 'disabled':
                document['policy']['backup']['enabled'] = False
            self.f.config.write_text('{"schema":true}' if change == 'invalid' else json.dumps(document))
            before = F.tree(self.f.base)
            code, out, err = self.f.tick(installed)
            self.assertEqual(code, 2)
            self.assertEqual(F.tree(self.f.base), before)
            self.f.config.write_text(json.dumps(self.f.document))

    def test_real_installed_tick_due_age_and_no_alarm(self):
        installed = self.f.install(self.a)
        saved_at = __import__('time').time() - 120
        self.f.notes.write_bytes(F.NOTE + b'Changed by the recovery fixture.\n')
        os.utime(self.f.notes, (saved_at, saved_at))
        code, out, err = self.f.tick(installed)
        self.assertEqual((code, err), (0, ''))
        value = json.loads(out)
        self.assertEqual(value['status'], 'captured')
        self.assertGreaterEqual(value['context_age_seconds'], 120)
        self.f.verify_snapshot(value['snapshot']['snapshot_path'], self.f.notes.read_bytes())
        before = F.tree(self.f.backup)
        code, out, err = self.f.tick(installed)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['status'], 'not-due')
        self.assertGreaterEqual(json.loads(out)['context_age_seconds'], 120)
        self.assertEqual(F.tree(self.f.backup), before)
        owned = [p for p in self.f.state.iterdir() if p.is_file()]
        installed_paths = {p.path for p in installed.files} | {installed.receipt.path, installed.guard.path}
        self.f.table.write_bytes(self.f.table.read_bytes() + F.LATER)
        self.f.monitor.write_text(json.dumps(list(map(str, owned))))
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED + F.LATER)
        self.assertTrue(all(not p.exists() for p in installed_paths))
        calls = self.f.calls()
        written = next(i for i, row in enumerate(calls) if row['args'] == ['-'])
        self.assertTrue(all(calls[written]['owned_present']))
        self.assertTrue(all(next(row for row in calls[written+1:] if row['args'] == ['-l'])['owned_present']))

    def test_staged_guard_failure_is_durably_removable(self):
        request = self.f.snapshot_request()
        self.assertEqual(self.f.s.create_snapshot(request).status, 'complete')
        preview = self.f.preview(self.a, request, service_qualified=True, exclusive_writer=True)
        write = self.a.Host._write_new
        def fail_guard(path, data, mode):
            if path.name == 'global-guard.json':
                raise OSError('fixture guard creation failure')
            return write(path, data, mode)
        with patch.object(self.a.Host, '_write_new', side_effect=fail_guard):
            result = self.a.apply_global(preview, self.f.driver, approval=True)
        self.assertEqual(result.status, 'blocked')
        loaded = self.a.load_global(self.f.state, identity=self.f.identity)
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)
        self.assertEqual(self.f.driver.writes, [])
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertFalse((self.f.state / 'global-receipt.json').exists())

    def test_existing_runtime_state_is_preserved_and_due(self):
        request = self.f.snapshot_request()
        result = self.f.h.checkpoint(self.f.h.CheckpointRequest(request, self.f.state, trigger='due'))
        self.assertEqual(result.status, 'captured')
        state = self.f.state / 'state.json'
        previous = state.read_bytes()
        preview = self.f.preview(self.a, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, 'approval_required')
        installed = self.a.apply_global(preview, self.f.driver, approval=True).installation
        code, out, err = self.f.tick(installed)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['status'], 'not-due')
        self.assertEqual(state.read_bytes(), previous)
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertEqual(state.read_bytes(), previous)

    def test_scoped_receipt_capacity_precedes_staging(self):
        request = self.f.snapshot_request()
        self.assertEqual(self.f.s.create_snapshot(request).status, 'complete')
        # A legal bound on the encoded ownership envelope must be checked by
        # preview, not first discovered after exclusive staging writes.
        original = self.a.Host._encoded
        def bounded(value):
            data = original(value)
            if isinstance(value, dict) and value.get('kind') == 'global-checkpoint-guard':
                self.a.Host._require(len(data) <= 4096, 'global_metadata_budget_exceeded')
            return data
        before = F.tree(self.f.base)
        with patch.object(self.a, '_encoded', side_effect=bounded):
            preview = self.f.preview(self.a, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, 'blocked')
        self.assertIn('global_metadata_budget_exceeded', preview.blockers)
        self.assertEqual(F.tree(self.f.base), before)
        self.assertEqual(self.f.driver.reads, 0)

    def test_interpreter_alias_refused(self):
        request = self.f.snapshot_request()
        self.assertEqual(self.f.s.create_snapshot(request).status, 'complete')
        alias = self.f.base / 'python-alias'
        alias.symlink_to(self.f.python)
        schedule = self.a.GlobalScheduleRequest(request, self.f.state, alias, self.f.config)
        before = F.tree(self.f.base)
        self.assertEqual(self.a.preview_global(schedule, self.f.driver, service_qualified=True,
                                             exclusive_writer=True).status, 'blocked')
        self.assertEqual(F.tree(self.f.base), before)
        self.assertEqual(self.f.driver.reads, 0)

    def test_scaled_cadence_bounds_refuse_before_staging(self):
        # Valid configured maximum source count, but the frozen H cadence
        # envelope cannot fit 64KiB. Admission must refuse before any writes.
        sources = tuple(self.f.sources / ('note-' + str(i) + '-' + 'x' * 100) for i in range(500))
        for source in sources:
            source.write_bytes(b'Inert note.\n')
        self.f.document['global']['approved_sources'] = list(map(str, sources))
        self.f.config.write_text(json.dumps(self.f.document))
        settings = self.f.c.resolve_continuity(global_config=self.f.config, scope='global',
                                               expected_identity=self.f.identity, environment={})
        q = self.f.s.qualify_storage(self.f.s.inspect_storage(self.f.backup), approval=True)
        request = self.f.c.SnapshotRequest(settings, q.identity, sources)
        before = F.tree(self.f.base)
        preview = self.f.preview(self.a, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(preview.status, 'blocked')
        self.assertIn('global_state_budget_exceeded', preview.blockers)
        self.assertEqual(F.tree(self.f.base), before)
        self.assertEqual(self.f.driver.reads, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
