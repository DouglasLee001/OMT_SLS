import copy
import importlib
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
SOLVERS = ('z3', 'optimathsat', 'NIA_LS')


def row(name, values, states=('B', 'B', 'B')):
    result = {'文件名': name}
    for solver, value, state in zip(SOLVERS, values, states):
        result.update({solver + '状态': state, solver + '目标值': value,
                       solver + '时间（秒）': '1.0'})
    return result


class ResultComparisonTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPT / 'result_comparison.py').is_file(),
                        'Pure result comparison module is missing')
        self.compute = importlib.import_module('result_comparison').compute_statistics

    def test_empty_input_returns_complete_zero_statistics(self):
        result = self.compute([], {})
        self.assertEqual(set(result), {'all', 'all_unsolved'})
        for group in result.values():
            self.assertEqual(set(group), {'instances', 'all_na', 'best_including_na',
                                         'best_excluding_na', 'states', 'solved', 'nia_wdl'})
            self.assertEqual(group['instances'], 0)
            self.assertEqual(group['all_na'], 0)
            for field in ('best_including_na', 'best_excluding_na', 'solved'):
                self.assertEqual(group[field], dict.fromkeys(SOLVERS, 0))
            for solver in SOLVERS:
                self.assertEqual(group['states'][solver], dict.fromkeys(('A1', 'A2', 'B', 'C'), 0))
            for target in ('z3', 'optimathsat', 'best_competitor'):
                self.assertEqual(group['nia_wdl'][target], {'win': 0, 'tie': 0, 'loss': 0})

    def test_minimization_counts_ties_and_solved_status_separately(self):
        result = self.compute([row('min', ('7', '5', '5'), ('B', 'A2', 'B'))],
                              {'min': 'min'})
        group = result['all']
        self.assertEqual(group['best_excluding_na'], {'z3': 0, 'optimathsat': 1, 'NIA_LS': 1})
        self.assertEqual(group['solved'], {'z3': 0, 'optimathsat': 1, 'NIA_LS': 0})
        self.assertEqual(group['nia_wdl']['z3'], {'win': 1, 'tie': 0, 'loss': 0})
        self.assertEqual(group['nia_wdl']['optimathsat'], {'win': 0, 'tie': 1, 'loss': 0})
        self.assertEqual(group['nia_wdl']['best_competitor'], {'win': 0, 'tie': 1, 'loss': 0})
        self.assertEqual(result['all_unsolved']['instances'], 0)

    def test_maximization_uses_larger_finite_value(self):
        result = self.compute([row('max', ('2', '7', '5'))], {'max': 'max'})
        group = result['all']
        self.assertEqual(group['best_excluding_na'], {'z3': 0, 'optimathsat': 1, 'NIA_LS': 0})
        self.assertEqual(group['nia_wdl']['z3'], {'win': 1, 'tie': 0, 'loss': 0})
        self.assertEqual(group['nia_wdl']['best_competitor'], {'win': 0, 'tie': 0, 'loss': 1})
        self.assertEqual(result['all_unsolved'], group)

    def test_values_above_float_precision_and_2000_digits_remain_exact(self):
        boundary = 2 ** 53
        rows = [row('precise', tuple(str(boundary + i) for i in range(3))),
                row('huge', ('-' + '9' * 2000, '-' + '9' * 1999, '-1'))]
        group = self.compute(rows, {'precise': 'min', 'huge': 'min'})['all']
        self.assertEqual(group['best_excluding_na'], {'z3': 2, 'optimathsat': 0, 'NIA_LS': 0})
        for target in ('z3', 'optimathsat', 'best_competitor'):
            self.assertEqual(group['nia_wdl'][target], {'win': 0, 'tie': 0, 'loss': 2})

    def test_4301_digit_values_are_exact_without_changing_global_digit_limit(self):
        limit = sys.get_int_max_str_digits()
        values = ('1' + '0' * 4300, '9' * 4300, '1' + '0' * 4299 + '1')
        rows = [row('positive', values), row('negative', tuple('-' + v for v in values))]
        try:
            group = self.compute(rows, {'positive': 'min', 'negative': 'max'})['all']
        except ValueError as error:
            self.fail(f'Valid 4301-digit integers must remain comparable: {error}')
        self.assertEqual(sys.get_int_max_str_digits(), limit)
        self.assertEqual(group['best_excluding_na'], {'z3': 0, 'optimathsat': 2, 'NIA_LS': 0})
        for target in ('z3', 'optimathsat', 'best_competitor'):
            self.assertEqual(group['nia_wdl'][target], {'win': 0, 'tie': 0, 'loss': 2})

    def test_na_and_infinity_have_defined_priorities_and_all_na_ties(self):
        rows = [row('none', ('NA', 'NA', 'NA'), ('C', 'C', 'C')),
                row('minus', ('-oo', '0', 'NA'), ('A1', 'B', 'C')),
                row('plus', ('NA', '+oo', 'oo'), ('C', 'A1', 'A1'))]
        result = self.compute(rows, {'none': 'min', 'minus': 'min', 'plus': 'max'})
        group = result['all']
        self.assertEqual(group['all_na'], 1)
        self.assertEqual(group['best_including_na'], dict.fromkeys(SOLVERS, 2))
        self.assertEqual(group['best_excluding_na'], dict.fromkeys(SOLVERS, 1))
        self.assertEqual(group['nia_wdl']['z3'], {'win': 1, 'tie': 1, 'loss': 1})
        self.assertEqual(group['nia_wdl']['optimathsat'], {'win': 0, 'tie': 2, 'loss': 1})
        self.assertEqual(result['all_unsolved']['instances'], 1)
        self.assertEqual(result['all_unsolved']['all_na'], 1)
        self.assertEqual(result['all_unsolved']['best_including_na'], dict.fromkeys(SOLVERS, 1))
        self.assertEqual(result['all_unsolved']['best_excluding_na'], dict.fromkeys(SOLVERS, 0))

    def test_equal_values_tie_regardless_of_proof_status_or_time(self):
        item = row('same', ('3', '3', '3'), ('A2', 'B', 'B'))
        item['z3时间（秒）'] = '0.1'
        item['NIA_LS时间（秒）'] = '1199'
        group = self.compute([item], {'same': 'min'})['all']
        self.assertEqual(group['best_excluding_na'], dict.fromkeys(SOLVERS, 1))
        for target in ('z3', 'optimathsat', 'best_competitor'):
            self.assertEqual(group['nia_wdl'][target], {'win': 0, 'tie': 1, 'loss': 0})

    def test_any_solver_solved_excludes_case_from_all_unsolved(self):
        rows = [row('b', ('1', '2', '3')),
                row('c', ('NA', 'NA', 'NA'), ('C', 'C', 'C'))]
        for index, solver in enumerate(SOLVERS):
            states = ['B', 'B', 'B']
            states[index] = 'A2'
            rows.append(row(solver, ('1', '2', '3'), states))
        result = self.compute(rows, {r['文件名']: 'min' for r in rows})
        self.assertEqual(result['all']['instances'], 5)
        self.assertEqual(result['all_unsolved']['instances'], 2)
        self.assertEqual(result['all_unsolved']['solved'], dict.fromkeys(SOLVERS, 0))

    def test_duplicate_names_are_rejected(self):
        item = row('duplicate', ('1', '2', '3'))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.compute([item, item], {'duplicate': 'min'})

    def test_missing_or_invalid_direction_is_rejected(self):
        for directions in ({}, {'case': None}, {'case': 'minimum'}, {'case': 'MIN'}):
            with self.subTest(directions=directions), self.assertRaises(ValueError):
                self.compute([row('case', ('1', '2', '3'))], directions)

    def test_invalid_status_is_rejected(self):
        for state in ('A3', '', None, 'sat'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.compute([row('case', ('1', '2', '3'), ('B', state, 'B'))], {'case': 'min'})

    def test_invalid_or_noninteger_value_is_rejected(self):
        for value in ('1.5', '1e3', '(/ 1 2)', 'nan', 'Infinity', '', None, True, 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compute([row('case', ('1', value, '3'))], {'case': 'min'})

    def test_infinity_opposite_to_optimization_direction_is_rejected(self):
        for direction, value in (('min', '+oo'), ('min', 'oo'), ('max', '-oo')):
            with self.subTest(direction=direction, value=value), self.assertRaises(ValueError):
                self.compute([row('case', ('1', value, '3'))], {'case': direction})

    def test_missing_or_empty_name_and_missing_value_are_rejected(self):
        for name in ('', ' ', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.compute([row(name, ('1', '2', '3'))], {})
        item = row('case', ('1', '2', '3'))
        del item['z3目标值']
        with self.assertRaises(ValueError):
            self.compute([item], {'case': 'min'})

    def test_inputs_are_not_mutated_and_iterable_rows_are_accepted(self):
        rows = [row('case', ('1', '2', '3'))]
        original = copy.deepcopy(rows)
        directions = {'case': 'min'}
        result = self.compute(iter(rows), directions)
        self.assertEqual(rows, original)
        self.assertEqual(directions, {'case': 'min'})
        self.assertEqual(result, self.compute(rows, directions))


if __name__ == '__main__':
    unittest.main()
