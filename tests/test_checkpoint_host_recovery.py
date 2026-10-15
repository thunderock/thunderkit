"""Fresh-process recovery of partial host and scheduler operations."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
import test_checkpoint_host_lifecycle as L
import test_checkpoint_schedule as T

class RecoveryTests(L.HostLifecycleFixture):
    def fresh(self, body, *args):
        code = 'import json,sys; from pathlib import Path; '+f'sys.path.insert(0,{str(L.REFERENCES)!r}); import checkpoint_host as A; '+body
        return self.child([str(Path(sys.executable).resolve()), '-I', '-B', '-c', code, str(self.state), str(self.f.project), *map(str,args)])

    def test_partial_removal_fresh_approved_retry(self):
        r = self.install()
        unlink = os.unlink
        def fail(path, *a, **kw):
            if path == 'connection.json':
                raise PermissionError('fixture unlink')
            return unlink(path, *a, **kw)
        with patch.object(self.api.os, 'unlink', side_effect=fail):
            failed = self.api.remove_connections(r, approval=True)
        self.assertEqual(failed.status, 'blocked')
        self.assertFalse(self.hook.exists())
        self.assertTrue(self.binding.exists())
        self.loader()
        body = "r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); v=A.remove_connections(r,approval=sys.argv[3]=='yes'); print(json.dumps({'status':v.status,'blockers':v.blockers}))"
        before=L.tree(self.f.base)
        code,out,err,_=self.fresh(body,'no')
        self.assertEqual((code,json.loads(out)['status']),(0,'approval_required'),err)
        self.assertEqual(L.tree(self.f.base),before)
        code,out,err,_=self.fresh(body,'yes')
        self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))
        self.assertFalse(self.binding.exists())
        self.assertFalse(self.ownership.exists())

    def test_unknown_schedule_fresh_guard_and_readback_retry(self):
        r=self.install()
        class Unknown(T.SchedulerFixture):
            def read(self):
                if self.writes: raise OSError('fixture unknown')
                return super().read()
        driver=Unknown()
        p=self.api.preview_schedule(r,driver,service_qualified=True,exclusive_writer=True)
        v=self.api.apply_schedule(p,driver,approval=True)
        self.assertEqual(v.status,'blocked')
        self.assertTrue(callable(getattr(self.api,'load_schedule',None)), 'missing persisted schedule recovery loader')
        table=self.f.base/'saved-table'; table.write_bytes(driver.table)
        body=("r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); v=A.remove_connections(r,approval=True); print(json.dumps({'status':v.status,'blockers':v.blockers}))")
        before=L.tree(self.f.base)
        code,out,err,_=self.fresh(body)
        self.assertEqual(code,0,err)
        self.assertIn('owned_schedule_present',json.loads(out)['blockers'])
        self.assertEqual(L.tree(self.f.base),before)
        body=("s=A.load_schedule(Path(sys.argv[1]),project_root=Path(sys.argv[2])); "
              "D=type('D',(),{'read':lambda self:Path(sys.argv[3]).read_bytes(),'replace':lambda self,e,c:False if self.read()!=e else (Path(sys.argv[3]).write_bytes(c)>=0)}); "
              "v=A.remove_schedule(s,D(),approval=True); print(json.dumps({'status':v.status,'blockers':v.blockers}))")
        code,out,err,_=self.fresh(body,table)
        self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))
        self.assertEqual(table.read_bytes(),p.previous)
        self.assertEqual(self.api.remove_connections(r,approval=True).status,'removed')

    def test_unknown_removal_readback_fresh_retry(self):
        r=self.install(); driver=T.SchedulerFixture()
        p=self.api.preview_schedule(r,driver,service_qualified=True,exclusive_writer=True)
        installed=self.api.apply_schedule(p,driver,approval=True)
        read=driver.read
        def unknown():
            if len(driver.writes)>1: raise OSError('fixture removal readback')
            return read()
        with patch.object(driver,'read',side_effect=unknown):
            failed=self.api.remove_schedule(installed.installation,driver,approval=True)
        self.assertEqual(failed.status,'blocked')
        self.assertEqual(driver.table,p.previous)
        self.assertEqual(self.api.remove_connections(r,approval=True).blockers,('owned_schedule_present',))
        table=self.f.base/'table'; table.write_bytes(driver.table)
        body=("s=A.load_schedule(Path(sys.argv[1]),project_root=Path(sys.argv[2])); "
              "D=type('D',(),{'read':lambda self:Path(sys.argv[3]).read_bytes(),'replace':lambda *args:(_ for _ in ()).throw(AssertionError('must reconcile without write'))}); "
              "v=A.remove_schedule(s,D(),approval=True); print(json.dumps({'status':v.status,'blockers':v.blockers}))")
        code,out,err,_=self.fresh(body,table)
        self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))
        self.assertEqual(self.api.remove_connections(r,approval=True).status,'removed')

    def test_initial_receipt_write_failure_retains_restartable_ownership(self):
        original=self.api._write_new
        unlink=os.unlink
        def failed_write(path,data,mode):
            if path.name=='connection-receipt.json': raise PermissionError('fixture receipt creation')
            return original(path,data,mode)
        def failed_unlink(path,*args,**kwargs):
            if path=='connection.json': raise PermissionError('fixture cleanup unavailable')
            return unlink(path,*args,**kwargs)
        with patch.object(self.api,'_write_new',side_effect=failed_write), patch.object(self.api.os,'unlink',side_effect=failed_unlink):
            result=self.api.apply_connections(self.api.preview_connections(self.case.request(self.api)),approval=True)
        self.assertEqual(result.status,'blocked')
        self.assertFalse(self.hook.exists(),'no executable hook before persistent ownership')
        if self.binding.exists():
            self.assertTrue(self.ownership.is_file(),'retained binding needs fresh-process ownership')
            code,out,err,_=self.fresh("r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); v=A.remove_connections(r,approval=True); print(json.dumps({'status':v.status}))")
            self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))

    def test_byte_identical_owned_replacement_rejected(self):
        r=self.install()
        old=self.binding.with_name('original-binding')
        self.binding.rename(old); self.binding.write_bytes(old.read_bytes()); self.binding.chmod(0o600)
        before=L.tree(self.f.base)
        with self.assertRaises(self.f.c.ContinuityError): self.loader()(self.state,project_root=self.f.project)
        self.assertEqual(self.api.remove_connections(r,approval=True).status,'blocked')
        self.assertEqual(L.tree(self.f.base),before)

    def test_install_readback_failure_fresh_retry(self):
        load=self.api.load_connections
        def unavailable(*args,**kwargs):
            raise OSError('fixture receipt readback')
        with patch.object(self.api,'load_connections',side_effect=unavailable):
            result=self.api.apply_connections(self.api.preview_connections(self.case.request(self.api)),approval=True)
        self.assertEqual(result.status,'blocked')
        self.assertTrue(self.ownership.exists())
        self.assertFalse(self.hook.exists())
        code,out,err,_=self.fresh("r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); v=A.remove_connections(r,approval=True); print(json.dumps({'status':v.status,'blockers':v.blockers}))")
        self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))
        self.assertFalse(self.ownership.exists())

    def test_decoder_rejects_foreign_directory_duplicate_and_private_modes(self):
        self.install(); original=self.ownership.read_bytes()
        foreign=self.f.base/'foreign';foreign.mkdir()
        for value in (dict(json.loads(original),directories=json.loads(original)['directories']+[[str(foreign),list(self.api._identity(foreign))]]),
                      dict(json.loads(original),files=json.loads(original)['files']*2)):
            self.ownership.write_text(json.dumps(value));before=L.tree(self.f.base)
            with self.assertRaises(self.f.c.ContinuityError): self.loader()(self.state,project_root=self.f.project)
            self.assertEqual(before,L.tree(self.f.base))
        self.ownership.write_bytes(original); self.ownership.chmod(0o644)
        with self.assertRaises(self.f.c.ContinuityError): self.loader()(self.state,project_root=self.f.project)
        self.ownership.chmod(0o600)
        self.assertIsNotNone(self.loader()(self.state,project_root=self.f.project))

    def test_changed_and_special_helpers_never_execute(self):
        self.install(local_runtime=True)
        runtime=self.runtime/'checkpoint_runtime.py';original=runtime.read_bytes()
        marker=self.f.base/'executed'
        for data in ((f"from pathlib import Path; Path({str(marker)!r}).touch()\n").encode(), b'x'*(self.f.c.CONFIG_MAX_BYTES+1)):
            runtime.write_bytes(data)
            code,out,err,_=self.child([str(self.hook),'--tick'])
            self.assertEqual(code,2);self.assertEqual(json.loads(err)['error']['code'],'connection_runtime_changed')
            self.assertNotIn(str(self.f.base).encode(),err);self.assertFalse(marker.exists())
        runtime.unlink(); os.mkfifo(runtime)
        code,out,err,_=self.child([str(self.hook),'--tick'])
        self.assertEqual(code,2); self.assertEqual(json.loads(err)['error']['code'],'connection_runtime_changed')
        runtime.unlink();runtime.write_bytes(original)

    def test_scheduler_accepts_pinned_root_owned_executable_without_launch(self):
        from unittest.mock import patch
        with patch.object(self.api.subprocess,'Popen',side_effect=AssertionError('constructor must not launch')):
            try:
                driver=self.api.CrontabDriver(Path('/usr/bin/true'),service_qualified=True,exclusive_writer=True)
            except self.f.c.ContinuityError as error:
                self.fail('Root-owned regular executable should qualify without launch: '+error.code)
            self.assertIsNotNone(driver)

    def test_staged_cleanup_failure_fresh_retry(self):
        unlink=os.unlink
        def failed_unlink(path,*args,**kwargs):
            if isinstance(path,str) and path.startswith('.connection-'):
                raise PermissionError('fixture stage cleanup')
            return unlink(path,*args,**kwargs)
        with patch.object(self.api,'load_connections',side_effect=OSError('fixture initial readback')), patch.object(self.api.os,'unlink',side_effect=failed_unlink):
            result=self.api.apply_connections(self.api.preview_connections(self.case.request(self.api)),approval=True)
        self.assertEqual(result.status,'blocked')
        stages=[p for parent in (self.state,self.hook.parent) for p in parent.iterdir() if p.name.startswith('.connection-')]
        self.assertEqual(len(stages),2)
        self.assertTrue(set(stages)<=set(result.retained_paths))
        code,out,err,_=self.fresh("r=A.load_connections(Path(sys.argv[1]),project_root=Path(sys.argv[2])); v=A.remove_connections(r,approval=True); print(json.dumps({'status':v.status,'blockers':v.blockers}))")
        self.assertEqual((code,json.loads(out)['status']),(0,'removed'),(out,err))
        self.assertTrue(all(not p.exists() for p in stages),'fresh retry must remove exact pending ownership')

    def test_pending_receipt_cannot_adopt_foreign_or_changed_files(self):
        unlink=os.unlink
        def failed_unlink(path,*args,**kwargs):
            if isinstance(path,str) and path.startswith('.connection-'): raise PermissionError('fixture pending')
            return unlink(path,*args,**kwargs)
        with patch.object(self.api,'load_connections',side_effect=OSError('fixture readback')), patch.object(self.api.os,'unlink',side_effect=failed_unlink):
            result=self.api.apply_connections(self.api.preview_connections(self.case.request(self.api)),approval=True)
        original=self.ownership.read_bytes(); raw=json.loads(original)
        pending=Path(raw['pending'][0]['path']); saved=pending.read_bytes()
        pending.write_bytes(saved+b'altered')
        before=L.tree(self.f.base)
        with self.assertRaises(self.f.c.ContinuityError): self.loader()(self.state,project_root=self.f.project)
        self.assertEqual(before,L.tree(self.f.base));pending.write_bytes(saved)
        raw['pending'][0]['path']=str(self.f.project/'notes.md')
        self.ownership.write_text(json.dumps(raw));before=L.tree(self.f.base)
        with self.assertRaises(self.f.c.ContinuityError): self.loader()(self.state,project_root=self.f.project)
        self.assertEqual(before,L.tree(self.f.base));self.ownership.write_bytes(original)
        loaded=self.loader()(self.state,project_root=self.f.project)
        self.assertEqual(self.api.remove_connections(loaded,approval=True).status,'removed')

    def test_unrelated_hooks_content_and_runtime_loss_do_not_prevent_removal(self):
        r=self.install(local_runtime=True)
        extra=self.hook.parent/'unrelated-note';extra.write_bytes(b'not owned')
        (self.runtime/'checkpoint_runtime.py').unlink()
        r=self.loader()(self.state,project_root=self.f.project)
        result=self.api.remove_connections(r,approval=True)
        self.assertEqual(result.status,'removed',result.blockers)
        self.assertEqual(extra.read_bytes(),b'not owned')
        self.assertFalse(self.hook.exists())
        self.assertFalse(self.binding.exists())

if __name__=='__main__': unittest.main(verbosity=2)
