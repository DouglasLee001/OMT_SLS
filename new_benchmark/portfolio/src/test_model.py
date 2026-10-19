"""Semantic regression tests for exact integer portfolio conversion."""
import copy
from fractions import Fraction
import itertools
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as p

GAMS = '''Variables i1,i2,objvar;
Integer Variables i1,i2;
Equations e1,e2,e3,e4;
e1.. 0.5*sqr(i1)+0.25*i1*i2+0.75*sqr(i2)-objvar =L= 0;
e2.. i1+i2 =G= 2;
e3.. 2*i1+3*i2 =G= 5;
e4.. 2*i1+3*i2 =L= 6;
Model m / all /;
Solve m using MINLP minimizing objvar;
'''


class PortfolioSemantics(unittest.TestCase):
    def test_exact_decimal_scientific_tokens_and_canonical_terms(self):
        self.assertEqual(p.parse_polynomial('1e-20*sqr(i1) + .30*i2*i1 - 0.1*i1*i2 + 2'),
                         {('i1','i1'):Fraction(1,10**20),('i1','i2'):Fraction(1,5),():Fraction(2)})

    def test_polynomial_rejects_unknown_syntax(self):
        for text in ['sin(i1)', 'i1/i2', 'i1**2', 'i1+i999', 'sqr(i1, i2)', 'True', 'i1[0]']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                p.parse_polynomial(text, {'i1','i2'})

    def test_domain_modifications_and_initial_values(self):
        data = p.parse_gams(GAMS.replace('Model m', 'i1.lo=1.2; i1.up=4.8; i2.fx=1; i1.l=99; Model m'))
        self.assertEqual(data['variables'][0]['lower'], '2')
        self.assertEqual(data['variables'][0]['upper'], '3')  # budget 6 / price 2
        self.assertEqual(data['variables'][1]['lower'], '1')
        self.assertEqual(data['variables'][1]['upper'], '1')
        with self.assertRaises(ValueError):
            p.check(data, {'holdings':[1,1]})
        feasible = p.parse_gams(GAMS.replace('Model m', 'i1.lo=.2; i2.fx=1; Model m'))
        p.check(feasible, {'holdings':[1,1]})
        with self.assertRaises(ValueError):
            p.check(data, {'holdings':[0,2]})
        data = p.parse_gams(GAMS.replace('Model m', 'i1.lo=-1; Model m'))
        self.assertIsNone(data['variables'][0]['upper'])
        self.assertIn('nonnegative', data['bound_derivation']['reason'])

    def test_rejects_unhandled_statements_and_unsafe_elimination(self):
        for suffix in ['i1.foo=2;', 'objvar.lo=0;', 'objvar.up=100;',
                       'e5.. i1 =L= 5;', '$include bad.inc', 'execute "anything";']:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                p.parse_gams(GAMS.replace('Model m', suffix+'\nModel m'))
        with self.assertRaises(ValueError):
            p.parse_gams(GAMS.replace('e2.. i1+i2', 'e2.. objvar+i1+i2'))
        with self.assertRaises(ValueError):
            p.parse_gams(GAMS.replace('minimizing', 'maximizing'))
        with self.assertRaises(ValueError):
            p.parse_gams(GAMS.replace('Integer Variables i1,i2;', 'Integer Variables i1,i2,objvar;'))
        with self.assertRaises(ValueError):
            p.parse_gams(GAMS, external_parameters={'gams.u1':'anything'})

    def test_tiny_complete_enumeration(self):
        case = p.make('tiny')[0]
        data = case['data']
        found = {}
        for holdings in itertools.product(range(4), range(3)):
            try:
                result = p.check(data, {'holdings':list(holdings)})
                found[holdings] = result['cost']
            except ValueError:
                pass
        self.assertEqual(found, {(0,2):16,(1,1):9,(3,0):27})
        self.assertEqual(case['metadata']['objective']['scale'], '6')
        self.assertEqual(case['metadata']['expected_optimum'], '9')
        with self.assertRaises(ValueError):
            p.check(data, {'holdings':[1.0,1]})
        with self.assertRaises(ValueError):
            p.check(data, {'holdings':[1,1], 'cost':'10'})
        self.assertEqual(p.decode(data, p.assignment(data, {'holdings':[1,1]})), {'holdings':[1,1]})

    def test_fraction_and_integer_scaled_equations_agree(self):
        data = p.parse_gams(GAMS)
        rng=random.Random(2718)
        for _ in range(100):
            values={'i1':rng.randrange(10),'i2':rng.randrange(10)}
            for equation in data['equations']:
                if equation['name']=='e1': continue
                rational=sum((Fraction(int(t['coefficient']['num']),int(t['coefficient']['den']))*
                              __import__('math').prod(values[x] for x in t['variables'])
                              for t in equation['polynomial']), Fraction(0))
                expr,scale=p.polynomial_smt(p.unpack_polynomial(equation['polynomial']))
                integer=sum(int(c*scale)*__import__('math').prod(values[x] for x in mon)
                            for mon,c in p.unpack_polynomial(equation['polynomial']).items())
                self.assertEqual(rational*scale,integer)
                self.assertEqual(rational>=0,integer>=0)

    def test_synthetic_gram_witness_and_matrix_independent_objective(self):
        for m in (4,8,16):
            for seed in (1,2):
                case=p.make('synthetic-gram',assets=m,seed=seed)[0]
                self.assertEqual(case,p.make('synthetic-gram',assets=m,seed=seed)[0])
                data=case['data']; x=case['witness']['decision']['holdings']
                A=[[int(v) for v in row] for row in data['risk_factor_matrix']]
                lam=int(data['lambda'])
                expected=sum(sum(a*v for a,v in zip(row,x))**2 for row in A)+lam*sum(v*v for v in x)
                self.assertEqual(p.check(data, {'holdings':x})['cost'],expected)
                self.assertEqual(case['metadata']['origin'],'new_application_family')
                self.assertGreater(sum(v>0 for v in x),1)
                self.assertTrue(any(v>1 for v in x))
                self.assertTrue(all(0 <= v <= 5 for v in x))
                alternative = data['generation']['alternative_feasible_holdings']
                self.assertNotEqual(x, alternative)
                p.check(data, {'holdings': alternative})
                with self.assertRaises(ValueError):
                    p.check(data, {'holdings': data['generation']['return_nonredundancy_counterexample']})
                self.assertGreater(case['metadata']['nonlinearity']['risk_cross_terms'],0)
                bad=copy.deepcopy(data); bad['covariance'][0][0]=str(int(bad['covariance'][0][0])+1)
                with self.assertRaises(ValueError): p.check(bad, {'holdings':x})

    def test_original_four_sources_and_public_p2_point(self):
        cases=p.make('minlplib')
        self.assertEqual(len(cases),4)
        for case in cases:
            self.assertTrue(case['metadata']['replay'])
            self.assertEqual(case['metadata']['objective']['scale'],'2500000000')
            self.assertEqual(len(case['data']['variables']),30)
            self.assertIsNotNone(case['witness'])
            changed=copy.deepcopy(case['data'])
            for term in changed['risk_terms']:
                coefficient=term['coefficient']
                coefficient['num']=str(int(coefficient['num'])+1)
            with self.assertRaises(ValueError):
                p.check(changed, case['witness']['decision'])
            self.assertEqual(str(p.check(case['data'],case['witness']['decision'])['cost']),case['witness']['cost'])
            p.build(case['data']).base()
        public=next(c for c in cases if '150-165' in c['label'])
        result=p.check(public['data'],public['witness']['decision'])
        self.assertEqual(result['cost'],220262336900)
        self.assertEqual(Fraction(int(result['risk']['num']),int(result['risk']['den'])),Fraction('88.10493476'))

    def test_loose_budget_tiny_return_threshold_still_has_strict_counterexample(self):
        case=p.make('synthetic-gram', assets=2, seed=1, budget_slack='0.99', return_tightness='0.001')[0]
        data=case['data']
        counter=data['generation']['return_nonredundancy_counterexample']
        spent=sum(p.rational(price)*value for price,value in zip(data['prices'], counter))
        score=sum(p.rational(coefficient)*value for coefficient,value in zip(data['return_coefficients'], counter))
        self.assertGreater(sum(counter),0)
        self.assertLessEqual(p.rational(data['budget_min']),spent)
        self.assertLessEqual(spent,p.rational(data['budget_max']))
        self.assertLess(score,p.rational(data['return_min']))
        with self.assertRaisesRegex(ValueError, 'return_requirement'):
            p.check(data, {'holdings':counter})
        p.check(data, case['witness']['decision'])

    def test_source_pin_refuses_changed_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)/'portfolio'
            cache.mkdir()
            (cache/(p.IDS[0]+'.gms')).write_text('changed upstream source')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                p.fetch(cache=cache, ids=p.IDS[0])

    def test_generated_base_contains_actual_nonfixed_holdings(self):
        case=p.make('tiny')[0]
        base=p.build(case['data']).base()
        self.assertIn('(set-logic QF_NIA)',base)
        self.assertIn('(* 2 x1 x2)',base)
        self.assertNotIn('Real',base)
        self.assertNotIn('check-sat',base)
        self.assertNotIn('(= x1 1)',base)


if __name__=='__main__': unittest.main()
