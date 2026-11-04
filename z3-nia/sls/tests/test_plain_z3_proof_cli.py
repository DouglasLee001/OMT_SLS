"""Plain original-source Z3 proofs bypass every optional proof enhancement."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
MINIMUM = '''(set-logic QF_NIA)
(declare-const x Int)
(declare-const b Bool)
(assert (or (and b (= x 0)) (and (not b) (= x 1))))
(minimize x)
(check-sat)
'''
FORBIDDEN_LOGS = ('omt_guarded_product',
                  'omt_optimality_cutset', 'omt_smt_exchange',
                  'omt_optimality_bv', 'omt_objective_slice', 'omt_advisory_prefix')


class PlainZ3ProofCliTests(unittest.TestCase):
    def invoke(self, source=MINIMUM, options=(), *, execution=('--mode=optimal',)):
        with tempfile.TemporaryDirectory(prefix='omt-plain-proof-') as directory:
            root = Path(directory)
            input_path, query_path = root / 'input.smt2', root / 'proof.smt2'
            input_path.write_text(source)
            result = subprocess.run(
                [str(BINARY), '--nia-ls', str(input_path), *execution,
                 '--initialize=off', '--timeout=2', '--plain-z3-proof',
                 '--optimality-stall=.000000001', '--optimality-timeout=.4',
                 '--optimality-query-output=' + str(query_path), *options],
                capture_output=True, text=True, timeout=10)
            query = query_path.read_text() if query_path.exists() else None
            audit = None
            if result.returncode == 0 and query is not None:
                audit = subprocess.run([str(BINARY), str(query_path)],
                                       capture_output=True, text=True, timeout=5)
            return result, query, audit

    def successful(self, *args, **kwargs):
        result, query, audit = self.invoke(*args, **kwargs)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(events, result.stdout)
        self.assertTrue(events[-1]['optimality_plain_z3_proof'])
        for marker in FORBIDDEN_LOGS:
            self.assertNotIn(marker, result.stderr)
        return events, result.stderr, query, audit

    def test_help_exposes_plain_mode(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--plain-z3-proof', result.stdout)

    def test_original_boolean_query_proves_minimum_without_enhancements(self):
        events, stderr, query, audit = self.successful()
        final = events[-1]
        self.assertTrue(final['is_optimal'], (events, stderr))
        self.assertEqual(final['objective_value'], '0')
        self.assertIn('omt_plain_z3_proof', stderr)
        self.assertIn('(or ', query)
        self.assertIn('(< x 0)', query)
        self.assertNotIn('(minimize', query)
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertEqual(audit.stdout.strip(), 'unsat')
        for key in ('optimality_cutset', 'optimality_guarded_product',
                    'isolate_exchange_context_enabled'):
            self.assertFalse(final[key], key)
        self.assertEqual(final['optimality_bv_seconds'], 0)

    def test_maximum_preserves_strict_original_direction(self):
        source = '(set-logic QF_NIA)\n(declare-const x Int)\n(assert (<= x (- 2)))\n(maximize x)\n(check-sat)\n'
        events, stderr, query, audit = self.successful(source, options=('--optimality-resume',))
        self.assertTrue(events[-1]['is_optimal'], (events, stderr))
        self.assertEqual(events[-1]['objective_value'], '-2')
        self.assertIn('(> x (- 2))', query)
        self.assertEqual(audit.stdout.strip(), 'unsat')

    def test_sat_models_return_to_sls_and_can_be_proved(self):
        source = '''(set-logic QF_NIA)
(declare-const x Int)
(assert (<= 0 x)) (assert (<= x 20))
(minimize (* (- x 7) (- x 7)))
(check-sat)
'''
        events, stderr, _, _ = self.successful(source, options=(
            '--no-preprocess', '--optimality-resume', '--optimality-burst'))
        final = events[-1]
        self.assertTrue(final['is_optimal'], (events, stderr))
        self.assertEqual(final['objective_value'], '0')
        self.assertGreater(final['optimality_sat_models_accepted'], 0, (events, stderr))
        self.assertTrue(any(e.get('origin') == 'z3' for e in events), events)

    def test_plain_mode_conflicts_with_every_proof_enhancement(self):
        for option in ('--guarded-product', '--optimality-bv-seconds=120', '--optimality-bv-seconds=0',
                       '--optimality-cutset',
                       '--smt-exchange=hints', '--smt-exchange=backend',
                       '--isolate-exchange-context', '--objective-slice',
                       '--objective-slice-budget=2', '--advisory-share=.5'):
            with self.subTest(option=option):
                result, _, _ = self.invoke(options=(option,))
                self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                self.assertIn('--plain-z3-proof conflicts', result.stderr)

    def test_plain_mode_requires_optimality_proof(self):
        result, _, _ = self.invoke(execution=('--optimize',))
        self.assertEqual(result.returncode, 2)
        self.assertIn('--plain-z3-proof requires --prove-optimal', result.stderr)

    def test_direction_sls_unbounded_certification_remains_enabled(self):
        source = '(set-logic QF_NIA)\n(declare-const x Int)\n(maximize x)\n(check-sat)\n'
        events, stderr, _, _ = self.successful(source, options=(
            '--unbounded-threshold=3', '--large-target=3', '--optimality-stall=1'))
        self.assertTrue(events[-1]['is_unbounded'], (events, stderr))
        self.assertEqual(events[-1]['unbounded_direction_backend'], 'sls')


if __name__ == '__main__':
    unittest.main()
