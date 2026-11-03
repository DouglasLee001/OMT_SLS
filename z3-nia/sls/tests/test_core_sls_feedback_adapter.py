"""The retired core-to-SLS switch must not reach new experiment commands."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'core_feedback_runtime_adapter', ROOT / 'experiment/runtime_adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class RemovedCoreSlsFeedbackAdapterTests(unittest.TestCase):
    def test_core_neighborhood_still_reaches_optimization(self):
        options = ('--core-neighborhood', '--core-neighborhood-stall=60')
        self.assertEqual(ADAPTER.validated_solver_options(options), options)
        command = ADAPTER.translate_command(
            ['old', 'case.nia', '--optimize', '--timeout=1200'],
            'embedded', '/solver', '/converter', options)
        self.assertEqual(command[-len(options):], list(options))

    def test_core_neighborhood_does_not_affect_import(self):
        command = ADAPTER.translate_command(
            ['python', '/converter', 'case.smt2', '-o', 'case.nia'],
            'embedded', '/solver', '/converter', ('--core-neighborhood',))
        self.assertNotIn('--core-neighborhood', command)

    def test_removed_switch_is_rejected_even_with_neighborhood_enabled(self):
        for prefix in ((), ('--core-neighborhood',)):
            for mode in ('on', 'off'):
                options = (*prefix, '--core-sls-feedback=' + mode)
                with self.subTest(options=options), self.assertRaises(ValueError):
                    ADAPTER.validated_solver_options(options)


if __name__ == '__main__':
    unittest.main()
