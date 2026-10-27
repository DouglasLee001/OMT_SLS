"""Initial-model Z3 scheduling options are opt-in and frozen per run."""
import contextlib
import csv
from dataclasses import replace
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_optimality_runner as fixtures


class InitialZ3OptionsTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner = self.base.runner
        self.slurm = importlib.import_module('script.run_optimize_slurm')

    def test_defaults_disable_initial_z3_and_parser_preserves_custom_timing(self):
        config = self.runner.Config()
        self.assertEqual((config.initial_z3_after, config.initial_z3_timeout), (0, 120))
        parser = self.runner.make_parser({})
        args = parser.parse_args(['--initial-z3-after', '300', '--initial-z3-timeout', '60'])
        self.assertEqual((args.initial_z3_after, args.initial_z3_timeout), (300, 60))

    def test_rejects_invalid_timing_and_nondefault_without_proof(self):
        for field, values in (('initial_z3_after', (-1, float('nan'), float('inf'), True)),
                              ('initial_z3_timeout', (0, -1, float('nan'), float('inf'), True))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.runner.Config(prove_optimal=True, **{field: value})
        for values in ({'initial_z3_after': 300}, {'initial_z3_timeout': 60}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.runner.Config(**values)

    def test_native_command_and_batch_metadata_keep_initial_settings(self):
        config, case = self.base.config_and_case(timeout=.25, body=
            "import json,sys,time\n"
            "assert '--initial-z3-after=0.125' in sys.argv\n"
            "assert '--initial-z3-timeout=0.05' in sys.argv\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
            'time.sleep(20)\n')
        config = replace(config, initial_z3_after=.125, initial_z3_timeout=.05)
        output = self.base.root / 'initial-z3-batch'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            reader = csv.DictReader(handle); row = next(reader)
            self.assertFalse(any(name.startswith('initial_z3') for name in reader.fieldnames))
        self.assertEqual((row['best_value'], row['termination']), ('8', 'wall_timeout'))
        for name in ('manifest.json', 'summary.json'):
            data = json.loads((output / name).read_text())
            self.assertEqual((data['initial_z3_after_seconds'], data['initial_z3_timeout_seconds']), (.125, .05))

    def test_slurm_frozen_config_and_manifest_keep_initial_settings(self):
        config = self.runner.Config(prove_optimal=True, initial_z3_after=300, initial_z3_timeout=60)
        record = self.slurm.config_record(config)
        restored = self.slurm.config_from_record(json.loads(json.dumps(record)))
        self.assertEqual((restored.initial_z3_after, restored.initial_z3_timeout), (300, 60))
        manifest = self.slurm.make_manifest([], restored)
        self.assertEqual((manifest['initial_z3_after_seconds'], manifest['initial_z3_timeout_seconds']), (300, 60))

    def test_slurm_cli_forwards_initial_settings_to_worker_configuration(self):
        _, case = self.base.config_and_case()
        real_config = self.slurm.runner.Config
        with patch.object(self.slurm.runner, 'collect_inputs', return_value=[case]), \
             patch.object(self.slurm.runner, 'Config', wraps=real_config) as configured, \
             contextlib.redirect_stdout(io.StringIO()):
            code = self.slurm.main(['--dry-run', '--prove-optimal', '--initial-z3-after', '300',
                                   '--initial-z3-timeout', '60'])
        self.assertEqual(code, 0)
        self.assertEqual((configured.call_args.kwargs['initial_z3_after'],
                          configured.call_args.kwargs['initial_z3_timeout']), (300, 60))

    def test_local_and_slurm_reject_initial_options_before_selection(self):
        for module in (self.runner, self.slurm):
            for arguments in (['--initial-z3-after', '1'], ['--initial-z3-timeout', '60'],
                              ['--prove-optimal', '--initial-z3-after', 'nan'],
                              ['--prove-optimal', '--initial-z3-timeout', '0']):
                with self.subTest(module=module.__name__, args=arguments), \
                     patch.object(module if module is self.runner else module.runner, 'collect_inputs',
                                  side_effect=AssertionError('selection ran before validation')), \
                     contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(['--dry-run', *arguments]), 2)


if __name__ == '__main__':
    unittest.main()
