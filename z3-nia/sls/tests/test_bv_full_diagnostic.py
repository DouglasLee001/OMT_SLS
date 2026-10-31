"""Small generated Task16 tests; six one-second external arm windows total."""
from pathlib import Path
import hashlib
import importlib.util
import inspect
import json
import math
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[3]
MODULE=ROOT/'z3-nia/experiment/bv_full_diagnostic.py'
SOURCE=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_042232_924488/source')
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load():
    spec=importlib.util.spec_from_file_location('bv_full_under_test',MODULE)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

class FullBvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.before=set(sys.modules);cls.module=load()
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='bv-full-test-');self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)
    def tearDown(self):
        self.assertFalse(any(n=='z3' or n.startswith('z3.') for n in set(sys.modules)-self.before))
    def config(self,timeout=3,arm_seconds=1):
        return self.module.Config(sdk_python=SOURCE/'python',sdk_library=SOURCE/'python/z3/lib/libz3.so',
            initial_helper=SOURCE/'script/run_initial.py',strict_helper=SOURCE/'script/run_strict_bound.py',
            first_helper=SOURCE/'z3-nia/experiment/strict_first_diagnostic.py',timeout=timeout,arm_seconds=arm_seconds)
    def record(self,text='(declare-const x Int)(minimize x)(check-sat)',best='0',direction='min',index=0):
        source=self.directory/f'source-{index}.smt2';source.write_text(text)
        return dict(index=index,filename=f'generated-{index}',category='generated',source=str(source.resolve()),
                    source_sha256=digest(source),direction=direction,M=best)
    def call_core(self,record,config,path,arms,cap=None,stop=None):
        kwargs=dict(arms=arms)
        if cap is not None and 'max_arm_seconds' in inspect.signature(self.module.runner.run_case).parameters:
            kwargs['max_arm_seconds']=cap
        return self.module.runner.run_case(record,config,path,stop or threading.Event(),**kwargs)
    def test_explicit_singleton_long_cap_admitted_without_process(self):
        first=self.module.runner._first(self.config().first_helper)
        initial=first._helper(self.config().initial_helper)
        record=self.record()
        for index,(timeout,allowance,cap) in enumerate([(1200,1140,1140),(1200,1200,1200)]):
            with mock.patch.object(initial,'_stage',side_effect=RuntimeError('admitted-to-stage')) as stage:
                result=self.call_core(record,self.config(timeout,allowance),self.directory/f'accepted-{index}',['bv_extended_full'],cap)
                self.assertEqual(stage.call_count,1,result)
                self.assertIn('admitted-to-stage',result['reason'])
    def test_default_rejects31_but_short_case_keeps_deadline_clamp(self):
        first=self.module.runner._first(self.config().first_helper);initial=first._helper(self.config().initial_helper)
        record=self.record()
        with mock.patch.object(initial,'_stage',side_effect=RuntimeError('admitted-short')) as stage:
            result=self.call_core(record,self.config(1200,31),self.directory/'default31',['int_closed'])
            self.assertEqual(stage.call_count,0,result);self.assertEqual(result['status'],'error')
            result=self.call_core(record,self.config(3,30),self.directory/'short30',['int_closed'])
            self.assertEqual(stage.call_count,1,result);self.assertIn('admitted-short',result['reason'])
    def test_invalid_caps_and_long_multi_arm_rejected(self):
        record=self.record()
        cases=[(0,1200,1,['int_closed']),(-1,1200,1,['int_closed']),(float('nan'),1200,1,['int_closed']),
               (float('inf'),1200,1,['int_closed']),(1201,1200,1,['int_closed']),
               (1140,1000,1,['int_closed']),(1140,1200,1,['int_closed','int_strict']),
               (1140,1200,1141,['int_closed'])]
        with mock.patch('subprocess.Popen',side_effect=AssertionError('invalid must not launch')) as process:
            for i,(cap,total,allowance,arms) in enumerate(cases):
                result=self.call_core(record,self.config(total,allowance),self.directory/f'invalid-{i}',arms,cap)
                self.assertEqual(result['status'],'error',result)
                self.assertFalse(any(a['answer'] is not None for a in result['arms']))
            process.assert_not_called()
    def test_stop_and_bad_source_do_not_launch(self):
        record=self.record();stop=threading.Event();stop.set()
        with mock.patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            result=self.module.run_case(record,self.config(),self.directory/'stop',stop)
            self.assertEqual(result['status'],'interrupted',result)
            record['source_sha256']='0'*64
            result=self.module.run_case(record,self.config(),self.directory/'bad',threading.Event())
            self.assertEqual(result['status'],'error',result)

    def test_range_metadata_distinguishes_source_and_objective(self):
        classify=getattr(self.module,'classify_int128',lambda *args:dict(source_int128_compatible=None,objective_int128_compatible=None))
        huge=1<<127
        flags=classify({'x':'1','b':True},{'x':'Int','b':'Bool'},huge)
        self.assertIs(flags['source_int128_compatible'],True)
        self.assertIs(flags['objective_int128_compatible'],False)
        flags=classify({'x':str(huge)},{'x':'Int'},0)
        self.assertIs(flags['source_int128_compatible'],False)
        self.assertIs(flags['objective_int128_compatible'],True)

    def test_late_completed_receipt_loses_answer_and_flags(self):
        first=self.module.runner._first(self.config().first_helper);initial=first._helper(self.config().initial_helper)
        def stage(command,deadline,stop,stdout,stderr):
            payload=json.loads(Path(command[-2]).read_text())
            arm=self.module.runner._empty_arm(payload['arm'])
            arm.update(status='complete',answer='sat',check_started=True,integer_sat_verified=True)
            first._write(command[-1],arm)
            return 0
        with mock.patch.object(initial,'_stage',side_effect=stage):
            result=self.module.run_case(self.record(),self.config(3,1e-7),self.directory/'late',threading.Event())
        self.assertEqual(result['status'],'complete',result)
        arm=result['arms'][0]
        self.assertEqual(arm['status'],'timeout',arm)
        self.assertIsNone(arm['answer'])
        self.assertIs(arm['integer_sat_verified'],False)
        self.assertIs(arm['integer_bound_unsat_verified'],False)

    @unittest.skipUnless(os.environ.get('NIA_BV_FULL_REAL_TOYS')=='1','fixed generated-only SDK group')
    def test_fixed_min_max_models_and_under(self):
        evidence=Path(os.environ['NIA_BV_FULL_TEST_ARTIFACT_DIR']).resolve();evidence.mkdir(exist_ok=False);self.directory=evidence
        hard=('(declare-const x Int)(declare-const g Int)(declare-const b Bool)'
              '(assert (>= x (- 2)))(assert (<= x 2))'
              '(assert (or (and b (>= x 0)) (and (not b) (<= x 0))))(assert (= g (* x x)))')
        cases=[(hard+'(assert (= x 0))(minimize g)(check-sat)','2','min','sat','0'),
               (hard+'(minimize g)(check-sat)','0','min','unsat',None),
               (hard+'(maximize g)(check-sat)','1','max','sat','4'),
               (hard+'(maximize g)(check-sat)','4','max','unsat',None),
               ('(declare-const x Int)(assert (= (* x x) 10000))(minimize x)(check-sat)','100','min','not-unsat',None),
               ('(declare-const x Int)(declare-const g Int)(assert (= x 170141183460469231731687303715884105728))'
                '(assert (= g x))(minimize g)(check-sat)','170141183460469231731687303715884105730','min','sat','170141183460469231731687303715884105728')]
        for index,(text,best,direction,expected,value) in enumerate(cases):
            result=self.module.run_case(self.record(text,best,direction,index),self.config(),evidence/f'case-{index}',threading.Event())
            self.assertEqual(result['status'],'complete',result);self.assertEqual(len(result['arms']),1)
            arm=result['arms'][0];self.assertEqual(arm['status'],'complete',arm)
            self.assertEqual(arm['arm'],'bv_extended_full')
            if expected=='not-unsat':self.assertNotEqual(arm['answer'],'unsat',arm)
            else:self.assertEqual(arm['answer'],expected,arm)
            self.assertEqual(digest(arm['query_path']),arm['query_sha256'])
            self.assertEqual(arm['policy']['sat_max_conflicts'],4294967295)
            self.assertIsNone(arm['policy']['engine_max_memory_mb'])
            self.assertIs(result['is_optimal'],False)
            if arm['answer']=='sat':
                saved=json.loads(Path(arm['policy']['model_path']).read_text())
                self.assertEqual(digest(arm['policy']['model_path']),arm['policy']['model_sha256'])
                self.assertEqual(saved['source_sha256'],result['hashes']['source'])
                self.assertEqual(saved['objective_value'],arm['objective_value'])
                if value is not None:self.assertEqual(saved['objective_value_exact'],value)
                self.assertIs(saved['native_int128_compatible'],index!=5)
                self.assertIs(saved['native_bridge_verified'],False)

if __name__=='__main__':unittest.main()
