"""The source SMT backend preserves the full strict query."""
import json
import os
import re
from pathlib import Path
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
    def test_all_four_switch_combinations_keep_one_complete_chain_check(self):
        for smt in (False, True):
            for bv in (False, True):
                for resume in (False, True):
                    with self.subTest(smt=smt, bv=bv, resume=resume), tempfile.TemporaryDirectory() as d:
                        source = Path(d) / 'source.smt2'
                        source.write_text(SOURCE)
                        options = ['--smt-exchange=backend'] if smt else []
                        if resume:
                            options.append('--optimality-resume')
                        result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                            '--mode=optimal', '--initialize=off', '--timeout=2',
                            '--optimality-stall=.001', '--optimality-timeout=.5',
                            '--optimality-bv-seconds=' + ('120' if bv else '0'), *options],
                            capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                        final = json.loads(result.stdout.splitlines()[-1])
                        self.assertTrue(final['is_optimal'], (final, result.stderr))
                        self.assertEqual(final['objective_value'], '0')
                        self.assertEqual(final['smt_exchange_mode'], 'backend' if smt else 'disabled')
                        self.assertEqual(final['optimality_bv_seconds'], 120 if bv else 0)
                        configured = re.findall(r'omt_tactic_chain phase=configured smt_ms=(\d+) bv_ms=(\d+)', result.stderr)
                        self.assertEqual(len(configured), 1, result.stderr)
                        smt_ms, bv_ms = map(int, configured[0])
                        self.assertTrue(0 < smt_ms <= 5000 if smt else smt_ms == 0)
                        self.assertEqual(bv_ms, 120000 if bv else 0)
                        self.assertEqual(result.stderr.count('omt_tactic_chain phase=check '), 1, result.stderr)
                        self.assertEqual(result.stderr.count('omt_tactic_chain phase=result '), 1, result.stderr)
                        self.assertIn('answer=unsat', result.stderr)
                        self.assertNotIn('omt_smt_exchange_preflight answer=', result.stderr)
                        self.assertNotIn('omt_optimality_bv phase=check', result.stderr)
                        self.assertEqual(source.read_text(), SOURCE)

    def test_backend_proves_original_optimum(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.smt2'
            source.write_text(SOURCE)
            for resume in (False, True):
                query = Path(d) / ('resumed.smt2' if resume else 'finite.smt2')
                r = subprocess.run([str(BINARY), '--nia-ls', str(source), '--mode=optimal',
                                    '--initialize=off', '--timeout=2', '--optimality-stall=.001',
                                    '--optimality-timeout=.5',
                                    *(['--optimality-resume'] if resume else []),
                                    '--smt-exchange=backend', '--optimality-query-output=' + str(query)],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
                final = json.loads(r.stdout.splitlines()[-1])
                self.assertTrue(final['is_optimal'], (final, r.stderr))
                self.assertEqual(final['objective_value'], '0')
                self.assertEqual(final['smt_exchange_mode'], 'backend')
                configured = re.search(r'omt_tactic_chain phase=configured smt_ms=(\d+) bv_ms=0', r.stderr)
                self.assertIsNotNone(configured, r.stderr)
                self.assertTrue(0 < int(configured.group(1)) <= 5000)
                self.assertIn('omt_tactic_chain phase=check', r.stderr)
                self.assertIn('omt_tactic_chain phase=result', r.stderr)
                self.assertNotIn('omt_smt_exchange_preflight answer=', r.stderr)
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
            for options in (['--mode=sls', '--smt-exchange=backend'],
                            ['--mode=optimal', '--smt-exchange=unrecognized']):
                r = subprocess.run([str(BINARY), '--nia-ls', str(source), *options, '--timeout=.1'],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 2)
                self.assertIn('smt-exchange', r.stderr)


if __name__ == '__main__':
    unittest.main()
