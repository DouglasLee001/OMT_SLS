"""Pure proof mode forwards only the selected backend, never extra helpers."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'plain_z3_adapter', ROOT / 'experiment/runtime_adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class PlainZ3AdapterTests(unittest.TestCase):
    def test_pure_mode_reaches_only_optimization(self):
        options = ('--plain-z3-proof',)
        self.assertEqual(ADAPTER.validated_solver_options(options), options)
        optimized = ADAPTER.translate_command(
            ['old', 'case.nia', '--optimize', '--prove-optimal'],
            'embedded', '/solver', '/converter', options)
        self.assertEqual(optimized[-1], '--plain-z3-proof')
        imported = ADAPTER.translate_command(
            ['python', '/converter', 'case.smt2', '-o', 'case.nia'],
            'embedded', '/solver', '/converter', options)
        self.assertNotIn('--plain-z3-proof', imported)

    def test_pure_mode_rejects_proof_auxiliaries(self):
        for helper in ('--guarded-product', '--optimality-bv-seconds=120',
                       '--smt-exchange=hints',
                       '--smt-exchange=backend', '--objective-slice',
                       '--isolate-exchange-context', '--advisory-share=.5'):
            with self.subTest(helper=helper), self.assertRaisesRegex(
                    ValueError, 'plain-z3-proof.*incompatible'):
                ADAPTER.validated_solver_options(('--plain-z3-proof', helper))

    def test_plain_mode_preserves_sls_and_scheduling_options(self):
        options = ('--plain-z3-proof', '--slack-repair', '--optimality-tail')
        self.assertEqual(ADAPTER.validated_solver_options(options), options)

    def test_valued_or_duplicate_mode_is_rejected(self):
        for options in (('--plain-z3-proof=on',),
                        ('--plain-z3-proof', '--plain-z3-proof')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ADAPTER.validated_solver_options(options)


if __name__ == '__main__':
    unittest.main()
