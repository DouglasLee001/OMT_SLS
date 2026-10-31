"""Generated independent product-graph certificate and mutation tests."""
import copy
import ctypes
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

TREE = Path(__file__).resolve().parents[2]
CHECKER = TREE / 'experiment/verify_product_graph_certificate.py'
SDK = Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_051111_801046/source/python')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProductGraphCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load(CHECKER, 'product_graph_independent_tested')
        ctypes.CDLL(str(SDK / 'z3/lib/libz3.so'))
        builtins.Z3_LIB_DIRS = [str(SDK / 'z3/lib')]
        sys.path.insert(0, str(SDK))
        import z3
        cls.z3 = z3
        cls.strict = cls.checker.base.converter

    def fixture(self, *, maximize=False, boolean=False):
        z = self.z3
        source = '(declare-const x Int)(declare-const g Int)(declare-const b Bool)'
        source += '(assert (= g (* x x)))'
        if boolean:
            source += '(assert (or b (not (= x 2))))(assert (= x (ite b x x)))'
        source += '(maximize (- g))' if maximize else '(minimize g)'
        filtered, direction, objective_text = self.strict._query(source)
        original = z.Optimize(); original.from_string(filtered)
        hard, obj = list(original.assertions()), original.objectives()[0]
        x, p = z.Int('x'), z.Int('__graph_p0')
        bound = obj.__ge__(z.IntVal(1)) if maximize else obj.__le__(z.IntVal(-1))
        lifted = [z.substitute(form, (x * x, p)) for form in hard + [bound]]
        cuts = [{'kind': 'zero', 'product': '__graph_p0'},
                {'kind': 'square_nonnegative', 'product': '__graph_p0'},
                {'kind': 'integer_square', 'product': '__graph_p0', 'a': '-2'},
                {'kind': 'orthant', 'product': '__graph_p0', 'a': '-1', 'b': '2'}]
        # Independent hand construction: no production module is imported.
        q = (-1) * x + 2 * x - (-1) * 2
        cut_forms = [(p == 0) == z.Or(x == 0, x == 0), p >= 0,
                     p >= -3 * x - 2,
                     z.Implies(z.And(x >= -1, x >= 2), p >= q),
                     z.Implies(z.And(x <= -1, x <= 2), p >= q),
                     z.Implies(z.And(x >= -1, x <= 2), p <= q),
                     z.Implies(z.And(x <= -1, x >= 2), p <= q)]
        solver = z.SolverFor('QF_LIA'); solver.add(*lifted, *cut_forms)
        text = solver.to_smt2()
        cert = dict(version='product-graph-lia-v1', source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                    direction=direction, M='0', original_objective=objective_text,
                    products=[dict(name='__graph_p0', left='x', right='x')],
                    lifted_assertions=[form.sexpr() for form in lifted], cuts=cuts,
                    query_smt2=text, query_sha256=hashlib.sha256(text.encode()).hexdigest())
        return source, cert

    def validate(self, source, cert):
        return self.checker.validate_certificate(self.z3, cert, source, self.strict, time.monotonic() + 2)

    def resign(self, cert):
        cert['query_sha256'] = hashlib.sha256(cert['query_smt2'].encode()).hexdigest()

    def test_structural_success_is_not_a_proof(self):
        source, cert = self.fixture()
        result = self.validate(source, cert)
        self.assertTrue(result['structural_result']['certificate_valid'])
        self.assertIs(result['structural_result']['bound_verified'], False)
        self.assertIs(result['structural_result']['is_optimal'], False)
        self.assertEqual(len(result['validated_assertions']), 9)

    def test_maximum_alias_and_boolean_structure(self):
        source, cert = self.fixture(maximize=True, boolean=True)
        result = self.validate(source, cert)
        self.assertEqual(result['structural_result']['direction'], 'max')
        self.assertEqual(len(result['validated_assertions']), 11)

    def test_reflected_closed_bound_preserves_exact_integer_threshold(self):
        source, cert = self.fixture()
        cert['lifted_assertions'][-1] = '(>= (- 1) g)'
        self.assertTrue(self.validate(source, cert)['structural_result']['certificate_valid'])
        cert['lifted_assertions'][-1] = '(> (- 1) g)'
        with self.assertRaises(ValueError):
            self.validate(source, cert)

    def test_source_hash_direction_objective_and_bound_mutations(self):
        source, cert = self.fixture()
        for key, value in [('source_sha256', '0' * 64), ('direction', 'max'), ('M', '1'),
                           ('original_objective', 'x'), ('version', 'other')]:
            with self.subTest(key=key):
                changed = copy.deepcopy(cert); changed[key] = value
                with self.assertRaises(ValueError): self.validate(source, changed)

    def test_freshness_topology_and_wrong_factors(self):
        source, cert = self.fixture()
        changes = [[dict(name='x', left='x', right='x')],
                   [dict(name='__graph_p0', left='__graph_p0', right='x')],
                   [dict(name='__graph_p0', left='x', right='g')],
                   [dict(name='__graph_p0', left='(* x x)', right='x')],
                   cert['products'] * 2]
        for products in changes:
            with self.subTest(products=products):
                changed = copy.deepcopy(cert); changed['products'] = products
                with self.assertRaises(ValueError): self.validate(source, changed)

    def test_all_original_assertions_and_boolean_structure_are_required(self):
        source, cert = self.fixture(boolean=True)
        for change in ('missing', 'or-split', 'wrong-bound'):
            with self.subTest(change=change):
                changed = copy.deepcopy(cert)
                if change == 'missing': changed['lifted_assertions'].pop(1)
                elif change == 'or-split': changed['lifted_assertions'][1] = 'b'
                else: changed['lifted_assertions'][-1] = '(<= g 0)'
                with self.assertRaises(ValueError): self.validate(source, changed)

    def test_schema_cuts_and_actual_query_cannot_diverge(self):
        source, cert = self.fixture()
        for change in ('missing-cut', 'bad-kind', 'bad-coefficient', 'extra-false', 'extra-anchor',
                       'extra-setting', 'extra-declaration', 'hash'):
            with self.subTest(change=change):
                changed = copy.deepcopy(cert)
                if change == 'missing-cut': changed['cuts'].pop()
                elif change == 'bad-kind': changed['cuts'][0]['kind'] = 'trust_me'
                elif change == 'bad-coefficient': changed['cuts'][2]['a'] = '2'
                elif change == 'extra-false': changed['query_smt2'] = changed['query_smt2'].replace('(check-sat)', '(assert false)\n(check-sat)')
                elif change == 'extra-anchor': changed['query_smt2'] = changed['query_smt2'].replace('(check-sat)', '(assert (= x 0))\n(check-sat)')
                elif change == 'extra-setting': changed['query_smt2'] = '(set-option :timeout 1)\n' + changed['query_smt2']
                elif change == 'extra-declaration': changed['query_smt2'] = '(declare-const ghost Int)\n' + changed['query_smt2']
                if change == 'hash': changed['query_sha256'] = '0' * 64
                else: self.resign(changed)
                with self.assertRaises(ValueError): self.validate(source, changed)

    def test_nonlinear_relaxation_assumption_is_rejected(self):
        source, cert = self.fixture()
        cert['products'] = []
        cert['cuts'] = []
        original = self.z3.Optimize(); original.from_string(self.strict._query(source)[0])
        formulas = list(original.assertions()) + [self.z3.Int('g').__le__(self.z3.IntVal(-1))]
        solver = self.z3.Solver(); solver.add(*formulas)
        cert['lifted_assertions'] = [form.sexpr() for form in formulas]
        cert['query_smt2'] = solver.to_smt2(); self.resign(cert)
        with self.assertRaises(ValueError): self.validate(source, cert)

    def test_deadline_failure_is_closed(self):
        source, cert = self.fixture()
        with self.assertRaises(TimeoutError):
            self.checker.validate_certificate(self.z3, cert, source, self.strict, time.monotonic() - 1)

    def test_finite_lia_replay_proves_only_bound(self):
        source, cert = self.fixture()
        result = self.checker.replay_certificate(self.z3, cert, source, self.strict, time.monotonic() + 1)
        self.assertEqual(result['replay_result'], 'unsat')
        self.assertIs(result['bound_verified'], True)
        self.assertIs(result['is_optimal'], False)

    def test_cli_output_may_not_overwrite_source_before_replay(self):
        source_text, cert = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            source, receipt = Path(directory) / 'source.smt2', Path(directory) / 'cert.json'
            source.write_text(source_text); receipt.write_text(json.dumps(cert))
            arguments = ['--source', str(source), '--certificate', str(receipt), '--sdk-python', str(SDK),
                         '--sdk-library', str(SDK / 'z3/lib/libz3.so'),
                         '--strict-helper', str(SDK.parents[0] / 'script/run_strict_bound.py'),
                         '--timeout', '1', '--output', str(source)]
            with mock.patch.object(self.checker, 'replay_certificate', side_effect=AssertionError('must reject aliases before replay')):
                with self.assertRaisesRegex(ValueError, 'aliases'):
                    self.checker.main(arguments)
            self.assertEqual(source.read_text(), source_text)

    def line_fixture(self, kind='line_left', anchor=2, maximize=False):
        z = self.z3
        fixed, other = ('x', 'y') if kind == 'line_left' else ('y', 'x')
        source = ('(declare-const x Int)(declare-const y Int)(declare-const g Int)'
                  '(assert (= g (* x y)))(assert (= ' + fixed + ' ' + str(anchor) + '))'
                  '(assert (>= ' + other + ' 3))' + ('(maximize g)' if maximize else '(minimize g)'))
        filtered, direction, objtext = self.strict._query(source)
        parsed = z.Optimize(); parsed.from_string(filtered)
        hard, obj = list(parsed.assertions()), parsed.objectives()[0]
        x, y, p = z.Int('x'), z.Int('y'), z.Int('__line_p0')
        M = anchor * 3
        bound = obj.__ge__(z.IntVal(M + 1)) if maximize else obj.__le__(z.IntVal(M - 1))
        lifted = [z.substitute(form, (x * y, p)) for form in hard + [bound]]
        lemma = z.Or(z.Not(x == anchor), p == anchor * y) if kind == 'line_left' else z.Or(z.Not(y == anchor), p == anchor * x)
        solver = z.SolverFor('QF_LIA'); solver.add(*lifted, lemma)
        text = solver.to_smt2()
        return source, dict(version='product-graph-lia-v1', source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                            direction=direction, M=str(M), original_objective=objtext,
                            products=[dict(name='__line_p0', left='x', right='y')],
                            lifted_assertions=[form.sexpr() for form in lifted],
                            cuts=[dict(kind=kind, product='__line_p0', a=str(anchor))],
                            query_smt2=text, query_sha256=hashlib.sha256(text.encode()).hexdigest())

    def test_line_schemas_keep_guard_factor_and_anchor_exact(self):
        for kind in ('line_left', 'line_right'):
            with self.subTest(kind=kind):
                source, cert = self.line_fixture(kind)
                self.assertTrue(self.validate(source, cert)['structural_result']['certificate_valid'])
                for mutation in ('wrong-line', 'wrong-anchor', 'unguarded', 'wrong-factor', 'extra-seed', 'extra-unit'):
                    changed = copy.deepcopy(cert)
                    if mutation == 'wrong-line': changed['cuts'][0]['kind'] = 'line_right' if kind == 'line_left' else 'line_left'
                    elif mutation == 'wrong-anchor': changed['cuts'][0]['a'] = '3'
                    else:
                        q = self.z3.Solver(); q.from_string(changed['query_smt2']); forms = list(q.assertions())
                        p, x, y = self.z3.Int('__line_p0'), self.z3.Int('x'), self.z3.Int('y')
                        if mutation == 'unguarded': forms[-1] = p == 2 * (y if kind == 'line_left' else x)
                        elif mutation == 'wrong-factor': forms[-1] = self.z3.Or((x if kind == 'line_left' else y) != 2,
                                                                            p == 2 * (x if kind == 'line_left' else y))
                        elif mutation == 'extra-unit': forms[-1] = self.z3.Or(self.z3.And(forms[-1].arg(0), self.z3.BoolVal(True)), forms[-1].arg(1))
                        else: forms.append(x == 2)
                        changed_query = self.z3.Solver(); changed_query.add(*forms); changed['query_smt2'] = changed_query.to_smt2()
                    self.resign(changed)
                    with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                        self.validate(source, changed)

    def test_line_schemas_are_valid_for_all_small_signed_values_and_anchors(self):
        z = self.z3
        p, x, y = z.Int('__line_p'), z.Int('line_x'), z.Int('line_y')
        products = {'__line_p': (p, x, y)}
        count = 0
        budget = self.checker.Budget(time.monotonic() + 5)
        for kind in ('line_left', 'line_right'):
            for a in range(-3, 4):
                lemma, = self.checker._cut(z, dict(kind=kind, product='__line_p', a=str(a)), products, budget)
                for xv in range(-3, 4):
                    for yv in range(-3, 4):
                        ground = z.substitute(lemma, (x, z.IntVal(xv)), (y, z.IntVal(yv)), (p, z.IntVal(xv * yv)))
                        self.assertTrue(z.is_true(z.simplify(ground)), (kind, a, xv, yv))
                        count += 1
        self.assertEqual(count, 686)

    def test_line_lemmas_prove_fixed_factor_bounds_without_claiming_optimality(self):
        for kind, anchor, maximize in [('line_left', 2, False), ('line_right', -2, True)]:
            with self.subTest(kind=kind, anchor=anchor):
                source, cert = self.line_fixture(kind, anchor, maximize)
                result = self.checker.replay_certificate(self.z3, cert, source, self.strict, time.monotonic() + 1)
                self.assertEqual(result['replay_result'], 'unsat')
                self.assertTrue(result['bound_verified'])
                self.assertFalse(result['is_optimal'])


if __name__ == '__main__':
    unittest.main()
