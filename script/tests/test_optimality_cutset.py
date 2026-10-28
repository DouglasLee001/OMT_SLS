"""Cutset proofs carry an explicit opt-in through the existing proof protocol."""
import contextlib
import csv
from dataclasses import replace
import importlib
import inspect
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


class CutsetProtocolTests(unittest.TestCase):
    def setUp(self):
        self.base = runner_fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner = self.base.runner
        self.slurm = importlib.import_module('script.run_optimize_slurm')

    def test_default_false_boolean_validation_and_proof_dependency(self):
        self.assertIs(getattr(self.runner.Config(), 'optimality_cutset', None), False)
        parser = self.runner.make_parser({})
        self.assertIs(parser.parse_args([]).optimality_cutset, False)
        self.assertIs(parser.parse_args(['--optimality-cutset']).optimality_cutset, True)
        for value in (0, 1, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.Config(prove_optimal=True, optimality_cutset=value)
        with self.assertRaises(ValueError):
            self.runner.Config(optimality_cutset=True)
        for module in (self.runner, self.slurm):
            target = module if module is self.runner else module.runner
            with patch.object(target, 'collect_inputs', side_effect=AssertionError('selection before validation')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(module.main(['--dry-run', '--optimality-cutset']), 2)

    def test_slurm_cli_state_and_manifest_keep_cutset_with_burst(self):
        _, case = self.base.config_and_case()
        real_config = self.slurm.runner.Config
        with patch.object(self.slurm.runner, 'collect_inputs', return_value=[case]), \
             patch.object(self.slurm.runner, 'Config', wraps=real_config) as configured, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.slurm.main(['--dry-run', '--prove-optimal', '--optimality-cutset',
                                             '--optimality-burst', '--optimality-resume']), 0)
        self.assertIs(configured.call_args.kwargs['optimality_cutset'], True)
        config = real_config(prove_optimal=True, optimality_cutset=True, optimality_burst=True)
        restored = self.slurm.config_from_record(json.loads(json.dumps(self.slurm.config_record(config))))
        self.assertIs(restored.optimality_cutset, True)
        self.assertIs(restored.optimality_burst, True)
        self.assertIs(self.slurm.make_manifest([], restored)['optimality_cutset'], True)

    def test_native_flag_and_batch_csv_manifest_summary_for_both_modes(self):
        for enabled in (False, True):
            record = runner_fixtures.final_record(optimality_schedule='adaptive_v1',
                optimality_next_timeout_seconds=.03, optimality_sat_models_accepted=0)
            if enabled:
                record['optimality_cutset'] = True
            body = ("import json,sys\nassert ('--optimality-cutset' in sys.argv) is " + repr(enabled) + '\n'
                    "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
                    'print(json.dumps(' + repr(record) + '),flush=True)\n')
            config, case = self.base.config_and_case(body=body)
            config = replace(config, optimality_cutset=enabled)
            output = self.base.root / str(enabled)
            with self.subTest(enabled=enabled), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.runner.run_batch([case], config, output), 0)
                with (output / 'results.csv').open(newline='') as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual((row['optimality_cutset'], row['is_optimal']),
                                 (str(enabled).lower(), 'true'), row.get('error'))
                for name in ('manifest.json', 'summary.json'):
                    self.assertIs(json.loads((output / name).read_text())['optimality_cutset'], enabled)

    def test_native_cutset_cannot_be_accepted_as_legacy_proof(self):
        config, case = self.base.config_and_case(runner_fixtures.final_record(optimality_cutset=True))
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_optimal']), ('error', 'false'))
        self.assertIn('cutset', row['error'])

    def test_strict_native_boolean_and_unchanged_scheduler_identifiers(self):
        validate = self.base.verifier.validate_optimality_metadata
        self.assertIn('cutset', inspect.signature(validate).parameters)
        for resume, window, schedule in ((True, 0, 'persistent_v1'),
                                         (False, .05, 'windowed_v1'), (False, 0, 'adaptive_v1')):
            record = persistent.final_record(window, optimality_resume=resume,
                optimality_schedule=schedule, optimality_cutset=True, optimality_burst=True)
            self.assertTrue(validate(record, True, .05, .03, 8, 'min', 1,
                                     resume=resume, search_window=window, burst=True, cutset=True))
        for value in (False, None, 0, 1, 'true', 'missing'):
            record = persistent.final_record()
            if value != 'missing':
                record['optimality_cutset'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(record, True, .05, .03, 8, 'min', 1, resume=True, cutset=True)
        self.assertTrue(validate(persistent.final_record(), True, .05, .03, 8, 'min', 1, resume=True))

    def test_retained_artifacts_require_matching_cutset_at_every_layer(self):
        fixture = protocol_fixtures.BurstRetainedRunTests(methodName='runTest')
        fixture.flag = 'optimality_cutset'
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        report = fixture.verify()
        self.assertTrue(report['ok'], report)
        self.assertIs(report.get('optimality_cutset'), True)
        for changes in ({'manifest': False}, {'row': 'false'}, {'native': False}, {'summary': False},
                        {'manifest': 'missing'}, {'row': 'missing'}, {'native': 'missing'}, {'summary': 'missing'},
                        {'manifest': 1}, {'row': ''}, {'native': 1}, {'summary': 1}):
            with self.subTest(changes=changes):
                report = fixture.verify(**changes)
                self.assertFalse(report['ok'], report)
                self.assertIn('cutset', '\n'.join(report['errors'] + [c.get('error', '') for c in report.get('cases', [])]))

    def test_report_validates_cutset_boolean_and_keeps_legacy_compatibility(self):
        fixture = protocol_fixtures.BurstReportTests(methodName='runTest')
        fixture.flag = 'optimality_cutset'
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        rows, _ = fixture.load()
        self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')
        for changes in ({'manifest': False}, {'row': 'false'}, {'summary': False}, {'proofs': False},
                        {'manifest': 'missing'}, {'row': 'missing'}, {'summary': 'missing'},
                        {'manifest': 1}, {'row': ''}, {'row': 'TRUE'}, {'summary': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                fixture.load(**changes)
        rows, _ = fixture.load(manifest='missing', row='missing', summary='missing')
        self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')


if __name__ == '__main__':
    unittest.main()
