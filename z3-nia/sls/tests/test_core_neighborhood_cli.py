"""CLI removal and global-proof regressions after deleting core neighborhoods."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
FIXTURE = """(set-logic QF_NIA)
(declare-const x Int)
(declare-const y Int)
(assert (and (<= 0 x) (<= x 1) (<= 0 y) (<= y 1) (= (+ x y) 1)))
(minimize x)
(check-sat)
"""


class RemovedCoreNeighborhoodCliTests(unittest.TestCase):
    def invoke(self, *options, mode='sls'):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'joint.smt2'
            source.write_text(FIXTURE)
            return subprocess.run(
                [str(BINARY), '--nia-ls', str(source), '--mode=' + mode,
                 '--timeout=1', '--initialize=off', *options],
                capture_output=True, text=True, timeout=10)

    def run_case(self, *options, mode='sls'):
        result = self.invoke(*options, mode=mode)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        return json.loads(result.stdout.splitlines()[-1])

    def test_help_has_no_removed_neighborhood_options(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('--core-neighborhood', result.stdout)
        self.assertNotIn('--core-sls-feedback', result.stdout)
        self.assertIn('--prove-optimal', result.stdout)

    def test_output_has_no_removed_neighborhood_statistics(self):
        final = self.run_case()
        self.assertFalse(any(key.startswith(('core_neighborhood_', 'core_sls_'))
                             for key in final), final)
        self.assertFalse(final['is_optimal'])
        self.assertFalse(final['is_unbounded'])

    def test_removed_neighborhood_switches_are_rejected(self):
        for option in ('--core-neighborhood', '--core-neighborhood-stall=60',
                       '--core-neighborhood-budget=60', '--core-neighborhood-stall=nan',
                       '--core-neighborhood-budget=-1', '--core-sls-feedback=on',
                       '--core-sls-feedback=off'):
            with self.subTest(option=option):
                result = self.invoke(option)
                self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                self.assertIn('unknown option', result.stderr)

    def test_global_optimality_proof_remains_available(self):
        final = self.run_case('--prove-optimal', '--optimality-stall=.001',
                              '--optimality-timeout=.2', mode='optimal')
        self.assertTrue(final['is_optimal'], final)
        self.assertEqual(final['objective_value'], '0')
        self.assertGreater(final['optimality_attempts'], 0)
        self.assertFalse(any(key.startswith('core_neighborhood_') for key in final))


if __name__ == '__main__':
    unittest.main()
