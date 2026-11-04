"""Retired core-neighborhood controls must not reach new experiments."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'core_feedback_runtime_adapter', ROOT / 'experiment/runtime_adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class RemovedCoreNeighborhoodAdapterTests(unittest.TestCase):
    def test_remaining_search_controls_reach_optimization(self):
        options = ('--smt-exchange=backend', '--optimality-bv-seconds=120',
                   '--guarded-product')
        self.assertEqual(ADAPTER.validated_solver_options(options), options)
        command = ADAPTER.translate_command(
            ['old', 'case.nia', '--optimize', '--timeout=1200'],
            'embedded', '/solver', '/converter', options)
        self.assertEqual(command[-len(options):], list(options))

    def test_remaining_search_controls_do_not_affect_import(self):
        command = ADAPTER.translate_command(
            ['python', '/converter', 'case.smt2', '-o', 'case.nia'],
            'embedded', '/solver', '/converter',
            ('--guarded-product', '--smt-exchange=backend'))
        self.assertNotIn('--guarded-product', command)
        self.assertNotIn('--smt-exchange=backend', command)

    def test_removed_neighborhood_and_feedback_controls_are_rejected(self):
        for prefix in ((), ('--guarded-product',)):
            for option in ('--core-neighborhood', '--core-neighborhood-stall=60',
                           '--core-neighborhood-budget=60',
                           '--core-sls-feedback=on', '--core-sls-feedback=off'):
                options = (*prefix, option)
                with self.subTest(options=options), self.assertRaisesRegex(
                        ValueError, 'unsupported solver option'):
                    ADAPTER.validated_solver_options(options)


if __name__ == '__main__':
    unittest.main()
