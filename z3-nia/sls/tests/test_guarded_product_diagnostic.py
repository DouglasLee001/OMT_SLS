#!/usr/bin/env python3
"""Task22 pure RED/GREEN and one opt-in <=8s generated-only SDK group."""
from pathlib import Path
import hashlib,importlib.util,json,math,os,subprocess,sys,tempfile,unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[3]
MODULE=ROOT/'z3-nia/experiment/guarded_product_diagnostic.py'
def load():
    assert MODULE.is_file(),'Task22 guarded source-product producer is missing'
    spec=importlib.util.spec_from_file_location('_guarded_product_test',MODULE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
class PureTests(unittest.TestCase):
    def test_commutative_cubic_terms_cancel_exactly(self):
        p=load();h={('C',):-1,('A','x'):-1};e={('A','l'):1,('l0',):-1}
        result=p._add(p._multiply({('l',):1},h),p._multiply({('x',):1},e))
        result=p._add(result,p._multiply({('x',):1,():-1},{('l0',):1}))
        result=p._add(result,{('l0',):1,('C','l'):1,():-1})
        self.assertEqual(result,{():-1})
        self.assertEqual(p._multiply({('z',):2},{('a',):-3}),{('a','z'):-6})
    def test_integer_comparison_complements_are_exact(self):
        p=load();relations={'<':lambda v:v<0,'<=':lambda v:v<=0,'>':lambda v:v>0,'>=':lambda v:v>=0}
        for relation,predicate in relations.items():
            for value in range(-3,4):
                for polarity in (True,False):
                    poly=p._normalized(relation,{():value},polarity)
                    self.assertEqual(poly.get((),0)>=0,predicate(value)==polarity)
        with self.assertRaises(p.Unsupported):p._normalized('=',{('x',):1},False)
    def test_strict_bound_keeps_original_sense(self):
        p=load();objective={('g',):1}
        self.assertEqual(p._bound_poly(objective,'min',2),{('g',):-1,():1})
        self.assertEqual(p._bound_poly(objective,'max',2),{('g',):1,():-3})
    def test_cubic_inventory_precedes_product_selection(self):
        p=load();atoms=[dict(atom='eq',relation='=',poly={('A','l'):1,('l0',):-1}),dict(atom='primal',relation='<=',poly={('C',):1,('A','x'):1}),dict(atom='nonneg',relation='>=',poly={('l',):1}),dict(atom='dual',relation='>=',poly={('l0',):1,('C','l'):1,():-1})]
        inventory={('A',),('C',),('l',),('l0',),('x',),('g',),('A','l'),('A','x'),('C','l')}
        inventory,cuts,stats=p._closure(atoms,['A','C','g','l','l0','x'],{('g',):-1,():-1},inventory,lambda:None,lambda:True)
        self.assertIn(('A','l','x'),inventory)
        self.assertTrue(any(c['kind']=='equality_times_variable'and c['variable']=='x'for c in cuts))
        self.assertTrue(any(c['kind']=='nonnegative_product'and {c['left']['atom'],c['right']['atom']}=={'primal','nonneg'}and c['left']['polarity']and c['right']['polarity']for c in cuts))
        self.assertGreater(stats['pair_scans'],0)
    def test_caps_may_omit_cuts_without_mutating_base(self):
        p=load();atoms=[dict(atom='eq',relation='=',poly={('x',):1,('y',):-1})];inventory={('x',),('y',)};before=set(inventory)
        with mock.patch.object(p,'MAX_CUTS',0):
            after,cuts,stats=p._closure(atoms,['x','y'],{('x',):-1},inventory,lambda:None,lambda:True)
        self.assertEqual(inventory,before);self.assertEqual(after,before);self.assertEqual(cuts,[])
        self.assertIn('cut_cap',stats['truncations'])
        with self.assertRaises(p.Unsupported):p._multiply({('a','b'):1},{('c','d'):1})
        with mock.patch.object(p,'MAX_BITS',3):
            with self.assertRaises(p.Unsupported):p._multiply({('a',):7},{('b',):7})
    def test_raw_fragment_rejects_hidden_unsupported(self):
        p=load();strict=p._strict(None)
        good='(declare-const x Int)(assert (or (not (< x 0)) (= x 1)))(minimize x)'
        p._raw_validate(strict._commands(strict._query(good)[0]),lambda:None)
        for text in ['(declare-const b Bool)(minimize 0)','(declare-const x Real)(minimize 0)','(declare-fun f (Int) Int)(minimize 0)','(declare-const x Int)(minimize (* 0 (div x 1)))','(declare-const x Int)(minimize (ite true x x))']:
            with self.assertRaises(p.Unsupported):p._raw_validate(strict._commands(strict._query(text)[0]),lambda:None)
    def test_invalid_inputs_never_start_sdk(self):
        p=load()
        with tempfile.TemporaryDirectory()as folder:
            d=Path(folder);source=d/'source.smt2';source.write_text('(declare-const x Int)(minimize x)');old=source.read_bytes()
            invalid=[('min','0',v)for v in [0,-1,math.inf,math.nan,1201]]+[('bad','0',1),('min','NA',1),('min',1.5,1)]
            for i,(direction,best,seconds)in enumerate(invalid):
                result=p.analyze(source,direction,best,seconds,d/f'out{i}',None)
                self.assertEqual(result['status'],'error');self.assertFalse(result['check_started']);self.assertFalse(result['diagnostic_bound_unsat'])
            self.assertEqual(source.read_bytes(),old)
            self.assertEqual(p.analyze(source,'min','0',1,d,None)['status'],'error')
    def test_late_result_clears_bound_flag(self):
        p=load();result={'status':'bound_unsat','answer':'unsat','diagnostic_bound_unsat':True,'is_optimal':False}
        p._deadline_gate(result,1.,2.)
        self.assertEqual(result['status'],'timeout');self.assertIsNone(result['answer']);self.assertFalse(result['diagnostic_bound_unsat']);self.assertFalse(result['is_optimal'])
    def test_source_guard_and_no_clobber_publication(self):
        p=load()
        with tempfile.TemporaryDirectory()as folder:
            source=Path(folder)/'source';source.write_text('old');digest=hashlib.sha256(source.read_bytes()).hexdigest();p._source_guard(source,digest,lambda:None)
            source.write_text('new')
            with self.assertRaises(ValueError):p._source_guard(source,digest,lambda:None)
            with self.assertRaises(OSError):p._publish(source,'other',lambda:None)
            self.assertEqual(source.read_text(),'new')
            calls=0;target=Path(folder)/'late'
            def remaining():
                nonlocal calls
                calls+=1
                if calls==4:raise TimeoutError('late after publication')
            with self.assertRaises(TimeoutError):p._publish(target,'data',remaining)
            self.assertFalse(target.exists())
    def test_canonicalization_combines_commuted_monomials(self):
        p=load();self.assertEqual(p._clean({('z','a'):2,('a','z'):3}),{('a','z'):5})
    def test_generation_deadline_stops_before_fact_expansion(self):
        p=load();atoms=[dict(atom='nonnegative',relation='>=',poly={('x',):1})]
        with mock.patch.object(p,'_normalized',side_effect=AssertionError('fact expansion after generation deadline')):
            inventory,cuts,stats=p._closure(atoms,['x'],{('x',):-1},{('x',)},lambda:None,lambda:False)
        self.assertEqual(cuts,[]);self.assertIn('generation_deadline',stats['truncations'])
    def test_artifact_guard_rejects_tampered_or_missing_query(self):
        p=load()
        with tempfile.TemporaryDirectory()as folder:
            d=Path(folder);source=d/'source';query=d/'query';cert=d/'certificate'
            for file in(source,query,cert):file.write_text(file.name)
            h=lambda file:hashlib.sha256(file.read_bytes()).hexdigest()
            args=(source,h(source),query,h(query),cert,h(cert),lambda:None)
            p._artifact_guard(*args)
            query.write_text('extra assumption')
            with self.assertRaises(ValueError):p._artifact_guard(*args)
            query.unlink()
            with self.assertRaises(OSError):p._artifact_guard(*args)
    def test_half_deadline_interrupts_optional_product_and_discards_current_cut(self):
        for equality in(True,False):
            for during in(True,False):
                with self.subTest(equality=equality,during=during):
                    p=load();expired=[False];original=p._multiply
                    def multiplication(left,right,checkpoint=lambda:None):
                        if during:expired[0]=True;checkpoint()
                        value=original(left,right)
                        expired[0]=True
                        return value
                    if equality:
                        atoms=[dict(atom='eq',relation='=',poly={('x',):1,('y',):-1})];variables=['x','y'];inventory={('x',),('y',)}
                    else:
                        atoms=[dict(atom='ineq',relation='>=',poly={('x',):1})];variables=['x'];inventory={('x',),('x','x')}
                    with mock.patch.object(p,'_multiply',side_effect=multiplication):
                        after,cuts,stats=p._closure(atoms,variables,{('x',):-1,():-1},inventory,lambda:None,lambda:not expired[0])
                    self.assertEqual(cuts,[]);self.assertEqual(after,inventory)
                    self.assertIn('generation_deadline',stats['truncations'])
    def test_import_does_not_load_sdk(self):
        load();self.assertNotIn('z3',sys.modules)

class SharedCubicPureTests(unittest.TestCase):
    def fixture(self):
        p=load();atoms=[dict(atom='eq',relation='=',poly={('a','b'):1,('d',):-1}),dict(atom='primal',relation='<=',poly={('C',):1,('a','c'):1}),dict(atom='nonneg',relation='>=',poly={('b',):1}),dict(atom='dual',relation='>=',poly={('d',):1,('C','b'):1,():-1})]
        variables=['C','a','b','c','d','g'];inventory={(v,)for v in variables}|{('a','b'),('a','c'),('C','b')};bound={('g',):-1,():-1}
        return p,atoms,variables,bound,inventory
    def test_profile_is_explicit_paired_and_legacy_defaults_remain(self):
        import inspect
        p=load();signature=inspect.signature(p.analyze)
        self.assertIn('closure_policy',signature.parameters);self.assertIn('variable_limit',signature.parameters)
        self.assertEqual(signature.parameters['closure_policy'].default,'all-variables');self.assertEqual(signature.parameters['variable_limit'].default,256)
        self.assertEqual(p._profile('all-variables',256),('all-variables',256));self.assertEqual(p._profile('shared-cubic',8192),('shared-cubic',8192))
        with tempfile.TemporaryDirectory()as folder:
            d=Path(folder);source=d/'source.smt2';source.write_text('(declare-const x Int)(minimize x)')
            for i,pair in enumerate([('all-variables',8192),('shared-cubic',256),('shared-cubic',8193),('other',256),('shared-cubic',True),('shared-cubic',float('nan'))]):
                result=p.analyze(source,'min','0',1,d/str(i),None,closure_policy=pair[0],variable_limit=pair[1])
                self.assertEqual(result['status'],'error');self.assertFalse(result['check_started']);self.assertFalse((d/str(i)).exists())
    def test_shared_term_join_requires_actual_linear_nonnegative_fact(self):
        p,a,v,b,i=self.fixture();candidates,stats=p._shared_equality_candidates(a,v,b,i,lambda:None,lambda:True)
        self.assertEqual(candidates,[(0,'c')]);self.assertEqual(stats['join_candidates'],1)
        without=[x for x in a if x['atom']!='nonneg']
        self.assertEqual(p._shared_equality_candidates(without,v,b,i,lambda:None,lambda:True)[0],[])
    def test_swapped_factors_and_square_multiplicity_deduplicate(self):
        p,a,v,b,i=self.fixture();a[0]['poly']={('b','a'):1,('d',):-1};a.append(dict(a[1]))
        self.assertEqual(p._shared_equality_candidates(a,v,b,i,lambda:None,lambda:True)[0],[(0,'c')])
        a[0]['poly']={('a','a'):1,('d',):-1};a[2]['poly']={('a',):1}
        self.assertEqual(p._shared_equality_candidates(a,v,b,i,lambda:None,lambda:True)[0],[(0,'c')])
    def test_unrelated_variables_do_not_expand_candidate_cartesian_product(self):
        p,a,v,b,i=self.fixture();names=['unrelated'+str(n)for n in range(1000)];a.append(dict(atom='unrelatedLinear',relation='>=',poly={(n,):1 for n in names}))
        candidates,stats=p._shared_equality_candidates(a,v+names,b,i|{(n,)for n in names},lambda:None,lambda:True)
        self.assertEqual(candidates,[(0,'c')]);self.assertEqual(stats['join_candidates'],1)
    def test_linear_equality_only_uses_base_quadratic_neighbors(self):
        p=load();a=[dict(atom='eq',relation='=',poly={('x',):1,('y',):-1})];v=['g','x','y','z','unused'];i={(n,)for n in v}|{('x','z')}
        self.assertEqual(p._shared_equality_candidates(a,v,{('g',):-1},i,lambda:None,lambda:True)[0],[(0,'z')])
    def test_cancelled_zero_quadratic_terms_do_not_create_join(self):
        p,a,v,b,i=self.fixture();a[0]['poly']={('a','b'):0,('d',):1}
        self.assertEqual(p._shared_equality_candidates(a,v,b,i,lambda:None,lambda:True)[0],[])
    def test_full_equality_product_is_kept_not_only_shared_term(self):
        p,a,v,b,i=self.fixture();a[0]['poly'][('a','z')]=2;v.append('z');i|={('z',),('a','z')}
        inventory,cuts,stats=p._sparse_closure(a,v,b,i,lambda:None,lambda:True)
        self.assertIn(('a','b','c'),inventory);self.assertIn(('a','c','z'),inventory)
        eq=[c for c in cuts if c['kind']=='equality_times_variable'];self.assertEqual(eq,[dict(kind='equality_times_variable',atom='eq',variable='c')])
    def test_sparse_truncation_never_mutates_base_or_commits_late_product(self):
        p,a,v,b,i=self.fixture();base=set(i)
        inventory,cuts,stats=p._sparse_closure(a,v,b,i,lambda:None,lambda:False)
        self.assertEqual(inventory,base);self.assertEqual(cuts,[]);self.assertIn('generation_deadline',stats['truncations'])
        expired=[False];original=p._multiply
        def multiplication(left,right,checkpoint=lambda:None):expired[0]=True;checkpoint();return original(left,right)
        with mock.patch.object(p,'_multiply',side_effect=multiplication):
            inventory,cuts,stats=p._sparse_closure(a,v,b,i,lambda:None,lambda:not expired[0])
        self.assertEqual(inventory,base);self.assertEqual(cuts,[]);self.assertIn('generation_deadline',stats['truncations']);self.assertEqual(i,base)
        with mock.patch.object(p,'MAX_MONOMIALS',len(base)):
            inventory,cuts,stats=p._sparse_closure(a,v,b,i,lambda:None,lambda:True)
        self.assertEqual(inventory,base);self.assertIn('monomial_cap',stats['truncations'])
    def test_join_and_RLT_share_finite_scan_cap(self):
        p,a,v,b,i=self.fixture()
        with mock.patch.object(p,'MAX_PAIR_SCANS',0):
            inventory,cuts,stats=p._sparse_closure(a,v,b,i,lambda:None,lambda:True)
        self.assertEqual(cuts,[]);self.assertEqual(stats['join_scans']+stats['pair_scans'],0);self.assertIn('join_scan_cap',stats['truncations'])
    def test_legacy_closure_math_matches_archived_ba73(self):
        p,a,v,b,i=self.fixture();old_path=ROOT/'z3-nia/logs/research-round2/guarded-product-shared-cubic-component/prior-producer-ba73.py'
        spec=importlib.util.spec_from_file_location('_prior_ba73_for_math',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        self.assertEqual(p._closure(a,v,b,i,lambda:None,lambda:True),old._closure(a,v,b,i,lambda:None,lambda:True))

class IndexedRLTPureTests(unittest.TestCase):
    def facts(self,polys):return [(dict(atom='fact'+str(i),polarity=True),poly)for i,poly in enumerate(polys)]
    def pairs(self,p,facts,inventory,*,optional=lambda:True,remaining=lambda:None,offset=0):
        state=p._index_state(offset);pairs=list(p._indexed_rlt_pairs(facts,inventory,remaining,optional,state));return pairs,state
    def test_third_profile_is_explicit_without_changing_two_prior_pairs(self):
        p=load();self.assertEqual(p._profile('indexed-shared-cubic',8192),('indexed-shared-cubic',8192))
        self.assertEqual(p._profile('shared-cubic',8192),('shared-cubic',8192));self.assertEqual(p._profile('all-variables',256),('all-variables',256))
        for pair in [('indexed-shared-cubic',256),('indexed-shared-cubic',8193),('indexed-shared-cubic',True)]:
            with self.assertRaises(ValueError):p._profile(*pair)
    def test_leading_monomial_multiplication_in_small_exhaustive_domain(self):
        import itertools
        p=load();basis=[(),('x',),('y',),('x','x'),('x','y'),('y','y')]
        linears=[];allpolys=[]
        for coefficients in itertools.product((-1,0,1),repeat=6):
            poly={m:c for m,c in zip(basis,coefficients)if c}
            if p._degree(poly)==0:continue
            allpolys.append(poly)
            if p._degree(poly)==1:linears.append(poly)
        for left in linears:
            for right in allpolys:
                product=p._multiply(left,right)
                self.assertEqual(p._leading_monomial(product),tuple(sorted(p._leading_monomial(left)+p._leading_monomial(right))))
    def test_index_preserves_every_old_accepted_pair_with_cancellation(self):
        import itertools
        p=load();basis=[(),('x',),('y',),('x','x'),('x','y'),('y','y')];polys=[]
        for coefficients in itertools.product((-1,0,1),repeat=6):
            poly={m:c for m,c in zip(basis,coefficients)if c}
            if p._degree(poly)>0:polys.append(poly)
        degree=[p._degree(poly)for poly in polys];facts=self.facts(polys)
        inventories=[{('x',),('y',),('x','x'),('x','y'),('y','y'),('x','x','x'),('x','x','y'),('x','y','y'),('y','y','y')},{('x',),('y',),('x','x'),('y','y')},{('x',),('y',),('x','y'),('x','x','y'),('x','y','y')}]
        for inventory in inventories:
            pairs,state=self.pairs(p,facts,inventory);candidate=set(pairs);accepted=set()
            for i,left in enumerate(polys):
                for j in range(i,len(polys)):
                    if min(degree[i],degree[j])>1 or degree[i]+degree[j]>3:continue
                    product=p._multiply(left,polys[j])
                    if product and all(not m or m in inventory for m in product):accepted.add((i,j))
            self.assertLessEqual(accepted,candidate);self.assertEqual(len(candidate),len(pairs))
            indexed_accepted={(i,j)for i,j in pairs if all(not m or m in inventory for m in p._multiply(polys[i],polys[j]))}
            self.assertEqual(indexed_accepted,accepted);self.assertEqual(state['truncations'],[])
        special=self.facts([{('x',):1,('y',):1},{('x',):1,('y',):-1}]);pairs,state=self.pairs(p,special,inventories[1]);self.assertIn((0,1),pairs)
    def test_repeated_factor_splits_selfpairs_and_degree3_priority(self):
        p=load();facts=self.facts([{('a',):1},{('a','b'):1},{('a','a'):1},{('b',):1}]);inventory={('a',),('b',),('a','a'),('a','b'),('a','a','b')}
        pairs,state=self.pairs(p,facts,inventory)
        self.assertIn((0,1),pairs);self.assertIn((2,3),pairs);self.assertIn((0,0),pairs);self.assertEqual(len(pairs),len(set(pairs)))
        self.assertTrue(all(len(tuple(sorted(p._leading_monomial(facts[i][1])+p._leading_monomial(facts[j][1]))))==3 for i,j in pairs[:2]))
    def test_bucket_cartesian_is_lazy_and_budgeted_before_materialization(self):
        p=load();facts=self.facts([{('x',):1,():-i}for i in range(1000)]);state=p._index_state(0);stream=p._indexed_rlt_pairs(facts,{('x',),('x','x')},lambda:None,lambda:True,state)
        self.assertEqual(next(stream),(0,0));self.assertEqual(state['candidate_pairs'],1);self.assertLess(state['total_scans'],200000);stream.close()
    def test_index_budget_time_and_full_deadline_are_distinct(self):
        p=load();facts=self.facts([{('x',):1},{('x',):-1,():-1}]);inventory={('x',),('x','x')}
        with mock.patch.object(p,'MAX_PAIR_SCANS',3):
            pairs,state=self.pairs(p,facts,inventory)
        self.assertEqual(pairs,[]);self.assertLessEqual(state['total_scans'],3);self.assertIn('index_scan_cap',state['truncations'])
        pairs,state=self.pairs(p,facts,inventory,optional=lambda:False);self.assertEqual(pairs,[]);self.assertIn('generation_deadline',state['truncations'])
        def expired():raise TimeoutError('full deadline')
        with self.assertRaises(TimeoutError):self.pairs(p,facts,inventory,remaining=expired)
    def test_indexed_equality_multiplication_is_budgeted_and_transactional(self):
        p=load();atoms=[dict(atom='(= x y)',relation='=',poly={('x',):1,('y',):-1})];inventory={('x',),('y',),('x','x'),('x','y'),('y','y')}
        lifted,cuts,stats=p._indexed_sparse_closure(atoms,['x','y'],{},inventory,lambda:None,lambda:True)
        self.assertEqual(stats['multiply_term_visits'],4);self.assertEqual(stats['equality_cuts'],2)
        self.assertEqual(stats['total_scans'],stats['join_scans']+stats['index_scans'])
        with mock.patch.object(p,'MAX_PAIR_SCANS',5):
            lifted,cuts,stats=p._indexed_sparse_closure(atoms,['x','y'],{},inventory,lambda:None,lambda:True)
        self.assertEqual(lifted,inventory);self.assertEqual(cuts,[]);self.assertEqual(stats['multiply_term_visits'],1);self.assertEqual(stats['total_scans'],5);self.assertIn('index_scan_cap',stats['truncations'])
    def test_unrelated_pair_prefix_does_not_hide_relevant_indexed_pair(self):
        p=load();polys=[{('u'+str(i),):1}for i in range(100)]+[{('a',):1,('b',):1},{('a',):1,('b',):-1}];facts=self.facts(polys);inventory={(next(iter(poly))[0],)for poly in polys}|{('a',),('b',),('a','a'),('b','b')}
        old_prefix=[]
        for i in range(len(facts)):
            for j in range(i,len(facts)):
                if len(old_prefix)<400:old_prefix.append((i,j))
        old_accepted=[(i,j)for i,j in old_prefix if all(not m or m in inventory for m in p._multiply(polys[i],polys[j]))]
        self.assertEqual(old_accepted,[])
        with mock.patch.object(p,'MAX_PAIR_SCANS',400):pairs,state=self.pairs(p,facts,inventory)
        self.assertIn((100,101),pairs);self.assertLessEqual(state['total_scans'],400)
    def test_default_and_shared_closure_match_archived_a96_byte_semantics(self):
        p=load();old_path=ROOT/'z3-nia/logs/research-round2/guarded-product-indexed-rlt-component/prior-producer-a96.py';spec=importlib.util.spec_from_file_location('_prior_a96_for_indexed_control',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        _,atoms,variables,bound,inventory=SharedCubicPureTests().fixture()
        def encoded(result):return json.dumps([sorted(result[0]),result[1],result[2]],sort_keys=True)
        for name in('_closure','_sparse_closure'):
            self.assertEqual(encoded(getattr(p,name)(atoms,variables,bound,inventory,lambda:None,lambda:True)),encoded(getattr(old,name)(atoms,variables,bound,inventory,lambda:None,lambda:True)))

class LinearEqualityMomentPureTests(unittest.TestCase):
    POLICY='indexed-linear-equality-moments'
    def api(self):
        p=load();self.assertTrue(callable(getattr(p,'_append_linear_moments',None)),'covered linear-Eq moment append phase is missing')
        return p
    def fixture(self):
        atoms=[dict(atom='linear-equality',relation='=',poly={('x',):2,('y',):-3,():5})]
        variables=['w','x','y','z'];inventory={(v,)for v in variables}|{('w','x'),('w','y'),('x','z'),('y','z')}
        cuts=[dict(kind='equality_times_variable',atom='old-equality',variable='w')]
        return atoms,variables,inventory,cuts
    def append(self,p,atoms,variables,inventory,cuts,remaining=lambda:None,optional=lambda:True):
        return p._append_linear_moments(atoms,variables,inventory,cuts,remaining,optional)
    def test_expanded_inventory_links_append_complete_guarded_products_only(self):
        p=self.api();a,v,i,c=self.fixture();before=set(i);prefix=list(c)
        after,cuts,stats=self.append(p,a,v,i,c)
        self.assertIs(after,i);self.assertEqual(i,before);self.assertEqual(cuts[:len(prefix)],prefix)
        self.assertEqual(cuts[len(prefix):],[dict(kind='equality_times_variable',atom='linear-equality',variable=x)for x in ['w','z']])
        self.assertEqual(stats['moment_cuts'],2);self.assertLessEqual(stats['moment_work'],50000)
        i.remove(('w','y'));_,cuts,_=self.append(p,a,v,i,prefix.copy())
        self.assertFalse(any(q['atom']=='linear-equality'and q['variable']=='w'for q in cuts))
    def test_exact_pair_dedup_original_used_variables_and_linear_only(self):
        p=self.api();a,v,i,c=self.fixture();a*=2;a+=[dict(atom='quadratic',relation='=',poly={('x','x'):1}),dict(atom='constant',relation='=',poly={():1})]
        i|={('ghost','x'),('ghost','y')};c.append(dict(kind='equality_times_variable',atom='linear-equality',variable='w'))
        _,cuts,stats=self.append(p,a,v,i,c);new=cuts[2:]
        self.assertEqual(new,[dict(kind='equality_times_variable',atom='linear-equality',variable='z')]);self.assertEqual(stats['moment_cuts'],1)
    def test_50000_work_cap_and_all_traversal_categories_are_explicit(self):
        p=self.api();a,v,i,c=self.fixture();_,full,stats=self.append(p,a,v,i,c.copy())
        categories=[k for k in stats if k.startswith('moment_')and k.endswith('_visits')]
        self.assertGreaterEqual(len(categories),8);self.assertEqual(stats['moment_work'],sum(stats[k]for k in categories))
        for limit in range(stats['moment_work']+1):
            with mock.patch.object(p,'MAX_LINEAR_MOMENT_WORK',limit):
                after,cuts,truncated=self.append(p,a,v,i,c.copy())
            self.assertIs(after,i);self.assertLessEqual(truncated['moment_work'],limit);self.assertEqual(cuts,full[:len(cuts)])
            if limit<stats['moment_work']:self.assertIn('moment_work_cap',truncated['moment_truncations'])
    def test_total_cut_cap_cannot_replace_old_RLT_or_grow_inventory(self):
        p=self.api();a,v,i,c=self.fixture();before=set(i)
        for cap in (1,2):
            with mock.patch.object(p,'MAX_CUTS',cap):after,cuts,stats=self.append(p,a,v,i,c.copy())
            self.assertIs(after,i);self.assertEqual(i,before);self.assertEqual(cuts[:1],c);self.assertEqual(len(cuts),cap)
            self.assertIn('moment_cut_cap',stats['moment_truncations'])
    def test_shared_half_deadline_full_timeout_and_optional_exception_are_distinct(self):
        p=self.api();a,v,i,c=self.fixture();_,cuts,stats=self.append(p,a,v,i,c.copy(),optional=lambda:False)
        self.assertEqual(cuts,c);self.assertIn('moment_generation_deadline',stats['moment_truncations'])
        def expired():raise TimeoutError('caller total deadline')
        with self.assertRaises(TimeoutError):self.append(p,a,v,i,c.copy(),remaining=expired)
        product=p._moment_product;calls=0
        def fail_second(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise p.Unsupported('optional arithmetic guard')
            return product(*args)
        with mock.patch.object(p,'_moment_product',side_effect=fail_second):_,cuts,stats=self.append(p,a,v,i,c.copy())
        self.assertEqual(cuts,c+[dict(kind='equality_times_variable',atom='linear-equality',variable='w')]);self.assertIn('moment_optional_arithmetic_cap',stats['moment_truncations'])
    def test_deadline_and_memory_failure_cannot_publish_partial_new_cut(self):
        p=self.api();a,v,i,c=self.fixture();expired=[False];original=p._moment_product
        def product(*args):
            value=original(*args);expired[0]=True;return value
        with mock.patch.object(p,'_moment_product',side_effect=product):_,cuts,stats=self.append(p,a,v,i,c.copy(),optional=lambda:not expired[0])
        self.assertEqual(cuts,c);self.assertIn('moment_generation_deadline',stats['moment_truncations'])
        class FailingAppend(list):
            def append(self,value):super().append(value);raise MemoryError('after append')
        original_cuts=FailingAppend(c)
        _,cuts,stats=self.append(p,a,v,i,original_cuts)
        self.assertEqual(cuts,c);self.assertEqual(i,self.fixture()[2]);self.assertIn('moment_memory_cap',stats['moment_truncations'])
    def test_old_functions_AST_and_seven_EB_fixtures_remain_compatible(self):
        import ast
        p=self.api();old_path=ROOT/'z3-nia/logs/research-round2/linear-equality-moment-component/prior-producer-eb628.py';spec=importlib.util.spec_from_file_location('_frozen_EB_moments',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        functions=lambda text:{n.name:ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        before,after=functions(old_path.read_text()),functions(MODULE.read_text())
        for name,value in before.items():
            if name!='analyze':self.assertEqual(after[name],value,name+' old helper AST changed')
        _,base,variables,bound,inventory=SharedCubicPureTests().fixture();fixtures=[]
        for index in range(7):
            atoms=[dict(a,poly=dict(a['poly']))for a in base];inv=set(inventory);names=list(variables)
            if index==1:atoms[0]['poly']={('a','a'):1,('d',):-1};atoms[2]['poly']={('a',):1};inv|={('a','a')}
            elif index==2:atoms[0]['poly']={('a',):1,('d',):-1}
            elif index==3:atoms=[dict(atom='h',relation='>=',poly={('a',):1,('b',):1}),dict(atom='k',relation='>=',poly={('a',):1,('b',):-1})];inv|={('a','a'),('b','b')}
            elif index==4:atoms.append(dict(atom='zero',relation='=',poly={}))
            elif index==5:atoms.append(dict(atom='linear',relation='=',poly={('c',):1,('d',):-1}))
            elif index==6:names+=['unrelated'];inv.add(('unrelated',))
            fixtures.append((atoms,names,bound,inv))
        encode=lambda value:json.dumps([sorted(value[0]),value[1],value[2]],sort_keys=True)
        for fixture in fixtures:
            for name in ('_closure','_sparse_closure','_indexed_sparse_closure'):
                self.assertEqual(encode(getattr(p,name)(*fixture,lambda:None,lambda:True)),encode(getattr(old,name)(*fixture,lambda:None,lambda:True)))
    def test_new_profile_is_closed_default_off_and_preserves_old_cut_prefix(self):
        import inspect
        p=self.api();self.assertEqual(inspect.signature(p.analyze).parameters['closure_policy'].default,'all-variables')
        self.assertEqual(p._analysis_profile(self.POLICY,8192),(self.POLICY,8192))
        for name,cap in [('all-variables',256),('shared-cubic',8192),('indexed-shared-cubic',8192)]:self.assertEqual(p._analysis_profile(name,cap),p._profile(name,cap))
        for cap in (256,8193,True):
            with self.assertRaises(ValueError):p._analysis_profile(self.POLICY,cap)
        _,atoms,variables,bound,inventory=SharedCubicPureTests().fixture();old=p._indexed_sparse_closure(atoms,variables,bound,inventory,lambda:None,lambda:True);new=p._linear_moment_closure(atoms,variables,bound,inventory,lambda:None,lambda:True)
        self.assertEqual(new[0],old[0]);self.assertEqual(new[1][:len(old[1])],old[1]);self.assertEqual(new[2]['total_scans'],old[2]['total_scans']);self.assertEqual(new[2]['base_equality_cuts'],old[2]['equality_cuts'])

class QuadraticEqualityMomentPureTests(unittest.TestCase):
    POLICY='indexed-quadratic-equality-moments'
    def api(self):
        p=load();self.assertTrue(callable(getattr(p,'_append_quadratic_moments',None)),'covered quadratic-multiplier phase is missing');return p
    def fixture(self):
        atoms=[dict(atom='linear',relation='=',poly={('x',):2,('y',):-3,():5})];variables=['u','v','x','y'];inventory={(v,)for v in variables}|{('u','u'),('u','v'),('u','u','x'),('u','u','y'),('u','v','x'),('u','v','y')};cuts=[dict(kind='equality_times_variable',atom='old',variable='u')]
        return atoms,variables,inventory,cuts
    def append(self,p,a,v,i,c,remaining=lambda:None,optional=lambda:True):return p._append_quadratic_moments(a,v,i,c,remaining,optional)
    def test_cubic_carriers_cover_complete_product_and_repeated_factors(self):
        p=self.api();a,v,i,c=self.fixture();prefix=list(c);before=set(i);after,cuts,stats=self.append(p,a,v,i,c)
        self.assertIs(after,i);self.assertEqual(i,before);self.assertEqual(cuts[:1],prefix)
        self.assertEqual(cuts[1:],[dict(kind='equality_times_monomial',atom='linear',variables=list(m))for m in [('u','u'),('u','v')]])
        self.assertEqual(stats['quadratic_cuts'],2)
        for missing in [('u','u'),('u','u','y')]:
            smaller=before-{missing};_,result,_=self.append(p,a,v,smaller,prefix.copy());self.assertFalse(any(q.get('variables')==['u','u']for q in result))
    def test_guard_exact_pair_duplicates_source_variables_and_linear_only(self):
        p=self.api();a,v,i,c=self.fixture();a*=2;a+=[dict(atom='quadratic',relation='=',poly={('x','x'):1}),dict(atom='constant',relation='=',poly={():1})]
        i|={('ghost','u'),('ghost','u','x'),('ghost','u','y')};c.append(dict(kind='equality_times_monomial',atom='linear',variables=['u','u']))
        _,cuts,stats=self.append(p,a,v,i,c);self.assertEqual(cuts[2:],[dict(kind='equality_times_monomial',atom='linear',variables=['u','v'])]);self.assertEqual(stats['quadratic_cuts'],1)
    def test_multiplier_coordinate_is_unnecessary_without_constant_term(self):
        p=self.api();a,v,i,c=self.fixture();a[0]['poly'].pop(());i.remove(('u','u'))
        _,cuts,_=self.append(p,a,v,i,c)
        self.assertIn(dict(kind='equality_times_monomial',atom='linear',variables=['u','u']),cuts)
    def test_new50k_counts_all_index_term_membership_and_commit_work(self):
        p=self.api();a,v,i,c=self.fixture();_,full,stats=self.append(p,a,v,i,c.copy());categories=[k for k in stats if k.startswith('quadratic_')and k.endswith('_visits')]
        self.assertGreaterEqual(len(categories),10);self.assertEqual(stats['quadratic_work'],sum(stats[k]for k in categories))
        for limit in range(stats['quadratic_work']+1):
            with mock.patch.object(p,'MAX_QUADRATIC_MOMENT_WORK',limit):after,cuts,s=self.append(p,a,v,i,c.copy())
            self.assertIs(after,i);self.assertLessEqual(s['quadratic_work'],limit);self.assertEqual(cuts,full[:len(cuts)])
            if limit<stats['quadratic_work']:self.assertIn('quadratic_work_cap',s['quadratic_truncations'])
    def test_shared_deadlines_and_atomic_optional_memory_or_arithmetic_failure(self):
        p=self.api();a,v,i,c=self.fixture();_,cuts,stats=self.append(p,a,v,i,c.copy(),optional=lambda:False);self.assertEqual(cuts,c);self.assertIn('quadratic_generation_deadline',stats['quadratic_truncations'])
        def expired():raise TimeoutError('same original total deadline')
        with self.assertRaises(TimeoutError):self.append(p,a,v,i,c.copy(),remaining=expired)
        original=p._quadratic_product;expired_flag=[False]
        def late(*args):value=original(*args);expired_flag[0]=True;return value
        with mock.patch.object(p,'_quadratic_product',side_effect=late):_,cuts,stats=self.append(p,a,v,i,c.copy(),optional=lambda:not expired_flag[0])
        self.assertEqual(cuts,c)
        class FailingAppend(list):
            def append(self,value):super().append(value);raise MemoryError('after insertion')
        _,cuts,stats=self.append(p,a,v,i,FailingAppend(c));self.assertEqual(cuts,c);self.assertIn('quadratic_memory_cap',stats['quadratic_truncations'])
        calls=0
        def fail_second(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise p.Unsupported('optional integer cap')
            return original(*args)
        with mock.patch.object(p,'_quadratic_product',side_effect=fail_second):_,cuts,stats=self.append(p,a,v,i,c.copy())
        self.assertEqual(cuts,c+[dict(kind='equality_times_monomial',atom='linear',variables=['u','u'])]);self.assertIn('quadratic_optional_arithmetic_cap',stats['quadratic_truncations'])
    def test_total_cut_cap_and_independent_base_linear_and_quadratic_counters(self):
        p=self.api();a,v,i,c=self.fixture()
        for cap in (1,2):
            with mock.patch.object(p,'MAX_CUTS',cap):after,cuts,stats=self.append(p,a,v,i,c.copy())
            self.assertEqual(len(cuts),cap);self.assertIs(after,i);self.assertIn('quadratic_cut_cap',stats['quadratic_truncations'])
        _,atoms,names,bound,inventory=SharedCubicPureTests().fixture();old=p._linear_moment_closure(atoms,names,bound,inventory,lambda:None,lambda:True);new=p._quadratic_moment_closure(atoms,names,bound,inventory,lambda:None,lambda:True)
        self.assertEqual(new[0],old[0]);self.assertEqual(new[1][:len(old[1])],old[1])
        for key,value in old[2].items():
            if key not in('closure_policy','truncations'):self.assertEqual(new[2][key],value,key)
        self.assertEqual(new[2]['quadratic_base_cut_count'],len(old[1]))
    def test_old_four_profiles_helpers_AST_and_seven_fixture_parity(self):
        import ast
        p=self.api();path=ROOT/'z3-nia/logs/research-round2/quadratic-equality-multiplier-component/prior-producer-b480.py';spec=importlib.util.spec_from_file_location('_frozen_b480_quadratic',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        functions=lambda text:{n.name:ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};before,after=functions(path.read_text()),functions(MODULE.read_text())
        for name,value in before.items():
            if name!='analyze':self.assertEqual(after[name],value,name)
        _,atoms,names,bound,inventory=SharedCubicPureTests().fixture();encode=lambda r:json.dumps([sorted(r[0]),r[1],r[2]],sort_keys=True)
        for index in range(7):
            extra=dict(atom='linear'+str(index),relation='=',poly={(names[index%len(names)],):index+1,():-index});fixture=(atoms+[extra],names,bound,inventory)
            for name in('_closure','_sparse_closure','_indexed_sparse_closure','_linear_moment_closure'):self.assertEqual(encode(getattr(p,name)(*fixture,lambda:None,lambda:True)),encode(getattr(old,name)(*fixture,lambda:None,lambda:True)))
    def test_new_profile_closed_default_OFF_and_v2_builder_isolated(self):
        import inspect
        p=self.api();self.assertTrue(callable(getattr(p,'_lift_monomial_query',None)));self.assertEqual(inspect.signature(p.analyze).parameters['closure_policy'].default,'all-variables')
        self.assertEqual(p._quadratic_profile(self.POLICY,8192),(self.POLICY,8192))
        for name,cap in [('all-variables',256),('shared-cubic',8192),('indexed-shared-cubic',8192),('indexed-linear-equality-moments',8192)]:self.assertEqual(p._quadratic_profile(name,cap),p._analysis_profile(name,cap))
        for cap in (256,8193,True):
            with self.assertRaises(ValueError):p._quadratic_profile(self.POLICY,cap)

MOMENT_POLICY='indexed-linear-equality-moments'
MOMENT_TOY='''(set-logic QF_NIA)
(declare-const p Int)(declare-const q Int)(declare-const r Int)(declare-const s Int)(declare-const w Int)(declare-const x Int)(declare-const y Int)(declare-const a Int)(declare-const b Int)(declare-const g Int)
(assert (= x y))
(assert (= x (* p q)))
(assert (= y (* r s)))
(assert (= a (* p w)))
(assert (= b (* r w)))
(assert (= g (- (* a q) (* b s))))
(assert (>= q 0))(assert (>= s 0))(assert (>= w 0))
(assert (>= (* p q) 0))(assert (>= (* p w) 0))(assert (>= (* r s) 0))(assert (>= (* r w) 0))
'''
MOMENT_SDK=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_104807_978529/source/python')
MOMENT_LIBRARY=MOMENT_SDK/'z3/lib/libz3.so'

def moment_group(output,*,preflight=False):
    import builtins,ctypes,time
    from unittest import mock
    output=Path(output);output.mkdir(exist_ok=False);producer=load()
    sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert sha(MOMENT_LIBRARY)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(MOMENT_LIBRARY));builtins.Z3_LIB_DIRS=[str(MOMENT_LIBRARY.resolve().parent)]
    for key in('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(key,None)
    sys.path.insert(0,str(MOMENT_SDK));import z3
    assert Path(z3.__file__).resolve().is_relative_to(MOMENT_SDK)
    calls=[];results=[];check=z3.Solver.check;began=time.monotonic()
    def counted(self,*args):
        if preflight:raise RuntimeError('zero-SMT moment preflight stopped at check')
        calls.append(dict(entered=time.monotonic()-began))
        try:answer=check(self,*args);calls[-1]['answer']=str(answer);return answer
        finally:calls[-1]['completed']=time.monotonic()-began
    def witness(query,mapping,values):
        context=z3.Context();forms=z3.parse_smt2_file(str(query),ctx=context)
        pairs=[(z3.Real(m['name'],ctx=context),z3.RealVal(str(values.get(tuple(m['variables']),0)),ctx=context))for m in mapping]
        return [z3.simplify(z3.substitute(form,*pairs))for form in forms]
    try:
        with mock.patch.object(z3.Optimize,'check',side_effect=AssertionError('Optimize.check forbidden')),mock.patch.object(z3.Solver,'check',counted):
            for index,(name,direction,policy,expected)in enumerate([('old-min','min','indexed-shared-cubic','relaxation_sat'),('new-min','min',MOMENT_POLICY,'bound_unsat'),('new-max','max',MOMENT_POLICY,'bound_unsat')]):
                folder=output/name;folder.mkdir();source=folder/'source.smt2';source.write_text(MOMENT_TOY+('('+('minimize'if direction=='min'else'maximize')+' g)\n'))
                result=producer.analyze(source,direction,'0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=policy,variable_limit=8192);results.append(result)
                (folder/'actual-result.json').write_text(json.dumps(result,indent=2)+'\n')
                if preflight:assert result['status']=='error'and 'zero-SMT moment preflight'in result['reason'],result
                else:assert result['status']==expected,(name,result)
                assert not result['is_optimal']and not result['source_model_verified']and result['check_started']
                cert=json.loads(Path(result['certificate_path']).read_text());assert len(cert)==13 and cert['source_sha256']==sha(source)and cert['query_sha256']==sha(result['query_path'])
                if index==0:
                    old=result;oldcert=cert
                    values={('g',):-1,('b','s'):1,('r','s','w'):1,('w','y'):1}
                    forms=witness(result['query_path'],cert['monomials'],values);assert all(z3.is_true(f)for f in forms),'fixed old SAT witness fails'
                    (folder/'exact-lifted-witness.json').write_text(json.dumps(dict(all_assertions_true=True,monomial_values=[dict(variables=list(m),value=str(c))for m,c in values.items()],all_other_monomials='0',solver_checks=0),indent=2)+'\n')
                else:
                    assert cert['monomials']==oldcert['monomials'];assert cert['cuts'][:len(oldcert['cuts'])]==oldcert['cuts'];added=cert['cuts'][len(oldcert['cuts']):]
                    assert added==[dict(kind='equality_times_variable',atom='(= x y)',variable='w')],added
                    counts=result['generation_counts'];assert counts['moment_cuts']==1 and counts['moment_work']<=50000
                    assert counts['total_scans']==old['generation_counts']['total_scans']
                    context=z3.Context();before=list(z3.parse_smt2_file(old['query_path'],ctx=context));after=list(z3.parse_smt2_file(result['query_path'],ctx=context))
                    # The strict-bound polarity is the only base difference in max.
                    if index==1:assert all(a.eq(b)for a,b in zip(before,after[:len(before)]))
                # Full original all-zero incumbent, verified without solving.
                ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(producer._strict(ROOT/'NIA_LS/converter/omt_to_ls.py')._query(source.read_text())[0])
                zero=[(z3.Int(n,ctx=ctx),z3.IntVal(0,ctx=ctx))for n in('p','q','r','s','w','x','y','a','b','g')]
                assert all(z3.is_true(z3.simplify(z3.substitute(f,*zero)))for f in parser.assertions())
                assert z3.simplify(z3.substitute(parser.objectives()[0],*zero)).as_long()==0
            if preflight:
                # A false Eq below OR must not make its moment unconditional.
                folder=output/'false-eq-guard';folder.mkdir();source=folder/'source.smt2';source.write_text(MOMENT_TOY.replace('(assert (= x y))','(assert (or (= x y) (< g 0)))')+'(minimize g)\n')
                result=producer.analyze(source,'min','0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=MOMENT_POLICY,variable_limit=8192)
                assert result['status']=='error'and 'zero-SMT moment preflight'in result['reason'],result
                cert=json.loads(Path(result['certificate_path']).read_text());point=dict(p=0,q=0,r=1,s=1,w=1,x=0,y=1,a=0,b=1,g=-1);values={}
                for m in cert['monomials']:
                    value=1
                    for n in m['variables']:value*=point[n]
                    values[tuple(m['variables'])]=value
                assert all(z3.is_true(f)for f in witness(result['query_path'],cert['monomials'],values)),'false Eq guard leaked into unconditional moment'
                assert any(c==dict(kind='equality_times_variable',atom='(= x y)',variable='w')for c in cert['cuts'])
        assert len(calls)==(0 if preflight else 3),calls
        receipt=dict(passed=True,preflight=preflight,actual_solver_checks=len(calls),checks=calls,results=results,module_sha256=sha(MODULE),tests_sha256=sha(__file__),library_sha256=sha(MOMENT_LIBRARY),nominal_solver_seconds=0 if preflight else 2.1,parent_hard_seconds=6,elapsed_seconds=time.monotonic()-began,old_inventory_and_cut_prefix_preserved=True,exact_old_SAT_witness_zero_SMT=True,full_source_zero_incumbents=3,no_benchmark=True)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS moment group preflight=',preflight,'actual checks=',len(calls));return 0
    except BaseException as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results,checks=calls,actual_solver_checks=len(calls),module_sha256=sha(MODULE),tests_sha256=sha(__file__)),indent=2)+'\n');raise

QUADRATIC_POLICY='indexed-quadratic-equality-moments'
QUADRATIC_TOY='''(set-logic QF_NIA)
(declare-const x Int)(declare-const y Int)(declare-const u Int)(declare-const v Int)(declare-const a Int)(declare-const b Int)(declare-const g Int)
(assert (= x y))
(assert (= a (* x u)))
(assert (= b (* y u)))
(assert (= g (- (* a v) (* b v))))
(assert (>= x 0))(assert (>= y 0))(assert (>= (* u v) 0))
'''

def quadratic_group(output,*,preflight=False):
    import builtins,ctypes,time
    from unittest import mock
    output=Path(output);output.mkdir(exist_ok=False);producer=load()
    sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert sha(MOMENT_LIBRARY)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(MOMENT_LIBRARY));builtins.Z3_LIB_DIRS=[str(MOMENT_LIBRARY.resolve().parent)]
    for key in('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(key,None)
    sys.path.insert(0,str(MOMENT_SDK));import z3
    assert Path(z3.__file__).resolve().is_relative_to(MOMENT_SDK)
    calls=[];results=[];check=z3.Solver.check;began=time.monotonic()
    def counted(self,*args):
        if preflight:raise RuntimeError('zero-SMT quadratic preflight stopped at check')
        calls.append(dict(entered=time.monotonic()-began))
        try:answer=check(self,*args);calls[-1]['answer']=str(answer);return answer
        finally:calls[-1]['completed']=time.monotonic()-began
    def witness(query,mapping,values):
        context=z3.Context();forms=z3.parse_smt2_file(str(query),ctx=context)
        pairs=[(z3.Real(m['name'],ctx=context),z3.RealVal(str(values.get(tuple(m['variables']),0)),ctx=context))for m in mapping]
        return [z3.simplify(z3.substitute(form,*pairs))for form in forms]
    try:
        with mock.patch.object(z3.Optimize,'check',side_effect=AssertionError('Optimize.check forbidden')),mock.patch.object(z3.Solver,'check',counted):
            for index,(name,direction,policy,expected)in enumerate([('old-min','min','indexed-linear-equality-moments','relaxation_sat'),('new-min','min',QUADRATIC_POLICY,'bound_unsat'),('new-max','max',QUADRATIC_POLICY,'bound_unsat')]):
                folder=output/name;folder.mkdir();source=folder/'source.smt2';source.write_text(QUADRATIC_TOY+('('+('minimize'if direction=='min'else'maximize')+' g)\n'))
                result=producer.analyze(source,direction,'0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=policy,variable_limit=8192);results.append(result)
                (folder/'actual-result.json').write_text(json.dumps(result,indent=2)+'\n')
                if preflight:assert result['status']=='error'and 'zero-SMT quadratic preflight'in result['reason'],result
                else:assert result['status']==expected,(name,result)
                assert not result['is_optimal']and not result['source_model_verified']and result['check_started']
                cert=json.loads(Path(result['certificate_path']).read_text());assert len(cert)==13 and cert['source_sha256']==sha(source)and cert['query_sha256']==sha(result['query_path']);assert cert['schema']==('source-guarded-product-bound-v1'if index==0 else'source-guarded-product-bound-v2')
                if index==0:
                    old=result;oldcert=cert
                    values={('g',):-1,('b','v'):1,('u','v','y'):1}
                    forms=witness(result['query_path'],cert['monomials'],values);assert all(z3.is_true(f)for f in forms),'fixed old SAT witness fails'
                    (folder/'exact-lifted-witness.json').write_text(json.dumps(dict(all_assertions_true=True,monomial_values=[dict(variables=list(m),value=str(c))for m,c in values.items()],all_other_monomials='0',solver_checks=0),indent=2)+'\n')
                else:
                    assert cert['monomials']==oldcert['monomials'];assert cert['cuts'][:len(oldcert['cuts'])]==oldcert['cuts'];added=cert['cuts'][len(oldcert['cuts']):]
                    assert added==[dict(kind='equality_times_monomial',atom='(= x y)',variables=['u','v'])],added
                    counts=result['generation_counts'];assert counts['quadratic_cuts']==1 and counts['quadratic_work']<=50000
                    assert counts['total_scans']==old['generation_counts']['total_scans']
                    context=z3.Context();before=list(z3.parse_smt2_file(old['query_path'],ctx=context));after=list(z3.parse_smt2_file(result['query_path'],ctx=context))
                    # The strict-bound polarity is the only base difference in max.
                    if index==1:assert all(a.eq(b)for a,b in zip(before,after[:len(before)]))
                # Full original all-zero incumbent, verified without solving.
                ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(producer._strict(ROOT/'NIA_LS/converter/omt_to_ls.py')._query(source.read_text())[0])
                zero=[(z3.Int(n,ctx=ctx),z3.IntVal(0,ctx=ctx))for n in('x','y','u','v','a','b','g')]
                assert all(z3.is_true(z3.simplify(z3.substitute(f,*zero)))for f in parser.assertions())
                assert z3.simplify(z3.substitute(parser.objectives()[0],*zero)).as_long()==0
            if preflight:
                # A false Eq below OR must not make its moment unconditional.
                folder=output/'false-eq-guard';folder.mkdir();source=folder/'source.smt2';source.write_text(QUADRATIC_TOY.replace('(assert (= x y))','(assert (or (= x y) (< g 0)))')+'(minimize g)\n')
                result=producer.analyze(source,'min','0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=QUADRATIC_POLICY,variable_limit=8192)
                assert result['status']=='error'and 'zero-SMT quadratic preflight'in result['reason'],result
                cert=json.loads(Path(result['certificate_path']).read_text());point=dict(u=1,v=1,x=0,y=1,a=0,b=1,g=-1);values={}
                for m in cert['monomials']:
                    value=1
                    for n in m['variables']:value*=point[n]
                    values[tuple(m['variables'])]=value
                assert all(z3.is_true(f)for f in witness(result['query_path'],cert['monomials'],values)),'false Eq guard leaked into unconditional moment'
                assert any(c==dict(kind='equality_times_monomial',atom='(= x y)',variables=['u','v'])for c in cert['cuts'])
                # Repeated factors are real source variables, never a fake lifted Int.
                folder=output/'false-eq-repeated';folder.mkdir();source=folder/'source.smt2'
                repeated=QUADRATIC_TOY.replace('(* a v)','(* a u)').replace('(* b v)','(* b u)').replace('(* u v)','(* u u)').replace('(assert (= x y))','(assert (or (= x y) (< g 0)))')
                source.write_text(repeated+'(minimize g)\n');result=producer.analyze(source,'min','0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=QUADRATIC_POLICY,variable_limit=8192)
                assert result['status']=='error'and 'zero-SMT quadratic preflight'in result['reason'],result
                cert=json.loads(Path(result['certificate_path']).read_text());assert any(c==dict(kind='equality_times_monomial',atom='(= x y)',variables=['u','u'])for c in cert['cuts'])
                point=dict(x=0,y=1,u=1,v=0,a=0,b=1,g=-1);values={}
                for m in cert['monomials']:
                    value=1
                    for n in m['variables']:value*=point[n]
                    values[tuple(m['variables'])]=value
                assert all(z3.is_true(f)for f in witness(result['query_path'],cert['monomials'],values))
                # A zero-constant E does not need a separate multiplier coordinate.
                folder=output/'missing-multiplier-coordinate';folder.mkdir();source=folder/'source.smt2'
                missing=QUADRATIC_TOY.replace('(assert (>= (* u v) 0))','(assert (>= u 0))(assert (>= (* x v) 0))(assert (>= (* y v) 0))')
                source.write_text(missing+'(minimize g)\n');result=producer.analyze(source,'min','0',.7,folder/'producer',z3,strict_helper=ROOT/'NIA_LS/converter/omt_to_ls.py',closure_policy=QUADRATIC_POLICY,variable_limit=8192)
                assert result['status']=='error'and 'zero-SMT quadratic preflight'in result['reason'],result
                cert=json.loads(Path(result['certificate_path']).read_text());assert ['u','v']not in[m['variables']for m in cert['monomials']]
                assert any(c==dict(kind='equality_times_monomial',atom='(= x y)',variables=['u','v'])for c in cert['cuts'])
        assert len(calls)==(0 if preflight else 3),calls
        receipt=dict(passed=True,preflight=preflight,actual_solver_checks=len(calls),checks=calls,results=results,module_sha256=sha(MODULE),tests_sha256=sha(__file__),library_sha256=sha(MOMENT_LIBRARY),nominal_solver_seconds=0 if preflight else 2.1,parent_hard_seconds=6,elapsed_seconds=time.monotonic()-began,old_inventory_and_cut_prefix_preserved=True,exact_old_SAT_witness_zero_SMT=True,full_source_zero_incumbents=3,no_benchmark=True)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS moment group preflight=',preflight,'actual checks=',len(calls));return 0
    except BaseException as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results,checks=calls,actual_solver_checks=len(calls),module_sha256=sha(MODULE),tests_sha256=sha(__file__)),indent=2)+'\n');raise

class QuadraticEqualityMomentGeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_QUADRATIC_REAL')=='1','one explicit six-second quadratic generated group only')
    def test_one_fixed_quadratic_group(self):
        output=Path(os.environ['NIA_GUARDED_QUADRATIC_ARTIFACTS'])
        try:result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--quadratic-child',str(output)],text=True,capture_output=True,timeout=6)
        except subprocess.TimeoutExpired as error:
            decode=lambda value:value.decode(errors='replace')if isinstance(value,bytes)else(value or'')
            (output.parent/'generated.stdout').write_text(decode(error.stdout));(output.parent/'generated.stderr').write_text(decode(error.stderr));(output.parent/'generated-timeout.json').write_text(json.dumps(dict(timeout_seconds=6,partial_artifacts_preserved=True))+'\n');raise
        (output.parent/'generated.stdout').write_text(result.stdout);(output.parent/'generated.stderr').write_text(result.stderr);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

class LinearEqualityMomentGeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_MOMENT_REAL')=='1','one explicit six-second generated moment group only')
    def test_one_fixed_moment_group(self):
        output=Path(os.environ['NIA_GUARDED_MOMENT_ARTIFACTS'])
        try:result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--moment-child',str(output)],text=True,capture_output=True,timeout=6)
        except subprocess.TimeoutExpired as error:
            decode=lambda value:value.decode(errors='replace')if isinstance(value,bytes)else(value or'')
            (output.parent/'generated.stdout').write_text(decode(error.stdout));(output.parent/'generated.stderr').write_text(decode(error.stderr));(output.parent/'generated-timeout.json').write_text(json.dumps(dict(timeout_seconds=6,partial_artifacts_preserved=True))+'\n');raise
        (output.parent/'generated.stdout').write_text(result.stdout);(output.parent/'generated.stderr').write_text(result.stderr);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

class GeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_PRODUCT_REAL')=='1','explicit generated SDK group only')
    def test_unique_generated_group(self):
        output=Path(os.environ['NIA_GUARDED_PRODUCT_ARTIFACTS'])
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',os.environ['NIA_GUARDED_PRODUCT_LIBRARY'],os.environ['NIA_GUARDED_PRODUCT_SDK'],str(output)],text=True,capture_output=True,timeout=7.5)
        (output.parent/'generated.stdout').write_text(completed.stdout);(output.parent/'generated.stderr').write_text(completed.stderr)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)

class SharedCubicGeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_SHARED_REAL')=='1','explicit new shared-cubic generated group only')
    def test_unique_shared_generated_group(self):
        output=Path(os.environ['NIA_GUARDED_SHARED_ARTIFACTS'])
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--shared-child',os.environ['NIA_GUARDED_SHARED_LIBRARY'],os.environ['NIA_GUARDED_SHARED_SDK'],str(output)],text=True,capture_output=True,timeout=6)
        (output.parent/'generated.stdout').write_text(completed.stdout);(output.parent/'generated.stderr').write_text(completed.stderr)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)

class IndexedRLTGeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_INDEXED_REAL')=='1','explicit new indexed RLT generated group only')
    def test_unique_indexed_generated_group(self):
        output=Path(os.environ['NIA_GUARDED_INDEXED_ARTIFACTS'])
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--indexed-child',os.environ['NIA_GUARDED_INDEXED_LIBRARY'],os.environ['NIA_GUARDED_INDEXED_SDK'],str(output)],text=True,capture_output=True,timeout=6)
        (output.parent/'generated.stdout').write_text(completed.stdout);(output.parent/'generated.stderr').write_text(completed.stderr)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)
    @unittest.skipUnless(os.environ.get('NIA_GUARDED_INDEXED_REMAINDER')=='1','only unexecuted generated checks; requires separate authorization')
    def test_remaining_generated_checks(self):
        output=Path(os.environ['NIA_GUARDED_INDEXED_ARTIFACTS'])
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--indexed-rest-child',os.environ['NIA_GUARDED_INDEXED_LIBRARY'],os.environ['NIA_GUARDED_INDEXED_SDK'],str(output)],text=True,capture_output=True,timeout=3)
        (output.parent/'remainder.stdout').write_text(completed.stdout);(output.parent/'remainder.stderr').write_text(completed.stderr)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)

def generated(library,sdk,output):
    import builtins,ctypes,time
    producer=load();library=Path(library).resolve();sdk=Path(sdk).resolve();output=Path(output);output.mkdir(exist_ok=False)
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    assert sha(library)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for name in('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(name,None)
    sys.path.insert(0,str(sdk));import z3
    assert Path(z3.__file__).resolve().is_relative_to(sdk)
    checks=[];original_check=z3.Solver.check
    def counted(self,*args):
        began=time.monotonic()
        try:answer=original_check(self,*args)
        finally:checks.append(dict(seconds=time.monotonic()-began))
        checks[-1]['answer']=str(answer);return answer
    z3.Solver.check=counted
    declarations=''.join('(declare-const '+name+' Int)'for name in('A','C','x','l','l0','g'))
    branch='(or (and (>= l 0) (>= l0 0) (>= (+ l0 (* C l)) 1) (= (* A l) l0)) (>= g 0))'
    hard='(assert (>= x 1))(assert (<= (+ C (* A x)) 0))'
    cases=[('min-cubic-farkas','min','0',declarations+hard+'(assert '+branch+')(minimize g)','bound_unsat'),
           ('max-complete-or-not','max','0',declarations+hard+'(assert (not (not '+branch+')))(maximize (- g))','bound_unsat'),
           ('relaxation-sat-is-not-source-model','min','1','(declare-const x Int)(assert (>= x 0))(minimize x)','relaxation_sat')]
    results=[];identities=[];began=time.monotonic()
    try:
        for index,(name,direction,best,text,expected)in enumerate(cases):
            folder=output/f'{index}-{name}';folder.mkdir();source=folder/'source.smt2';source.write_text(text)
            result=producer.analyze(source,direction,best,.8,folder/'producer',z3)
            results.append(result);assert result['status']==expected,(name,result)
            assert source.read_text()==text and not result['is_optimal']and not result['source_model_verified']
            cert=json.loads(Path(result['certificate_path']).read_text());assert cert['query_sha256']==sha(result['query_path'])
            assert cert['source_sha256']==sha(source)and cert['direction']==direction and cert['own_best']==best
            assert cert['used_bool_names']==[]and cert['schema']=='source-guarded-product-bound-v1'
            assert cert['used_integer_names']==sorted(cert['used_integer_names'])
            if index<2:
                # Exact source ground substitution, with no solver call.
                filtered=producer._strict(None)._query(text)[0];ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(filtered)
                values={name:(1 if name=='x'else 0)for name in('A','C','x','l','l0','g')}
                substitutions=[(z3.Int(name,ctx=ctx),z3.IntVal(value,ctx=ctx))for name,value in values.items()]
                assert all(z3.is_true(z3.simplify(z3.substitute(form,*substitutions)))for form in parser.assertions())
                objective=z3.simplify(z3.substitute(parser.objectives()[0],*substitutions));assert z3.is_int_value(objective)and objective.as_long()==0
                ground=dict(source=str(source),source_sha256=sha(source),direction=direction,own_best='0',integer_assignment={name:str(value)for name,value in values.items()},complete_source_ground_verified=True,objective='0',solver_calls=0)
                (folder/'incumbent-ground.json').write_text(json.dumps(ground,indent=2)+'\n')
                assert any(c['kind']=='equality_times_variable'and c['variable']=='x'for c in cert['cuts'])
                assert any(len(m['variables'])==3 for m in cert['monomials'])
                assert result['diagnostic_bound_unsat']
            else:assert not result['diagnostic_bound_unsat']
            if index==0:
                # Strip only ledger consequences, leaving complete lifted F and bound.
                ctx=z3.Context();assertions=list(z3.parse_smt2_file(result['query_path'],ctx=ctx))
                assert len(assertions)==result['base_assertion_count']+result['cut_count']
                bare=z3.SolverFor('QF_LRA',ctx=ctx);bare.add(*assertions[:result['base_assertion_count']]);bare.set(timeout=200)
                answer=bare.check();assert answer==z3.sat,('bare monomial relaxation',answer)
                (folder/'bare.smt2').write_text('(set-logic QF_LRA)\n'+bare.to_smt2())
                (folder/'bare.json').write_text(json.dumps(dict(answer='sat',only_relaxation=True,timeout_seconds=.2),indent=2)+'\n')
            identities.append(dict(source_sha256=sha(source),query_sha256=sha(result['query_path']),certificate_sha256=sha(result['certificate_path'])))
        unsupported=output/'unsupported.smt2';unsupported.write_text('(declare-const b Bool)(minimize 0)')
        before=len(checks);bad=producer.analyze(unsupported,'min','0',.2,output/'unsupported',z3)
        assert bad['status']=='unsupported'and len(checks)==before and not bad['check_started']
        assert len(checks)==4,checks
        receipt=dict(passed=True,results=results,checks=checks,solver_checks=4,internal_nominal_seconds=2.6,parent_hard_seconds=7.5,elapsed_seconds=time.monotonic()-began,module_sha256=sha(MODULE),test_sha256=sha(__file__),library_sha256=sha(library),identities=identities,no_benchmark=True,source_incumbents_ground_only=2)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS: three source diagnostics + one bare relaxation, four solver checks, two exact source incumbents')
        return 0
    except Exception as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results,checks=checks,module_sha256=sha(MODULE),test_sha256=sha(__file__)),indent=2)+'\n');raise

def shared_generated(library,sdk,output):
    import builtins,ctypes,time
    producer=load();library=Path(library).resolve();sdk=Path(sdk).resolve();output=Path(output);output.mkdir(exist_ok=False)
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    assert sha(library)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for name in('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(name,None)
    sys.path.insert(0,str(sdk));import z3
    assert Path(z3.__file__).resolve().is_relative_to(sdk)
    checks=[];original=z3.Solver.check
    def counted(self,*args):
        began=time.monotonic()
        try:answer=original(self,*args)
        finally:checks.append(dict(seconds=time.monotonic()-began))
        checks[-1]['answer']=str(answer);return answer
    z3.Solver.check=counted
    names=['irrelevant_'+str(i)for i in range(300)]
    declarations=''.join('(declare-const '+n+' Int)'for n in ['A','C','x','l','l0','g']+names)
    irrelevant='(assert (>= (+ '+' '.join(names)+') 0))'
    hard=irrelevant+'(assert (>= x 1))(assert (<= (+ C (* A x)) 0))'
    branch='(or (and (>= l 0) (>= l0 0) (>= (+ l0 (* C l)) 1) (= (* A l) l0)) (>= g 0))'
    cases=[('large-min','min','0',declarations+hard+'(assert '+branch+')(minimize g)','bound_unsat'),('large-max-or-not','max','0',declarations+hard+'(assert (not (not '+branch+')))(maximize (- g))','bound_unsat'),('sparse-relaxation-only','min','1','(declare-const x Int)(assert (>= x 0))(minimize x)','relaxation_sat')]
    results=[];legacy=[];began=time.monotonic()
    try:
        for index,(name,direction,best,text,expected)in enumerate(cases):
            folder=output/str(index);folder.mkdir();source=folder/'source.smt2';source.write_text(text)
            if index<2:
                before=len(checks);prior=producer.analyze(source,direction,best,.5,folder/'legacy-default',z3)
                assert prior['status']=='unsupported'and prior['reason']=='used Int variable cap'and len(checks)==before and not prior['check_started'],prior
                legacy.append(prior)
            result=producer.analyze(source,direction,best,1.,folder/'producer',z3,closure_policy='shared-cubic',variable_limit=8192);results.append(result)
            assert result['status']==expected,(name,result)
            assert result['closure_policy']=='shared-cubic'and result['variable_limit']==8192 and not result['is_optimal']and not result['source_model_verified']
            cert=json.loads(Path(result['certificate_path']).read_text());assert cert['source_sha256']==sha(source)and cert['query_sha256']==sha(result['query_path'])
            assert source.read_text()==text and cert['direction']==direction and cert['own_best']==best
            if index<2:
                assert len(cert['used_integer_names'])==306
                selected=[c for c in cert['cuts']if c['kind']=='equality_times_variable'];assert len(selected)==1 and selected[0]['variable']=='x'
                assert result['generation_counts']['join_candidates']==1 and result['generation_counts']['total_scans']<=producer.MAX_PAIR_SCANS
                filtered=producer._strict(None)._query(text)[0];ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(filtered)
                values={n:1 if n=='x'else 0 for n in ['A','C','x','l','l0','g']+names}
                substitutions=[(z3.Int(n,ctx=ctx),z3.IntVal(v,ctx=ctx))for n,v in values.items()]
                assert all(z3.is_true(z3.simplify(z3.substitute(f,*substitutions)))for f in parser.assertions())
                objective=z3.simplify(z3.substitute(parser.objectives()[0],*substitutions));assert z3.is_int_value(objective)and objective.as_long()==0
                (folder/'incumbent-ground.json').write_text(json.dumps(dict(source=str(source),source_sha256=sha(source),direction=direction,own_best=best,complete_original_ground_verified=True,integer_assignment={n:str(v)for n,v in values.items()},solver_calls=0),indent=2)+'\n')
        assert len(checks)==3,checks
        receipt=dict(passed=True,solver_checks=3,checks=checks,results=results,legacy_default_unsupported=legacy,legacy_default_solver_checks=0,parent_hard_seconds=6,nominal_producer_seconds=3.,nominal_legacy_probe_seconds=1.,elapsed_seconds=time.monotonic()-began,module_sha256=sha(MODULE),test_sha256=sha(__file__),SDK_library_sha256=sha(library),no_benchmark=True,source_incumbents_ground_only=2,certificate_schema_unchanged=True)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS shared-cubic: default256 unsupported0check; two306var UNSAT; relaxationSAT; three actual checks');return 0
    except Exception as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results,legacy=legacy,checks=checks,module_sha256=sha(MODULE),test_sha256=sha(__file__)),indent=2)+'\n');raise

