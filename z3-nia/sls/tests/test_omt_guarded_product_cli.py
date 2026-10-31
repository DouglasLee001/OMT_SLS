"""Guarded cubic consequences are an opt-in source-bound proof phase."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = '''(set-logic QF_NIA)
(declare-const A Int) (declare-const C Int) (declare-const x Int)
(declare-const l Int) (declare-const l0 Int) (declare-const g Int)
(assert (>= x 1))
(assert (<= (+ C (* A x)) 0))
(assert (or (and (>= l 0) (>= l0 0) (>= (+ l0 (* C l)) 1)
                 (= (* A l) l0)) (>= g 0)))
(minimize g)
(check-sat)
'''


class GuardedProductAdapterTests(unittest.TestCase):
    @staticmethod
    def adapter():
        spec = importlib.util.spec_from_file_location(
            'guarded_product_adapter', ROOT / 'experiment/runtime_adapter.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        return adapter

    def test_flag_is_forwarded_only_to_optimization(self):
        adapter = self.adapter()
        options = ('--guarded-product',)
        self.assertEqual(adapter.validated_solver_options(options), options)
        optimized = adapter.translate_command(
            ['/native', '/input.nia', '--optimize', '--prove-optimal'],
            'embedded', '/solver', '/converter.py', options)
        self.assertEqual(optimized[-1], options[0])
        imported = adapter.translate_command(
            ['python3', '/converter.py', '/source.smt2', '-o', '/input.nia'],
            'embedded', '/solver', '/converter.py', options)
        self.assertNotIn(options[0], imported)

    def test_values_and_duplicate_flags_are_rejected(self):
        adapter = self.adapter()
        for options in (('--guarded-product=1',),
                        ('--guarded-product', '--guarded-product')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                adapter.validated_solver_options(options)


class GuardedProductCliTests(unittest.TestCase):
    def invoke(self, options, seconds='1', source_text=SOURCE):
        with tempfile.TemporaryDirectory(prefix='omt-guarded-cli-') as directory:
            source = Path(directory) / 'source.smt2'
            query = Path(directory) / 'proof.smt2'
            source.write_text(source_text)
            result = subprocess.run([
                str(BINARY), '--nia-ls', str(source), '--initialize=off',
                '--timeout=' + seconds,
                '--optimality-query-output=' + str(query), *options],
                text=True, capture_output=True, timeout=5)
            self.assertEqual(source.read_text(), source_text)
            return result

    def test_default_off_does_not_invoke_new_phase(self):
        result = self.invoke(['--optimize', '--prove-optimal'], '.04')
        self.assertEqual(result.returncode, 0, result.stderr)
        final = json.loads(result.stdout.splitlines()[-1])
        self.assertFalse(final['optimality_guarded_product'])
        self.assertNotIn('omt_guarded_product', result.stderr)

    def test_finite_and_resumed_workers_use_native_source_bound_phase(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                options = ['--optimize', '--prove-optimal', '--guarded-product',
                           '--optimality-stall=.001', '--optimality-timeout=.5']
                if resumed:
                    options.append('--optimality-resume')
                result = self.invoke(options)
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertTrue(final['optimality_guarded_product'])
                self.assertEqual(final['objective_value'], '0')
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertIn('omt_guarded_product', result.stderr)
                self.assertIn('answer=unsat', result.stderr)

    def test_opt_in_requires_proof_and_help_explains_it(self):
        result = self.invoke(['--optimize', '--guarded-product'], '.04')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--prove-optimal', result.stderr)
        help_result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                     text=True, capture_output=True, timeout=3)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn('--guarded-product', help_result.stdout)

    def test_relaxation_sat_keeps_original_bv_query_and_proof(self):
        # Without square cuts the Real proxy for x*x may be negative. That
        # model cannot stop the unchanged integer source check proving x*x>=0.
        square = '(set-logic QF_NIA)\n(declare-const x Int)\n(minimize (* x x))\n(check-sat)\n'
        result = self.invoke(['--optimize', '--prove-optimal', '--guarded-product',
                              '--optimality-bv-seconds=.1',
                              '--optimality-stall=.001', '--optimality-timeout=.5'],
                             source_text=square)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        final = json.loads(result.stdout.splitlines()[-1])
        self.assertTrue(final['is_optimal'], (final, result.stderr))
        self.assertEqual(final['objective_value'], '0')
        self.assertRegex(result.stderr, r'omt_guarded_product[^\n]*answer=sat')
        self.assertIn('omt_optimality_bv phase=check', result.stderr)


if __name__ == '__main__':
    unittest.main()
