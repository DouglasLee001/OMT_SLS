import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from fractions import Fraction
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'script'))
import verify_reference_induction as checker

class IntegerTraceProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        certificate=ROOT/'result/optimize/reference_diagnostics/induction_certificates/0110.json'
        if not certificate.is_file() or not (ROOT/'result/optimize/full_v8_20261022/manifest.json').is_file():
            raise unittest.SkipTest('optional retained induction proof artifacts are unavailable')
        cls.certificate=json.loads(certificate.read_text())
        cls.references,cls.artifacts=checker.context(ROOT/'result/optimize/full_v8_20261022')
    def changed(self):return copy.deepcopy(self.certificate)
    def result(self,c):
        with tempfile.TemporaryDirectory(prefix='omt-induction-test-') as temp:
            p=Path(temp)/'cert.json';p.write_text(json.dumps(c))
            return checker.verify_certificate(p,self.references,self.artifacts)
    def reject(self,c,fragment):
        r=self.result(c);self.assertFalse(r['verified'],r);self.assertIn(fragment,r['error'])
    def test_real110_exact_induction(self):
        r=self.result(self.certificate);self.assertTrue(r['verified'],r)
        self.assertEqual(r['functions'],3);self.assertEqual(r['identities'],8)
    def test_wrong_step_count(self):
        c=self.changed();c['step_count_offset']=-3;self.reject(c,'step_count_offset')
    def test_wrong_start(self):
        c=self.changed();c['start']=0;self.reject(c,'start')
    def test_wrong_step(self):
        c=self.changed();c['step']=2;self.reject(c,'step')
    def test_wrong_terminal(self):
        c=self.changed();c['terminal_offset']=-2;self.reject(c,'terminal_offset')
    def test_native_t_collision(self):
        c=self.changed();c['iteration_variable']=44;self.reject(c,'fresh')
    def test_t_rename_does_not_rename_function(self):
        c=self.changed();c['iteration_variable']=1001;self.reject(c,'known integer')
    def test_length_must_be_native_integer(self):
        c=self.changed();c['length_variable']=25;self.reject(c,'native integer')
    def test_missing_initial(self):
        c=self.changed();c['initial'].pop();self.reject(c,'initial')
    def test_negative_matrix_entry(self):
        c=self.changed();c['transition_matrix'][0][0]['weight']='-1';self.reject(c,'negative inequality weight')
    def test_negative_exit_multiplier(self):
        c=self.changed();c['exit_multipliers'][0]['weight']='-1';self.reject(c,'negative inequality weight')
    def test_loop_guard_only_in_transition(self):
        c=self.changed();c['length_nonnegative']['terms'][0]['why']={'type':'loop_interval_slack'}
        self.reject(c,'only in a transition')
    def test_loop_guard_cannot_certify_multiplier(self):
        c=self.changed();c['transition_matrix'][0][0]['why']={'type':'loop_interval_slack'}
        self.reject(c,'only in a transition')
    def test_unproved_counterfact(self):
        c=self.changed();c['transitions'][0]['terms'][0]['why']={'type':'forced_literal','literal':49}
        self.reject(c,'unproved literal')
    def test_forged_initial_column(self):
        c=self.changed();c['initial'][0]['terms'][0]['polynomial'][0][1]='99'
        self.reject(c,'attached polynomial differs')
    def test_incomplete_transition_matrix(self):
        c=self.changed();c['transition_matrix'][0].pop();self.reject(c,'square')
    def test_wrong_transition_identity(self):
        c=self.changed();c['transitions'][0]['terms'][0]['weight']='2'
        self.reject(c,'polynomial identity')
    def test_wrong_exit_identity(self):
        c=self.changed();c['exit']['terms'][0]['weight']='2';self.reject(c,'polynomial identity')
    def test_forged_reference(self):
        c=self.changed();c['reference_best_value']='1';self.reject(c,'nonzero historical reference')
    def test_bad_sha(self):
        c=self.changed();c['input_sha256']='0'*64;self.reject(c,'SHA256')
    def test_polynomial_substitution_matches_integer_evaluation(self):
        t,x,length=1000,1,44
        function={((x,1),(t,5)):Fraction(3),((t,2),):Fraction(-2),((x,2),):Fraction(7),():Fraction(-5)}
        replacements=[{():Fraction(1)},{((t,1),):Fraction(1),():Fraction(1)},
                      {((length,1),):Fraction(1),():Fraction(-1)},
                      {((t,1),):Fraction(2),((x,1),):Fraction(1)}]
        def evaluate(poly,values):
            result=Fraction(0)
            for mon,c in poly.items():
                term=c
                for var,power in mon:term*=values[var]**power
                result+=term
            return result
        original=copy.deepcopy(function)
        for replacement in replacements:
            expanded=checker.substitute(function,t,replacement)
            for xv in range(-2,3):
                for lv in range(2,6):
                    for tv in range(1,5):
                        values={x:xv,length:lv,t:tv};expected=dict(values);expected[t]=evaluate(replacement,values)
                        self.assertEqual(evaluate(expanded,values),evaluate(function,expected))
        self.assertEqual(function,original)


class InductionExtensionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures=ROOT/'result/optimize/reference_diagnostics/induction_certificates'
        if not all((fixtures/name).is_file() for name in ('0110.json','0117.json')) or not (ROOT/'result/optimize/full_v8_20261022/manifest.json').is_file():
            raise unittest.SkipTest('optional retained induction proof artifacts are unavailable')
        cls.certificate=json.loads((fixtures/'0117.json').read_text())
        cls.old=json.loads((fixtures/'0110.json').read_text())
        cls.references,cls.artifacts=checker.context(ROOT/'result/optimize/full_v8_20261022')
    def changed(self):return copy.deepcopy(self.certificate)
    def result(self,c):
        with tempfile.TemporaryDirectory(prefix='omt-induction-extension-') as temp:
            p=Path(temp)/'cert.json';p.write_text(json.dumps(c))
            return checker.verify_certificate(p,self.references,self.artifacts)
    def reject(self,c,fragment):
        r=self.result(c);self.assertFalse(r['verified'],r);self.assertIn(fragment,r['error'])
    def lower(self):
        return {'kind':'inequality','why':{'type':'loop_start_slack'},'weight':'0','normalization_divisor':1,
                'polynomial':[[[],'-1'],[[[1000,1]],'1']]}
    def test_real117_complete_two_exit_inductions(self):
        r=self.result(self.certificate);self.assertTrue(r['verified'],r)
        self.assertEqual(r['leaves'],2);self.assertEqual(r['identities'],16);self.assertEqual(r['proof_terms'],202)
    def test_missing_binary_branch_rejected(self):
        c=self.changed();del c['tree']['righttrue'];self.reject(c,'incomplete binary')
    def test_arithmetic_split_cannot_keep_boolean_leaf_paths(self):
        c=self.changed();c['tree']['split_literal']=178;self.reject(c,'exhaustive split path')
    def test_wrong_branch_path_rejected(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['branch_assumptions'][0][1]=True
        self.reject(c,'exhaustive split path')
    def test_repeated_split_rejected(self):
        c=self.changed();c['tree']['righttrue']=copy.deepcopy(c['tree']);self.reject(c,'repeated split')
    def test_leaf_hash_rejected(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['input_sha256']='0'*64;self.reject(c,'leaf input SHA256')
    def test_lower_slack_rejected_in_initial_even_with_zero_weight(self):
        c=copy.deepcopy(self.old);c['initial'][0]['terms'].append(self.lower());self.reject(c,'only in a transition')
    def test_lower_slack_rejected_in_matrix_even_with_zero_weight(self):
        c=copy.deepcopy(self.old);c['transition_matrix'][0][0]=self.lower();self.reject(c,'only in a transition')
    def test_lower_slack_rejected_in_exit_even_with_zero_weight(self):
        c=copy.deepcopy(self.old);c['exit']['terms'].append(self.lower());self.reject(c,'only in a transition')
    def test_lower_slack_rejected_in_length_proof_even_with_zero_weight(self):
        c=copy.deepcopy(self.old);c['length_nonnegative']['terms'].append(self.lower());self.reject(c,'only in a transition')
    def test_lower_value_reconstructed_not_trusted(self):
        c=self.changed();terms=c['tree']['leftfalse']['certificate']['transitions'][0]['terms']
        term=next(x for x in terms if x['why'].get('right',{}).get('type')=='loop_start_slack')
        term['polynomial'][0][1]='-99';self.reject(c,'attached polynomial differs')


class MixedLeafTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures=ROOT/'result/optimize/reference_diagnostics/induction_certificates'
        if not all((fixtures/name).is_file() for name in ('0110.json','0179.json')) or not (ROOT/'result/optimize/full_v8_20261022/manifest.json').is_file():
            raise unittest.SkipTest('optional retained induction proof artifacts are unavailable')
        cls.certificate=json.loads((fixtures/'0179.json').read_text())
        cls.other_input=json.loads((fixtures/'0110.json').read_text())['input']
        cls.references,cls.artifacts=checker.context(ROOT/'result/optimize/full_v8_20261022')
    def changed(self):return copy.deepcopy(self.certificate)
    def result(self,c):
        with tempfile.TemporaryDirectory(prefix='omt-mixed-proof-') as temp:
            p=Path(temp)/'cert.json';p.write_text(json.dumps(c))
            return checker.verify_certificate(p,self.references,self.artifacts)
    def reject(self,c,fragment):
        r=self.result(c);self.assertFalse(r['verified'],r);self.assertIn(fragment,r['error'])
    def test_real179_all_three_leaves(self):
        r=self.result(self.certificate);self.assertTrue(r['verified'],r)
        self.assertEqual(r['leaves'],3);self.assertEqual(r['proof_terms'],118)
    def test_missing_induction_branch(self):
        c=self.changed();del c['tree']['righttrue']['leftfalse'];self.reject(c,'incomplete binary')
    def test_rlt_leaf_wrong_provenance(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][1]['why']['literal']=129
        self.reject(c,'unproved literal')
    def test_rlt_leaf_wrong_path(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['branch_assumptions'][0][1]=True
        self.reject(c,'exhaustive split path')
    def test_rlt_leaf_weight_mutation(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['weight']='2'
        self.reject(c,'not exactly -1')
    def test_rlt_leaf_polynomial_mutation(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['polynomial'][0][1]='-99'
        self.reject(c,'attached polynomial differs')
    def test_rlt_negative_inequality_weight(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['certificate_terms'][0]['weight']='-1'
        self.reject(c,'negative inequality weight')
    def test_rlt_leaf_cannot_replace_other_branch(self):
        c=self.changed();c['tree']['righttrue']['leftfalse']=copy.deepcopy(c['tree']['leftfalse'])
        self.reject(c,'exhaustive split path')
    def test_rlt_leaf_sha_mutation(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['input_sha256']='0'*64
        self.reject(c,'leaf input SHA256')
    def test_rlt_leaf_different_input(self):
        c=self.changed();c['tree']['leftfalse']['certificate']['input']=self.other_input
        self.reject(c,'leaf input path')

class AffineLengthEncodingTests(unittest.TestCase):
    """Exercise the induction rule directly, including a real zero-step case."""
    def fixture(self, length):
        native={'variables':{0:('I','objective'),1:('I','z'),2:('I','y'),3:('B','b')},
                'direction':'min','objective':(0,[(1,[(0,1)])]),
                'literals':{1:('L',(-1,[])),2:('L',(2,[(-1,[(1,1)])])),
                            3:('L',(1,[(1,[(1,1)])])),4:('L',(-2,[(1,[(2,1)])])),
                            5:('L',(-3,[(1,[(2,1)])]))},
                'clauses':[[2],[3],[4],[5]]}
        assigned={i:True for i in range(1,6)}
        bound=checker.add({((0,1),):Fraction(-1)},{():Fraction(-1)})
        def nn(literal):return {'type':'forced_literal','literal':literal}
        def column(why,weight=1):
            kind,p=checker.reconstruct(why,native,assigned,bound,native['variables'])
            return {'kind':kind,'why':why,'normalization_divisor':1,'weight':str(weight),
                    'polynomial':[[[[v,e] for v,e in m],str(c)] for m,c in sorted(p.items())]}
        initial={'terms':[column(nn(3)),column(nn(1))]}
        if length==2:
            length_poly=[[[],'2']];length_proof={'terms':[column(nn(1),0)]}
            exit_proof={'terms':[column(nn(2))]}
        elif length==3:
            length_poly=[[[],'3']];length_proof={'terms':[column(nn(1))]}
            exit_proof={'terms':[column(nn(2),2),column(nn(1),2)]}
        else:
            length_poly=[[[],'4'],[[[2,1]],'-1']]
            length_proof={'terms':[column(nn(4))]}
            product={'type':'product_of_forced_nonnegative_polynomials','left':nn(2),'right':nn(5)}
            exit_proof={'terms':[column(product),column(nn(4),2)]}
        certificate={'iteration_variable':1000,'length_polynomial':length_poly,
                     'start':1,'step':1,'step_count_offset':-2,'terminal_offset':-1,
                     'branch_assumptions':[],
                     'unitprop_trail':[[1,True,{'type':'constant_literal'}]]+
                         [[lit,True,{'type':'unit_clause','clause':lit-2}] for lit in range(2,6)],
                     'functions':[[[[[1,1],[1000,1]],'1']]],
                     'length_nonnegative':length_proof,'initial':[initial],
                     'transitions':[copy.deepcopy(initial)],
                     'transition_matrix':[[column(nn(1))]],'exit_multipliers':[column(nn(1))],
                     'exit':exit_proof}
        return certificate,native
    def reject(self,mutate):
        certificate,native=self.fixture('affine');mutate(certificate)
        with self.assertRaises(checker.VerificationError):checker.check_induction(certificate,native)
    def test_constant_length_two_zero_steps(self):
        certificate,native=self.fixture(2);result=checker.check_induction(certificate,native)
        self.assertTrue(result['zero_step_case_included']);self.assertEqual(result['functions'],1)
    def test_constant_length_three_one_step(self):
        certificate,native=self.fixture(3);self.assertEqual(checker.check_induction(certificate,native)['identities'],4)
    def test_native_affine_length_and_symbolic_endpoint(self):
        certificate,native=self.fixture('affine');self.assertEqual(checker.check_induction(certificate,native)['functions'],1)
    def test_fractional_length_coefficient_rejected(self):
        self.reject(lambda c:c['length_polynomial'][0].__setitem__(1,'1/2'))
    def test_nonlinear_length_rejected(self):
        self.reject(lambda c:c.__setitem__('length_polynomial',[[[[2,2]],'1']]))
    def test_mixed_native_monomial_length_rejected(self):
        self.reject(lambda c:c.__setitem__('length_polynomial',[[[[1,1],[2,1]],'1']]))
    def test_fresh_iteration_in_length_rejected(self):
        self.reject(lambda c:c.__setitem__('length_polynomial',[[[[1000,1]],'1']]))
    def test_boolean_length_coordinate_rejected(self):
        self.reject(lambda c:c.__setitem__('length_polynomial',[[[[3,1]],'1']]))
    def test_ambiguous_length_encodings_rejected(self):
        self.reject(lambda c:c.__setitem__('length_variable',2))
    def test_missing_length_proof_rejected(self):
        self.reject(lambda c:c.pop('length_nonnegative'))
    def test_forged_length_identity_rejected(self):
        self.reject(lambda c:c['length_nonnegative']['terms'][0].__setitem__('weight','2'))
    def test_wrong_affine_terminal_rejected(self):
        self.reject(lambda c:c['length_polynomial'][0].__setitem__(1,'5'))
    def test_wrong_step_count_rejected(self):
        self.reject(lambda c:c.__setitem__('step_count_offset',-1))
    def test_wrong_terminal_offset_rejected(self):
        self.reject(lambda c:c.__setitem__('terminal_offset',0))
    def test_length_cannot_use_transition_slack(self):
        self.reject(lambda c:c['length_nonnegative']['terms'][0].__setitem__('why',{'type':'loop_interval_slack'}))
    def test_initial_cannot_use_transition_slack(self):
        self.reject(lambda c:c['initial'][0]['terms'][0].__setitem__('why',{'type':'loop_interval_slack'}))
    def test_exit_cannot_use_transition_slack(self):
        self.reject(lambda c:c['exit']['terms'][0].__setitem__('why',{'type':'loop_start_slack'}))
    def test_matrix_cannot_use_transition_slack(self):
        self.reject(lambda c:c['transition_matrix'][0][0].__setitem__('why',{'type':'loop_interval_slack'}))
    def test_matrix_cannot_depend_on_iteration(self):
        self.reject(lambda c:c['transition_matrix'][0][0].__setitem__('polynomial',[[[[1000,1]],'1']]))
    def test_exit_multiplier_cannot_depend_on_iteration(self):
        self.reject(lambda c:c['exit_multipliers'][0].__setitem__('polynomial',[[[[1000,1]],'1']]))


class AffineCase61Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=ROOT/'result/optimize/reference_diagnostics/induction_certificates/0061.json'
        if not fixture.is_file() or not (ROOT/'result/optimize/full_v8_20261022/manifest.json').is_file():
            raise unittest.SkipTest('optional retained case61 affine proof artifacts are unavailable')
        cls.certificate=json.loads(fixture.read_text())
        cls.references,cls.artifacts=checker.context(ROOT/'result/optimize/full_v8_20261022')
    def changed(self):return copy.deepcopy(self.certificate)
    @staticmethod
    def induction(c,truth=False):
        return c['tree']['righttrue']['righttrue']['righttrue' if truth else 'leftfalse']['certificate']
    def result(self,c):
        with tempfile.TemporaryDirectory(prefix='omt-affine-proof-') as temporary:
            path=Path(temporary)/'cert.json';path.write_text(json.dumps(c))
            return checker.verify_certificate(path,self.references,self.artifacts)
    def reject(self,c):self.assertFalse(self.result(c)['verified'])
    def test_real61_all_five_leaves(self):
        result=self.result(self.certificate);self.assertTrue(result['verified'],result)
        self.assertEqual((result['leaves'],result['identities'],result['proof_terms']),(5,19,70))
    def test_missing_native_inequality_branch_rejected(self):
        c=self.changed();del c['tree']['leftfalse'];self.reject(c)
    def test_native_inequality_leaf_path_bound(self):
        c=self.changed();c['tree']['leftfalse']['leftfalse']['certificate']['branch_assumptions'][0][1]=True;self.reject(c)
    def test_cancellation_requires_own_nonzero_branch(self):
        c=self.changed();self.induction(c,True)['derived_equalities']=copy.deepcopy(self.induction(c)['derived_equalities']);self.reject(c)
    def test_cancellation_wrong_factor_rejected(self):
        c=self.changed();self.induction(c)['derived_equalities'][0]['why']['factor_variable']=24;self.reject(c)
    def test_cancellation_attached_value_rejected(self):
        c=self.changed();self.induction(c)['derived_equalities'][0]['polynomial'][0][1]='2';self.reject(c)
    def test_unproved_derived_equality_rejected(self):
        c=self.changed();self.induction(c)['derived_equalities']=[];self.reject(c)
    def test_nested_equality_multiplier_rejected_if_wrong(self):
        c=self.changed();self.induction(c)['exit']['terms'][3]['why']['variable']=101;self.reject(c)
    def test_affine_length_ambiguity_rejected(self):
        c=self.changed();self.induction(c)['length_variable']=101;self.reject(c)
    def test_affine_length_missing_unit_source_rejected(self):
        c=self.changed();self.induction(c)['length_nonnegative']['terms'][0]['why']['literal']=174;self.reject(c)


if __name__=='__main__':unittest.main()
