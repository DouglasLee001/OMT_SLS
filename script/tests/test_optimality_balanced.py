"""Balanced scheduling is frozen explicitly and cannot impersonate old proofs."""
import contextlib
import csv
from dataclasses import replace
import importlib
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_optimality_runner as runner_fixtures
import test_optimality_burst_metadata as protocol_fixtures
import test_persistent_optimality_metadata as persistent


class BalancedProtocolTests(unittest.TestCase):
    def setUp(self):
        self.base = runner_fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner = self.base.runner
        self.slurm = importlib.import_module('script.run_optimize_slurm')

    def test_default_dependency_boolean_and_window_conflict(self):
        self.assertIs(getattr(self.runner.Config(), 'optimality_balanced', None), False)
        parser = self.runner.make_parser({})
        self.assertIs(parser.parse_args([]).optimality_balanced, False)
        self.assertIs(parser.parse_args(['--optimality-balanced']).optimality_balanced, True)
        for kwargs in ({'optimality_balanced': True},
                       {'prove_optimal': True, 'optimality_balanced': True, 'optimality_search_window': 1},
                       *({'prove_optimal': True, 'optimality_balanced': v} for v in (0, 1, 'true', None))):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.runner.Config(**kwargs)
        for module in (self.runner, self.slurm):
            for argv in (['--optimality-balanced'], ['--prove-optimal', '--optimality-balanced',
                                                   '--optimality-search-window', '1']):
                target = module if module is self.runner else module.runner
                with patch.object(target, 'collect_inputs', side_effect=AssertionError('selected before validation')), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main(['--dry-run', *argv]), 2)

    def test_slurm_cli_round_trip_with_other_opt_in_flags(self):
        _, case = self.base.config_and_case()
        real_config = self.slurm.runner.Config
        with patch.object(self.slurm.runner, 'collect_inputs', return_value=[case]), \
             patch.object(self.slurm.runner, 'Config', wraps=real_config) as configured, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.slurm.main(['--dry-run', '--prove-optimal', '--optimality-balanced',
                '--optimality-burst', '--optimality-cutset', '--optimality-resume']), 0)
        self.assertIs(configured.call_args.kwargs['optimality_balanced'], True)
        config = real_config(prove_optimal=True, optimality_balanced=True, optimality_burst=True,
                             optimality_cutset=True, optimality_resume=True)
        restored = self.slurm.config_from_record(json.loads(json.dumps(self.slurm.config_record(config))))
        self.assertIs(restored.optimality_balanced, True)
        self.assertIs(self.slurm.make_manifest([], restored)['optimality_balanced'], True)

    def test_native_flag_csv_manifest_summary_and_legacy_missing_field(self):
        for enabled in (False, True):
            record = runner_fixtures.final_record(optimality_schedule='adaptive_v1',
                optimality_next_timeout_seconds=.03, optimality_sat_models_accepted=0)
            if enabled:record['optimality_balanced'] = True
            body = ("import json,sys\nassert ('--optimality-balanced' in sys.argv) is " + repr(enabled) + '\n'
                "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
                'print(json.dumps(' + repr(record) + '),flush=True)\n')
            config, case = self.base.config_and_case(body=body)
            config = replace(config, optimality_balanced=enabled)
            output = self.base.root / str(enabled)
            with self.subTest(enabled=enabled), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.runner.run_batch([case], config, output), 0)
                with (output / 'results.csv').open(newline='') as handle:row = next(csv.DictReader(handle))
                self.assertEqual((row['optimality_balanced'], row['is_optimal']),
                                 (str(enabled).lower(), 'true'), row.get('error'))
                for name in ('manifest.json', 'summary.json'):
                    self.assertIs(json.loads((output / name).read_text())['optimality_balanced'], enabled)
        config, case = self.base.config_and_case(runner_fixtures.final_record(optimality_balanced=True))
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_optimal']), ('error', 'false'))
        self.assertIn('balanced', row['error'])

    def test_native_metadata_strict_truth_unchanged_names_and_window_exclusion(self):
        validate = self.base.verifier.validate_optimality_metadata
        for resume, schedule in ((False, 'adaptive_v1'), (True, 'persistent_v1')):
            record = persistent.final_record(optimality_balanced=True, optimality_resume=resume,
                optimality_schedule=schedule, optimality_burst=True, optimality_cutset=True)
            self.assertTrue(validate(record, True, .05, .03, 8, 'min', 1, resume=resume,
                                     burst=True, cutset=True, balanced=True))
        for value in (False, None, 0, 1, 'true', 'missing'):
            record = persistent.final_record()
            if value != 'missing':record['optimality_balanced'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(record, True, .05, .03, 8, 'min', 1, resume=True, balanced=True)
        with self.assertRaises(ValueError):
            validate(persistent.final_record(.05, optimality_balanced=True),
                     True, .05, .03, 8, 'min', 1, resume=True, search_window=.05, balanced=True)
        self.assertTrue(validate(persistent.final_record(), True, .05, .03, 8, 'min', 1, resume=True))

    def test_retained_and_report_layers_cannot_downgrade_balanced_proofs(self):
        retained = protocol_fixtures.BurstRetainedRunTests(methodName='runTest')
        retained.flag = 'optimality_balanced';retained.setUp();self.addCleanup(retained.doCleanups)
        report = retained.verify()
        self.assertTrue(report['ok'], report);self.assertIs(report.get('optimality_balanced'), True)
        for layer in ('manifest', 'row', 'native', 'summary'):
            for value in (False if layer != 'row' else 'false', 'missing', 1):
                with self.subTest(layer=layer, value=value):
                    self.assertFalse(retained.verify(**{layer: value})['ok'])
        exporter = protocol_fixtures.BurstReportTests(methodName='runTest')
        exporter.flag = 'optimality_balanced';exporter.setUp();self.addCleanup(exporter.doCleanups)
        rows, _ = exporter.load();self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')
        for changes in ({'manifest': False}, {'row': 'false'}, {'summary': False}, {'proofs': False},
                        {'manifest': 'missing'}, {'row': 'missing'}, {'summary': 'missing'},
                        {'manifest': 1}, {'row': ''}, {'summary': 1}, {'window': .05}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):exporter.load(**changes)
        rows, _ = exporter.load(manifest='missing', row='missing', summary='missing')
        self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')


if __name__ == '__main__':
    unittest.main()
