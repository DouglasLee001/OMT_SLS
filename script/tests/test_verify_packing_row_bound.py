import copy
import hashlib
import itertools
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'script'))
try:
    import verify_packing_row_bound as checker
except ModuleNotFoundError:
    checker = None

SOURCE = ROOT / 'new_benchmark/packing/instance/rectangles-n12-max10-seed1072--dfffe7dd2e9e.smt2'
# Exact vectors independently checked against all 4096 subsets.  No LP or
# generated packing model is loaded by these self-contained tests.
PATTERNS = [
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 1, 1), 1, 54),
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1), 1, 49),
    ('horizontal', (1, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1, 1), 1, 44),
    ('horizontal', (500, 167, 333, 167, 500, 167, 667, 500, 500, 333, 500, 500), 1001, 30173),
    ('horizontal', (500, 0, 250, 0, 500, 250, 750, 500, 500, 250, 500, 500), 1000, 28750),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 18, 469),
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 1, 1), 2, 54),
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1), 2, 49),
    ('horizontal', (1, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1, 1), 2, 44),
    ('horizontal', (333, 111, 222, 111, 333, 111, 444, 333, 333, 222, 333, 333), 999, 20091),
    ('horizontal', (333, 0, 167, 0, 333, 167, 500, 333, 333, 167, 333, 333), 1001, 19161),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 26, 469),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 27, 469),
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 1, 1), 3, 54),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 29, 469),
    ('horizontal', (1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1), 3, 49),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 31, 469),
    ('horizontal', (250, 83, 167, 83, 250, 83, 333, 250, 250, 167, 250, 250), 1000, 15077),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 33, 469),
    ('transposed', (500, 750, 375, 125, 250, 500, 750, 750, 250, 375, 250, 500), 1000, 34625),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 35, 469),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 36, 469),
    ('transposed', (500, 1000, 500, 0, 0, 500, 1000, 1000, 0, 500, 500, 500), 1000, 38500),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 38, 469),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 39, 469),
    ('horizontal', (200, 67, 133, 67, 200, 67, 267, 200, 200, 133, 200, 200), 1001, 12073),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 41, 469),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 42, 469),
    ('horizontal', (176, 59, 176, 118, 176, 59, 235, 176, 176, 118, 176, 176), 999, 11043),
    ('transposed', (750, 1000, 500, 250, 250, 750, 1000, 1000, 250, 500, 250, 750), 1000, 46000),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 45, 469),
    ('horizontal', (8, 1, 7, 4, 8, 2, 10, 8, 8, 6, 8, 8), 46, 469),
    ('horizontal', (143, 143, 143, 0, 143, 0, 286, 143, 143, 143, 143, 143), 1001, 10582),
    ('horizontal', (143, 0, 143, 0, 143, 143, 286, 143, 143, 143, 143, 143), 1001, 10153),
]
CASE_PATTERNS = {10: 0, 11: 0, 12: 0, 13: 1, 14: 1, 15: 2, 16: 3, 17: 4, 18: 5, 19: 6, 20: 6, 21: 7, 22: 7, 23: 8, 24: 9, 25: 10, 26: 11, 27: 12, 28: 13, 29: 14, 30: 15, 31: 16, 32: 17, 33: 18, 34: 19, 35: 20, 36: 21, 37: 22, 38: 23, 39: 24, 40: 25, 41: 26, 42: 27, 43: 28, 44: 29, 45: 30, 46: 31, 47: 32, 48: 33}
DIMENSIONS = [[8, 7], [1, 10], [7, 5], [4, 2], [8, 3], [2, 7], [10, 10], [8, 10], [8, 3], [6, 5], [8, 4], [8, 7]]


