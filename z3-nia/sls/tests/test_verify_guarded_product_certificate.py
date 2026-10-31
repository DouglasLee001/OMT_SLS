"""Independent hand-built guarded-product translation tests; no SMT checks."""
import builtins,copy,ctypes,hashlib,importlib.util,json,re,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock

TREE=Path(__file__).resolve().parents[2]
MODULE=TREE/'experiment/verify_guarded_product_certificate.py'
SDK=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_094020_422847/source/python')

class GuardedProductCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctypes.CDLL(str(SDK/'z3/lib/libz3.so'));builtins.Z3_LIB_DIRS=[str(SDK/'z3/lib')];sys.path.insert(0,str(SDK))
        import z3
        cls.z3=z3;cls.checker=None
        if MODULE.is_file():
            spec=importlib.util.spec_from_file_location('independent_guarded_certificate_tested',MODULE);cls.checker=importlib.util.module_from_spec(spec);sys.modules[spec.name]=cls.checker;spec.loader.exec_module(cls.checker)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name);self.source=self.folder/'source.smt2';self.query=self.folder/'actual.smt2'

    def save_query(self,cert,forms=None,text=None):
        if text is None:
            solver=self.z3.Solver();solver.add(*(forms if forms is not None else self.forms));text='(set-logic QF_LRA)\n'+solver.to_smt2()
        self.query.write_text(text);cert['query_path']=str(self.query);cert['query_sha256']=hashlib.sha256(text.encode()).hexdigest()

    def fixture(self,maximize=False):
        z=self.z3;x,y=z.Ints('x y');rx,ry,rxy,rxx,rxyy=z.Reals('R_x R_y R_xy R_xx R_xyy');self.real=(rx,ry,rxy,rxx,rxyy)
        source='(declare-const x Int)(declare-const y Int)(declare-const unused Int)(assert (or (>= x 0) (not (= (* x y) 0))))'
        source+='(maximize x)'if maximize else'(minimize x)';self.source.write_text(source)
        guard=rx>=0;equality=rxy==0;bound=rx-1 if maximize else-rx-1
        positive=rxx-rx if maximize else-rxx-rx;negative=-rxx+1 if maximize else rxx+2*rx+1
        self.forms=[z.Or(guard,z.Not(equality)),bound>=0,z.Implies(equality,rxyy==0),z.Implies(z.And(guard,bound>=0),positive>=0),z.Implies(z.And(z.Not(guard),bound>=0),negative>=0)]
        cert=dict(schema='source-guarded-product-bound-v1',source=str(self.source),source_sha256=hashlib.sha256(source.encode()).hexdigest(),direction='max'if maximize else'min',own_best='0',original_objective='x',used_integer_names=['x','y'],used_bool_names=[],
                  monomials=[dict(name='R_x',variables=['x']),dict(name='R_y',variables=['y']),dict(name='R_xy',variables=['x','y']),dict(name='R_xx',variables=['x','x']),dict(name='R_xyy',variables=['x','y','y'])],
                  cuts=[dict(kind='equality_times_variable',atom=(x*y==0).sexpr(),variable='y'),dict(kind='nonnegative_product',left=dict(atom=(x>=0).sexpr(),polarity=True),right=dict(atom='@bound',polarity=True)),dict(kind='nonnegative_product',left=dict(atom=(x>=0).sexpr(),polarity=False),right=dict(atom='@bound',polarity=True))],query_path=str(self.query),query_sha256='',truncations=[])
        self.save_query(cert);return cert

    def validate(self,cert,_seconds=3,**kwargs):
        self.assertIsNotNone(self.checker,'independent guarded-product checker is missing')
        with mock.patch.object(self.z3.Solver,'check',side_effect=AssertionError('Solver.check forbidden')),mock.patch.object(self.z3.Optimize,'check',side_effect=AssertionError('Optimize.check forbidden')):
            try:result=self.checker.validate_certificate(self.z3,cert,time.monotonic()+_seconds,expected_source=self.source,expected_query=self.query,**kwargs)
            except TypeError as error:
                if "unexpected keyword argument 'max_variables'"in str(error):self.fail('explicit variable resource API missing')
                raise
        self.assertTrue(result['translation_verified']);self.assertEqual(result['solver_calls'],0);self.assertFalse(result['formal_A2']);self.assertFalse(result['is_optimal']);self.assertFalse(result['source_bound_verified']);self.assertFalse(result['UNSAT_replayed'])
        return result

    def test_min_and_max_full_boolean_lift_and_unused_inventory(self):
        for maximize in(False,True):
            with self.subTest(maximize=maximize):
                result=self.validate(self.fixture(maximize));self.assertEqual(result['query_assertion_count'],5);self.assertEqual(result['cut_count'],3);self.assertEqual(result['used_integer_count'],2)

    def test_strict_integer_comparisons_and_negative_fact_shift(self):
        z=self.z3;x,y=z.Ints('x y');rx,ry,rxy=z.Reals('R_x R_y R_xy');source='(declare-const x Int)(declare-const y Int)(assert (or (< x 0) (> y 1)))(minimize x)';self.source.write_text(source)
        cert=self.fixture();self.source.write_text(source);cert.update(source_sha256=hashlib.sha256(source.encode()).hexdigest(),monomials=[dict(name='R_x',variables=['x']),dict(name='R_y',variables=['y']),dict(name='R_xy',variables=['x','y'])],cuts=[dict(kind='nonnegative_product',left=dict(atom=(x<0).sexpr(),polarity=True),right=dict(atom=(y>1).sexpr(),polarity=False))])
        gx=-rx-1>=0;gy=ry-2>=0;self.forms=[z.Or(gx,gy),-rx-1>=0,z.Implies(z.And(gx,z.Not(gy)),rxy-rx+ry-1>=0)];self.save_query(cert);self.validate(cert)
        self.save_query(cert,[*self.forms[:2],z.Implies(z.And(gx,z.Not(gy)),rxy-rx+ry>=0)])
        with self.assertRaises(ValueError):self.validate(cert)

    def test_guard_cannot_be_dropped_or_replaced_by_current_branch(self):
        cert=self.fixture();self.save_query(cert,[*self.forms[:2],self.real[-1]==0,*self.forms[3:]])
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();changed=copy.deepcopy(cert);changed['cuts'][1]['left']['polarity']=False
        with self.assertRaises(ValueError):self.validate(changed)

    def test_actual_query_has_exact_base_bound_and_ledger_no_extra_assumptions(self):
        for mutation in('missing-base','missing-bound','missing-cut','or-to-and','extra-false','extra-anchor'):
            cert=self.fixture();forms=list(self.forms)
            if mutation=='missing-base':forms.pop(0)
            elif mutation=='missing-bound':forms.pop(1)
            elif mutation=='missing-cut':forms.pop()
            elif mutation=='or-to-and':forms[0]=self.z3.And(self.real[0]>=0,self.z3.Not(self.real[2]==0))
            elif mutation=='extra-false':forms.append(self.z3.BoolVal(False))
            else:forms.append(self.real[0]==0)
            self.save_query(cert,forms)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.validate(cert)

    def test_printer_omitted_source_true_and_polynomial_identity_are_neutral(self):
        for assertion in('true','(= (+ (* x y) (- (* x y))) 0)'):
            with self.subTest(assertion=assertion):
                cert=self.fixture();source=self.source.read_text().replace('(minimize x)','(assert '+assertion+')(minimize x)');self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
                if assertion!='true':cert['cuts'].append(dict(kind='equality_times_variable',atom=assertion,variable='x'))
                # The saved query has only the five non-neutral assertions.
                try:result=self.validate(cert)
                except ValueError as error:self.fail('exact source/ledger true was rejected: '+str(error))
                self.assertEqual(result['query_assertion_count'],5)

    def test_actual_extra_true_and_polynomial_identity_are_neutral(self):
        for assertion in('true','(= (- R_xy R_xy) 0.0)'):
            with self.subTest(assertion=assertion):
                cert=self.fixture();self.save_query(cert,text=self.query.read_text().replace('(check-sat)','(assert '+assertion+')\n(check-sat)'))
                try:self.validate(cert)
                except ValueError as error:self.fail('exact query true was rejected: '+str(error))

    def test_false_source_assertion_is_required_not_a_neutral(self):
        cert=self.fixture();source=self.source.read_text().replace('(minimize x)','(assert false)(minimize x)');self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
        with self.assertRaises(ValueError):self.validate(cert)
        self.save_query(cert,text=self.query.read_text().replace('(check-sat)','(assert false)\n(check-sat)'));self.validate(cert)

    def test_shared_query_boolean_DAG_has_bounded_reconstruction_work(self):
        cert=self.fixture();declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        shared='(let ((shared_guard '+self.forms[0].sexpr()+')) (and '+' '.join(['shared_guard']*400)+'))'
        text='(set-logic QF_LRA)\n'+declarations+'\n(assert '+shared+')\n'+'\n'.join('(assert '+f.sexpr()+')'for f in self.forms[1:])
        self.save_query(cert,text=text)
        try:result=self.validate(cert,max_nodes=6000)
        except ValueError as error:self.fail('shared Boolean DAG repeated reconstruction exceeded budget: '+str(error))
        self.assertLessEqual(result['work'],6000)

    def test_trivia_is_skipped_without_a_Python_budget_step_per_space(self):
        cert=self.fixture();self.save_query(cert,text=' '*80000+'; ignored () | \"\n'+self.query.read_text())
        try:result=self.validate(cert,max_nodes=10000)
        except ValueError as error:self.fail('trivia scanning exhausted semantic work budget: '+str(error))
        self.assertLessEqual(result['work'],10000)

    def test_query_let_shadowing_simultaneous_scope_and_quoted_names(self):
        cert=self.fixture();declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        first='(let ((R_x R_xy) (R_xy R_x)) (or (>= R_xy 0.0) (not (= R_x 0.0))))'
        text='(set-logic QF_LRA)\n'+declarations+'\n(assert '+first+')\n'+'\n'.join('(assert '+f.sexpr()+')'for f in self.forms[1:]);self.save_query(cert,text=text);self.validate(cert)
        # A wrong interpretation of simultaneous let bindings must not pass.
        self.save_query(cert,text=text.replace('(>= R_xy 0.0)','(>= R_x 0.0)'))
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.fixture();cert['monomials'][0]['name']='R ; x';self.save_query(cert,text=re.sub(r'\bR_x\b','|R ; x|',self.query.read_text()));self.validate(cert)

    def test_default_work_budget_handles_a_large_shared_boolean_query(self):
        cert=self.fixture();declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        first='(and '+' '.join([self.forms[0].sexpr()]*40000)+')'
        text='(set-logic QF_LRA)\n'+declarations+'\n(assert '+first+')\n'+'\n'.join('(assert '+f.sexpr()+')'for f in self.forms[1:]);self.save_query(cert,text=text)
        try:result=self.validate(cert,_seconds=5)
        except ValueError as error:self.fail('default work budget rejected a bounded large query: '+str(error))
        self.assertGreater(result['work'],1000000);self.assertLessEqual(result['work'],4000000)

    def many_variables_fixture(self,count=257):
        z=self.z3;names=['v_'+str(i).zfill(4)for i in range(count)];variables=[z.Int(name)for name in names];reals=[z.Real('R_'+name)for name in names]
        source=''.join('(declare-const '+name+' Int)'for name in names)+'(assert '+(z.Sum(*variables)>=0).sexpr()+')(minimize '+names[0]+')';self.source.write_text(source)
        self.forms=[z.Sum(*reals)>=0,-reals[0]-1>=0]
        cert=dict(schema='source-guarded-product-bound-v1',source=str(self.source),source_sha256=hashlib.sha256(source.encode()).hexdigest(),direction='min',own_best='0',original_objective=names[0],used_integer_names=names,used_bool_names=[],monomials=[dict(name='R_'+name,variables=[name])for name in names],cuts=[],query_path=str(self.query),query_sha256='',truncations=[])
        self.save_query(cert);return cert

    def test_variable_resource_default_accepts_257_but_explicit_256_rejects(self):
        cert=self.many_variables_fixture()
        try:result=self.validate(cert)
        except ValueError as error:self.fail('reasonable 257-variable source rejected by default: '+str(error))
        self.assertEqual(result['used_integer_count'],257)
        with self.assertRaises(ValueError):self.validate(cert,max_variables=256)

    def test_variable_resource_parameter_has_finite_integer_upper_limit(self):
        cert=self.fixture()
        for cap in(0,-1,8193,True,1.5,'8192'):
            with self.subTest(cap=cap),self.assertRaises(ValueError):self.validate(cert,max_variables=cap)

    def test_larger_variable_resource_does_not_relax_complete_sorted_set_binding(self):
        cert=self.many_variables_fixture()
        for change in(lambda c:c['used_integer_names'].pop(),lambda c:c['used_integer_names'].append('foreign'),lambda c:c['used_integer_names'].reverse(),lambda c:c['monomials'].pop()):
            changed=copy.deepcopy(cert);change(changed)
            with self.subTest(change=change),self.assertRaises(ValueError):self.validate(changed,max_variables=8192)

    def query_with_first_assertion(self,cert,first):
        declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        self.save_query(cert,text='(set-logic QF_LRA)\n'+declarations+'\n(assert '+first+')\n'+'\n'.join('(assert '+f.sexpr()+')'for f in self.forms[1:]))

    def test_deep_printer_let_chain_does_not_count_as_mathematical_AST_depth(self):
        cert=self.fixture();count=1200
        prefix='(let ((alias_0 R_x)) '+''.join('(let ((alias_'+str(i)+' alias_'+str(i-1)+')) 'for i in range(1,count))
        body='(or (>= alias_'+str(count-1)+' 0.0) (not (= R_xy 0.0)))';self.query_with_first_assertion(cert,prefix+body+')'*count)
        try:self.validate(cert)
        except (ValueError,RecursionError)as error:self.fail('flat math in bounded deep printer let rejected: '+str(error))

    def test_real_mathematical_depth_limit_survives_direct_or_aliased_arithmetic(self):
        for aliases in(False,True):
            cert=self.fixture();count=300
            if aliases:
                prefix='(let ((a_0 R_x)) '+''.join('(let ((a_'+str(i)+' (+ a_'+str(i-1)+' 0.0))) 'for i in range(1,count));left='a_'+str(count-1);first=prefix+'(or (>= '+left+' 0.0) (not (= R_xy 0.0)))'+')'*count
            else:
                left='(+ '*count+'R_x'+' 0.0)'*count;first='(or (>= '+left+' 0.0) (not (= R_xy 0.0)))'
            self.query_with_first_assertion(cert,first)
            with self.subTest(aliases=aliases),self.assertRaises(ValueError):self.validate(cert)

    def test_deep_let_simultaneous_RHS_shadow_restore_and_unused_bad_binding(self):
        cert=self.fixture();first='(let ((R_x true) (a (+ R_x 1.0))) (or (>= (- a 1.0) 0.0) (not (= R_xy 0.0))))';self.query_with_first_assertion(cert,first);self.validate(cert)
        cert=self.fixture();first='(or (let ((R_x true)) (not (= R_xy 0.0))) (>= R_x 0.0))';self.query_with_first_assertion(cert,first);self.validate(cert)
        for bindings in('((unused (* R_x R_y)))','((unused (div 4 2)))','((a R_x) (b (+ a 1.0)))','((R_x true) (a (+ R_x false)))'):
            cert=self.fixture();hidden='(let '+bindings+' '+self.forms[0].sexpr()+')';self.query_with_first_assertion(cert,'(let ((pad R_x)) '*400+hidden+')'*400)
            with self.subTest(bindings=bindings),self.assertRaises(ValueError):self.validate(cert)

    def test_printer_syntax_nesting_remains_finitely_bounded(self):
        cert=self.fixture();count=8300;self.query_with_first_assertion(cert,'(let ((unused R_x)) '*count+self.forms[0].sexpr()+')'*count)
        with self.assertRaisesRegex(ValueError,'nesting'):self.validate(cert)

    def test_source_expression_depth_is_unchanged_by_larger_reader_nesting(self):
        cert=self.fixture();term='(+ '*300+'x'+' 0)'*300;source=self.source.read_text().replace('(minimize x)','(assert (>= '+term+' 0))(minimize x)');self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,'source expression depth'):self.validate(cert)

    def test_real_query_linearity_is_checked_before_monomial_expansion(self):
        cert=self.fixture();rx,ry,rxy,_,_=self.real;self.save_query(cert,[self.z3.substitute(f,(rxy,rx*ry))for f in self.forms])
        with self.assertRaisesRegex(ValueError,'linear|LRA'):self.validate(cert)

    def test_let_fraction_and_signed_printer_forms_are_exact(self):
        cert=self.fixture();z=self.z3;rx,ry,rxy,rxx,rxyy=self.real;bound=-rx-1
        forms=[z.Or(rx/2>=0,z.Not(-rxy/7==0)),bound/3>=0,z.Implies(-rxy/5==0,-rxyy/7==0),z.Implies(z.And(rx>=0,bound>=0),(-rxx-rx)/11>=0),z.Implies(z.And(z.Not(rx>=0),bound>=0),(rxx+2*rx+1)/13>=0)]
        declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials']);first=re.sub(r'\bR_x\b','alias_x',forms[0].sexpr());text='(set-logic QF_LRA)\n'+declarations+'\n(assert (let ((alias_x R_x)) '+first+'))\n'+'\n'.join('(assert '+f.sexpr()+')'for f in forms[1:])+'\n(check-sat)\n(get-model)\n(exit)\n';self.save_query(cert,text=text);self.validate(cert)

    def test_monomial_freshness_degree_canonical_names_and_coverage(self):
        cert=self.fixture()
        changes=[lambda c:c['monomials'][0].update(name='unused'),lambda c:c['monomials'][0].update(name='x'),lambda c:c['monomials'][2].update(variables=['y','x']),lambda c:c['monomials'][2].update(variables=['x','x','x','x']),lambda c:c['monomials'][2].update(variables=['unused']),lambda c:c['monomials'].append(copy.deepcopy(c['monomials'][0])),lambda c:c['monomials'].__delitem__(1),lambda c:c['used_integer_names'].reverse(),lambda c:c['used_integer_names'].append('unused'),lambda c:c.update(used_bool_names=['b'])]
        for change in changes:
            changed=copy.deepcopy(cert);change(changed)
            with self.subTest(change=change),self.assertRaises(ValueError):self.validate(changed)

    def test_only_actual_source_atoms_and_allowed_cut_schemas(self):
        cert=self.fixture()
        for cut in [dict(kind='equality_times_variable',atom='(>= x 0)',variable='y'),dict(kind='equality_times_variable',atom='(= (* x y) 0)',variable='unused'),dict(kind='nonnegative_product',left=dict(atom='(= (* x y) 0)',polarity=True),right=dict(atom='@bound',polarity=True)),dict(kind='nonnegative_product',left=dict(atom='(>= x 1)',polarity=True),right=dict(atom='@bound',polarity=True)),dict(kind='square',variable='x'),dict(kind='nonnegative_product',left=dict(atom='@bound',polarity=1),right=dict(atom='@bound',polarity=True))]:
            changed=copy.deepcopy(cert);changed['cuts'][0]=cut
            with self.subTest(cut=cut),self.assertRaises(ValueError):self.validate(changed)

    def test_two_quadratic_factors_are_not_a_legal_first_version_cut(self):
        cert=self.fixture();source='(declare-const x Int)(declare-const y Int)(assert (>= (* x y) 0))(minimize x)';self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest();cert['cuts']=[dict(kind='nonnegative_product',left=dict(atom='(>= (* x y) 0)',polarity=True),right=dict(atom='(>= (* x y) 0)',polarity=True))]
        with self.assertRaises(ValueError):self.validate(cert)

    def test_identity_M_direction_source_query_and_schema_mutations(self):
        cert=self.fixture()
        for key,value in [('schema','other'),('direction','max'),('own_best','1'),('own_best','1.0'),('original_objective','y'),('source_sha256','0'*64),('query_sha256','0'*64),('extra',1),('truncations',[{}]),('source','relative.smt2'),('query_path','relative.smt2')]:
            changed=copy.deepcopy(cert);changed[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.validate(changed)
        self.source.write_text('(declare-const x Int)(minimize x)')
        with self.assertRaises(ValueError):self.validate(cert)

    def test_source_unsupported_syntax_is_rejected_before_parser_folding(self):
        for extra in('(declare-const b Bool)','(declare-const r Real)','(declare-fun f (Int) Int)','(assert (= (div 4 2) 2))','(assert (= (mod 4 2) 0))','(assert (= (ite true x x) x))','(assert (= (^ x 2) (* x x)))','(assert (forall ((z Int)) (= z z)))'):
            cert=self.fixture();source='(declare-const x Int)(declare-const y Int)'+extra+'(assert (= y x))(minimize x)';self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest()
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.validate(cert)

    def test_query_foreign_or_Int_declarations_and_settings_are_rejected(self):
        for prefix in('(declare-const foreign Real)','(declare-const foreign Int)','(declare-fun foreign (Real) Real)','(declare-const b Bool)','(push 1)'):
            cert=self.fixture();self.save_query(cert,text=prefix+'\n'+self.query.read_text())
            with self.subTest(prefix=prefix),self.assertRaises(ValueError):self.validate(cert)

    def test_resource_deadline_and_external_best_binding(self):
        cert=self.fixture()
        with self.assertRaises(ValueError):self.validate(cert,max_nodes=1)
        with self.assertRaises(ValueError):self.validate(cert,expected_best='1')
        self.assertIsNotNone(self.checker)
        with self.assertRaises(TimeoutError):self.checker.validate_certificate(self.z3,cert,time.monotonic()-1)

if __name__=='__main__':unittest.main()
