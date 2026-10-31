"""Opt-in bounded native BV is forwarded only to actual OMT proof searches."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
SOURCE = '(set-logic QF_NIA)\n(declare-const x Int)\n(minimize (* x x))\n(check-sat)\n'


def query_assertions(source):
    # Let-sharing is a printer choice, so compare the complete formula tree.
    sys.path.insert(0, str(ROOT.parent))
    from NIA_LS.converter.omt_to_ls import _commands

    def expand(node, environment):
        if isinstance(node, str):
            return environment.get(node, node)
        if node and node[0] == 'let':
            local = dict(environment)
            local.update({name: expand(value, environment) for name, value in node[1]})
            return expand(node[2], local)
        return [expand(child, environment) for child in node]

    return [expand(form[1], {}) for form in _commands(source) if form and form[0] == 'assert']


class NativeBvCliTests(unittest.TestCase):
    def invoke(self, options, seconds='.08'):
        with tempfile.TemporaryDirectory(prefix='omt-bv-cli-') as directory:
            source = Path(directory) / 'source.smt2'
            query = Path(directory) / 'proof.smt2'
            source.write_text(SOURCE)
            result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                '--initialize=off', '--timeout=' + seconds,
                '--optimality-query-output=' + str(query), *options],
                text=True, capture_output=True, timeout=4)
            self.assertEqual(source.read_text(), SOURCE)
            return result, query.read_text() if query.exists() else None

    def test_default_off_and_explicit_zero_keep_original_worker(self):
        for option in ([], ['--optimality-bv-seconds=0']):
            with self.subTest(option=option):
                result, _ = self.invoke(['--optimize', '--prove-optimal', *option])
                self.assertEqual(result.returncode, 0, result.stderr)
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(final.get('optimality_bv_seconds'), 0)
                self.assertNotIn('omt_optimality_bv', result.stderr)

    def test_finite_and_persistent_workers_receive_native_bv_budget(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                options = ['--optimize', '--prove-optimal', '--optimality-bv-seconds=.1',
                           '--optimality-stall=.001', '--optimality-timeout=.5']
                if resumed:
                    options.append('--optimality-resume')
                result, query = self.invoke(options, '1')
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                final = json.loads(result.stdout.splitlines()[-1])
                self.assertTrue(final['is_optimal'], (final, result.stderr))
                self.assertEqual(final['objective_value'], '0')
                self.assertEqual(final.get('optimality_bv_seconds'), .1)
                self.assertIn('omt_optimality_bv phase=check', result.stderr)
                self.assertIn('fallback=default', result.stderr)
                self.assertEqual(query_assertions(query), [['<', ['*', 'x', 'x'], '0']])

    def test_bad_values_and_missing_proof_are_rejected(self):
        for value in ('', '-1', '.0001', '1200.001', 'nan', 'inf', '1x', ' .1', '1_0'):
            with self.subTest(value=value):
                result, query = self.invoke(['--optimize', '--prove-optimal',
                                            '--optimality-bv-seconds=' + value])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('BV seconds', result.stderr)
                self.assertIsNone(query)
        result, _ = self.invoke(['--optimize', '--optimality-bv-seconds=.1'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('--prove-optimal', result.stderr)

    def test_help_explains_the_opt_in_wall_budget(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--optimality-bv-seconds=SECONDS', result.stdout)
        self.assertIn('wall', result.stdout)


class NativeBvAdapterTests(unittest.TestCase):
    @staticmethod
    def adapter():
        spec = importlib.util.spec_from_file_location('native_bv_adapter', ROOT / 'experiment/runtime_adapter.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        return adapter

    def test_budget_is_validated_and_forwarded_only_to_optimization(self):
        adapter = self.adapter()
        for value in ('0', '.001', '.1', '120', '1200', '1e-3'):
            with self.subTest(value=value):
                options = ('--optimality-bv-seconds=' + value,)
                self.assertEqual(adapter.validated_solver_options(options), options)
                optimized = adapter.translate_command(['/native', '/input.nia', '--optimize', '--prove-optimal'],
                    'embedded', '/solver', '/converter.py', options)
                self.assertEqual(optimized[-1], options[0])
                imported = adapter.translate_command(['python3', '/converter.py', '/source.smt2', '-o', '/input.nia'],
                    'embedded', '/solver', '/converter.py', options)
                self.assertNotIn(options[0], imported)

    def test_invalid_and_duplicate_budgets_are_rejected(self):
        adapter = self.adapter()
        invalid = [('--optimality-bv-seconds',), ('--optimality-bv-seconds=.1', '--optimality-bv-seconds=.2')]
        invalid += [('--optimality-bv-seconds=' + value,) for value in
                    ('', '-1', '.0001', '1200.001', 'nan', 'inf', '1x', ' .1', '1_0', '1e-9999', '-1e-9999')]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                adapter.validated_solver_options(options)


if __name__ == '__main__':
    unittest.main()
