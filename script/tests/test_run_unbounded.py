import contextlib
import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))

SOURCE = ('(set-logic QF_NIA)\n(declare-fun x () Int)\n'
          '(assert (>= x 0))\n(minimize x)\n(check-sat)\n')


class UnboundedRerunTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPT / 'run_unbounded.py').is_file(),
                        'Full competitor rerun entry point is missing')
        self.module = importlib.import_module('run_unbounded')
        self.tmp = tempfile.TemporaryDirectory(prefix='omt-unbounded-entry-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'benchmarks'
        (self.original / 'QF_NIA').mkdir(parents=True)
        (self.original / 'QF_NIA/a.smt2').write_text(SOURCE)
        (self.original / 'nia_benchmarks_subset.txt').write_text('QF_NIA/a.smt2\n')
        self.new = self.root / 'new_benchmark'
        for family in self.module.run_new.FAMILIES:
            (self.new / family / 'instance').mkdir(parents=True)
            (self.new / family / 'instance/a.smt2').write_text(SOURCE)
            (self.new / family / 'bounded').mkdir()
            (self.new / family / 'bounded/copy.smt2').write_text(SOURCE)
        self.out = self.root / 'output'
        self.addCleanup(patch.stopall)
        patch.object(self.module, 'ORIGINAL_ROOT', self.original).start()
        patch.object(self.module, 'NEW_ROOT', self.new).start()

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.module.main(list(args))

    def test_dry_run_selects_both_suites_without_writes_or_workers(self):
        with patch.object(self.module.runner, 'run_batch', side_effect=AssertionError('worker launched')), \
             patch.object(self.module.runner, 'create_run_directory', side_effect=AssertionError('directory created')):
            self.assertEqual(self.main('z3', '--dry-run', '--output-root', str(self.out)), 0)
        self.assertFalse(self.out.exists())

    def test_full_entry_uses_defaults_complete_selection_and_preserved_reports(self):
        with patch.object(self.module.runner, 'run_batch', return_value=0) as batch, \
             patch.object(self.module, 'check_solver'):
            self.assertEqual(self.main('optimathsat', '--output-root', str(self.out)), 0)
        self.assertEqual(batch.call_count, 2)
        configs = [c.args[0] for c in batch.call_args_list]
        selections = [c.args[1] for c in batch.call_args_list]
        self.assertEqual([c.result_root for c in configs], [self.out / 'original', self.out / 'new'])
        self.assertEqual(selections[0], ['QF_NIA/a.smt2'])
        self.assertEqual(len(selections[1]), 4)
        self.assertTrue(all('/instance/' in name for name in selections[1]))
        for call, config in zip(batch.call_args_list, configs):
            self.assertEqual(config.jobs, 180)
            self.assertEqual(config.timeout, 1200)
            self.assertEqual(config.memory_mb, 0)
            self.assertTrue(call.kwargs['preserve_optimality'])

    def test_explicit_limits_and_new_only_ignore_legacy_selection_environment(self):
        with patch.dict(os.environ, {'BENCHMARK_ROOT': '/missing', 'BENCHMARK_LIST': '/missing',
                                     'JOBS': '1', 'TIMEOUT': '1', 'MEMORY_MB': '4096'}), \
             patch.object(self.module.runner, 'run_batch', return_value=0) as batch, \
             patch.object(self.module, 'check_solver'):
            self.assertEqual(self.main('z3', '--suite', 'new', '--jobs', '7', '--timeout', '3',
                                       '--output-root', str(self.out)), 0)
        self.assertEqual(batch.call_count, 1)
        config = batch.call_args.args[0]
        self.assertEqual(config.jobs, 7)
        self.assertEqual(config.timeout, 3)
        self.assertEqual(config.memory_mb, 0)
        self.assertEqual(config.benchmark_root, self.new)

    def test_errors_in_original_do_not_skip_new_suite(self):
        with patch.object(self.module.runner, 'run_batch', side_effect=[1, 0]) as batch, \
             patch.object(self.module, 'check_solver'):
            self.assertEqual(self.main('z3', '--output-root', str(self.out)), 1)
        self.assertEqual(batch.call_count, 2)

    def test_interruption_stops_before_next_suite(self):
        with patch.object(self.module.runner, 'run_batch', return_value=130) as batch, \
             patch.object(self.module, 'check_solver'):
            self.assertEqual(self.main('z3', '--output-root', str(self.out)), 130)
        self.assertEqual(batch.call_count, 1)

    def test_preflight_rejects_nra_in_original_manifest(self):
        (self.original / 'nra.smt2').write_text(SOURCE.replace('QF_NIA', 'QF_NRA'))
        (self.original / 'nia_benchmarks_subset.txt').write_text('nra.smt2\n')
        self.assertEqual(self.main('z3', '--dry-run', '--output-root', str(self.out)), 2)
        self.assertFalse(self.out.exists())

    def test_invalid_resource_settings_fail_before_creating_output(self):
        for args in (('--jobs', '0'), ('--timeout', '0'), ('--timeout', '-1'),
                     ('--timeout', 'nan'), ('--timeout', 'inf')):
            with self.subTest(args=args):
                self.assertEqual(self.main('z3', '--output-root', str(self.out), *args), 2)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
