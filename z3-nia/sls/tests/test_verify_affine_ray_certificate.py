"""Generated source-affine certificates; AST parsing only, never solver.check."""
import builtins
import copy
import ctypes
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

TREE = Path(__file__).resolve().parents[2]
CHECKER = TREE / 'experiment/verify_affine_ray_certificate.py'
SDK = Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_051111_801046/source/python')


class AffineRayCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctypes.CDLL(str(SDK / 'z3/lib/libz3.so'))
        builtins.Z3_LIB_DIRS = [str(SDK / 'z3/lib')]
        sys.path.insert(0, str(SDK))
        import z3
        cls.z3 = z3
        cls.checker = None
        if CHECKER.is_file():
            spec = importlib.util.spec_from_file_location('independent_affine_ray_checker_tested', CHECKER)
            cls.checker = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.checker
            spec.loader.exec_module(cls.checker)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'original.smt2'

    def fixture(self, source, values, threshold='1', direction='min'):
        self.source.write_text(source)
        # Both senses parse as min so the original objective is not negated.
        normalized = source.replace('(maximize ', '(minimize ')
        parser = self.z3.Optimize()
        parser.from_string(normalized)
        return dict(schema='source-affine-ray-v1', source=str(self.source),
                    source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                    direction=direction, objective=parser.objectives()[0].sexpr(),
                    integer_variables=[dict(name=name, intercept=str(a), slope=str(d))
                                       for name, (a, d) in sorted(values.items())],
                    threshold=threshold)

    def validate(self, cert, **kwargs):
        self.assertIsNotNone(self.checker, 'independent source-affine checker is missing')
        with mock.patch.object(self.z3.Optimize, 'check', side_effect=AssertionError('Optimize.check forbidden')), \
             mock.patch.object(self.z3.Solver, 'check', side_effect=AssertionError('Solver.check forbidden')):
            result = self.checker.validate_certificate(self.z3, cert, time.monotonic() + 2,
                                                       expected_source=self.source, **kwargs)
        self.assertEqual(result['solver_calls'], 0)
        return result

    def test_linear_minimum_and_maximum_100x_from_zero_offset(self):
        for direction, command, slope, expected in [('min', 'minimize', -1, '-100'),
                                                      ('max', 'maximize', 1, '100')]:
            with self.subTest(direction=direction):
                cert = self.fixture(f'(declare-const x Int)({command} (* 100 x))',
                                    {'x': (0, slope)}, direction=direction)
                result = self.validate(cert)
                self.assertTrue(result['source_unbounded_verified'])
                self.assertEqual(result['objective_polynomial'], ['0', expected])
                self.assertEqual(result['computed_threshold'], '1')

    def test_origin_may_be_infeasible_and_tail_or_not_is_complete(self):
        source = '(declare-const x Int)(assert (or (> x 10) (< x 0)))(assert (not (= x 0)))(maximize x)'
        cert = self.fixture(source, {'x': (0, 1)}, '11', 'max')
        self.assertTrue(self.validate(cert)['certificate_valid'])

    def test_quadratic_objective_and_joint_cancellation(self):
        source = '(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (= (* x y) (* z z)))(minimize (- (* x x)))'
        cert = self.fixture(source, {'x': (0, 1), 'y': (0, 1), 'z': (0, 1)})
        result = self.validate(cert)
        self.assertEqual(result['objective_polynomial'], ['0', '0', '-1'])

    def test_safe_threshold_uses_ceiling_and_all_comparisons(self):
        cert = self.fixture('(declare-const x Int)(assert (>= (* 3 x) 4))(minimize (- x))',
                            {'x': (0, 1)}, '3')
        self.assertEqual(self.validate(cert)['computed_threshold'], '3')
        cert['threshold'] = '2'
        with self.assertRaisesRegex(ValueError, 'threshold'):
            self.validate(cert)

    def test_comparison_in_unselected_or_branch_still_contributes_threshold(self):
        cert = self.fixture('(declare-const x Int)(assert (or true (> x 100)))(maximize x)',
                            {'x': (0, 1)}, '101', 'max')
        self.assertEqual(self.validate(cert)['computed_threshold'], '101')
        cert['threshold'] = '1'
        with self.assertRaises(ValueError):
            self.validate(cert)

    def test_leading_sign_not_value_at_threshold_proves_objective(self):
        cert = self.fixture('(declare-const x Int)(minimize (- (* x x) 10000))',
                            {'x': (0, 1)}, '10001')
        with self.assertRaisesRegex(ValueError, 'objective'):
            self.validate(cert)

    def test_constant_objective_and_wrong_direction_are_rejected(self):
        cert = self.fixture('(declare-const x Int)(minimize x)', {'x': (-100, 0)}, '1')
        with self.assertRaises(ValueError): self.validate(cert)
        cert = self.fixture('(declare-const x Int)(minimize x)', {'x': (0, -1)})
        cert['direction'] = 'max'
        with self.assertRaises(ValueError): self.validate(cert)

    def test_finally_false_hard_formula_is_rejected(self):
        cert = self.fixture('(declare-const x Int)(assert (< x 0))(minimize (- x))', {'x': (0, 1)})
        with self.assertRaisesRegex(ValueError, 'assertion|formula|hard'):
            self.validate(cert)

    def test_zero_polynomial_strict_and_nonstrict_polarity(self):
        cert = self.fixture('(declare-const x Int)(assert (not (< (- x x) 0)))(assert (<= (- x x) 0))(minimize x)', {'x': (0, -1)})
        self.assertTrue(self.validate(cert)['source_unbounded_verified'])
        source = '(declare-const x Int)(assert (not (<= (- x x) 0)))(minimize x)'
        cert = self.fixture(source, {'x': (0, -1)})
        with self.assertRaises(ValueError): self.validate(cert)

    def test_complete_sorted_source_used_variables_exclude_unused_declarations(self):
        source = '(declare-const x Int)(declare-const y Int)(declare-const unused Int)(assert (= y (+ x 1)))(minimize (- x))'
        cert = self.fixture(source, {'x': (0, 1), 'y': (1, 1)})
        self.assertTrue(self.validate(cert)['certificate_valid'])
        changes = [cert['integer_variables'][:-1], cert['integer_variables'] * 2,
                   list(reversed(cert['integer_variables'])),
                   cert['integer_variables'] + [dict(name='unused', intercept='0', slope='0')],
                   cert['integer_variables'] + [dict(name='foreign', intercept='0', slope='0')]]
        for variables in changes:
            changed = copy.deepcopy(cert); changed['integer_variables'] = variables
            with self.subTest(variables=variables), self.assertRaises(ValueError): self.validate(changed)

    def test_certificate_identity_schema_and_exact_decimal_mutations(self):
        cert = self.fixture('(declare-const x Int)(minimize x)', {'x': (0, -1)})
        for key, value in [('schema', 'other'), ('source_sha256', '0' * 64), ('objective', '(- x)'),
                           ('threshold', '-1'), ('threshold', '0'), ('threshold', '1.0'), ('extra', True),
                           ('source', 'relative.smt2')]:
            changed = copy.deepcopy(cert); changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.validate(changed)
        for value in ('1.0', '1e3', True, 1, '+1'):
            changed = copy.deepcopy(cert); changed['integer_variables'][0]['slope'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.validate(changed)
        changed = copy.deepcopy(cert); changed['integer_variables'][0]['extra'] = 'x'
        with self.assertRaises(ValueError): self.validate(changed)

    def test_source_change_and_expected_path_are_rejected(self):
        cert = self.fixture('(declare-const x Int)(minimize x)', {'x': (0, -1)})
        self.source.write_text('(declare-const x Int)(assert (> x 0))(minimize x)')
        with self.assertRaises(ValueError): self.validate(cert)
        other = Path(self.tmp.name) / 'other.smt2'; other.write_text('(declare-const x Int)(minimize x)')
        cert['source'] = str(other)
        with self.assertRaises(ValueError): self.validate(cert)

    def test_raw_unsupported_syntax_is_rejected_even_if_z3_folds_it(self):
        self.assertIsNotNone(self.checker, 'independent source-affine checker is missing')
        invalid = [
            '(declare-const b Bool)', '(declare-const r Real)', '(declare-fun f (Int) Int)',
            '(assert (= (div 4 2) 2))', '(assert (= (mod 4 2) 0))',
            '(assert (= (ite true x x) x))', '(assert (= (^ x 2) (* x x)))',
            '(assert (forall ((y Int)) (= y y)))', '(assert (= (to_real 1) 1.0))',
            '(assert (= (let ((y x)) y) x))', '(assert (= true true))', '(push 1)']
        for text in invalid:
            source = '(declare-const x Int)' + text + '(minimize x)'
            self.source.write_text(source)
            cert = dict(schema='source-affine-ray-v1', source=str(self.source),
                        source_sha256=hashlib.sha256(source.encode()).hexdigest(), direction='min', objective='x',
                        integer_variables=[dict(name='x', intercept='0', slope='-1')], threshold='1')
            with self.subTest(source=text), self.assertRaises(ValueError): self.validate(cert)

    def test_zero_arity_integer_alias_and_source_check_commands_are_parse_only(self):
        source = '(declare-fun x () Int)(define-fun alias () Int (+ x 1))(assert (>= x 0))(minimize (- alias))(check-sat)(get-model)(exit)'
        cert = self.fixture(source, {'x': (0, 1)}, '2')
        self.assertTrue(self.validate(cert)['source_unbounded_verified'])

    def test_quoted_source_symbols_bind_canonical_ast_names(self):
        source = '(declare-const |x| Int)(declare-const |another name| Int)(assert (= |another name| (+ x 1)))(minimize (- x))'
        cert = self.fixture(source, {'x': (0, 1), 'another name': (1, 1)})
        try:
            result = self.validate(cert)
        except ValueError as error:
            self.fail('valid quoted source names were rejected: ' + str(error))
        self.assertTrue(result['certificate_valid'])

    def test_resource_and_deadline_guards_reject(self):
        cert = self.fixture('(declare-const x Int)(minimize x)', {'x': (0, -1)})
        with self.assertRaises(ValueError): self.validate(cert, max_nodes=1)
        cert['integer_variables'][0]['intercept'] = '1024'
        with self.assertRaises(ValueError): self.validate(cert, max_bits=8)
        self.assertIsNotNone(self.checker)
        with self.assertRaises(TimeoutError):
            self.checker.validate_certificate(self.z3, cert, time.monotonic() - 1)
        cert = self.fixture('(declare-const x Int)(minimize (* x x x))', {'x': (0, -1)})
        with self.assertRaisesRegex(ValueError, 'degree'):
            self.validate(cert)


if __name__ == '__main__':
    unittest.main()
