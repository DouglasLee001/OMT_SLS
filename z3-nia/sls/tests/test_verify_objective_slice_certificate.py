#!/usr/bin/env python3
"""Adversarial generated-source checks for objective-slice UNSAT receipts."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TREE = Path(__file__).resolve().parents[2]
SCRIPT = TREE / 'experiment/verify_objective_slice_certificate.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONVERTER = load(TREE / 'reference/NIA_LS/converter/omt_to_ls.py', '_slice_test_converter')
CONVERTER.Z3_ROOT = TREE.parent / 'competitor/z3-5.1.0'
Z3 = CONVERTER.load_z3()
SOURCE = ('(declare-const z Int)(declare-const x Int)(declare-const a Int)'
          '(declare-const b Bool)'
          '(assert (and (= z (* x x)) (and (or b (= x 0)) (= (* a a) 0))))'
          '(minimize z)(check-sat)')


def fixture(source=SOURCE, *, indices=(0,), best='0'):
    query, direction, _ = CONVERTER._query(source)
    parsed = Z3.Optimize(); parsed.from_string(query)
    objective = parsed.objectives()[0]
    flat, pending = [], list(reversed(list(parsed.assertions())))
    while pending:
        item = pending.pop()
        if Z3.is_and(item):
            pending.extend(reversed(item.children()))
        else:
            flat.append(item)
    selected = [flat[i] for i in indices]
    solver = Z3.Solver(); solver.add(*selected)
    bound = Z3.IntVal(best)
    # Explicit method calls avoid Python's IntNumRef-subclass operand reflection.
    solver.add(objective.__lt__(bound) if direction == 'min' else objective.__gt__(bound))
    return dict(version='omt-objective-slice-v1', original_objective=objective.sexpr(),
                direction=direction, original_best=best, flattened_source_count=len(flat),
                selected_indices=list(indices), selected_assertions=[x.sexpr() for x in selected],
                query_smt2=solver.to_smt2())


class ObjectiveSliceCertificateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='objective-slice-check-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.smt2'
        self.certificate = self.root / 'certificate.json'
        self.model = self.root / 'model.tsv'

    def checker(self):
        self.assertTrue(SCRIPT.is_file(), 'independent objective-slice certificate verifier is missing')
        return load(SCRIPT, '_slice_checker_under_test')

    def saved(self, source=SOURCE, **options):
        self.source.write_text(source)
        payload = fixture(source, **options)
        self.certificate.write_text(json.dumps(payload))
        return payload

    def changed(self, payload):
        self.certificate.write_text(json.dumps(payload))

    def verify(self, checker=None, **options):
        return (checker or self.checker()).verify_certificate(self.source, self.certificate, timeout=10, **options)

    def test_subset_unsat_proves_bound_without_claiming_incumbent(self):
        self.saved()
        report = self.verify()
        self.assertTrue(report['certificate_valid'])
        self.assertTrue(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])
        self.assertFalse(report['verification_incomplete'])
        self.assertEqual(report['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertTrue(report['source_unchanged'])
        self.assertEqual(report['subset_replay_result'], 'unsat')
        self.assertEqual(report['selected_components_verified'], 1)
        self.assertEqual(report['flattened_source_count'], 3)

    def test_complete_source_incumbent_and_partial_native_mapping(self):
        self.saved()
        self.model.write_text('x\t0\nb\ttrue\n__native_aux\t777\n')
        report = self.verify(incumbent_model=self.model)
        self.assertTrue(report['optimality_verified'])
        self.assertEqual(report['incumbent_validation']['matched_source_variables'], 2)
        self.assertEqual(report['incumbent_validation']['ignored_native_variables'], ['__native_aux'])

    def test_original_maximize_and_quoted_boolean_symbols(self):
        source = SOURCE.replace(' z ', ' |cost value| ').replace('(minimize z)', '(maximize |cost value|)')
        source = source.replace('(* x x)', '(- (* x x))').replace(' b ', ' |flag value| ')
        self.saved(source)
        self.model.write_text('x\t0\nflag value\ttrue\n')
        report = self.verify(incumbent_model=self.model)
        self.assertEqual(report['original_direction'], 'max')
        self.assertTrue(report['optimality_verified'])

    def test_source_objective_direction_and_selected_identity_are_authoritative(self):
        checker = self.checker()
        for field, value in [('version', 'other'), ('original_objective', 'x'),
                             ('direction', 'max'), ('flattened_source_count', 4),
                             ('selected_assertions', ['(>= z 0)'])]:
            with self.subTest(field=field):
                payload = self.saved(); payload[field] = value; self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)
        self.saved()
        self.source.write_text(SOURCE.replace('(* x x)', '(+ x 1)'))
        with self.assertRaises(ValueError): self.verify(checker)

    def test_external_expected_source_hash_binds_actual_bytes(self):
        self.saved()
        checker = self.checker()
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertTrue(self.verify(checker, expected_source_sha256=digest)['source_hash_verified'])
        for bad in ('0' * 64, 'not-a-sha256'):
            with self.subTest(hash=bad):
                with self.assertRaises(ValueError): self.verify(checker, expected_source_sha256=bad)

    def test_indices_reject_duplicates_ranges_booleans_and_missing_components(self):
        checker = self.checker()
        for indices in ([-1], [3], [0, 0], [True], ['0'], []):
            with self.subTest(indices=indices):
                payload = self.saved(); payload['selected_indices'] = indices; self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)
        payload = self.saved(); payload['selected_assertions'] = []; self.changed(payload)
        with self.assertRaises(ValueError): self.verify(checker)

    def test_depth_first_order_is_preserved(self):
        payload = self.saved(indices=(0, 1, 2))
        self.assertTrue(self.verify()['no_better_bound_verified'])
        payload['selected_indices'] = [2, 1, 0]; self.changed(payload)
        with self.assertRaises(ValueError): self.verify()

    def test_or_negation_implication_are_indivisible(self):
        checker = self.checker()
        for component, stolen in [('(or (>= z 0) (= z (- 1)))', '(>= z 0)'),
                                 ('(not (and (< z 0) b))', '(not (< z 0))'),
                                 ('(=> b (>= z 0))', '(>= z 0)')]:
            with self.subTest(component=component):
                source = '(declare-const z Int)(declare-const b Bool)(assert ' + component + ')(minimize z)'
                payload = self.saved(source); payload['selected_assertions'] = [stolen]
                solver = Z3.Solver(); solver.add(*Z3.parse_smt2_string('(assert ' + stolen + ')',
                    decls={'z': Z3.Int('z'), 'b': Z3.Bool('b')}), Z3.Int('z') < 0)
                payload['query_smt2'] = solver.to_smt2(); self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)

    def test_query_rejects_missing_extra_false_anchor_and_wrong_strict_bound(self):
        checker = self.checker()
        for mutation in ('missing-component', 'false', 'anchor', 'nonstrict', 'opposite', 'other-best'):
            with self.subTest(mutation=mutation):
                payload = self.saved()
                solver = Z3.Solver(); solver.from_string(payload['query_smt2'])
                items = list(solver.assertions())
                if mutation == 'missing-component': items.pop(0)
                elif mutation == 'false': items.append(Z3.BoolVal(False))
                elif mutation == 'anchor': items.append(Z3.Int('x') == 0)
                else:
                    z = Z3.Int('z')
                    items[-1] = {'nonstrict': z <= 0, 'opposite': z > 0, 'other-best': z < 1}[mutation]
                changed = Z3.Solver(); changed.add(*items)
                payload['query_smt2'] = changed.to_smt2(); self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)

    def test_query_rejects_foreign_unused_declaration_and_changed_sort(self):
        checker = self.checker()
        for mutation in ('foreign', 'unused-source', 'sort', 'state-after', 'option', 'second-check'):
            with self.subTest(mutation=mutation):
                payload = self.saved(); query = payload['query_smt2']
                if mutation == 'foreign': query = '(declare-const fresh Int)\n' + query
                elif mutation == 'unused-source': query = '(declare-const a Int)\n' + query
                elif mutation == 'sort': query = query.replace('(declare-fun z () Int)', '(declare-fun z () Bool)')
                elif mutation == 'state-after': query += '\n(assert false)'
                elif mutation == 'option': query = '(set-option :timeout 1)\n' + query
                else: query += '\n(check-sat)'
                payload['query_smt2'] = query; self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)

    def test_large_exact_integer_incumbent_avoids_python_decimal_digit_limit(self):
        best = '1' + '0' * 5000
        source = '(declare-const x Int)(assert (>= x ' + best + '))(minimize x)'
        self.saved(source, best=best)
        self.model.write_text('x\t' + best + '\n')
        report = self.verify(incumbent_model=self.model)
        self.assertTrue(report['optimality_verified'])
        self.assertEqual(report['original_best'], best)

    def test_bad_numeric_inputs_and_resource_guards_precede_replay(self):
        checker = self.checker()
        for best in ('0.0', '1e3', '1/1', True, 0, '9' * 20000):
            with self.subTest(best_type=type(best).__name__):
                payload = self.saved(); payload['original_best'] = best; self.changed(payload)
                with self.assertRaises(ValueError): self.verify(checker)
        self.saved()
        for guard in ('MAX_INPUT_BYTES', 'MAX_AST_NODES', 'MAX_COMPONENTS'):
            with self.subTest(guard=guard), patch.object(checker, guard, 1):
                with self.assertRaisesRegex(ValueError, 'guard'): self.verify(checker)
        for timeout in (0, -1, float('inf'), float('nan'), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                checker.verify_certificate(self.source, self.certificate, timeout=timeout)

    def test_sat_is_structurally_valid_but_no_bound_or_optimality_proof(self):
        self.saved(best='1')
        report = self.verify()
        self.assertTrue(report['certificate_valid'])
        self.assertFalse(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])
        self.assertFalse(report['verification_incomplete'])
        self.assertEqual(report['subset_replay_result'], 'sat')

    @staticmethod
    def solver_stub(answer):
        class Stub:
            def add(self, *items): pass
            def set(self, **options): pass
            def check(self): return answer
            def reason_unknown(self): return 'generated unknown'
        return Stub()

    def test_unknown_never_passes(self):
        self.saved(); checker = self.checker()
        with patch.object(Z3, 'Solver', return_value=self.solver_stub(Z3.unknown)):
            report = self.verify(checker)
        self.assertTrue(report['certificate_valid'])
        self.assertTrue(report['verification_incomplete'])
        self.assertFalse(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])

    def test_unsat_returned_after_deadline_never_passes(self):
        self.saved(); checker = self.checker(); active = []
        original = checker.Budget
        class ExpiringBudget(original):
            def check(self):
                if getattr(self, 'expired', False): raise TimeoutError('generated late UNSAT')
                return super().check()
        def budget_factory(timeout):
            budget = ExpiringBudget(timeout); active.append(budget); return budget
        solver = self.solver_stub(Z3.unsat)
        def late_check(): active[0].expired = True; return Z3.unsat
        solver.check = late_check
        with patch.object(checker, 'Budget', side_effect=budget_factory), patch.object(Z3, 'Solver', return_value=solver):
            report = self.verify(checker)
        self.assertTrue(report['verification_incomplete'])
        self.assertFalse(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])

    def test_incumbent_must_satisfy_full_original_formula(self):
        self.saved(); checker = self.checker()
        for model in ('x\t0\na\t1\n', 'x\t1\n', 'b\t1\n', 'x\tfalse\n', 'x\t0\nx\t1\n'):
            with self.subTest(model=model):
                self.model.write_text(model)
                report = self.verify(checker, incumbent_model=self.model)
                self.assertTrue(report['certificate_valid'])
                self.assertTrue(report['no_better_bound_verified'])
                self.assertFalse(report['optimality_verified'])
                self.assertFalse(report['incumbent_validation']['verified'])

    def test_input_modification_during_replay_is_rejected(self):
        self.saved(); checker = self.checker(); solver = self.solver_stub(Z3.unsat)
        def change_source(): self.source.write_text(SOURCE + '\n; changed'); return Z3.unsat
        solver.check = change_source
        with patch.object(Z3, 'Solver', return_value=solver):
            with self.assertRaisesRegex(ValueError, 'changed'): self.verify(checker)

    def test_exponential_source_macro_expansion_is_rejected_before_native_parse(self):
        source = '(declare-const x Int)(define-fun f0 ((v Int)) Bool (>= v 0))'
        for index in range(1, 21):
            source += ('(define-fun f%d ((v Int)) Bool '
                '(and (f%d (* v 2)) (f%d (+ (* v 2) 1))))') % (index, index - 1, index - 1)
        source += '(assert (f20 x))(minimize x)(check-sat)'
        self.source.write_text(source)
        self.certificate.write_text(json.dumps(dict(version='omt-objective-slice-v1')))
        checker = self.checker()
        with patch.object(Z3, 'Optimize', side_effect=AssertionError('unsafe native macro expansion reached')):
            try:
                self.verify(checker)
            except ValueError as exc:
                self.assertIn('macro expansion resource guard', str(exc))
            except AssertionError:
                self.fail('exponential source macros reached the unbounded native parser')
            else:
                self.fail('exponential source macros were accepted')

    def test_normal_nonrecursive_source_macros_preserve_original_semantics(self):
        source = ('(declare-const x Int)(define-fun zero () Int 0)'
                  '(define-fun square ((v Int)) Int (* v v))'
                  '(define-fun nonnegative ((v Int)) Bool (>= v zero))'
                  '(assert (nonnegative (square x)))(minimize (square x))(check-sat)')
        self.saved(source)
        self.model.write_text('x\t0\n')
        self.assertTrue(self.verify(incumbent_model=self.model)['optimality_verified'])

    def test_nested_associative_source_component_roundtrip(self):
        source = ('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                  '(declare-const a Bool)(declare-const b Bool)'
                  '(assert (or (or (and (>= x 0) (= (+ (+ y z) x) 0)) (and (>= x 0) a))'
                  ' (and (and (>= x 0) b) (= (+ y (+ z x)) 0))))(minimize x)(check-sat)')
        self.saved(source)
        try:
            report = self.verify()
        except ValueError as exc:
            self.fail('valid printer/parser reassociation was rejected: ' + str(exc))
        self.assertTrue(report['no_better_bound_verified'])

    def test_nested_integer_add_objective_roundtrip(self):
        source = ('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                  '(assert (>= (+ (+ x y) z) 0))(minimize (+ (+ x y) z))(check-sat)')
        self.saved(source)
        try:
            report = self.verify()
        except ValueError as exc:
            self.fail('valid objective ADD reassociation was rejected: ' + str(exc))
        self.assertTrue(report['no_better_bound_verified'])

    def test_ast_identity_allows_only_ordered_boolean_and_or_integer_add_mul(self):
        checker = self.checker()
        self.assertTrue(hasattr(checker, 'AstIdentity'), 'bounded AST identity helper is missing')
        identity = checker.AstIdentity(Z3, checker.Budget(10))
        a, b, c = Z3.Bools('a b c'); x, y, z = Z3.Ints('x y z')
        for left, right in ((Z3.Or(Z3.Or(a, b), c), Z3.Or(a, Z3.Or(b, c))),
                            (Z3.And(a, Z3.And(b, c)), Z3.And(a, b, c)),
                            ((x + y) + z, x + (y + z)),
                            ((x * y) * z, x * (y * z))):
            self.assertEqual(identity.key(left), identity.key(right))
        for left, right in ((Z3.Or(a, b, c), Z3.Or(a, c, b)),
                            (Z3.And(a, b, b), Z3.And(a, b)),
                            (Z3.Or(a, Z3.BoolVal(False)), a),
                            (Z3.And(a), a),
                            (Z3.And(Z3.And(a, b)), Z3.And(a, b)),
                            (x + y + z, x + z + y),
                            (x * y * z, x * z * y),
                            (x * x * y, x * y),
                            (2 * x * y, 3 * x * y),
                            ((x - y) - z, x - (y - z)),
                            ((x / y) / z, x / (y / z)),
                            ((Z3.IntVal(0) - 1) * x, -x),
                            (2 * x + y, 3 * x + y),
                            (x < 0, x <= 0), (x < 0, x > 0)):
            self.assertNotEqual(identity.key(left), identity.key(right))

    def test_query_only_multiplication_reassociation_preserves_source_identity(self):
        source = ('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                  '(assert (>= x 0))(assert (= (* x y z) 0))(minimize x)(check-sat)')
        payload = self.saved(source, indices=(0, 1))
        payload['query_smt2'] = ('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
            '(assert (>= x 0))(assert (= (* (* x y) z) 0))(assert (< x 0))(check-sat)')
        self.changed(payload)
        try:
            report = self.verify()
        except ValueError as exc:
            self.fail('valid ordered IntMUL query reassociation was rejected: ' + str(exc))
        self.assertTrue(report['no_better_bound_verified'])

    def test_original_nested_multiplication_roundtrip_keeps_literal_subtraction(self):
        source = ('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                  '(assert (>= x 0))(assert (= (* (- 0 1) (* y z)) (- 0 1)))'
                  '(minimize x)(check-sat)')
        self.saved(source, indices=(0, 1))
        self.model.write_text('x\t0\ny\t1\nz\t1\n')
        try:
            report = self.verify(incumbent_model=self.model)
        except ValueError as exc:
            self.fail('valid ordered IntMUL source roundtrip was rejected: ' + str(exc))
        self.assertTrue(report['optimality_verified'])

    def test_ast_identity_bounds_shared_dag_expansion_and_checks_budget(self):
        checker = self.checker()
        self.assertTrue(hasattr(checker, 'AstIdentity'), 'bounded AST identity helper is missing')
        expression = Z3.Bool('shared_leaf')
        for _ in range(30): expression = Z3.Or(expression, expression)
        with patch.object(checker, 'MAX_AST_NODES', 256):
            identity = checker.AstIdentity(Z3, checker.Budget(10))
            with self.assertRaisesRegex(ValueError, 'guard'): identity.key(expression)
            self.assertLessEqual(identity.work, 256)
        budget = checker.Budget(10); checks = []
        def expire_during_normalization():
            checks.append(True)
            if len(checks) >= 5: raise TimeoutError('generated AST identity timeout')
        budget.check = expire_during_normalization
        with self.assertRaisesRegex(TimeoutError, 'AST identity timeout'):
            checker.AstIdentity(Z3, budget).key(expression)
        self.assertEqual(len(checks), 5)

    def test_native_cpp_comparison_order_is_preserved_exactly(self):
        payload = self.saved()
        payload['query_smt2'] = ('(declare-fun z () Int)(declare-fun x () Int)'
            '(assert (= z (* x x)))(assert (< z 0))(check-sat)')
        self.changed(payload)
        try:
            report = self.verify()
        except ValueError as exc:
            self.fail('native C++ comparison order was rejected: ' + str(exc))
        self.assertTrue(report['no_better_bound_verified'])

    def test_source_symlink_retarget_during_replay_is_rejected(self):
        self.saved(); checker = self.checker()
        original = self.source
        link = self.root / 'linked-source.smt2'; link.symlink_to(original)
        other = self.root / 'other-source.smt2'; other.write_text(SOURCE + '\n; different identity')
        solver = self.solver_stub(Z3.unsat)
        def retarget():
            link.unlink(); link.symlink_to(other)
            return Z3.unsat
        solver.check = retarget
        with patch.object(Z3, 'Solver', return_value=solver):
            with self.assertRaisesRegex(ValueError, 'changed'):
                checker.verify_certificate(link, self.certificate, timeout=10)

    def test_timeout_in_full_incumbent_validation_preserves_only_proven_bound(self):
        self.saved(); checker = self.checker(); original = checker.Budget
        class IncumbentTimeout(original):
            calls = 0
            def milliseconds(self):
                self.calls += 1
                if self.calls == 2: raise TimeoutError('generated incumbent timeout')
                return super().milliseconds()
        self.model.write_text('x\t0\n')
        with patch.object(checker, 'Budget', IncumbentTimeout):
            report = self.verify(checker, incumbent_model=self.model)
        self.assertTrue(report['certificate_valid'])
        self.assertTrue(report['no_better_bound_verified'])
        self.assertFalse(report['optimality_verified'])
        self.assertTrue(report['verification_incomplete'])
        self.assertEqual(report['incumbent_validation']['result'], 'unknown')

    def test_cli_directly_selects_official_sdk_without_test_override(self):
        self.saved(); output = self.root / 'direct-report.json'
        result = subprocess.run([sys.executable, str(SCRIPT), '--source', str(self.source),
            '--certificate', str(self.certificate), '--output', str(output)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(report['z3_sdk_root'], str(CONVERTER.Z3_ROOT))
        self.assertTrue(report['z3_version'].startswith('5.1.0'))
        self.assertFalse(report['source_hash_verified'])

    def test_omitted_source_mutation_keeps_valid_subset_and_records_real_hash(self):
        self.saved()
        self.source.write_text(SOURCE.replace('(= (* a a) 0)', '(= a 17)'))
        report = self.verify()
        self.assertTrue(report['no_better_bound_verified'])
        self.assertEqual(report['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_nonregular_fifo_input_is_rejected_without_blocking(self):
        self.saved(); fifo = self.root / 'source.fifo'; os.mkfifo(fifo)
        output = self.root / 'fifo-report.json'
        try:
            result = subprocess.run([sys.executable, str(SCRIPT), '--source', str(fifo),
                '--certificate', str(self.certificate), '--output', str(output)],
                capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail('nonregular FIFO input blocked before the regular-file resource guard')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('regular file', json.loads(output.read_text())['error'])

    def test_large_finite_timeout_solver_milliseconds_are_capped(self):
        checker = self.checker()
        try:
            milliseconds = checker.Budget(1e308).milliseconds()
        except OverflowError:
            self.fail('finite timeout overflowed before the solver millisecond cap')
        self.assertGreaterEqual(milliseconds, 1)
        self.assertLessEqual(milliseconds, (1 << 32) - 1)

    def test_invalid_symlink_loop_is_rejected_by_cli(self):
        self.saved(); link = self.root / 'loop-source.smt2'; link.symlink_to(link)
        result = subprocess.run([sys.executable, str(SCRIPT), '--source', str(link),
            '--certificate', str(self.certificate), '--output', str(self.root / 'loop-report.json')],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_unrepresentably_large_timeout_is_rejected_as_invalid(self):
        self.saved(); checker = self.checker()
        try:
            checker.verify_certificate(self.source, self.certificate, timeout=10 ** 1000)
        except ValueError:
            return
        except OverflowError:
            self.fail('large timeout overflowed before the finite timeout guard')
        self.fail('unrepresentably large timeout was accepted')

    def test_nonfinite_json_and_unsupported_source_sorts_are_rejected(self):
        checker = self.checker()
        self.saved()
        self.certificate.write_text(self.certificate.read_text()[:-1] + ',"stats":NaN}')
        with self.assertRaisesRegex(ValueError, 'nonfinite'): self.verify(checker)
        for source in ('(declare-const x Real)(assert (>= x 0))(minimize x)',
                       '(declare-fun f (Int) Int)(assert (>= (f 0) 0))(minimize (f 0))',
                       '(declare-const x Int)(assert (forall ((y Int)) (>= y x)))(minimize x)'):
            with self.subTest(source=source):
                self.saved(source)
                with self.assertRaises(ValueError): self.verify(checker)

    def test_duplicate_json_keys_rejected(self):
        self.saved()
        self.certificate.write_text('{"version":"forged",' + self.certificate.read_text()[1:])
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'): self.verify()

    def test_cli_codes_and_output_input_alias_protection(self):
        checker = self.checker(); self.saved(); output = self.root / 'report.json'
        args = ['--source', str(self.source), '--certificate', str(self.certificate), '--output', str(output)]
        self.assertEqual(checker.main(args), 0)
        self.saved(best='1'); self.assertEqual(checker.main(args), 2)
        with patch.object(Z3, 'Solver', return_value=self.solver_stub(Z3.unknown)):
            self.assertEqual(checker.main(args), 3)
        self.saved(); original = self.source.read_bytes()
        alias = self.root / 'hardlink.smt2'; alias.hardlink_to(self.source)
        for target in (self.source, self.certificate, alias):
            with self.subTest(target=target):
                self.assertEqual(checker.main(args[:-1] + [str(target)]), 2)
        self.assertEqual(self.source.read_bytes(), original)
        self.model.write_text('x\t1\n')
        self.assertEqual(checker.main(args + ['--incumbent-model', str(self.model)]), 2)


if __name__ == '__main__': unittest.main()
