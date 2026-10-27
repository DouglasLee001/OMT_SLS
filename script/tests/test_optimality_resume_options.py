"""Opt-in persistent Z3 scheduling must be frozen consistently for each run."""
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


class ResumeOptionsTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner = self.base.runner
        self.slurm = importlib.import_module('script.run_optimize_slurm')

    def test_resume_default_is_false_and_requires_boolean_opt_in_with_proof(self):
        self.assertIs(self.runner.Config().optimality_resume, False)
        parser = self.runner.make_parser({})
        self.assertIs(parser.parse_args([]).optimality_resume, False)
        self.assertIs(parser.parse_args(['--optimality-resume']).optimality_resume, True)
        self.assertIs(self.runner.Config(prove_optimal=True, optimality_resume=True).optimality_resume, True)
        for invalid in (0, 1, 'true', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.runner.Config(prove_optimal=True, optimality_resume=invalid)
        with self.assertRaises(ValueError):
            self.runner.Config(optimality_resume=True)

    def test_native_receives_opt_in_and_batch_freezes_resume_bool(self):
        config, case = self.base.config_and_case(timeout=.25, body=
            "import json,sys,time\n"
            "assert '--optimality-resume' in sys.argv\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
            'time.sleep(20)\n')
        config = replace(config, optimality_resume=True)
        output = self.base.root / 'persistent-batch'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            reader = csv.DictReader(handle); row = next(reader)
            self.assertEqual(row['optimality_resume'], 'true')
        self.assertEqual((row['best_value'], row['termination']), ('8', 'wall_timeout'))
        for name in ('manifest.json', 'summary.json'):
            self.assertIs(json.loads((output / name).read_text())['optimality_resume'], True)

    def test_default_does_not_pass_resume_to_native(self):
        config, case = self.base.config_and_case(timeout=.25, body=
            "import json,sys,time\n"
            "assert '--optimality-resume' not in sys.argv\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
            'time.sleep(20)\n')
        result = self.runner.run_case(case, config, __import__('threading').Event())
        self.assertEqual((result['best_value'], result['termination']), ('8', 'wall_timeout'))

    def test_slurm_config_json_and_manifest_preserve_resume(self):
        config = self.runner.Config(prove_optimal=True, optimality_resume=True)
        record = self.slurm.config_record(config)
        self.assertIs(record['optimality_resume'], True)
        restored = self.slurm.config_from_record(json.loads(json.dumps(record)))
        self.assertIs(restored.optimality_resume, True)
        self.assertIs(self.slurm.make_manifest([], restored)['optimality_resume'], True)

    def test_cli_requires_proof_before_selection_and_slurm_forwards_resume(self):
        for module in (self.runner, self.slurm):
            with self.subTest(module=module.__name__), \
                 patch.object(module if module is self.runner else module.runner, 'collect_inputs',
                              side_effect=AssertionError('selection ran before validation')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['--dry-run', '--optimality-resume']), 2)
        _, case = self.base.config_and_case()
        real_config = self.slurm.runner.Config
        with patch.object(self.slurm.runner, 'collect_inputs', return_value=[case]), \
             patch.object(self.slurm.runner, 'Config', wraps=real_config) as configured, \
             contextlib.redirect_stdout(io.StringIO()):
            code = self.slurm.main(['--dry-run', '--prove-optimal', '--optimality-resume'])
        self.assertEqual(code, 0)
        self.assertIs(configured.call_args.kwargs['optimality_resume'], True)


if __name__ == '__main__':
    unittest.main()
