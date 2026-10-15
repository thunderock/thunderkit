"""Deterministic same-state install interleaving and receipt ownership."""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_checkpoint_global_schedule as F


class GlobalInstallConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.f = F.CheckpointGlobalScheduleTests()
        self.addCleanup(self.clean)
        self.f.setUp()
        self.a = self.f.api()

    def clean(self):
        self.assertTrue(self.f.doCleanups())
        self.assertFalse(self.f.base.exists())

    def preview(self):
        request = self.f.snapshot_request()
        self.assertEqual(self.f.s.create_snapshot(request).status, 'complete')
        value = self.f.preview(self.a, request, service_qualified=True, exclusive_writer=True)
        self.assertEqual(value.status, 'approval_required')
        return value

    def test_overlapping_installer_cannot_poison_first_installation(self):
        preview = self.preview()
        real_lock = self.a.Host._lock
        first = []
        active = False
        after_first = []

        @contextmanager
        def interleave(state, name):
            nonlocal active
            if not active and name == self.a._LOCK:
                active = True
                result = self.a.apply_global(preview, self.f.driver, approval=True)
                self.assertEqual(result.status, 'configured', result.blockers)
                first.append(result.installation)
                after_first.append(F.tree(self.f.state))
            with real_lock(state, name):
                yield

        # Actual first apply completes immediately before the outer apply takes
        # its lock. Its re-preview must therefore happen under that same lock.
        with patch.object(self.a.Host, '_lock', side_effect=interleave):
            second = self.a.apply_global(preview, self.f.driver, approval=True)
        self.assertEqual(second.status, 'blocked')
        self.assertEqual({p: v for p, v in F.tree(self.f.state).items() if p != '.'},
                         {p: v for p, v in after_first[0].items() if p != '.'},
                         'Second install left foreign staging beside the first install')
        self.assertIsNone(second.installation)
        self.assertEqual(second.retained_paths, ())
        self.assertEqual(self.a.load_global(self.f.state, identity=self.f.identity), first[0])
        code, out, err = self.f.tick(first[0])
        self.assertEqual((code, err), (0, ''))
        self.assertIn(json.loads(out)['status'], ('captured', 'not-due'))
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)

    def test_receipt_collision_does_not_adopt_foreign_file_or_leave_staging(self):
        preview = self.preview()
        write = self.a.Host._write_new
        foreign = b'Foreign receipt must survive unchanged.\n'
        def collide(path, content, mode):
            if path.name == self.a._RECEIPT:
                with path.open('xb') as stream:
                    stream.write(foreign)
                path.chmod(0o600)
                raise FileExistsError('fixture foreign receipt appeared')
            return write(path, content, mode)
        with patch.object(self.a.Host, '_write_new', side_effect=collide):
            result = self.a.apply_global(preview, self.f.driver, approval=True)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual({p.name for p in self.f.state.iterdir()}, {self.a._RECEIPT}, 'Only the unowned receipt may remain')
        self.assertEqual((self.f.state / self.a._RECEIPT).read_bytes(), foreign)
        self.assertIsNone(result.installation)
        self.assertEqual(result.retained_paths, ())
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)


    def test_completed_receipt_write_error_preserves_fresh_removal(self):
        preview = self.preview()
        record = self.a.Host._record
        def failed_readback(path):
            if path.name == self.a._RECEIPT:
                raise OSError('fixture receipt readback failed after completed exclusive write')
            return record(path)
        with patch.object(self.a.Host, '_record', side_effect=failed_readback):
            result = self.a.apply_global(preview, self.f.driver, approval=True)
        self.assertEqual(result.status, 'blocked')
        paths = {self.f.state / name for name in
                 ('.global-binding.stage', '.global-tick.stage', self.a._RECEIPT)}
        self.assertTrue(all(path.is_file() for path in paths), 'Completed receipt lost its owned stages')
        self.assertTrue(paths <= set(result.retained_paths), 'Owned receipt was not disclosed')
        self.assertEqual(self.f.table.read_bytes(), F.UNRELATED)
        loaded = self.a.load_global(self.f.state, identity=self.f.identity)
        self.assertEqual(loaded.phase, 'staged')
        self.assertEqual(self.f.fresh_remove(True)['status'], 'removed')
        self.assertTrue(all(not path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main(verbosity=2)
