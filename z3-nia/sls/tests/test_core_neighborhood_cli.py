"""CLI and proof-boundary regressions for the experimental core exchange."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
FIXTURE = '''(set-logic QF_NIA)
(declare-const x Int)
(declare-const y Int)
(assert (and (<= 0 x) (<= x 1) (<= 0 y) (<= y 1) (= (+ x y) 1)))
(minimize x)
(check-sat)
'''
CUBIC = '(set-logic QF_NIA)\n' + ''.join(
    f'(declare-const {v} Int)\n(assert (and (<= 0 {v}) (<= {v} 1)))\n'
    for v in ('x', 'y', 'z')) + '(minimize (+ x y z (* (- 4) x y z)))\n(check-sat)\n'


class CoreNeighborhoodCliTests(unittest.TestCase):
    def run_case(self, *options, fixture=FIXTURE):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'joint.smt2'
            source.write_text(fixture)
            result = subprocess.run(
                [str(BINARY), '--nia-ls', str(source), '--mode=sls',
                 '--timeout=.3', '--initialize=off', *options],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            return json.loads(result.stdout.splitlines()[-1])

    def test_help_exposes_opt_in(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--core-neighborhood', result.stdout)

    def test_exchange_is_disabled_by_default(self):
        final = self.run_case()
        self.assertFalse(final['core_neighborhood_enabled'])
        self.assertEqual(final['core_neighborhood_queries'], 0)

    def test_soft_neighborhood_does_not_claim_a_proof(self):
        final = self.run_case('--core-neighborhood', '--core-neighborhood-stall=.001',
                              '--core-neighborhood-budget=.05')
        self.assertTrue(final['core_neighborhood_enabled'])
        self.assertFalse(final['is_optimal'])
        self.assertFalse(final['is_unbounded'])
        self.assertEqual(final['optimality_attempts'], 0)
        self.assertGreater(final['core_neighborhood_queries'], 0)
        self.assertEqual(final['core_neighborhood_errors'], 0)

    def test_explicit_default_timing_still_requires_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'joint.smt2'
            source.write_text(FIXTURE)
            result = subprocess.run(
                [str(BINARY), '--nia-ls', str(source), '--mode=sls', '--timeout=.1',
                 '--core-neighborhood-budget=60'],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('core-neighborhood', result.stderr)

    def test_real_conflict_feedback_reaches_sls_ordering(self):
        final = self.run_case('--core-neighborhood', '--core-neighborhood-stall=.000001',
                              '--core-neighborhood-budget=.05', fixture=CUBIC)
        self.assertGreater(final['core_neighborhood_queries'], 0)
        self.assertGreater(final['core_neighborhood_cores'], 0)
        self.assertGreater(final['core_neighborhood_priority_scans'], 0)
        self.assertEqual(final['core_neighborhood_errors'], 0)
        self.assertFalse(final['is_optimal'])

    def test_negative_budget_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'joint.smt2'
            source.write_text(FIXTURE)
            result = subprocess.run(
                [str(BINARY), '--nia-ls', str(source), '--mode=sls',
                 '--core-neighborhood', '--core-neighborhood-budget=-1'],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('core-neighborhood', result.stderr)


if __name__ == '__main__':
    unittest.main()