def candidate():
    cases = []
    for W in range(10, 79):
        H = min(78, 485 // W)
        row = {'width': W, 'height_allowed': H}
        if H < 10:
            row['axis'] = 'height_bound'
        else:
            axis, weights, denominator, numerator = PATTERNS[CASE_PATTERNS[W]]
            row.update(axis=axis, weights=list(weights), D=denominator, N=numerator)
        cases.append(row)
    return {'method': 'exact_integer_packing_row_bound_v1', 'source': str(SOURCE),
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'filename': SOURCE.name, 'direction': 'min', 'reference_best_value': '486',
            'width': 'W', 'height': 'H', 'objective': 'cost', 'product': 'area',
            'width_bounds': [10, 78], 'height_bounds': [10, 78],
            'items': [{'x': 'x_' + str(i), 'y': 'y_' + str(i), 'width': w, 'height': h}
                      for i, (w, h) in enumerate(DIMENSIONS)], 'width_cases': cases}


class PackingRowBoundTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(checker, 'source-level packing row bound checker is not implemented')
        self.text = SOURCE.read_text()
        self.claim = candidate()

    def changed_source(self, old, new):
        self.assertEqual(self.text.count(old), 1, old)
        return self.text.replace(old, new, 1)

    def reject(self, claim=None, text=None):
        with self.assertRaises(checker.VerificationError):
            checker.verify_source(self.text if text is None else text, self.claim if claim is None else claim)

    def result(self, claim):
        with tempfile.TemporaryDirectory(prefix='omt-packing-proof-') as temp:
            path = Path(temp) / 'certificate.json'; path.write_text(json.dumps(claim))
            return checker.verify_certificate(path)

    def test_canonical_complete_335_proof(self):
        result = self.result(self.claim)
        self.assertTrue(result['verified'], result)
        self.assertEqual((result['items'], result['geometry_pairs'], result['widths_verified']), (12, 66, 69))
        self.assertEqual((result['horizontal_cuts'], result['transposed_cuts'], result['height_bound_exclusions']), (36, 3, 30))
        self.assertFalse(result['solver_calls']); self.assertFalse(result['lp_calls'])

    def test_dynamic_program_matches_independent_subset_enumeration(self):
        rng = random.Random(73531)
        for _ in range(200):
            sizes = [rng.randrange(1, 9) for _ in range(rng.randrange(1, 8))]
            weights = [rng.randrange(0, 11) for _ in sizes]
            capacity = rng.randrange(0, 25)
            expected = max(sum(weights[i] for i in range(len(sizes)) if mask >> i & 1)
                           for mask in range(1 << len(sizes))
                           if sum(sizes[i] for i in range(len(sizes)) if mask >> i & 1) <= capacity)
            self.assertEqual(checker.knapsack_max(sizes, weights, capacity), expected)

    def test_missing_or_weakened_nonoverlap_is_rejected(self):
        old = '(assert (or (<= (+ x_0 8) x_1) (<= (+ x_1 1) x_0) (<= (+ y_0 7) y_1) (<= (+ y_1 10) y_0)))'
        for new in ['', '(assert (or (<= x_0 x_1) (<= x_1 x_0) (<= y_0 y_1) (<= y_1 y_0)))']:
            with self.subTest(new=new): self.reject(text=self.changed_source(old, new))

    def test_missing_containment_or_nonnegative_coordinate_is_rejected(self):
        for assertion in ['(assert (<= (+ x_0 8) W))', '(assert (<= (+ y_0 7) H))', '(assert (>= x_0 0))', '(assert (>= y_0 0))']:
            with self.subTest(assertion=assertion): self.reject(text=self.changed_source(assertion, ''))

    def test_real_coordinate_or_container_is_rejected(self):
        for name in ['x_0', 'y_0', 'W', 'H', 'cost', 'area']:
            with self.subTest(name=name): self.reject(text=self.changed_source('(declare-fun ' + name + ' () Int)', '(declare-fun ' + name + ' () Real)'))

    def test_nonpositive_or_noninteger_item_dimensions_are_rejected(self):
        for value in [0, -1, True, 1.5]:
            claim = copy.deepcopy(self.claim); claim['items'][0]['width'] = value
            with self.subTest(value=value): self.reject(claim)

    def test_nonpositive_or_missing_container_bound_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['height_bounds'][0] = 0
        self.reject(claim, self.changed_source('(assert (>= H 10))', '(assert (>= H 0))'))
        self.reject(text=self.changed_source('(assert (<= H 78))', ''))

    def test_wrong_product_or_objective_alias_is_rejected(self):
        self.reject(text=self.changed_source('(assert (= area (* W H)))', '(assert (= area (+ W H)))'))
        self.reject(text=self.changed_source('(assert (= cost area))', '(assert (= cost W))'))
        self.reject(text=self.changed_source('(minimize cost)', '(minimize area)'))

    def test_direct_product_objective_needs_no_auxiliary_alias(self):
        claim = copy.deepcopy(self.claim); claim['product'] = 'cost'
        text = self.changed_source('(assert (= area (* W H)))', '(assert (= cost (* W H)))')
        text = text.replace('(assert (= cost area))', '', 1)
        result = checker.verify_source(text, claim)
        self.assertEqual(result['widths_verified'], 69)

    def test_missing_or_duplicate_width_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'].pop(0); self.reject(claim)
        claim = copy.deepcopy(self.claim); claim['width_cases'][-1] = copy.deepcopy(claim['width_cases'][0]); self.reject(claim)

    def test_forged_coverage_flag_does_not_replace_actual_cover(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'].pop(24); claim['all_source_widths_covered'] = True
        self.reject(claim)

    def test_outside_source_width_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][-1]['width'] = 79; self.reject(claim)

    def test_wrong_height_cap_including_non_strict_reference_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][0]['height_allowed'] += 1; self.reject(claim)
        claim = copy.deepcopy(self.claim); row = next(r for r in claim['width_cases'] if r['width'] == 27)
        row['height_allowed'] = 486 // 27; self.reject(claim)

    def test_height_bound_may_not_discard_a_nonempty_height_domain(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][0] = {'width': 10, 'height_allowed': 48, 'axis': 'height_bound'}
        self.reject(claim)

    def test_negative_or_noninteger_weights_are_rejected(self):
        for value in [-1, True, 0.5]:
            claim = copy.deepcopy(self.claim); claim['width_cases'][0]['weights'][0] = value
            with self.subTest(value=value): self.reject(claim)

    def test_weight_vector_length_is_checked(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][0]['weights'].pop(); self.reject(claim)

    def test_forged_denominator_and_numerator_are_rejected(self):
        for key, value in [('D', 0), ('N', 1000000)]:
            claim = copy.deepcopy(self.claim); claim['width_cases'][0][key] = value
            with self.subTest(key=key): self.reject(claim)

    def test_all_zero_weights_never_exclude_a_box(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][0].update(weights=[0] * 12, D=0, N=0)
        self.reject(claim)

    def test_unknown_axis_is_rejected(self):
        claim = copy.deepcopy(self.claim); claim['width_cases'][0]['axis'] = 'arbitrary'; self.reject(claim)

    def test_duplicate_item_roles_are_rejected(self):
        claim = copy.deepcopy(self.claim); claim['items'][1]['x'] = claim['items'][0]['x']; self.reject(claim)

    def test_scope_and_multiple_objectives_or_checks_are_rejected(self):
        for command in ['(push 1)', '(pop 1)', '(reset)', '(reset-assertions)', '(minimize H)', '(maximize cost)', '(check-sat)']:
            with self.subTest(command=command): self.reject(text=self.text.replace('(minimize cost)', command + '\n(minimize cost)'))
        self.reject(text=self.text + '\n(check-sat)\n')

    def test_assertion_after_check_and_duplicate_declaration_are_rejected(self):
        assertion = '(assert (>= x_0 0))'
        self.reject(text=self.changed_source(assertion + '\n', '') + '\n' + assertion)
        self.reject(text=self.text.replace('(declare-fun W () Int)', '(declare-fun W () Int)\n(declare-fun W () Int)'))

    def test_canonical_hash_reference_objective_and_filename_are_checked(self):
        for key, value in [('source_sha256', '0' * 64), ('reference_best_value', '485'), ('direction', 'max'), ('filename', 'other.smt2')]:
            claim = copy.deepcopy(self.claim); claim[key] = value
            with self.subTest(key=key): self.assertFalse(self.result(claim)['verified'])

    def test_identical_noncanonical_source_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='omt-noncanonical-packing-') as temp:
            source = Path(temp) / SOURCE.name; source.write_text(self.text)
            claim = copy.deepcopy(self.claim); claim['source'] = str(source)
            self.assertFalse(self.result(claim)['verified'])


if __name__ == '__main__': unittest.main()
