"""Generated-only product-graph tests; opt-in checks have <=13s total windows."""
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

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'z3-nia/experiment/product_graph_diagnostic.py'
SOURCE = Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_042232_924488/source')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load():
    spec = importlib.util.spec_from_file_location('product_graph_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

class ProductGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = set(sys.modules)
        cls.module = load()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='product-graph-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.assertFalse(any(n == 'z3' or n.startswith('z3.') for n in set(sys.modules) - self.before))

    def config(self):
        return self.module.Config(sdk_python=SOURCE / 'python', sdk_library=SOURCE / 'python/z3/lib/libz3.so',
                                  initial_helper=SOURCE / 'script/run_initial.py',
                                  strict_helper=SOURCE / 'script/run_strict_bound.py',
                                  first_helper=SOURCE / 'z3-nia/experiment/strict_first_diagnostic.py',
                                  timeout=3, arm_seconds=.75)

    def record(self, text, best='0', direction='min', index=0):
        source = self.directory / f'source-{index}.smt2'; source.write_text(text)
        return dict(index=index, filename=f'generated-{index}.smt2', category='generated', source=str(source),
                    source_sha256=digest(source), direction=direction, M=best,
                    seed={'x':'-999', 'y':'1000000000000000000000000', 'b':False})

    def test_rotation_and_parent_has_no_z3(self):
        self.assertEqual(self.module.arm_order(0), ['lia_plain','lia_seeded'])
        self.assertEqual(self.module.arm_order(1), ['lia_seeded','lia_plain'])

    def test_stop_and_bad_source_never_launch(self):
        record = self.record('(declare-const x Int)(minimize x)(check-sat)')
        stop = threading.Event(); stop.set()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            result = self.module.run_case(record, self.config(), self.directory / 'stopped', stop)
            self.assertEqual(result['status'], 'interrupted')
            record['source_sha256'] = '0'*64
            result = self.module.run_case(record, self.config(), self.directory / 'bad-sha', threading.Event())
            self.assertEqual(result['status'], 'error')
        self.assertIs(result['is_optimal'], False)

    def test_progress_checkpoint_is_durable_without_solver(self):
        facade = object.__new__(self.module.SolverFacade)
        facade.started, facade.last_progress = time.monotonic(), float('-inf')
        facade.progress_path = self.directory/'lia_plain.progress.json'
        facade.phase = 'graph_setup'
        facade.policy = dict(queries=4,refinements=3,products=2,cut_records=17)
        getattr(facade,'_progress',lambda *args,**kwargs: None)('lia_check',force=True)
        self.assertTrue(facade.progress_path.is_file(), 'actual check progress must survive a later child kill')
        saved = json.loads(facade.progress_path.read_text())
        self.assertEqual(saved['phase'],'lia_check')
        self.assertEqual(saved['queries'],4)
        self.assertEqual(saved['refinements'],3)

    @unittest.skipUnless(os.environ.get('NIA_PRODUCT_GRAPH_REAL_TOYS') == '1', 'generated-only SDK checks')
    def test_generated_models_refinement_and_certificates(self):
        evidence = Path(os.environ['NIA_PRODUCT_GRAPH_TEST_ARTIFACT_DIR']).resolve()
        evidence.mkdir(parents=True, exist_ok=False); self.directory = evidence
        self.assertEqual(digest(SOURCE / 'python/z3/lib/libz3.so'),
                         '01e8020b2e3283842f25e9dea77663a3a4af058c47c690ea065ccc0fe31443ef')
        hard = ('(declare-const x Int)(declare-const y Int)'
                '(assert (>= x 0))(assert (<= x 10))(assert (>= y 0))(assert (<= y 10))'
                '(assert (= (* x y) 7))')
        alias = (hard + '(declare-const b Bool)(declare-const g Int)'
                 '(assert (not (and (not b) (not (= y 1)))))'
                 '(assert (or (and b (= x 1)) (and (not b) (= y 1))))'
                 '(assert (= g (ite b (+ x y) (+ y x))))')
        square = ('(declare-const x Int)(declare-const g Int)(assert (= g (* x x)))'
                  '(assert (or (= x (- 3)) (= x 3)))')
        cases = [(hard+'(minimize (+ x y))(check-sat)', '8','min','unsat',''),
                 (hard+'(minimize (+ x y))(check-sat)', '9','min','sat','8'),
                 (hard+'(maximize (+ x y))(check-sat)', '7','max','sat','8'),
                 (hard+'(maximize (+ x y))(check-sat)', '8','max','unsat',''),
                 (alias+'(minimize g)(check-sat)', '8','min','unsat',''),
                 (square+'(minimize g)(check-sat)', '10','min','sat','9')]
        for index, (text,best,direction,answer,value) in enumerate(cases):
            result = self.module.run_case(self.record(text,best,direction,index), self.config(),
                                          evidence / f'case-{index}', threading.Event())
            self.assertEqual(result['status'], 'complete', result)
            for arm in result['arms']:
                self.assertEqual(arm['status'], 'complete', arm)
                self.assertEqual(arm['answer'], answer, arm)
                self.assertIs(arm['integer_sat_verified'], answer == 'sat')
                self.assertIs(arm['integer_bound_unsat_verified'], answer == 'unsat')
                self.assertIs(result['is_optimal'], False)
                self.assertEqual(digest(arm['query_path']), arm['query_sha256'])
                self.assertEqual(digest(arm['policy']['final_query_path']), arm['policy']['final_query_sha256'])
                if answer == 'sat':
                    self.assertEqual(arm['objective_value'], value)
                    self.assertEqual(digest(arm['policy']['model_path']), arm['policy']['model_sha256'])
                    model = json.loads(Path(arm['policy']['model_path']).read_text())
                    self.assertEqual(model['objective_value'], value)
                    self.assertNotIn('__product_graph_0', model['assignments'])
                if answer == 'unsat':
                    cert = json.loads(Path(arm['policy']['certificate_path']).read_text())
                    self.assertEqual(cert['version'], 'product-graph-lia-v1')
                    self.assertEqual(cert['source_sha256'], result['hashes']['source'])
                    self.assertEqual(hashlib.sha256(cert['query_smt2'].encode()).hexdigest(), cert['query_sha256'])
                    self.assertEqual(cert['M'], best)
        first = self.module.runner._first(self.config().first_helper)
        initial = first._helper(self.config().initial_helper)
        code = initial._stage([sys.executable, str(Path(__file__).resolve()), '--lemma-probe', str(evidence)],
                              time.monotonic()+4, threading.Event(), evidence/'lemmas.stdout', evidence/'lemmas.stderr')
        self.assertEqual(code, 0, (evidence/'lemmas.stderr').read_text())
        lemmas = json.loads((evidence/'lemmas.json').read_text())
        self.assertEqual(lemmas['solver_checks'], 0)
        self.assertGreater(lemmas['assignments'], 10000)

def lemma_probe(output):
    module = load()
    import builtins, ctypes
    library = SOURCE/'python/z3/lib/libz3.so'
    ctypes.CDLL(str(library)); builtins.Z3_LIB_DIRS = [str(library.parent)]
    for name in ('Z3_LIBRARY_PATH','PATH','PYTHONPATH'): os.environ.pop(name, None)
    sys.path.insert(0, str(SOURCE/'python'))
    import z3
    module.runner._mapped_libraries(library)
    x,y,p = z3.Ints('lemma_x lemma_y lemma_p')
    counts = 0
    for kind in ('zero','square_nonnegative','orthant','integer_square'):
        anchors = range(-3,4) if kind in ('orthant','integer_square') else [0]
        for a in anchors:
            for b in (range(-3,4) if kind == 'orthant' else [0]):
                right = x if kind in ('square_nonnegative','integer_square') else y
                node = dict(p=p,left=x,right=right)
                forms = module.cut_assertions(z3,node,dict(kind=kind,product='lemma_p',a=str(a),b=str(b)))
                for xv in range(-4,5):
                    for yv in range(-4,5):
                        product = xv*(xv if right.eq(x) else yv)
                        for form in forms:
                            instantiated = z3.substitute(form,(x,z3.IntVal(xv)),(y,z3.IntVal(yv)),(p,z3.IntVal(product)))
                            assert z3.is_true(z3.simplify(instantiated)), (kind,a,b,xv,yv,form)
                            counts += 1
    (Path(output)/'lemmas.json').write_text(json.dumps(dict(solver_checks=0,assignments=counts)))

def structure_probe(output):
    module = load()
    Path(output).mkdir(exist_ok=True)
    import builtins, ctypes
    library = SOURCE/'python/z3/lib/libz3.so'
    ctypes.CDLL(str(library)); builtins.Z3_LIB_DIRS = [str(library.parent)]
    for name in ('Z3_LIBRARY_PATH','PATH','PYTHONPATH'): os.environ.pop(name, None)
    sys.path.insert(0, str(SOURCE/'python'))
    import z3
    module.runner._mapped_libraries(library)
    x,y,z = z3.Ints('structure_x structure_y structure_z')
    b = z3.Bool('structure_b')
    used = z3.Int('__product_graph_0')
    payload = dict(record={'direction':'min'}, deadline=time.monotonic()+2)
    linear = module.SolverFacade(z3,'lia_plain',payload,Path(output)/'linear.json')
    linear.add((-(z3.IntVal(1)+z3.IntVal(2)))*x <= 9)
    assert len(linear.nodes) == 0, 'ground integer coefficient must remain linear'
    assert linear.objective.eq((-(z3.IntVal(1)+z3.IntVal(2)))*x)
    reflected = module.SolverFacade(z3,'lia_plain',payload,Path(output)/'reflection.json')
    reflected.add(x <= z3.IntVal(99))
    assert reflected.objective.eq(x), 'model objective is not the reflected bound literal'
    mixed = module.SolverFacade(z3,'lia_plain',payload,Path(output)/'mixed.json')
    term = (-3)*x*y*z
    forms = [z3.Or(z3.Not(b), term == used), z3.If(b,term,x) <= 8]
    mixed.add(*forms)
    assert len(mixed.nodes) == 2, 'binary DAG must share repeated nonlinear products'
    assert all(str(n['p']) != '__product_graph_0' for n in mixed.nodes), 'fresh name collision'
    substitutions = []
    for n in reversed(mixed.nodes):
        substitutions.append((n['p'],n['left']*n['right']))
    expanded = list(mixed.lifted)
    for pair in substitutions: expanded = [z3.substitute(f,pair) for f in expanded]
    assert all(z3.is_true(z3.simplify(a == b)) for a,b in zip(forms,expanded))
    # Scope/resource exits are conservative and do not perform a solver check.
    mixed.deadline = time.monotonic()-1
    assert mixed.check() == z3.unknown and mixed.policy['queries'] == 0
    bad = module.SolverFacade(z3,'lia_plain',payload,Path(output)/'unsupported.json')
    try: bad.add(x % 2 == 0)
    except module.runner.Unsupported: pass
    else: raise AssertionError('mod must be explicitly unsupported')
    Path(output).mkdir(exist_ok=True)
    (Path(output)/'structure.json').write_text(json.dumps(dict(solver_checks=0,products=2,constant_products=0,
                                                               expanded_source_equal=True,timeout_unknown=True)))

def final_focused(output):
    """Three .5s-per-arm cases; bind final progress/model/certificate changes."""
    module = load(); output = Path(output); output.mkdir(exist_ok=False)
    config = module.Config(sdk_python=SOURCE/'python',sdk_library=SOURCE/'python/z3/lib/libz3.so',
                           initial_helper=SOURCE/'script/run_initial.py',strict_helper=SOURCE/'script/run_strict_bound.py',
                           first_helper=SOURCE/'z3-nia/experiment/strict_first_diagnostic.py',timeout=3,arm_seconds=.5)
    ground = ('(declare-const x Int)(declare-const g Int)(declare-const b Bool)'
              '(assert (= x 2))(assert b)(assert (= g (* (- (+ 1 2)) x)))')
    nonlinear = ('(declare-const x Int)(declare-const y Int)'
                 '(assert (>= x 0))(assert (<= x 10))(assert (>= y 0))(assert (<= y 10))'
                 '(assert (= (* x y) 7))(minimize (+ x y))(check-sat)')
    cases = [(ground+'(minimize g)(check-sat)','0','min','sat'),
             (ground+'(maximize g)(check-sat)','-10','max','sat'),
             (nonlinear,'8','min','unsat')]
    for index,(text,best,direction,answer) in enumerate(cases):
        source = output/f'source-{index}.smt2'; source.write_text(text)
        record = dict(index=index,filename=f'focused-{index}',category='generated',source=str(source.resolve()),
                      source_sha256=digest(source),M=best,direction=direction,seed={'x':'-999','y':'99999','b':False})
        result = module.run_case(record,config,output/f'case-{index}',threading.Event())
        assert result['status']=='complete',result
        for arm in result['arms']:
            assert arm['status']=='complete' and arm['answer']==answer,arm
            progress = json.loads(Path(arm['policy']['progress_path']).read_text())
            assert progress['phase']=='return' and progress['queries']>=1,progress
            assert digest(arm['query_path'])==arm['query_sha256']
            if answer=='sat':
                model = json.loads(Path(arm['policy']['model_path']).read_text())
                assert model['objective_value']==arm['objective_value']=='(- 6)',model
                assert model['assignments']=={'x':'2','g':'-6','b':True},model
                assert arm['policy']['products']==0,arm
            else:
                assert Path(arm['policy']['certificate_path']).is_file(),arm
    first = module.runner._first(config.first_helper)
    code = first._helper(config.initial_helper)._stage(
        [sys.executable,str(Path(__file__).resolve()),'--model-replay',str(output)],
        time.monotonic()+2,threading.Event(),output/'model-replay.stdout',output/'model-replay.stderr')
    assert code==0,(output/'model-replay.stderr').read_text()
    (output/'verification.json').write_text(json.dumps(dict(module_sha256=digest(MODULE),test_sha256=digest(__file__),
                                                           cases=3,arms=6,arm_seconds=.5,nominal_solver_windows=3,
                                                           source_model_replays=4,model_replay_solver_checks=0),indent=2))

def model_replay(output):
    module = load()
    import builtins,ctypes
    library=SOURCE/'python/z3/lib/libz3.so'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for name in ('Z3_LIBRARY_PATH','PATH','PYTHONPATH'):os.environ.pop(name,None)
    sys.path.insert(0,str(SOURCE/'python'))
    import z3
    module.runner._mapped_libraries(library)
    strict=module.runner._first(SOURCE/'z3-nia/experiment/strict_first_diagnostic.py')._helper(SOURCE/'script/run_strict_bound.py')
    for path in Path(output).glob('case-*/*.model.json'):
        saved=json.loads(path.read_text());source=Path(saved['source_path'])
        assert digest(source)==saved['source_sha256']
        filtered,direction,_=strict._query(source.read_text())
        parsed=z3.Optimize();parsed.from_string(filtered)
        substitutions=[]
        for name,value in saved['assignments'].items():
            if saved['sorts'][name]=='Int':pair=z3.Int(name),z3.IntVal(value)
            else:pair=z3.Bool(name),z3.BoolVal(value)
            substitutions.append(pair)
        assert all(z3.is_true(z3.simplify(z3.substitute(f,*substitutions))) for f in parsed.assertions()),path
        value=z3.simplify(z3.substitute(parsed.objectives()[0],*substitutions))
        assert z3.is_int_value(value) and value.as_long()==-6
        assert (-6<int(saved['M']) if direction=='min' else -6>int(saved['M'])),path
        assert value.sexpr()==saved['objective_value'],path

if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--lemma-probe': lemma_probe(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--structure-probe': structure_probe(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--final-focused': final_focused(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--model-replay': model_replay(sys.argv[2])
    else: unittest.main()