def indexed_generated(library,sdk,output,*,remaining_only=False):
    import builtins,ctypes,time
    producer=load();library=Path(library).resolve();sdk=Path(sdk).resolve();output=Path(output);output.mkdir(exist_ok=False)
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    assert sha(library)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
    ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for name in('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(name,None)
    sys.path.insert(0,str(sdk));import z3
    assert Path(z3.__file__).resolve().is_relative_to(sdk)
    checks=[];original=z3.Solver.check
    def counted(self,*args):
        began=time.monotonic()
        try:answer=original(self,*args)
        finally:checks.append(dict(seconds=time.monotonic()-began))
        checks[-1]['answer']=str(answer);return answer
    z3.Solver.check=counted
    names=['irrelevant_'+str(i)for i in range(1000)]
    declarations=''.join('(declare-const '+n+' Int)'for n in ['A','C','x','l','l0','g']+names)
    hard='(assert (>= x 1))(assert (<= (+ C (* A x)) 0))'
    branch='(or (and (>= l 0) (>= l0 0) (>= (+ l0 (* C l)) 1) (= (* A l) l0)) (>= g 0))'
    # Separate irrelevant facts are last, hence first in the preserved AST order.
    prefix=''.join('(assert (>= '+n+' 0))'for n in names)
    minimum=declarations+hard+'(assert '+branch+')'+prefix+'(minimize g)'
    maximum=declarations+hard+'(assert (not (not '+branch+')))'+prefix+'(maximize (- g))'
    cases=[('min-with-unrelated-prefix','min','0',minimum,'bound_unsat',1.25),('max-complete-or-not','max','0',maximum,'bound_unsat',1.25),('relaxation-only','min','1','(declare-const x Int)(assert (>= x 0))(minimize x)','relaxation_sat',.5)]
    results=[];legacy=None;began=time.monotonic()
    try:
        for index,(name,direction,best,text,expected,seconds)in enumerate(cases):
            if remaining_only and index==0:continue
            folder=output/str(index);folder.mkdir();source=folder/'source.smt2';source.write_text(text)
            if index==0:
                legacy=producer.analyze(source,direction,best,1.5,folder/'prior-shared',z3,closure_policy='shared-cubic',variable_limit=8192)
                assert legacy['status']=='relaxation_sat',legacy
                assert legacy['generation_counts']['product_cuts']==0 and legacy['generation_counts']['equality_cuts']==1,legacy
                assert any(t in legacy['generation_counts']['truncations']for t in('generation_deadline','pair_scan_cap')),legacy
            result=producer.analyze(source,direction,best,seconds,folder/'indexed',z3,closure_policy='indexed-shared-cubic',variable_limit=8192);results.append(result)
            assert result['status']==expected,(name,result)
            assert result['closure_policy']=='indexed-shared-cubic'and result['variable_limit']==8192 and result['discovery_version']=='guarded-product-discovery-v3'
            assert not result['is_optimal']and not result['source_model_verified']
            counts=result['generation_counts'];assert counts['total_scans']==counts['join_scans']+counts['index_scans']<=producer.MAX_PAIR_SCANS
            assert counts['index_scans']==sum(counts[k]for k in('leading_term_visits','inventory_visits','split_visits','bucket_visits','pair_visits','multiply_term_visits'))
            cert=json.loads(Path(result['certificate_path']).read_text());assert cert['source_sha256']==sha(source)and cert['query_sha256']==sha(result['query_path'])
            assert source.read_text()==text and cert['direction']==direction and cert['own_best']==best
            if index<2:
                assert len(cert['used_integer_names'])==1006 and counts['equality_cuts']==1 and counts['product_cuts']>0
                assert not counts['truncations'],counts
                filtered=producer._strict(None)._query(text)[0];ctx=z3.Context();parser=z3.Optimize(ctx=ctx);parser.from_string(filtered)
                values={n:1 if n=='x'else 0 for n in ['A','C','x','l','l0','g']+names}
                substitutions=[(z3.Int(n,ctx=ctx),z3.IntVal(v,ctx=ctx))for n,v in values.items()]
                assert z3.is_true(z3.simplify(z3.substitute(z3.And(*parser.assertions()),*substitutions)))
                objective=z3.simplify(z3.substitute(parser.objectives()[0],*substitutions));assert z3.is_int_value(objective)and objective.as_long()==0
                (folder/'incumbent-ground.json').write_text(json.dumps(dict(source=str(source),source_sha256=sha(source),direction=direction,own_best=best,complete_original_ground_verified=True,integer_assignment={n:str(v)for n,v in values.items()},solver_calls=0),indent=2)+'\n')
        assert len(checks)==(2 if remaining_only else 4),checks
        receipt=dict(passed=True,remaining_only=remaining_only,solver_checks=len(checks),checks=checks,results=results,prior_shared=legacy,parent_hard_seconds=3 if remaining_only else 6,nominal_producer_seconds=1.75 if remaining_only else 4.5,elapsed_seconds=time.monotonic()-began,module_sha256=sha(MODULE),test_sha256=sha(__file__),SDK_library_sha256=sha(library),no_benchmark=True,source_incumbents_ground_only=1 if remaining_only else 2,certificate_schema_unchanged=True)
        (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS indexed RLT remaining max/SAT checks'if remaining_only else'PASS indexed RLT: prior shared prefix SAT; indexed min/max UNSAT; relaxation SAT; four actual checks');return 0
    except Exception as error:
        (output/'failure.json').write_text(json.dumps(dict(error=repr(error),results=results,prior_shared=legacy,checks=checks,module_sha256=sha(MODULE),test_sha256=sha(__file__)),indent=2)+'\n');raise

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]in('--quadratic-child','--quadratic-preflight'):
        raise SystemExit(quadratic_group(sys.argv[2],preflight=sys.argv[1]=='--quadratic-preflight'))
    if len(sys.argv)>1 and sys.argv[1]in('--moment-child','--moment-preflight'):
        raise SystemExit(moment_group(sys.argv[2],preflight=sys.argv[1]=='--moment-preflight'))
    if len(sys.argv)>1 and sys.argv[1]=='--indexed-rest-child':raise SystemExit(indexed_generated(*sys.argv[2:],remaining_only=True))
    if len(sys.argv)>1 and sys.argv[1]=='--indexed-child':raise SystemExit(indexed_generated(*sys.argv[2:]))
    if len(sys.argv)>1 and sys.argv[1]=='--shared-child':raise SystemExit(shared_generated(*sys.argv[2:]))
    if len(sys.argv)>1 and sys.argv[1]=='--child':raise SystemExit(generated(*sys.argv[2:]))
    unittest.main()
