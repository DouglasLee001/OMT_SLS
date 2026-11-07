"""SLS ablations are independent optimization controls, not proof switches."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get('NIA_EMBEDDED_Z3', ROOT / 'build/z3'))
FLAGS = {
    '--no-sls-objective-neighborhoods': 'sls_objective_neighborhoods_enabled',
}
SOURCE = '''(set-logic QF_NIA)
(declare-const x Int)
(assert (and (<= 0 x) (<= x 3)))
(minimize (* (- x 1) (- x 1)))
(check-sat)
'''


class SlsAblationCliTests(unittest.TestCase):
    def invoke(self, options=(), execution=('--mode=sls',)):
        with tempfile.TemporaryDirectory(prefix='omt-sls-ablation-cli-') as d:
            source = Path(d) / 'input.smt2'
            source.write_text(SOURCE)
            result = subprocess.run(
                [str(BINARY), '--nia-ls', str(source), *execution,
                 '--initialize=off', '--timeout=.1', *options],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(source.read_text(), SOURCE)
            return result

    def final(self, options=()):
        result = self.invoke(options)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(events, result.stdout)
        return events[-1]

    def test_all_sls_mechanisms_remain_enabled_by_default(self):
        final = self.final()
        for key in FLAGS.values():
            self.assertIs(final.get(key), True, key)

    def test_each_ablation_disables_only_its_selected_search_mechanism(self):
        for flag, disabled in FLAGS.items():
            with self.subTest(flag=flag):
                final = self.final((flag,))
                for key in FLAGS.values():
                    self.assertIs(final.get(key), key != disabled, key)
                self.assertFalse(final['is_optimal'])
                self.assertFalse(final['is_unbounded'])

    def test_disabled_mechanism_has_no_search_calls(self):
        counters = {
            '--no-sls-objective-neighborhoods': ('sls_objective_neighborhood_evaluations',),
        }
        for flag, keys in counters.items():
            with self.subTest(flag=flag):
                final = self.final((flag,))
                for key in keys:
                    self.assertEqual(final.get(key), 0, key)

    def test_help_advertises_all_search_ablations(self):
        result = subprocess.run([str(BINARY), '--nia-ls', '--help'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        for flag in FLAGS:
            self.assertIn(flag, result.stdout)
        self.assertNotIn('--no-sls-block-repair', result.stdout)
        self.assertNotIn('--no-sls-plateau', result.stdout)

    def test_removed_plateau_control_is_rejected(self):
        result = self.invoke(('--no-sls-plateau',))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn('unknown option', result.stderr)

    def test_removed_plateau_metadata_is_absent(self):
        final = self.final()
        for key in ('sls_plateau_enabled', 'sls_plateau_calls', 'sls_plateau_moves'):
            self.assertNotIn(key, final)

    def test_removed_block_repair_control_is_rejected(self):
        result = self.invoke(('--no-sls-block-repair',))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn('unknown option', result.stderr)

    def test_removed_block_repair_metadata_is_absent(self):
        final = self.final()
        self.assertNotIn('sls_block_repair_enabled', final)
        self.assertNotIn('sls_block_repair_calls', final)

    def test_search_ablations_require_optimization(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                result = self.invoke((flag,), ('--first-solution',))
                self.assertEqual(result.returncode, 2)
                self.assertIn('requires --optimize', result.stderr)

    def test_search_ablations_reject_values(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                result = self.invoke((flag + '=yes',))
                self.assertEqual(result.returncode, 2)
                self.assertIn('unknown option', result.stderr)


if __name__ == '__main__':
    unittest.main()
