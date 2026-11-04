"""Removed custom convex controls are rejected while supported proofs still run."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
REMOVED = ('--convex-exchange', '--integer-convex-exchange',
           '--integer-affine-convex-exchange')
SOURCE = '(set-logic QF_NIA)\n(declare-const x Int)\n(assert (= x 0))\n(minimize x)\n(check-sat)\n'


class RemovedConvexControlsTests(unittest.TestCase):
    def adapter(self):
        spec = importlib.util.spec_from_file_location(
            'removed_convex_adapter', ROOT / 'experiment/runtime_adapter.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_help_omits_removed_controls_and_keeps_supported_controls(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in REMOVED:
            self.assertNotIn(flag, result.stdout)
        for flag in ('--guarded-product', '--smt-exchange', '--plain-z3-proof'):
            self.assertIn(flag, result.stdout)

    def test_cli_rejects_removed_flags_and_valued_forms(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            for flag in REMOVED:
                for option in (flag, flag + '=true'):
                    with self.subTest(option=option):
                        result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                            '--mode=optimal', '--initialize=off', '--timeout=.1', option],
                            text=True, capture_output=True, timeout=5)
                        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                        self.assertIn('unknown option: ' + option, result.stderr)
            self.assertEqual(source.read_text(), SOURCE)

    def test_adapter_rejects_removed_flags_and_valued_forms(self):
        adapter = self.adapter()
        for flag in REMOVED:
            for option in (flag, flag + '=true'):
                with self.subTest(option=option), self.assertRaisesRegex(
                        ValueError, 'unsupported solver option'):
                    adapter.validated_solver_options((option,))

    def test_supported_proof_routes_still_prove_source_optimum(self):
        routes = ((), ('--guarded-product',), ('--smt-exchange=backend',),
                  ('--optimality-cutset',), ('--plain-z3-proof',))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.smt2'
            source.write_text(SOURCE)
            for route in routes:
                with self.subTest(route=route):
                    result = subprocess.run([str(BINARY), '--nia-ls', str(source),
                        '--mode=optimal', '--initialize=off', '--timeout=1',
                        '--optimality-stall=.000000001', '--optimality-timeout=.3', *route],
                        text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                    final = json.loads(result.stdout.splitlines()[-1])
                    self.assertTrue(final['is_optimal'], (final, result.stderr))
                    self.assertEqual(final['objective_value'], '0')
                    for key in ('convex_exchange_enabled', 'integer_convex_exchange_enabled',
                                'integer_affine_convex_exchange_enabled'):
                        self.assertNotIn(key, final)
            self.assertEqual(source.read_text(), SOURCE)


if __name__ == '__main__':
    unittest.main()
