#!/usr/bin/env python3
"""Independent OA certificate checks, including adversarial source/query changes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TREE = Path(__file__).resolve().parents[2]
SCRIPT = TREE / 'experiment/verify_convex_certificate.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONVERTER = load(TREE / 'reference/NIA_LS/converter/omt_to_ls.py', '_convex_test_parser')
Z3 = CONVERTER.load_z3()


def fixture(source, *, projection=False, maximize=False, best=None, points=None, two_variables=False):
    """Known integer-gap or two-variable quadratic, with hand-derived derivatives."""
    query, direction, _ = CONVERTER._query(source)
    parsed = Z3.Optimize(); parsed.from_string(query)
    hard, objective = list(parsed.assertions()), parsed.objectives()[0]
    if projection:
        rhs = hard[0].arg(1)
        raw = [Z3.substitute(expression, (objective, rhs)) for expression in hard[1:]]
        normalized = -rhs if maximize else rhs
        projected = dict(definition_index=0, variable=objective.sexpr(), rhs=rhs.sexpr())
    else:
        raw = hard
        normalized = -objective if maximize else objective
        projected = None
    linear = [Z3.simplify(expression) for expression in raw]
    if two_variables:
        variables = [Z3.Int('x'), Z3.Int('y')]
        constant, gradient, hessian = 0, [0, 0], [[2, 1], [1, 2]]
        quadratic = [['1', '1/2'], ['1/2', '1']]
        lower, diagonal = [['1', '0'], ['1/2', '1']], ['2', '3/2']
        best = 0 if best is None else best
        points = [[0, 0]] if points is None else points
    else:
        variables = [Z3.Int('x')]
        constant, gradient, hessian = 1, [-4], [[8]]
        quadratic, lower, diagonal = [['4']], [['1']], ['8']
        best = (-1 if maximize else 1) if best is None else best
        points = [[0], [1]] if points is None else points
    normalized_best = -best if maximize else best
    bound = normalized_best - 1
    cuts = []
    replay = Z3.SolverFor('QF_LIA'); replay.add(*linear)
    for point in points:
        coefficients = [gradient[i] + sum(hessian[i][j] * point[j] for j in range(len(point)))
                        for i in range(len(point))]
        value = constant + sum(gradient[i] * point[i] for i in range(len(point)))
        value += sum(hessian[i][j] * point[i] * point[j] for i in range(len(point))
                     for j in range(len(point))) // 2
        intercept = value - sum(a * b for a, b in zip(coefficients, point))
        cuts.append(dict(point=list(map(str, point)), coefficients=list(map(str, coefficients)), constant=str(intercept)))
        expression = Z3.IntVal(intercept)
        for variable, coefficient in zip(variables, coefficients):
            if coefficient:
                expression = expression + coefficient * variable
        replay.add(expression <= bound)
    original = Z3.Solver(); original.add(*hard)
    original.add(objective > best if maximize else objective < best)
    return dict(version='omt-convex-exchange-v1',
                source_identity=dict(hard=[x.sexpr() for x in hard], objective=objective.sexpr(), minimize=not maximize),
                projection=projected, projected_hard_raw=[x.sexpr() for x in raw],
                linear_hard=[x.sexpr() for x in linear], normalized_objective=normalized.sexpr(),
                variables=[x.sexpr() for x in variables],
                g=dict(constant=str(constant), linear=list(map(str, gradient)), quadratic=quadratic,
                       hessian=[list(map(str, row)) for row in hessian]),
                ldl=dict(lower=lower, diagonal=diagonal),
                incumbent=dict(original_best=str(best), normalized_best=str(normalized_best), strict_bound=str(bound)),
                cuts=cuts, linear_query_smt2=replay.to_smt2(), source_query_smt2=original.to_smt2(), queries=len(cuts))


GAP = '(declare-const x Int)(minimize (* (- (* 2 x) 1) (- (* 2 x) 1)))(check-sat)'
PROJECTED = ('(declare-const x Int)(declare-const cost Int)(declare-const b Bool)'
             '(assert (= cost (* (- (* 2 x) 1) (- (* 2 x) 1))))'
             '(assert (or (and b (= x 0)) (and (not b) (= x 1))))'
             '(minimize cost)(check-sat)')


class ConvexCertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='convex-cert-check-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.smt2'
        self.certificate = self.root / 'certificate.json'

    def checker(self):
        self.assertTrue(SCRIPT.is_file(), 'independent convex certificate verifier is missing')
        return load(SCRIPT, '_convex_certificate_under_test')

    def saved(self, source=GAP, **options):
        self.source.write_text(source)
        certificate = fixture(source, **options)
        self.certificate.write_text(json.dumps(certificate))
        return certificate

    def check(self, source=GAP, *, model=None, **options):
        self.saved(source, **options)
        return self.checker().verify_certificate(self.source, self.certificate, timeout=10, incumbent_model=model)

    def test_integer_gap_proves_only_bound_without_feasible_incumbent_witness(self):
        result = self.check()
        self.assertTrue(result['certificate_valid'])
        self.assertTrue(result['no_better_bound_verified'])
        self.assertFalse(result['optimality_verified'])
        self.assertEqual(result['linear_replay_result'], 'unsat')
        self.assertEqual(result['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_projection_boolean_hard_and_partial_native_source_witness(self):
        model = self.root / 'model.tsv'
        model.write_text('x\t0\nb\ttrue\n__native_aux\t777\n')
        result = self.check(PROJECTED, projection=True, model=model)
        self.assertTrue(result['optimality_verified'])
        self.assertEqual(result['incumbent_validation']['matched_source_variables'], 2)
        self.assertEqual(result['incumbent_validation']['ignored_native_variables'], ['__native_aux'])

    def test_original_maximize_direction_and_exact_rational_psd(self):
        source = GAP.replace('(minimize (*', '(maximize (- (*').replace('))(check-sat)', ')))(check-sat)')
        model = self.root / 'max.tsv'; model.write_text('x\t0\n')
        result = self.check(source, maximize=True, model=model)
        self.assertTrue(result['optimality_verified'])
        source = ('(declare-const x Int)(declare-const y Int)'
                  '(minimize (+ (* x x) (* x y) (* y y)))(check-sat)')
        result = self.check(source, two_variables=True)
        self.assertTrue(result['no_better_bound_verified'])

    def test_singular_and_zero_dimension_psd_certificates(self):
        source = ('(declare-const x Int)(declare-const y Int)'
                  '(minimize (* (+ x y) (+ x y)))(check-sat)')
        certificate = self.saved(source, two_variables=True)
        certificate['g']['hessian'] = [['2', '2'], ['2', '2']]
        certificate['g']['quadratic'] = [['1', '1'], ['1', '1']]
        certificate['ldl'] = dict(lower=[['1', '0'], ['1', '1']], diagonal=['2', '0'])
        self.certificate.write_text(json.dumps(certificate))
        checker = self.checker()
        self.assertTrue(checker.verify_certificate(self.source, self.certificate, timeout=10)['no_better_bound_verified'])
        source = '(assert true)(minimize 7)(check-sat)'
        certificate = self.saved(source, best=7, points=[[0]])
        certificate.update(variables=[], g=dict(constant='7', linear=[], quadratic=[], hessian=[]),
                           ldl=dict(lower=[], diagonal=[]), cuts=[dict(point=[], coefficients=[], constant='7')])
        solver = Z3.SolverFor('QF_LIA'); solver.add(Z3.BoolVal(True), Z3.IntVal(7) <= 6)
        certificate['linear_query_smt2'] = solver.to_smt2()
        self.certificate.write_text(json.dumps(certificate))
        self.assertTrue(checker.verify_certificate(self.source, self.certificate, timeout=10)['no_better_bound_verified'])

    def test_invalid_source_witness_sort_feasibility_duplicates_and_json_duplicates(self):
        checker = self.checker()
        self.saved(PROJECTED, projection=True)
        model = self.root / 'bad-model.tsv'
        for text in ('x\t0\nb\tfalse\n', 'x\tfalse\n', 'b\t1\n', 'x\t0\nx\t1\n'):
            with self.subTest(model=text):
                model.write_text(text)
                with self.assertRaises(ValueError):
                    checker.verify_certificate(self.source, self.certificate, timeout=10, incumbent_model=model)
        payload = self.certificate.read_text()
        self.certificate.write_text('{"version":"forged",' + payload[1:])
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            checker.verify_certificate(self.source, self.certificate, timeout=10)

    def test_incumbent_hash_records_the_same_bytes_that_were_verified(self):
        self.saved()
        checker = self.checker()
        model = self.root / 'replaced-model.tsv'
        original_bytes = b'x\t0\n'
        model.write_bytes(original_bytes)
        real_solver = Z3.Solver

        def replacing_solver(*args, **kwargs):
            solver = real_solver(*args, **kwargs)
            real_check = solver.check
            def check(*assumptions):
                model.write_text('x\t3\n')
                return real_check(*assumptions)
            solver.check = check
            return solver

        with patch.object(Z3, 'Solver', side_effect=replacing_solver):
            result = checker.verify_certificate(self.source, self.certificate, timeout=10, incumbent_model=model)
        self.assertTrue(result['optimality_verified'])
        self.assertEqual(result['incumbent_validation']['model_sha256'], hashlib.sha256(original_bytes).hexdigest())

    def test_tampered_source_projection_objective_direction_and_psd_are_rejected(self):
        checker = self.checker()
        mutations = [lambda c: c['source_identity'].update(minimize=False),
                     lambda c: c['source_identity'].update(objective='(+ cost 1)'),
                     lambda c: c['source_identity']['hard'].pop(),
                     lambda c: c['projection'].update(definition_index=1),
                     lambda c: c['projection'].update(rhs='(* x x)'),
                     lambda c: c['projected_hard_raw'].clear(),
                     lambda c: c['linear_hard'].clear(),
                     lambda c: c.update(normalized_objective='(+ (* x x) 1)'),
                     lambda c: c['g'].update(constant='2'),
                     lambda c: c['g']['hessian'][0].__setitem__(0, '6'),
                     lambda c: c['g']['quadratic'][0].__setitem__(0, '3'),
                     lambda c: c['ldl']['lower'][0].__setitem__(0, '2'),
                     lambda c: c['ldl']['diagonal'].__setitem__(0, '-8')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                certificate = self.saved(PROJECTED, projection=True)
                mutate(certificate)
                self.certificate.write_text(json.dumps(certificate))
                with self.assertRaises(ValueError):
                    checker.verify_certificate(self.source, self.certificate, timeout=10)

    def test_tampered_cut_point_gradient_constant_and_strict_bound_are_rejected(self):
        checker = self.checker()
        mutations = [lambda c: c['cuts'][0]['point'].__setitem__(0, '1'),
                     lambda c: c['cuts'][0]['coefficients'].__setitem__(0, '-5'),
                     lambda c: c['cuts'][0].update(constant='2'),
                     lambda c: c['incumbent'].update(strict_bound='1'),
                     lambda c: c['incumbent'].update(normalized_best='0'),
                     lambda c: c.update(variables=['cost']),
                     lambda c: c['cuts'][0]['point'].__setitem__(0, 0.0)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                certificate = self.saved()
                mutate(certificate)
                self.certificate.write_text(json.dumps(certificate))
                with self.assertRaises(ValueError):
                    checker.verify_certificate(self.source, self.certificate, timeout=10)

    def test_final_linear_query_rejects_extra_signed128_bounds_missing_hard_or_cut(self):
        checker = self.checker()
        for mutation in ('extra', 'missing-hard', 'missing-cut', 'source-query'):
            with self.subTest(mutation=mutation):
                certificate = self.saved(PROJECTED, projection=True)
                if mutation == 'source-query':
                    certificate['source_query_smt2'] = certificate['linear_query_smt2']
                else:
                    solver = Z3.Solver(); solver.from_string(certificate['linear_query_smt2'])
                    assertions = list(solver.assertions())
                    if mutation == 'extra':
                        assertions.append(Z3.Int('x') <= (1 << 127) - 1)
                    elif mutation == 'missing-hard':
                        assertions.pop(0)
                    else:
                        assertions.pop()
                    changed = Z3.Solver(); changed.add(*assertions)
                    certificate['linear_query_smt2'] = changed.to_smt2()
                self.certificate.write_text(json.dumps(certificate))
                with self.assertRaises(ValueError):
                    checker.verify_certificate(self.source, self.certificate, timeout=10)

    def test_real_twelve_variable_cut_accepts_exact_le_ge_mirror_only(self):
        # First real portfolio1058 cut: g(a) intercept -915, strict bound 914.
        names = ['x1', 'x10', 'x11', 'x12', 'x2', 'x3', 'x4', 'x5', 'x6', 'x7', 'x8', 'x9']
        coefficients = [236, 146, 120, 148, 18, 232, 302, 84, -16, 58, 74, 66]
        declarations = {name: Z3.Int(name) for name in names}
        expression = Z3.IntVal(-915)
        for name, coefficient in zip(names, coefficients):
            expression = expression + Z3.IntVal(coefficient) * declarations[name]
        # IntNumRef is an ArithRef subclass: Python dispatches this comparison
        # to the right operand's reflected __ge__, unlike the C++ <= AST.
        rebuilt = expression <= Z3.IntVal(914)
        rendered = '(+ (- 915) ' + ' '.join(
            f'(* {Z3.IntVal(coefficient).sexpr()} {name})' for name, coefficient in zip(names, coefficients)) + ')'
        exported = Z3.parse_smt2_string('(assert (<= ' + rendered + ' 914))', decls=declarations)[0]
        self.assertEqual(exported.decl().kind(), Z3.Z3_OP_LE)
        self.assertEqual(rebuilt.decl().kind(), Z3.Z3_OP_GE)
        checker = self.checker()
        checker.match_query(Z3, [exported], [rebuilt], 'real dense cut')
        for changed in (expression < Z3.IntVal(914), expression >= Z3.IntVal(914),
                        expression + declarations['x1'] <= Z3.IntVal(914),
                        expression <= Z3.IntVal(913)):
            with self.subTest(changed=changed.sexpr()), self.assertRaises(ValueError):
                checker.match_query(Z3, [changed], [rebuilt], 'changed dense cut')
        guard = Z3.Bool('guard')
        with self.assertRaises(ValueError):
            checker.match_query(Z3, [Z3.Or(exported, guard)], [Z3.And(rebuilt, guard)], 'changed Boolean connection')
        with self.assertRaises(ValueError):
            checker.match_query(Z3, [exported, declarations['x1'] <= (1 << 127) - 1], [rebuilt], 'extra bound')
        with self.assertRaises(ValueError):
            checker.match_query(Z3, [], [rebuilt], 'missing cut')
        with self.assertRaises(ValueError):
            checker.match_query(Z3, [exported, exported], [rebuilt, expression <= Z3.IntVal(913)], 'duplicate replacement cut')

    def test_coherent_unreachable_best_never_proves_optimality_with_witness(self):
        self.saved(best=0)
        model = self.root / 'model.tsv'; model.write_text('x\t0\n')
        with self.assertRaises(ValueError):
            self.checker().verify_certificate(self.source, self.certificate, timeout=10, incumbent_model=model)

    def test_sat_linear_query_has_valid_mathematics_but_no_proof(self):
        result = self.check(points=[[0]])
        self.assertTrue(result['certificate_valid'])
        self.assertEqual(result['linear_replay_result'], 'sat')
        self.assertFalse(result['no_better_bound_verified'])
        self.assertFalse(result['optimality_verified'])

    def test_incremental_and_batch_replay_agree_with_source_witness(self):
        self.saved(PROJECTED, projection=True)
        model = self.root / 'replay-model.tsv'; model.write_text('x\t0\nb\ttrue\n')
        checker = self.checker()
        for mode in ('batch', 'incremental'):
            with self.subTest(mode=mode):
                result = checker.verify_certificate(self.source, self.certificate, timeout=10,
                                                    incumbent_model=model, replay_mode=mode)
                self.assertEqual(result['replay_mode'], mode)
                self.assertEqual(result['linear_replay_result'], 'unsat')
                self.assertTrue(result['optimality_verified'])
                self.assertFalse(result['verification_incomplete'])
        self.assertEqual(checker.verify_certificate(self.source, self.certificate)['replay_mode'], 'batch')

    def test_incremental_early_unsat_still_validates_all_cuts_and_final_query_first(self):
        source = GAP.replace('(minimize', '(assert (= x 0))(minimize')
        checker = self.checker()
        for tamper in ('tail-cut', 'final-query'):
            with self.subTest(tamper=tamper):
                certificate = self.saved(source)
                if tamper == 'tail-cut':
                    certificate['cuts'][-1]['coefficients'][0] = '999'
                else:
                    solver = Z3.Solver(); solver.from_string(certificate['linear_query_smt2'])
                    solver.add(Z3.Int('x') <= (1 << 127) - 1)
                    certificate['linear_query_smt2'] = solver.to_smt2()
                self.certificate.write_text(json.dumps(certificate))
                with patch.object(Z3, 'SolverFor', side_effect=AssertionError('no replay before complete validation')):
                    with self.assertRaises(ValueError):
                        checker.verify_certificate(self.source, self.certificate, timeout=10, replay_mode='incremental')
        self.saved(source)
        result = checker.verify_certificate(self.source, self.certificate, timeout=10, replay_mode='incremental')
        self.assertTrue(result['no_better_bound_verified'])
        self.assertEqual(result['tangent_cuts_verified'], 2)
        self.assertEqual(result['replay_prefix_cuts'], 1)
        self.assertEqual(result['replay_checks'], 1)

    def replay_stub(self, answers):
        class Solver:
            def __init__(self):
                self.added, self.timeouts, self.checked = [], [], []
                self.answers = iter(answers)
            def add(self, *expressions):
                self.added.extend(expressions)
            def set(self, **options):
                self.timeouts.append(options['timeout'])
            def check(self):
                self.checked.append(len(self.added))
                return next(self.answers)
            def reason_unknown(self):
                return 'mock prefix unknown'
        return Solver()

    def test_incremental_unknown_prefix_continues_with_shared_remaining_budget(self):
        checker = self.checker()
        solver = self.replay_stub([Z3.unknown, Z3.unsat])
        class RemainingBudget:
            def __init__(self):
                self.remaining = iter([90, 60])
            def milliseconds(self):
                return next(self.remaining)
            def check(self):
                pass
        with patch.object(Z3, 'SolverFor', return_value=solver) as factory:
            answer, evidence = checker.replay_linear(Z3, [Z3.BoolVal(True)],
                                                     [Z3.Int('x') >= 1, Z3.Int('x') <= 0],
                                                     RemainingBudget(), replay_mode='incremental')
        factory.assert_called_once_with('QF_LIA')
        self.assertEqual(answer, Z3.unsat)
        self.assertEqual(solver.checked, [2, 3])
        self.assertEqual(solver.timeouts, [45, 60])
        self.assertEqual(evidence['replay_unknown_prefixes'], 1)

    def test_incremental_unknown_final_is_incomplete_rather_than_invalid(self):
        self.saved()
        checker = self.checker()
        output = self.root / 'unknown-replay.json'
        for mode, checks in (('batch', 1), ('incremental', 2)):
            with self.subTest(mode=mode):
                solver = self.replay_stub([Z3.unknown] * checks)
                with patch.object(Z3, 'SolverFor', return_value=solver), patch('sys.stderr'):
                    code = checker.main(['--source', str(self.source), '--certificate', str(self.certificate),
                                         '--replay-mode', mode, '--output', str(output)])
                self.assertEqual(code, 3)
                result = json.loads(output.read_text())
                self.assertIs(result['certificate_valid'], True)
                self.assertTrue(result['verification_incomplete'])
                self.assertEqual(result['source'], str(self.source))
                self.assertEqual(result['replay_checks'], checks)
                self.assertFalse(result['no_better_bound_verified'])
                self.assertFalse(result['optimality_verified'])

    def test_incremental_global_budget_exhaustion_does_not_start_another_check(self):
        checker = self.checker()
        solver = self.replay_stub([Z3.unknown])
        class ExhaustedBudget:
            def __init__(self):
                self.calls = 0
            def milliseconds(self):
                self.calls += 1
                if self.calls > 1:
                    raise TimeoutError('shared global budget exhausted')
                return 90
            def check(self):
                pass
        with patch.object(Z3, 'SolverFor', return_value=solver):
            answer, evidence = checker.replay_linear(Z3, [], [Z3.BoolVal(True), Z3.BoolVal(False)],
                                                     ExhaustedBudget(), replay_mode='incremental')
        self.assertEqual(answer, Z3.unknown)
        self.assertEqual(len(solver.checked), 1)
        self.assertIn('global budget', evidence['replay_last_unknown_reason'])

    def test_expired_global_deadline_cannot_accept_a_late_prefix_unsat(self):
        checker = self.checker()
        solver = self.replay_stub([Z3.unsat])
        class ExpiredAfterCheck:
            def milliseconds(self):
                return 90
            def check(self):
                raise TimeoutError('shared global deadline expired after solver check')
        with patch.object(Z3, 'SolverFor', return_value=solver):
            answer, evidence = checker.replay_linear(Z3, [], [Z3.BoolVal(False)],
                                                     ExpiredAfterCheck(), replay_mode='incremental')
        self.assertEqual(answer, Z3.unknown)
        self.assertEqual(evidence['replay_checks'], 1)
        self.assertEqual(evidence['replay_trace'][0]['result'], 'unknown')

    def test_changed_current_source_and_nonlinear_projected_hard_are_rejected(self):
        checker = self.checker()
        certificate = self.saved()
        self.source.write_text(GAP.replace('(check-sat)', '(assert (= x 3))(check-sat)'))
        with self.assertRaises(ValueError):
            checker.verify_certificate(self.source, self.certificate, timeout=10)
        source = GAP.replace('(minimize', '(assert (>= (* x x) 2))(minimize')
        self.saved(source)
        with self.assertRaises(ValueError):
            checker.verify_certificate(self.source, self.certificate, timeout=10)

    def test_cli_writes_auditable_report_and_preserves_inputs(self):
        self.saved()
        before = (self.source.read_bytes(), self.certificate.read_bytes())
        output = self.root / 'verification.json'
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--source', str(self.source),
                                 '--certificate', str(self.certificate), '--timeout', '10', '--output', str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        self.assertTrue(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])
        self.assertEqual((self.source.read_bytes(), self.certificate.read_bytes()), before)
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--source', str(self.source),
                                 '--certificate', str(self.certificate), '--output', str(self.source)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.source.read_bytes(), self.certificate.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
