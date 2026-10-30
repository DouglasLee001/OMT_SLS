"""Source SMT backend/hint ablations preserve the full strict query."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = '''(set-logic QF_NIA)
(declare-const x Int)
(assert (and (<= 0 x) (<= x 2)))
(minimize x)
(check-sat)
'''


class SmtExchangeCliTests(unittest.TestCase):
    def test_backend_and_soft_hints_prove_original_optimum(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.smt2'
            source.write_text(SOURCE)
            for mode in ('backend', 'hints'):
                query = Path(d) / (mode + '.smt2')
                r = subprocess.run([str(BINARY), '--nia-ls', str(source), '--mode=optimal',
                                    '--initialize=off', '--timeout=2', '--optimality-stall=.001',
                                    '--optimality-timeout=.5', '--optimality-resume',
                                    '--smt-exchange=' + mode, '--optimality-query-output=' + str(query)],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
                final = json.loads(r.stdout.splitlines()[-1])
                self.assertTrue(final['is_optimal'], (final, r.stderr))
                self.assertEqual(final['objective_value'], '0')
                self.assertEqual(final['smt_exchange_mode'], mode)
                self.assertIn('omt_smt_exchange mode=' + mode, r.stderr)
                mapped = re.search(r'omt_smt_exchange mode=' + mode + r' mapped=(\d+)', r.stderr)
                self.assertIsNotNone(mapped)
                self.assertGreater(int(mapped.group(1)), 0) if mode == 'hints' else self.assertEqual(int(mapped.group(1)), 0)
                self.assertEqual(source.read_text(), SOURCE)
                # No incumbent coordinate equality is allowed in this saved source query.
                text = query.read_text()
                self.assertNotIn('(assert (= x 0))', text)
                checked = subprocess.run([str(BINARY), '-smt2', str(query)],
                                         capture_output=True, text=True, timeout=5)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertEqual(checked.stdout.strip(), 'unsat')

    def test_proof_mode_and_valid_ablation_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.smt2'
            source.write_text(SOURCE)
            for options in (['--mode=sls', '--smt-exchange=hints'],
                            ['--mode=optimal', '--smt-exchange=unrecognized']):
                r = subprocess.run([str(BINARY), '--nia-ls', str(source), *options, '--timeout=.1'],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 2)
                self.assertIn('smt-exchange', r.stderr)


if __name__ == '__main__':
    unittest.main()
