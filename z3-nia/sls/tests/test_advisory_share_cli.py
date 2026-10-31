"""The shared advisory prefix is explicit, opt-in, and passed to both workers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = '(set-logic QF_NIA)\n(declare-const x Int)\n(minimize (* x x))\n(check-sat)\n'


class AdvisoryShareCliTests(unittest.TestCase):
    def invoke(self, options):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                '--initialize=off', '--timeout=1', *options],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(source.read_text(), SOURCE)
            return result

    def test_default_off_and_finite_and_resumed_forwarding(self):
        result = self.invoke(['--mode=sls'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1]).get('advisory_share'), 0)
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                options = ['--mode=optimal', '--convex-exchange', '--advisory-share=.5',
                           '--optimality-stall=.001', '--optimality-timeout=.3']
                if resumed:
                    options.append('--optimality-resume')
                result = self.invoke(options)
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertEqual(final['objective_value'], '0')
                self.assertEqual(final.get('advisory_share'), .5)
                self.assertIn('omt_advisory_prefix', result.stderr)

    def test_fraction_is_finite_strictly_between_zero_and_one_and_requires_proof(self):
        for value in ('', '0', '-1', '1', '2', 'nan', 'inf', '1x', ' .5'):
            with self.subTest(value=value):
                result = self.invoke(['--mode=optimal', '--advisory-share=' + value])
                self.assertEqual(result.returncode, 2)
                self.assertIn('fraction', result.stderr)
        result = self.invoke(['--mode=sls', '--advisory-share=.5'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('--prove-optimal', result.stderr)

    def test_help_and_frozen_adapter_forward_only_for_optimization(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--advisory-share=FRACTION', result.stdout)
        spec = importlib.util.spec_from_file_location('advisory_adapter', ROOT / 'experiment/runtime_adapter.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        options = ('--advisory-share=.5',)
        self.assertEqual(adapter.validated_solver_options(options), options)
        optimized = adapter.translate_command(['/native', '/input.nia', '--optimize', '--prove-optimal'],
                        'embedded', '/solver', '/converter.py', options)
        self.assertEqual(optimized[-1], '--advisory-share=.5')
        imported = adapter.translate_command(['python3', '/converter.py', '/source.smt2', '-o', '/input.nia'],
                        'embedded', '/solver', '/converter.py', options)
        self.assertNotIn('--advisory-share=.5', imported)
        invalid_options = [('--advisory-share',), ('--advisory-share=.5', '--advisory-share=.5')]
        invalid_options += [('--advisory-share=' + value,) for value in ('', '0', '1', '-1', 'nan', 'inf', '1x', ' .5')]
        for invalid in invalid_options:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapter.validated_solver_options(invalid)


if __name__ == '__main__':
    unittest.main()
