"""Soft incumbent initialization is removed; source proof backends remain usable."""
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
(declare-const x Int)
(assert (and (<= 0 x) (<= x 2)))
(minimize x)
(check-sat)
'''


class RemovedSoftHintsTests(unittest.TestCase):
    def adapter(self):
        spec = importlib.util.spec_from_file_location(
            'removed_soft_hints_adapter', ROOT / 'experiment/runtime_adapter.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_help_advertises_only_the_supported_smt_exchange_backend(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--smt-exchange=backend', result.stdout)
        self.assertNotIn('backend|hints', result.stdout)
        self.assertNotIn('--smt-exchange=hints', result.stdout)

    def test_cli_rejects_removed_hints_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                '--mode=optimal', '--initialize=off', '--timeout=.1',
                '--smt-exchange=hints'], text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
            self.assertIn('invalid --smt-exchange', result.stderr)
            self.assertIn('expected backend', result.stderr)
            self.assertEqual(source.read_text(), SOURCE)

    def test_adapter_rejects_removed_mode_and_forwards_backend_with_helpers(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'unsupported solver option'):
            adapter.validated_solver_options(('--smt-exchange=hints',))
        options = ('--smt-exchange=backend', '--isolate-exchange-context',
                   '--objective-slice', '--objective-slice-budget=.1',
                   '--guarded-product', '--advisory-share=.5',
                   '--optimality-bv-seconds=.01', '--slack-repair', '--optimality-tail')
        self.assertEqual(adapter.validated_solver_options(options), options)
        command = adapter.translate_command(
            ['/native', '/source.nia', '--optimize', '--prove-optimal'],
            'embedded', '/solver', '/converter', options)
        self.assertEqual(command[-len(options):], list(options))

    def test_backend_and_other_proof_routes_keep_original_source_semantics(self):
        routes = ((), ('--plain-z3-proof',), ('--smt-exchange=backend',),
                  ('--smt-exchange=backend', '--isolate-exchange-context'),
                  ('--smt-exchange=backend', '--objective-slice'),
                  ('--smt-exchange=backend', '--advisory-share=.5'),
                  ('--smt-exchange=backend', '--optimality-bv-seconds=.01'),
                  ('--smt-exchange=backend', '--guarded-product'),
                  ('--optimality-cutset',))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            for route in routes:
                with self.subTest(route=route):
                    result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                        '--mode=optimal', '--initialize=off', '--timeout=1',
                        '--optimality-stall=.000000001', '--optimality-timeout=.3',
                        *route], text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                    final = json.loads(result.stdout.splitlines()[-1])
                    self.assertTrue(final['is_optimal'], (final, result.stderr))
                    self.assertEqual(final['objective_value'], '0')
                    self.assertNotIn('mapped=', result.stderr)
                    self.assertNotIn('set_initial_value', result.stderr)
                    self.assertNotIn('smt_exchange hints', result.stderr)
                    if route == ('--smt-exchange=backend',):
                        self.assertIn('arithmetic=2 preflight_limit_seconds=5', result.stderr)
            self.assertEqual(source.read_text(), SOURCE)

    def test_proof_source_has_no_soft_initializer_and_retains_unbounded_guidance(self):
        for name in ('nia_omt_optimality.h', 'nia_omt_optimality.cpp',
                     'nia_omt_optimize.cpp'):
            with self.subTest(source=name):
                text = (ROOT / 'sls/src' / name).read_text()
                for removed in ('set_initial_value', 'incumbent_hint', 'mapped='):
                    self.assertFalse(removed in text, name + ' still contains ' + removed)
        optimize = (ROOT / 'sls/src/nia_omt_optimize.cpp').read_text()
        self.assertIn('ray_hints->improvement_activity', optimize)
        self.assertIn('prove_omt_unbounded(solver,best,allowance,ray_hints.get())', optimize)


if __name__ == '__main__':
    unittest.main()
