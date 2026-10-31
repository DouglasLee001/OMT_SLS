"""The optional tail schedule preserves the total budget and is frozen explicitly."""
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


class TailCliTests(unittest.TestCase):
    def invoke(self, options):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                '--initialize=off', '--timeout=1', *options],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(source.read_text(), SOURCE)
            return result

    def test_tail_requires_balanced_and_optimality(self):
        for options in [('--mode=sls', '--optimality-tail'),
                        ('--mode=optimal', '--optimality-tail')]:
            with self.subTest(options=options):
                result = self.invoke(options)
                self.assertEqual(result.returncode, 2)
                self.assertIn('--optimality-tail', result.stderr)
                if options[0] == '--mode=optimal':
                    self.assertIn('requires --optimality-balanced', result.stderr)

    def test_default_off_and_enabled_finite_and_resumed_workers(self):
        result = self.invoke(['--mode=sls'])
        self.assertEqual(result.returncode, 0, result.stderr)
        final = json.loads(result.stdout.splitlines()[-1])
        self.assertIs(final.get('optimality_tail'), False)
        self.assertEqual(final.get('optimality_tail_caps'), 0)
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                options = ['--mode=optimal', '--optimality-balanced', '--optimality-tail',
                           '--optimality-stall=.001', '--optimality-timeout=.2']
                if resumed:
                    options.append('--optimality-resume')
                result = self.invoke(options)
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertEqual(final['objective_value'], '0')
                self.assertIs(final.get('optimality_tail'), True)
                self.assertGreaterEqual(final.get('optimality_tail_caps', -1), 0)

    def test_freezable_help_and_adapter_optimization_only_forwarding(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--optimality-tail', result.stdout)
        spec = importlib.util.spec_from_file_location('tail_adapter', ROOT / 'experiment/runtime_adapter.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        options = ('--optimality-tail',)
        self.assertEqual(adapter.validated_solver_options(options), options)
        optimized = adapter.translate_command(['/native', '/input.nia', '--optimize',
                        '--prove-optimal', '--optimality-balanced'],
                        'embedded', '/solver', '/converter.py', options)
        self.assertEqual(optimized[-1], '--optimality-tail')
        imported = adapter.translate_command(['python3', '/converter.py', '/source.smt2',
                        '-o', '/input.nia'], 'embedded', '/solver', '/converter.py', options)
        self.assertNotIn('--optimality-tail', imported)
        for invalid in [('--optimality-tail=on',), ('--optimality-tail', '--optimality-tail')]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapter.validated_solver_options(invalid)


if __name__ == '__main__':
    unittest.main()
