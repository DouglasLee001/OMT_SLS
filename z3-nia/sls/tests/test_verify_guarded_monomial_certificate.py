"""Task29 independent v2 translation tests; all solving/model/tactic calls forbidden."""
import ast,builtins,copy,ctypes,hashlib,importlib.util,sys,time,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode=True
TREE=Path(__file__).resolve().parents[2]
MODULE=TREE/'experiment/verify_guarded_monomial_certificate.py'
LEGACY=TREE/'experiment/verify_guarded_product_certificate.py'
LEGACY_SHA='1abdad7d3975796a40a0f045bc740d4d9bfd9c3ac36e4a241c16d1bc14c7d3e8'
SDK=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_104807_978529/source/python')
LIBRARY=SDK/'z3/lib/libz3.so'
LIBRARY_SHA='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

old_tests=load(TREE/'sls/tests/test_verify_guarded_product_certificate.py','guarded_monomial_legacy_tests')

class GuardedMonomialCertificateTests(old_tests.GuardedProductCertificateTests):
    """The unchanged 28 v1 tests plus v2-specific schema and mathematical rules."""
    @classmethod
    def setUpClass(cls):
        assert hashlib.sha256(LEGACY.read_bytes()).hexdigest()==LEGACY_SHA
        assert hashlib.sha256(LIBRARY.read_bytes()).hexdigest()==LIBRARY_SHA
        ctypes.CDLL(str(LIBRARY));builtins.Z3_LIB_DIRS=[str(LIBRARY.parent)];sys.path.insert(0,str(SDK))
        import z3
        assert Path(z3.__file__).resolve()==(SDK/'z3/__init__.py').resolve()
        cls.z3=z3;cls.checker=load(MODULE,'guarded_monomial_checker_tested')if MODULE.is_file()else None

    def setUp(self):
        super().setUp();stack=ExitStack();self.addCleanup(stack.close)
        forbidden=AssertionError('Task29 zero-SMT: solving/model/tactic entry forbidden')
        for owner,names in((self.z3.Solver,('check','model','consequences','cube')),(self.z3.Optimize,('check','model','lower','upper')),(self.z3.ModelRef,('eval','evaluate')),(self.z3.Tactic,('apply','__call__')),(self.z3,('solve','prove','Model'))):
            for name in names:
                if hasattr(owner,name):stack.enter_context(mock.patch.object(owner,name,side_effect=forbidden))
        for owner in(self.z3.z3core,self.z3.z3):
            for name in dir(owner):
                if name.startswith(('Z3_solver_check','Z3_optimize_check','Z3_tactic_apply','Z3_model_eval','Z3_mk_model','Z3_solver_get_model','Z3_optimize_get_model')):
                    stack.enter_context(mock.patch.object(owner,name,side_effect=forbidden))

    def moment_fixture(self,repeat=False,maximize=False,mixed=False):
        z=self.z3;x,y,u,v=z.Ints('x y u v')
        source='(declare-const x Int)(declare-const y Int)(declare-const u Int)(declare-const v Int)(declare-const unused Int)(assert (or (= x y) (< u 0)))(assert (>= v 0))'
        source+='(maximize x)'if maximize else'(minimize x)';self.source.write_text(source)
        pair=['u','u']if repeat else['u','v']
        monos={('x',),('y',),('u',),('v',),tuple(pair),tuple(sorted(['x',*pair])),tuple(sorted(['y',*pair]))}
        if mixed:monos|={('u','x'),('u','y'),('v','v')}
        entries=[dict(name='L'+str(i),variables=list(m))for i,m in enumerate(sorted(monos))]
        real={tuple(e['variables']):z.Real(e['name'])for e in entries};self.lifted=real
        rx,ry,ru,rv=(real[(k,)]for k in('x','y','u','v'));eq=rx==ry;bound=(rx-1>=0)if maximize else(-rx-1>=0)
        cut=dict(kind='equality_times_monomial',atom=(x==y).sexpr(),variables=pair)
        self.forms=[z.Or(eq,-ru-1>=0),rv>=0,bound,z.Implies(eq,real[tuple(sorted(['x',*pair]))]-real[tuple(sorted(['y',*pair]))]==0)]
        cuts=[cut]
        if mixed:
            cuts.extend([dict(kind='equality_times_variable',atom=(x==y).sexpr(),variable='u'),dict(kind='nonnegative_product',left=dict(atom=(v>=0).sexpr(),polarity=True),right=dict(atom=(v>=0).sexpr(),polarity=True))])
            self.forms.extend([z.Implies(eq,real[('u','x')]-real[('u','y')]==0),z.Implies(z.And(rv>=0,rv>=0),real[('v','v')]>=0)])
        cert=dict(schema='source-guarded-product-bound-v2',source=str(self.source),source_sha256=hashlib.sha256(source.encode()).hexdigest(),direction='max'if maximize else'min',own_best='0',original_objective='x',used_integer_names=['u','v','x','y'],used_bool_names=[],monomials=entries,cuts=cuts,query_path=str(self.query),query_sha256='',truncations=[])
        self.save_query(cert);return cert

    def test_v2_complete_guarded_quadratic_multiplier_min_max_and_repeat(self):
        for repeat in(False,True):
            for maximize in(False,True):
                with self.subTest(repeat=repeat,maximize=maximize):
                    result=self.validate(self.moment_fixture(repeat,maximize));self.assertEqual(result['cut_count'],1)

    def test_v2_mixed_old_and_new_cut_kinds_and_legacy_only_v2(self):
        self.assertEqual(self.validate(self.moment_fixture(mixed=True))['cut_count'],3)
        cert=self.fixture();cert['schema']='source-guarded-product-bound-v2';self.validate(cert)

    def test_v1_rejects_new_kind_and_outer_fields_remain_exact(self):
        cert=self.moment_fixture();cert['schema']='source-guarded-product-bound-v1'
        with self.assertRaises(ValueError):self.validate(cert)
        for mutation in('extra','missing','wrong-schema'):
            cert=self.moment_fixture()
            if mutation=='extra':cert['new_policy']=True
            elif mutation=='missing':del cert['truncations']
            else:cert['schema']='source-guarded-product-bound-v3'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.validate(cert)

    def test_v2_pair_exact_list_length_sorted_used_Int_and_repeated_legal(self):
        for pair in((),('u','v'),[],['u'],['u','v','x'],['v','u'],['u','unused'],['u','foreign'],['u',1],['u',True],['u',None]):
            cert=self.moment_fixture();cert['cuts'][0]['variables']=pair
            with self.subTest(pair=pair),self.assertRaises(ValueError):self.validate(cert)
        cert=self.moment_fixture();cert['cuts'][0]['variables']=['u',cert['monomials'][0]['name']]
        with self.assertRaises(ValueError):self.validate(cert)
        self.validate(self.moment_fixture(repeat=True))

    def test_v2_fields_atom_identity_and_source_Eq_must_be_nonconstant_linear(self):
        for mutation in('extra','missing','foreign','inequality','atom-list','quadratic','constant-true','constant-false'):
            cert=self.moment_fixture();cut=cert['cuts'][0]
            if mutation=='extra':cut['variable']='u'
            elif mutation=='missing':del cut['variables']
            elif mutation=='foreign':cut['atom']='(= x foreign)'
            elif mutation=='inequality':cut['atom']='(>= v 0)'
            elif mutation=='atom-list':cut['atom']=['=','x','y']
            else:
                atom={'quadratic':'(= (* x y) 0)','constant-true':'(= 1 1)','constant-false':'(= 1 0)'}[mutation]
                source=self.source.read_text().replace('(minimize x)','(assert '+atom+')(minimize x)');self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest();cut['atom']=atom
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.validate(cert)

    def test_v2_zero_constant_Eq_does_not_require_unused_multiplier_coordinate(self):
        cert=self.moment_fixture();cert['monomials']=[m for m in cert['monomials']if m['variables']!=['u','v']]
        self.validate(cert)

    def test_v2_full_product_inventory_is_required(self):
        for monomial in(('u','v','x'),('u','v','y')):
            cert=self.moment_fixture();cert['monomials']=[m for m in cert['monomials']if tuple(m['variables'])!=monomial]
            with self.subTest(monomial=monomial),self.assertRaises(ValueError):self.validate(cert)
        cert=self.moment_fixture();self.save_query(cert,[*self.forms[:3],self.z3.Implies(self.lifted[('x',)]==self.lifted[('y',)],self.lifted[('u','v','x')]==0)])
        with self.assertRaises(ValueError):self.validate(cert)

    def test_v2_nonzero_constant_Eq_requires_multiplier_term_via_full_product(self):
        cert=self.moment_fixture();z=self.z3;x,y=z.Ints('x y')
        source=self.source.read_text().replace('(= x y)','(= (+ x 1) y)');self.source.write_text(source);cert['source_sha256']=hashlib.sha256(source.encode()).hexdigest();cert['cuts'][0]['atom']=(x+1==y).sexpr()
        eq=self.lifted[('x',)]+1==self.lifted[('y',)];self.forms[0]=z.Or(eq,-self.lifted[('u',)]-1>=0);self.forms[-1]=z.Implies(eq,self.lifted[('u','v','x')]+self.lifted[('u','v')]-self.lifted[('u','v','y')]==0)
        self.save_query(cert);self.validate(cert)
        cert['monomials']=[m for m in cert['monomials']if m['variables']!=['u','v']]
        with self.assertRaises(ValueError):self.validate(cert)

    def test_v2_false_equality_guard_cannot_be_promoted_and_full_Boolean_is_retained(self):
        cert=self.moment_fixture();z=self.z3
        assignment={('x',):-1,('y',):0,('u',):-1,('v',):1,('u','v'):-1,('u','v','x'):1,('u','v','y'):0}
        subs=[(r,z.RealVal(assignment[m]))for m,r in self.lifted.items()]
        self.assertTrue(all(z.is_true(z.simplify(z.substitute(f,*subs)))for f in self.forms))
        self.validate(cert)
        self.save_query(cert,[*self.forms[:3],self.lifted[('u','v','x')]-self.lifted[('u','v','y')]==0])
        with self.assertRaises(ValueError):self.validate(cert)
        cert=self.moment_fixture();self.save_query(cert,[self.lifted[('x',)]==self.lifted[('y',)],*self.forms[1:]])
        with self.assertRaises(ValueError):self.validate(cert)

    def test_v2_exact_base_strict_bound_guard_and_cut_multiplicity(self):
        for mutation in('missing-base','missing-bound','nonstrict','missing-cut','duplicate-cut','extra-anchor','wrong-sign'):
            cert=self.moment_fixture();forms=list(self.forms)
            if mutation=='missing-base':forms.pop(0)
            elif mutation=='missing-bound':forms.pop(2)
            elif mutation=='nonstrict':forms[2]=-self.lifted[('x',)]>=0
            elif mutation=='missing-cut':forms.pop()
            elif mutation=='duplicate-cut':forms.append(forms[-1])
            elif mutation=='extra-anchor':forms.append(self.lifted[('u',)]==0)
            else:forms[-1]=self.z3.Implies(self.lifted[('x',)]==self.lifted[('y',)],self.lifted[('u','v','x')]+self.lifted[('u','v','y')]==0)
            self.save_query(cert,forms)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.validate(cert)

    def test_v2_implicit_query_let_preserves_typed_semantics_and_rejects_hidden_bad_rhs(self):
        cert=self.moment_fixture();raw=self.query.read_text();self.save_query(cert,text=raw.replace('(assert\n','(assert\n',1))
        name=next(m['name']for m in cert['monomials']if m['variables']==['x'])
        # Bind a source-independent alias once; original Real declaration remains complete.
        wrapped='\n'.join('(assert (let ((alias '+name+')) '+f.sexpr().replace(name,'alias')+'))'for f in self.forms)
        declarations='\n'.join('(declare-const '+m['name']+' Real)'for m in cert['monomials'])
        self.save_query(cert,text='(set-logic QF_LRA)\n'+declarations+'\n'+wrapped);self.validate(cert)
        self.save_query(cert,text='(set-logic QF_LRA)\n'+declarations+'\n'+wrapped.replace('((alias '+name+'))','((alias '+name+') (hidden (* '+name+' '+name+')))',1))
        with self.assertRaises(ValueError):self.validate(cert)

    def test_only_validate_function_AST_changes_from_frozen_1ab(self):
        self.assertIsNotNone(self.checker,'new independent checker missing')
        old=ast.parse(LEGACY.read_text());new=ast.parse(MODULE.read_text())
        def without_validate(tree):return ast.dump(ast.Module(body=[node for node in tree.body if not(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))and node.name=='validate_certificate')],type_ignores=[]),include_attributes=False)
        self.assertEqual(without_validate(old),without_validate(new))

if __name__=='__main__':unittest.main(verbosity=2)
