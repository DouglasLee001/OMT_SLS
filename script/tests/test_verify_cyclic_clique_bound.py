import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'script'))
from smtlib import parse, render
try:
    import verify_cyclic_clique_bound as checker
except ModuleNotFoundError:
    checker = None

SOURCE = ROOT / 'new_benchmark/cyclic/instance/medium_1--6cd2743f249f.smt2'


def candidate():
    return {'method': 'exact_integer_cyclic_clique_bound_v1', 'source': str(SOURCE),
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'filename': SOURCE.name, 'direction': 'min', 'reference_best_value': '585',
            'period': 'T', 'lower': 'lo', 'upper': 'hi', 'span': 'C', 'objective': 'cost',
            'period_weight': '44', 'claimed_lower_bound': '585',
            'tasks': [{'phase': 's_' + str(i), 'cycle': 'k_' + str(i), 'time': 'u_' + str(i)}
                      for i in range(2, 15)]}


class CyclicCliqueBoundTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(checker, 'source-level cyclic clique bound checker is not implemented')
        self.text = SOURCE.read_text()
        self.claim = candidate()

    def changed_source(self, old, new):
        self.assertEqual(self.text.count(old), 1, old)
        return self.text.replace(old, new, 1)

    def reject_source(self, text, claim=None):
        with self.assertRaises(checker.VerificationError):
            checker.verify_source(text, self.claim if claim is None else claim)

    def certificate_result(self, claim):
        with tempfile.TemporaryDirectory(prefix='omt-cyclic-proof-test-') as temp:
            path = Path(temp) / 'certificate.json'
            path.write_text(json.dumps(claim))
            return checker.verify_certificate(path)

    def test_canonical_source_exact_585_bound(self):
        result = self.certificate_result(self.claim)
        self.assertTrue(result['verified'], result)
        self.assertEqual((result['tasks'], result['distinct_phase_pairs']), (13, 78))
        self.assertEqual(result['lower_bound'], '585')
        self.assertFalse(result['solver_calls'])
        self.assertFalse(result['lp_calls'])

    def test_missing_pair_is_rejected(self):
        self.reject_source(self.changed_source('(assert (or (<= (+ s_2 1) s_3) (<= (+ s_3 1) s_2)))\n', ''))

    def test_weakened_pair_is_rejected(self):
        self.reject_source(self.changed_source('(or (<= (+ s_2 1) s_3) (<= (+ s_3 1) s_2))', '(or (<= s_2 s_3) (<= s_3 s_2))'))

    def test_non_strict_period_bound_is_rejected(self):
        self.reject_source(self.changed_source('(assert (<= (+ s_2 1) T))', '(assert (<= s_2 T))'))

    def test_missing_phase_lower_bound_is_rejected(self):
        self.reject_source(self.changed_source('(assert (>= s_2 0))\n', ''))

    def test_real_phase_or_cycle_or_time_is_rejected(self):
        for variable in ['s_2', 'k_2', 'u_2', 'T', 'lo', 'hi', 'C', 'cost']:
            with self.subTest(variable=variable):
                self.reject_source(self.changed_source('(declare-fun ' + variable + ' () Int)', '(declare-fun ' + variable + ' () Real)'))

    def test_wrong_lift_equation_is_rejected(self):
        self.reject_source(self.changed_source('(assert (= u_2 (- s_2 (* k_2 T))))', '(assert (= u_2 (+ s_2 (* k_2 T))))'))

    def test_missing_or_reversed_envelope_is_rejected(self):
        for old, new in [('(assert (>= hi u_2))\n', ''), ('(assert (<= lo u_2))', '(assert (<= u_2 lo))')]:
            with self.subTest(old=old): self.reject_source(self.changed_source(old, new))

    def test_wrong_span_is_rejected(self):
        self.reject_source(self.changed_source('(assert (= C (+ (- hi lo) 1)))', '(assert (= C (- hi lo)))'))

    def test_wrong_objective_weight_is_rejected(self):
        self.reject_source(self.changed_source('(assert (= cost (+ (* 44 T) C)))', '(assert (= cost (+ (* 43 T) C)))'))

    def test_negative_weight_is_rejected_even_when_claim_matches(self):
        claim = copy.deepcopy(self.claim); claim['period_weight'] = '-44'; claim['claimed_lower_bound'] = '-559'
        text = self.changed_source('(assert (= cost (+ (* 44 T) C)))', '(assert (= cost (+ (* (- 44) T) C)))')
        self.reject_source(text, claim)

    def test_zero_weight_has_valid_span_lower_bound(self):
        claim = copy.deepcopy(self.claim); claim['period_weight'] = '0'; claim['claimed_lower_bound'] = '13'
        text = self.changed_source('(assert (= cost (+ (* 44 T) C)))', '(assert (= cost (+ (* 0 T) C)))')
        self.assertEqual(checker.verify_source(text, claim)['lower_bound'], '13')

    def test_multiple_or_wrong_objective_is_rejected(self):
        for command in ['(minimize T)', '(maximize cost)']:
            with self.subTest(command=command):self.reject_source(self.text.replace('(minimize cost)', '(minimize cost)\n' + command))
        self.reject_source(self.changed_source('(minimize cost)', '(minimize T)'))

    def test_multiple_check_is_rejected(self):
        self.reject_source(self.text + '\n(check-sat)\n')

    def test_context_mutation_commands_are_rejected(self):
        for command in ['(push 1)', '(pop 1)', '(reset)', '(reset-assertions)']:
            with self.subTest(command=command):self.reject_source(self.text.replace('(minimize cost)', command + '\n(minimize cost)'))

    def test_proof_assertion_after_check_is_rejected(self):
        assertion = '(assert (<= (+ s_2 1) T))'
        self.reject_source(self.changed_source(assertion + '\n', '') + '\n' + assertion + '\n')

    def test_duplicate_declaration_is_rejected(self):
        self.reject_source(self.text.replace('(declare-fun T () Int)', '(declare-fun T () Int)\n(declare-fun T () Int)'))

    def test_duplicate_or_missing_tasks_are_rejected(self):
        for change in ['duplicate', 'missing', 'empty']:
            claim = copy.deepcopy(self.claim)
            if change == 'duplicate':claim['tasks'][1] = claim['tasks'][0]
            elif change == 'missing':claim['tasks'].pop()
            else:claim['tasks'] = []
            with self.subTest(change=change):self.reject_source(self.text, claim)

    def test_wrong_claimed_bound_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['claimed_lower_bound'] = '586'
        self.reject_source(self.text, claim)

    def test_certificate_hash_and_reference_are_not_trusted(self):
        for key, value in [('source_sha256', '0' * 64), ('reference_best_value', '584'), ('direction', 'max'), ('filename', 'other.smt2')]:
            claim = copy.deepcopy(self.claim); claim[key] = value
            with self.subTest(key=key):self.assertFalse(self.certificate_result(claim)['verified'])

    def test_identical_noncanonical_source_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='omt-noncanonical-source-') as temp:
            source = Path(temp) / SOURCE.name; source.write_text(self.text)
            claim = copy.deepcopy(self.claim); claim['source'] = str(source)
            self.assertFalse(self.certificate_result(claim)['verified'])

    def test_attainment_or_is_not_needed_for_valid_envelope_bound(self):
        ast = parse(self.text)
        ast = [command for command in ast if not (command[0] == 'assert' and isinstance(command[1], list)
               and command[1] and command[1][0] == 'or' and all(isinstance(v, list) and v[:2] in [['=', 'hi'], ['=', 'lo']] for v in command[1][1:]))]
        self.assertEqual(checker.verify_source('\n'.join(map(render, ast)), self.claim)['lower_bound'], '585')


if __name__ == '__main__':
    unittest.main()
