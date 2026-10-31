"""Generated-only Task15 tests, 6.75s nominal check arms plus3s zero-check probe."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[3]
MODULE=ROOT/'z3-nia/experiment/product_line_diagnostic.py'
CORE=ROOT/'z3-nia/experiment/product_graph_diagnostic.py'
SOURCE=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_042232_924488/source')

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load():
    spec=importlib.util.spec_from_file_location('product_line_under_test',MODULE)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

class LineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=set(sys.modules);cls.module=load()
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='product-line-test-')
        self.addCleanup(self.temporary.cleanup);self.directory=Path(self.temporary.name)
    def tearDown(self):
        self.assertFalse(any(n=='z3' or n.startswith('z3.') for n in set(sys.modules)-self.before))
    def config(self):
        return self.module.Config(sdk_python=SOURCE/'python',sdk_library=SOURCE/'python/z3/lib/libz3.so',
            initial_helper=SOURCE/'script/run_initial.py',strict_helper=SOURCE/'script/run_strict_bound.py',
            first_helper=SOURCE/'z3-nia/experiment/strict_first_diagnostic.py',timeout=3,arm_seconds=.75)
    def record(self,text,best='8',direction='min',index=0):
        source=self.directory/f'source-{index}.smt2';source.write_text(text)
        return dict(index=index,filename=f'generated-{index}',category='generated',source=str(source.resolve()),
            source_sha256=digest(source),direction=direction,M=best,seed={'x':'-99','y':'999999','b':False})
    def test_rotation_default_core_unchanged(self):
        self.assertEqual(self.module.arm_order(0),['line_plain','line_seeded'])
        self.assertEqual(self.module.arm_order(1),['line_seeded','line_plain'])
        self.assertEqual(self.module.core.arm_order(0),['lia_plain','lia_seeded'])
    def test_stop_and_source_guards_without_z3(self):
        record=self.record('(declare-const x Int)(minimize x)(check-sat)')
        stop=threading.Event();stop.set()
        with mock.patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            result=self.module.run_case(record,self.config(),self.directory/'stop',stop)
            self.assertEqual(result['status'],'interrupted')
            record['source_sha256']='0'*64
            result=self.module.run_case(record,self.config(),self.directory/'bad-sha',threading.Event())
            self.assertEqual(result['status'],'error')

    @unittest.skipUnless(os.environ.get('NIA_PRODUCT_LINE_REAL_TOYS')=='1','fixed generated-only solver group')
    def test_line_semantics_and_one_default_proof(self):
        evidence=Path(os.environ['NIA_PRODUCT_LINE_TEST_ARTIFACT_DIR']).resolve()
        evidence.mkdir(exist_ok=False);self.directory=evidence
        first=self.module.core.runner._first(self.config().first_helper)
        initial=first._helper(self.config().initial_helper)
        code=initial._stage([sys.executable,str(Path(__file__).resolve()),'--structure-probe',str(evidence)],
                           time.monotonic()+3,threading.Event(),evidence/'structure.stdout',evidence/'structure.stderr')
        self.assertEqual(code,0,(evidence/'structure.stderr').read_text())
        base=('(declare-const x Int)(declare-const y Int)'
              '(assert (>= x 0))(assert (<= x 10))(assert (>= y 0))(assert (<= y 10))'
              '(assert (= (* x y) 7))')
        boolean=base+('(declare-const b Bool)(declare-const g Int)'
            '(assert (not (and (not b) (not (= y 1)))))'
            '(assert (or (and b (= x 1)) (and (not b) (= y 1))))'
            '(assert (= g (ite b (+ x y) (+ y x))))')
        cases=[(base+'(minimize (+ x y))(check-sat)','8','min','unsat'),
               (base+'(minimize (+ x y))(check-sat)','9','min','sat'),
               (boolean+'(maximize g)(check-sat)','7','max','sat'),
               (boolean+'(maximize g)(check-sat)','8','max','unsat')]
        for index,(text,best,direction,answer) in enumerate(cases):
            result=self.module.run_case(self.record(text,best,direction,index),self.config(),evidence/f'case-{index}',threading.Event())
            self.assertEqual(result['status'],'complete',result)
            for arm in result['arms']:
                self.assertEqual((arm['status'],arm['answer']),('complete',answer),arm)
                self.assertIs(arm['integer_sat_verified'],answer=='sat')
                self.assertIs(arm['integer_bound_unsat_verified'],answer=='unsat')
                self.assertIs(result['is_optimal'],False)
                policy=arm['policy'];self.assertEqual(policy['tactic'],'product-line-qf-lia')
                self.assertEqual(policy['initial_cut_records'],1 if arm['arm']=='line_plain' else 3)
                self.assertEqual(digest(policy['final_query_path']),policy['final_query_sha256'])
                if answer=='sat':
                    self.assertEqual(arm['objective_value'],'8')
                    model=json.loads(Path(policy['model_path']).read_text())
                    self.assertEqual(model['objective_value'],'8')
                else:
                    cert=json.loads(Path(policy['certificate_path']).read_text())
                    self.assertTrue(any(c['kind']=='line_left' for c in cert['cuts']))
                    self.assertFalse(any(c['kind']=='orthant' for c in cert['cuts']))
        # Exactly one needed default real proof; the default setup query must
        # remain byte-identical to retained Task14's xy=7/M8 initial query.
        record=self.record(cases[0][0],index=10)
        result=self.module.core.runner.run_case(record,self.config(),evidence/'default-proof',threading.Event(),
            arms=['lia_plain'],worker_path=CORE,run_metadata=dict(version='product-graph-diagnostic-v1',tactic='product-graph-qf-lia'))
        self.assertEqual(result['status'],'complete',result)
        arm=result['arms'][0];self.assertEqual(arm['answer'],'unsat',arm)
        old=ROOT/'z3-nia/logs/research-round2/product-graph-diagnostic-component/final-focused/case-2/lia_plain.smt2'
        self.assertEqual(digest(arm['query_path']),digest(old))

def structure_probe(output):
    module=load();output=Path(output)
    import builtins,ctypes
    library=SOURCE/'python/z3/lib/libz3.so'
    assert digest(library)=='01e8020b2e3283842f25e9dea77663a3a4af058c47c690ea065ccc0fe31443ef'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for name in ('Z3_LIBRARY_PATH','PATH','PYTHONPATH'):os.environ.pop(name,None)
    sys.path.insert(0,str(SOURCE/'python'))
    import z3
    module.core.runner._mapped_libraries(library)
    x,y,p=z3.Ints('line_x line_y line_p')
    results=[]
    for square in (False,True):
        factors=x*x if square else x*y
        forms=[factors==7,x+y<=7]
        for arm in ('line_plain','line_seeded'):
            payload=dict(record={'direction':'min','seed':{'line_x':'-99','line_y':'999'}},deadline=time.monotonic()+2)
            # A semantic RED uses the current production orthant strategy,
            # before strategy='line' exists, rather than a constructor error.
            if 'strategy' in __import__('inspect').signature(module.core.SolverFacade).parameters:
                facade=module.core.SolverFacade(z3,arm,payload,output/f'{arm}-{square}.json',strategy='line')
            else:
                facade=module.core.SolverFacade(z3,'lia_plain',payload,output/f'{arm}-{square}.json')
            facade.add(*forms)
            count=len(facade.solver.assertions())-len(facade.lifted)
            expected=(5 if square else 1)+(2 if arm=='line_seeded' else 0)
            assert count==expected,(arm,square,count,expected)
            assert all(c['kind']!='orthant' for c in facade.cuts)
            results.append(dict(arm=arm,square=square,initial_assertions=count))
    count=0
    for kind in ('line_left','line_right'):
        for a in range(-4,5):
            forms=module.core.cut_assertions(z3,dict(p=p,left=x,right=y),dict(kind=kind,product='line_p',a=str(a)))
            assert len(forms)==1
            for xv in range(-5,6):
                for yv in range(-5,6):
                    exact=z3.substitute(forms[0],(x,z3.IntVal(xv)),(y,z3.IntVal(yv)),(p,z3.IntVal(xv*yv)))
                    assert z3.is_true(z3.simplify(exact)),(kind,a,xv,yv)
                    if (xv if kind=='line_left' else yv)==a:
                        wrong=z3.substitute(forms[0],(x,z3.IntVal(xv)),(y,z3.IntVal(yv)),(p,z3.IntVal(xv*yv+1)))
                        assert z3.is_false(z3.simplify(wrong))
                    count+=1
    (output/'structure.json').write_text(json.dumps(dict(solver_checks=0,initial=results,signed_assignments=count)))

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--structure-probe':structure_probe(sys.argv[2])
    else:unittest.main()
