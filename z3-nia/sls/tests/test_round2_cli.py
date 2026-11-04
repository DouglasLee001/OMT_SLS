"""Optional second-round search/proof controls preserve full source solving."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = '''(set-logic QF_NIA)
(declare-const x Int)
(assert (and (<= 0 x) (<= x 1)))
(minimize (+ (* 4 x x) (* (- 4) x) 1))
(check-sat)
'''


class Round2CliTests(unittest.TestCase):
    def invoke(self, folder, options, seconds='1'):
        source = folder / 'source.smt2'
        source.write_text(SOURCE)
        result = subprocess.run([str(BINARY), '--nia-ls', str(source),
            '--initialize=off', '--timeout=' + seconds, *options],
            text=True, capture_output=True, timeout=15)
        self.assertEqual(source.read_text(), SOURCE)
        return result

    def assert_isolated_source_proof(self, mode):
        for resume in (False, True):
            with self.subTest(mode=mode, resume=resume), tempfile.TemporaryDirectory() as d:
                folder = Path(d)
                proof = folder / 'proof.smt2'
                options = ['--mode=optimal', '--smt-exchange=' + mode,
                    '--isolate-exchange-context', '--optimality-stall=.001',
                    '--optimality-timeout=.5', '--optimality-query-output=' + str(proof)]
                if resume:
                    options.append('--optimality-resume')
                result = self.invoke(folder, options, '2')
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(final['objective_value'], '1')
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertIs(final['isolate_exchange_context_enabled'], True)
                self.assertEqual(final['smt_exchange_mode'], mode)
                self.assertIn('omt_smt_exchange', result.stderr)
                self.assertTrue(proof.is_file())
                audit = subprocess.run([str(BINARY), str(proof)], text=True,
                                       capture_output=True, timeout=5)
                self.assertEqual(audit.returncode, 0, audit.stderr)
                self.assertEqual(audit.stdout.strip(), 'unsat')

    def test_isolated_backend_reaches_source_optimum_in_finite_and_resumed_workers(self):
        self.assert_isolated_source_proof('backend')

    def test_slack_search_is_available_without_a_proof_engine(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.invoke(Path(d), ['--mode=sls', '--slack-repair'], '.1')
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            final = json.loads(result.stdout.splitlines()[-1])
            self.assertIs(final.get('slack_repair_enabled'), True)
            self.assertFalse(final['is_optimal'])

    def test_help_advertises_each_frozen_experiment_control(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        for option in ('--isolate-exchange-context', '--slack-repair'):
            self.assertIn(option, result.stdout)

    def test_new_controls_are_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.invoke(Path(d), ['--mode=sls'], '.1')
            self.assertEqual(result.returncode, 0, result.stderr)
            final = json.loads(result.stdout.splitlines()[-1])
            for key in ('isolate_exchange_context_enabled',
                        'slack_repair_enabled'):
                self.assertIs(final.get(key), False, key)

    def test_proof_controls_require_their_backends(self):
        with tempfile.TemporaryDirectory() as d:
            for options, message in [
                (['--mode=optimal', '--isolate-exchange-context'], 'requires'),
                (['--mode=sls', '--smt-exchange=backend', '--isolate-exchange-context'], '--prove-optimal')]:
                result = self.invoke(Path(d), options)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)


if __name__ == '__main__':
    unittest.main()
