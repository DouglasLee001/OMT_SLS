"""The convex source proof must be opt-in, replayable, and protect inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
CONVEX = '''(set-logic QF_NIA)
(declare-const x Int)
(declare-const cost Int)
(assert (= cost (* (- (* 2 x) 1) (- (* 2 x) 1))))
(minimize cost)
(check-sat)
'''


class ConvexExchangeCliTests(unittest.TestCase):
    def test_convex_source_certificate_proves_integer_optimum(self):
        with tempfile.TemporaryDirectory() as d:
            source, query = Path(d) / 'input.smt2', Path(d) / 'proof.smt2'
            model = Path(d) / 'model.tsv'
            source.write_text(CONVEX)
            r = subprocess.run([str(BINARY), '--nia-ls', str(source), '--mode=optimal',
                                '--timeout=2', '--initialize=off', '--optimality-resume',
                                '--optimality-stall=.001', '--optimality-timeout=.5',
                                '--convex-exchange', '--optimality-query-output=' + str(query),
                                '--model-output=' + str(model)],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            final = json.loads(r.stdout.splitlines()[-1])
            self.assertTrue(final['is_optimal'], (final, r.stderr))
            self.assertEqual(final['objective_value'], '1')
            self.assertIn('convex', final['optimality_reason'])
            self.assertTrue(Path(str(query) + '.convex.json').is_file())
            verified = Path(d) / 'independent.json'
            checked = subprocess.run(
                [sys.executable, '-B', str(ROOT / 'experiment/verify_convex_certificate.py'),
                 '--source', str(source), '--certificate', str(query) + '.convex.json',
                 '--incumbent-model', str(model), '--timeout', '2', '--output', str(verified)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, (checked.stdout, checked.stderr))
            self.assertTrue(json.loads(verified.read_text())['optimality_verified'])

    def test_certificate_output_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as d:
            query = Path(d) / 'proof.smt2'
            source = Path(str(query) + '.convex.json')
            source.write_text(CONVEX)
            r = subprocess.run([str(BINARY), '--nia-ls', str(source), '--mode=optimal',
                                '--convex-exchange', '--optimality-query-output=' + str(query)],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 2)
            self.assertIn('protected input', r.stderr)
            self.assertEqual(source.read_text(), CONVEX)

    def test_proof_exchange_requires_proof_mode(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.smt2'
            source.write_text(CONVEX)
            r = subprocess.run([str(BINARY), '--nia-ls', str(source), '--mode=sls',
                                '--convex-exchange', '--timeout=.1'],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 2)
            self.assertIn('requires --prove-optimal', r.stderr)


if __name__ == '__main__':
    unittest.main()
