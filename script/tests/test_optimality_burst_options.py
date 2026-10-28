"""An SMT improvement burst is explicit and frozen through every runner layer."""
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
import test_optimality_runner as fixtures


class BurstOptionsTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner = self.base.runner
        self.slurm = importlib.import_module('script.run_optimize_slurm')

    def test_burst_defaults_off_and_requires_boolean_opt_in_with_proofs(self):
        self.assertIs(getattr(self.runner.Config(), 'optimality_burst', None), False)
        parser = self.runner.make_parser({})
        self.assertIs(parser.parse_args([]).optimality_burst, False)
        self.assertIs(parser.parse_args(['--optimality-burst']).optimality_burst, True)
        self.assertIs(self.runner.Config(prove_optimal=True, optimality_burst=True).optimality_burst, True)
        for invalid in (0, 1, 'true', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.runner.Config(prove_optimal=True, optimality_burst=invalid)
        with self.assertRaises(ValueError):
            self.runner.Config(optimality_burst=True)

    def test_local_and_slurm_reject_burst_before_selecting_inputs_without_proofs(self):
        for module in (self.runner, self.slurm):
            with self.subTest(module=module.__name__), \
                 patch.object(module if module is self.runner else module.runner, 'collect_inputs',
                              side_effect=AssertionError('selection preceded burst validation')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['--dry-run', '--optimality-burst']), 2)

    def test_burst_reaches_native_and_csv_manifest_summary(self):
        record = fixtures.final_record(optimality_burst=True, optimality_schedule='adaptive_v1',
                                       optimality_next_timeout_seconds=.03, optimality_sat_models_accepted=0)
        body = ("import json,sys\nassert '--optimality-burst' in sys.argv\n"
                "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
                'print(json.dumps(' + repr(record) + '),flush=True)\n')
        config, case = self.base.config_and_case(body=body)
        config = replace(config, optimality_burst=True)
        output = self.base.root / 'burst-batch'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual((row['optimality_burst'], row['is_optimal'], row['status']),
                         ('true', 'true', 'sat'), row.get('error'))
        for name in ('manifest.json', 'summary.json'):
            self.assertIs(json.loads((output / name).read_text())['optimality_burst'], True)

    def test_default_omits_native_flag_and_accepts_historical_final_without_burst(self):
        record = fixtures.final_record()
        body = ("import json,sys\nassert '--optimality-burst' not in sys.argv\n"
                "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
                'print(json.dumps(' + repr(record) + '),flush=True)\n')
        config, case = self.base.config_and_case(body=body)
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row.get('optimality_burst'), row['is_optimal']), ('false', 'true'), row.get('error'))

    def test_slurm_config_round_trip_and_manifest_preserve_burst(self):
        config = self.runner.Config(prove_optimal=True, optimality_burst=True)
        record = self.slurm.config_record(config)
        self.assertIs(record['optimality_burst'], True)
        restored = self.slurm.config_from_record(json.loads(json.dumps(record)))
        self.assertIs(restored.optimality_burst, True)
        self.assertIs(self.slurm.make_manifest([], restored)['optimality_burst'], True)

    def test_local_and_slurm_cli_forward_burst_to_configuration(self):
        _, case = self.base.config_and_case()
        for module in (self.runner, self.slurm):
            target = module if module is self.runner else module.runner
            real_config = target.Config
            with self.subTest(module=module.__name__), \
                 patch.object(target, 'collect_inputs', return_value=[case]), \
                 patch.object(target, 'Config', wraps=real_config) as configured, \
                 patch.object(target, 'run_batch', return_value=0), \
                 patch.object(module.subprocess, 'run', return_value=type('Result', (), {'returncode': 0})()), \
                 contextlib.redirect_stdout(io.StringIO()):
                # Local dry-run exits before constructing Config; use its normal
                # path with existing fixture executables and mocked execution.
                if module is self.runner:
                    configured.side_effect = lambda **kwargs: real_config(
                        **(kwargs | {'solver': self.base.root / 'native', 'converter': self.base.root / 'converter.py'}))
                    argv = ['--prove-optimal', '--optimality-burst']
                else:
                    argv = ['--dry-run', '--prove-optimal', '--optimality-burst']
                self.assertEqual(module.main(argv), 0)
                self.assertIs(configured.call_args.kwargs['optimality_burst'], True)

    def test_runner_does_not_accept_burst_proof_under_legacy_configuration(self):
        config, case = self.base.config_and_case(fixtures.final_record(optimality_burst=True))
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_optimal']), ('error', 'false'))
        self.assertIn('burst', row['error'])

    def test_burst_runner_requires_matching_native_boolean(self):
        for value in (False, None, 1, 'true'):
            record = fixtures.final_record(optimality_burst=value)
            config, case = self.base.config_and_case(record)
            config = replace(config, optimality_burst=True)
            with self.subTest(value=value):
                row = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((row['status'], row['is_optimal']), ('error', 'false'))
                self.assertIn('burst', row['error'])
        config, case = self.base.config_and_case(fixtures.final_record())
        config = replace(config, optimality_burst=True)
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['is_optimal']), ('error', 'false'))
        self.assertIn('burst', row['error'])


if __name__ == '__main__':
    unittest.main()
