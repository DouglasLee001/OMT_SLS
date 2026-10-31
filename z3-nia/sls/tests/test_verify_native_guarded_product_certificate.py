"""Hand-built native/source bridge fixtures; every solver entry is forbidden."""
import builtins,copy,ctypes,hashlib,importlib.util,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock

TREE=Path(__file__).resolve().parents[2]
MODULE=TREE/'experiment/verify_native_guarded_product_certificate.py'
SDK=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_094020_422847/source/python')
FIELDS={'schema','source','source_sha256','direction','own_best','original_objective','used_integer_names','used_bool_names','monomials','cuts','query_path','query_sha256','truncations'}
CURRENT_TRANSLATION_SHA256='1abdad7d3975796a40a0f045bc740d4d9bfd9c3ac36e4a241c16d1bc14c7d3e8'
LEGACY_CHECKER=TREE/'logs/research-round2/guarded-product-resource-component/prior/experiment/verify_guarded_product_certificate.py'
PREVIOUS_CHECKER=TREE/'logs/research-round2/guarded-product-let-depth-component/prior/experiment/verify_guarded_product_certificate.py'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class NativeGuardedProductCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctypes.CDLL(str(SDK/'z3/lib/libz3.so'));builtins.Z3_LIB_DIRS=[str(SDK/'z3/lib')];sys.path.insert(0,str(SDK));import z3
        cls.z3=z3;cls.bridge=None
        if MODULE.is_file():
            spec=importlib.util.spec_from_file_location('native_guarded_bridge_tested',MODULE);cls.bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.bridge)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name);self.source=self.folder/'source.smt2';self.query=self.folder/'actual.smt2';self.native=self.folder/'proof.json'
    def write(self,cert):self.native.write_text(json.dumps(cert,sort_keys=True)+'\n')
    def fixture(self,maximize=False):
        z=self.z3;x,y=z.Ints('x y');rx,ry,rxy,rxx,rxyy=z.Reals('R_x R_y R_xy R_xx R_xyy');guard=rx>=0;eq=rxy==0;bound=rx-1 if maximize else-rx-1
        self.hard=z.Or(x>=0,z.Not(x*y==0));self.source.write_text('(declare-const x Int)(declare-const y Int)(declare-const unused Int)(assert '+self.hard.sexpr()+')'+('(maximize x)'if maximize else'(minimize x)'));self.source_sha=sha(self.source)
        pos=rxx-rx if maximize else-rxx-rx;neg=-rxx+1 if maximize else rxx+2*rx+1
        self.forms=[z.Or(guard,z.Not(eq)),bound>=0,z.Implies(eq,rxyy==0),z.Implies(z.And(guard,bound>=0),pos>=0),z.Implies(z.And(z.Not(guard),bound>=0),neg>=0)]
        solver=z.Solver();solver.add(*self.forms);self.query.write_text('(set-logic QF_LRA)\n'+solver.to_smt2())
        cert=dict(schema='native-source-guarded-product-bound-v1',source_hard=[self.hard.sexpr()],original_objective='x',direction='max'if maximize else'min',own_best='0',used_integer_names=['x','y'],used_bool_names=[],monomials=[dict(name='R_x',variables=['x']),dict(name='R_y',variables=['y']),dict(name='R_xy',variables=['x','y']),dict(name='R_xx',variables=['x','x']),dict(name='R_xyy',variables=['x','y','y'])],cuts=[dict(kind='equality_times_variable',atom=(x*y==0).sexpr(),variable='y'),dict(kind='nonnegative_product',left=dict(atom=(x>=0).sexpr(),polarity=True),right=dict(atom='@bound',polarity=True)),dict(kind='nonnegative_product',left=dict(atom=(x>=0).sexpr(),polarity=False),right=dict(atom='@bound',polarity=True))],query_path=str(self.query),actual_query_smt2=self.query.read_text(),truncations=[])
        self.write(cert);return cert
    def validate(self,cert=None,**kwargs):
        self.assertIsNotNone(self.bridge,'native/source bridge checker missing')
        if cert is not None:self.write(cert)
        settings=dict(deadline=time.monotonic()+3,expected_source=self.source,expected_query=self.query,expected_best='0',expected_source_sha256=self.source_sha);settings.update(kwargs)
        with mock.patch.object(self.z3.Solver,'check',side_effect=AssertionError('Solver.check forbidden')),mock.patch.object(self.z3.Optimize,'check',side_effect=AssertionError('Optimize.check forbidden')),mock.patch.object(self.z3.Tactic,'apply',side_effect=AssertionError('Tactic.apply forbidden')),mock.patch.object(self.z3.Tactic,'__call__',side_effect=AssertionError('Tactic call forbidden')):
            result=self.bridge.validate_certificate(self.z3,self.native,**settings)
        self.assertTrue(result['native_translation_verified']);self.assertTrue(result['source_hard_verified']);self.assertTrue(result['translation_verified']);self.assertEqual(result['solver_calls'],0);self.assertFalse(result['UNSAT_replayed']);self.assertFalse(result['formal_A2']);self.assertFalse(result['is_optimal']);self.assertFalse(result['source_bound_verified']);self.assertEqual(set(result['converted_certificate']),FIELDS)
        return result
    def test_min_max_complete_source_and_query_bind_to_standard_certificate(self):
        for maximize in(False,True):
            with self.subTest(maximize=maximize):
                cert=self.fixture(maximize);result=self.validate(cert);converted=result['converted_certificate'];self.assertEqual(converted['source_sha256'],self.source_sha);self.assertEqual(converted['query_sha256'],sha(self.query));self.assertEqual(result['native_certificate_sha256'],sha(self.native));self.assertEqual(result['translation_checker_sha256'],CURRENT_TRANSLATION_SHA256)
    def test_native_hard_cannot_replace_or_omit_authoritative_source_assertions(self):
        for hard in([],['true'],['false'],['(>= x 0)'],['(and (>= x 0) (not (= (* x y) 0)))'],['(or (>= y 0) (not (= (* x y) 0)))']):
            cert=self.fixture();cert['source_hard']=hard
            with self.subTest(hard=hard),self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();cert['source_hard'].append('true')
        with self.assertRaises(ValueError):self.validate(cert)
    def test_original_objective_direction_and_own_best_are_external_bindings(self):
        for key,value in(('original_objective','y'),('original_objective','(- (- x))'),('direction','max'),('own_best','1'),('own_best','0.0')):
            cert=self.fixture();cert[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture()
        with self.assertRaises(ValueError):self.validate(cert,expected_direction='max')
        with self.assertRaises(ValueError):self.validate(cert,expected_best=None)
    def test_source_bytes_and_expected_path_are_authoritative(self):
        cert=self.fixture();self.source.write_text(self.source.read_text()+'\n; changed after incumbent freeze\n')
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();other=self.folder/'other.smt2';other.write_text('(declare-const x Int)(minimize x)')
        with self.assertRaises(ValueError):self.validate(cert,expected_source=other)
    def test_embedded_actual_query_and_disk_query_must_be_identical(self):
        cert=self.fixture();cert['actual_query_smt2']+='\n; different checked bytes\n'
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();cert['query_path']=str(self.folder/'foreign.smt2')
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();self.query.write_text(self.query.read_text()+'\n; disk changed\n')
        with self.assertRaises(ValueError):self.validate(cert)
    def test_empty_native_query_path_requires_explicit_identical_saved_query(self):
        cert=self.fixture();cert['query_path']='';result=self.validate(cert);self.assertFalse(result['native_query_path_present'])
        with self.assertRaises(ValueError):self.validate(cert,expected_query=None)
    def test_native_printer_let_Bool_and_Int_scope_is_parsed_without_trusting_it(self):
        cert=self.fixture();cert['source_hard']=['(let ((g (>= x 0)) (e (= (* x y) 0))) (or g (not e)))'];cert['original_objective']='(let ((x y) (y x)) y)'
        try:self.validate(cert)
        except ValueError as error:self.fail('valid printer let/scopes were rejected: '+str(error))
        cert['original_objective']='(let ((x y) (y x)) x)'
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();cert['original_objective']='(let ((unused (div 4 2))) x)'
        with self.assertRaises(ValueError):self.validate(cert)
    def test_standard_checker_still_rejects_cut_mapping_and_extra_query_assumptions(self):
        changes=[lambda c:c['cuts'][1]['left'].update(polarity=False),lambda c:c['monomials'][2].update(variables=['y','x']),lambda c:c['used_integer_names'].append('unused')]
        for change in changes:
            cert=self.fixture();change(cert)
            with self.subTest(change=change),self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();self.query.write_text(self.query.read_text().replace('(check-sat)','(assert false)\n(check-sat)'));cert['actual_query_smt2']=self.query.read_text()
        with self.assertRaises(ValueError):self.validate(cert)
    def test_native_expression_injection_unsupported_syntax_and_schema_are_rejected(self):
        for key,value in(('source_hard',['(>= x 0)) (check-sat) (assert false)']),('source_hard',['(= (div 4 2) 2)']),('source_hard',['b']),('original_objective','x) (check-sat)'),('used_bool_names',['b']),('extra',1),('schema','other')):
            cert=self.fixture();cert[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate(cert)
    def test_duplicate_json_keys_wrong_checker_and_resource_guards_fail_closed(self):
        cert=self.fixture();self.write(cert);text=self.native.read_text();self.native.write_text(text.replace('"schema":','"schema":"other","schema":',1))
        with self.assertRaises(ValueError):self.validate()
        cert=self.fixture();fake=self.folder/'checker.py';fake.write_text('raise RuntimeError("must not execute")\n')
        with self.assertRaises(ValueError):self.validate(cert,translation_checker=fake)
        with self.assertRaises(ValueError):self.validate(cert,max_nodes=1)
        with self.assertRaises(TimeoutError):self.validate(cert,deadline=time.monotonic()-1)

    def many_variables_fixture(self):
        z=self.z3;names=['v_'+str(i).zfill(4)for i in range(257)];variables=[z.Int(name)for name in names];reals=[z.Real('R_'+name)for name in names];hard=z.Sum(*variables)>=0
        self.source.write_text(''.join('(declare-const '+name+' Int)'for name in names)+'(assert '+hard.sexpr()+')(minimize '+names[0]+')');self.source_sha=sha(self.source)
        solver=z.Solver();solver.add(z.Sum(*reals)>=0,-reals[0]-1>=0);self.query.write_text('(set-logic QF_LRA)\n'+solver.to_smt2())
        return dict(schema='native-source-guarded-product-bound-v1',source_hard=[hard.sexpr()],original_objective=names[0],direction='min',own_best='0',used_integer_names=names,used_bool_names=[],monomials=[dict(name='R_'+name,variables=[name])for name in names],cuts=[],query_path=str(self.query),actual_query_smt2=self.query.read_text(),truncations=[])

    def test_new_checker_variable_resource_is_forwarded_without_relaxing_other_gates(self):
        cert=self.many_variables_fixture()
        try:self.validate(cert)
        except ValueError as error:self.fail('new default resource checker not used: '+str(error))
        self.validate(cert,max_variables=8192)
        with self.assertRaises(ValueError):self.validate(cert,max_variables=256)
        for cap in(0,8193,True):
            with self.subTest(cap=cap),self.assertRaises(ValueError):self.validate(cert,max_variables=cap)

    def test_explicit_legacy_frozen_checker_keeps_its_256_variable_contract(self):
        cert=self.fixture();result=self.validate(cert,translation_checker=LEGACY_CHECKER);self.assertEqual(result['translation_checker_sha256'],'9fb3cf5db313d7968f68c75960999049969ea85d4b0931ef3ef516af4704d5b4')
        spec=importlib.util.spec_from_file_location('explicit_legacy_guarded_checker',LEGACY_CHECKER);legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy);self.validate(cert,translation_checker=legacy)
        with self.assertRaises(ValueError):self.validate(cert,translation_checker=legacy,max_variables=8192)
        cert=self.many_variables_fixture()
        with self.assertRaises(ValueError):self.validate(cert,translation_checker=legacy)

    def test_new_default_accepts_deep_query_printer_let_without_changing_old_versions(self):
        cert=self.fixture();count=400;declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        first='(let ((unused R_x)) '*count+self.forms[0].sexpr()+')'*count
        self.query.write_text('(set-logic QF_LRA)\n'+declarations+'\n(assert '+first+')\n'+'\n'.join('(assert '+f.sexpr()+')'for f in self.forms[1:]));cert['actual_query_smt2']=self.query.read_text()
        try:self.validate(cert)
        except ValueError as error:self.fail('new query let dependency was not selected: '+str(error))
        for old in(LEGACY_CHECKER,PREVIOUS_CHECKER):
            with self.subTest(old=old),self.assertRaises(ValueError):self.validate(cert,translation_checker=old)

    def test_explicit_previous_resource_checker_remains_available(self):
        cert=self.fixture();result=self.validate(cert,translation_checker=PREVIOUS_CHECKER,max_variables=8192);self.assertEqual(result['translation_checker_sha256'],'dd897b9bd1aa4596cf899dc66da45f567d4a88c97a4e43e26a15337cabd39091')

if __name__=='__main__':unittest.main()
