"""Output-directory and automatic-report integration for NIA_LS runs."""
import contextlib
import csv
from datetime import datetime
import importlib
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))


class OptimizationOutputTests(unittest.TestCase):
    def setUp(self):
        self.runner = importlib.import_module('run_optimize')
        self.tmp = tempfile.TemporaryDirectory(prefix='nia-output-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'input.smt2'
        source.write_text('(minimize x)\n(check-sat)\n')
        native = self.root / 'nia_ls'
        native.write_text('#!/bin/sh\nexit 0\n')
        native.chmod(0o755)
        converter = self.root / 'converter.py'
        converter.write_text('# converter fixture\n')
        self.config = self.runner.Config(jobs=1, timeout=1, solver=native, converter=converter)
        self.case = self.runner.InputCase(source, 'QF_NIA/input.smt2', 'original',
                                          'original', 'min', 10, 12)
        self.output_root = self.root / 'results'

    def invoke(self, *args, returncode=0, report_code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.runner, 'collect_inputs', return_value=[self.case]), \
             patch.object(self.runner, 'Config', return_value=self.config), \
             patch.object(self.runner, 'run_batch', return_value=returncode) as batch, \
             patch.object(self.runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], report_code)) as report, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.runner.main(list(args))
        return code, batch, report, stdout.getvalue(), stderr.getvalue()

    def test_default_root_and_beijing_timestamp(self):
        self.assertTrue(hasattr(self.runner, 'default_output_directory'),
                        'Timestamped external output helper is missing')
        self.assertEqual(self.runner.RESULT_ROOT, Path('/data/shared/lbh/OMT/result'))
        fixed = datetime(2026, 10, 24, 12, 34, 56, 123456, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch.object(self.runner, 'datetime') as clock:
            clock.now.return_value = fixed
            output = self.runner.default_output_directory()
        self.assertEqual(output, self.runner.RESULT_ROOT / 'NIA_20261024_123456_123456')
        clock.now.assert_called_once_with(ZoneInfo('Asia/Shanghai'))

    def test_complete_run_reports_into_the_same_default_directory(self):
        self.assertTrue(hasattr(self.runner, 'RESULT_ROOT'), 'External output root is missing')
        with patch.object(self.runner, 'RESULT_ROOT', self.output_root):
            code, batch, report, _, _ = self.invoke('all', '--subset', 'all')
        self.assertEqual(code, 0)
        output = Path(batch.call_args.args[2])
        self.assertEqual(output.parent, self.output_root)
        self.assertRegex(output.name, r'^NIA_[0-9]{8}_[0-9]{6}_[0-9]{6}$')
        report.assert_called_once()
        command = report.call_args.args[0]
        self.assertEqual(Path(command[1]), SCRIPT / 'report_results.py')
        self.assertEqual(Path(command[2]), output)
        self.assertEqual(Path(command[command.index('--output') + 1]), output)

    def test_explicit_output_still_places_report_beside_raw_results(self):
        chosen = self.root / 'chosen'
        code, batch, report, _, _ = self.invoke('--output', str(chosen))
        self.assertEqual(code, 0)
        self.assertEqual(Path(batch.call_args.args[2]), chosen)
        report.assert_called_once()
        self.assertIn(str(chosen), report.call_args.args[0])

    def test_interrupted_run_does_not_generate_a_complete_report(self):
        code, _, report, _, _ = self.invoke(returncode=130)
        self.assertEqual(code, 130)
        report.assert_not_called()

    def test_report_failure_is_visible_without_running_the_solver_again(self):
        code, batch, report, _, error = self.invoke(report_code=2)
        self.assertEqual(code, 2)
        batch.assert_called_once()
        report.assert_called_once()
        self.assertIn('report', error.lower())

    def test_dry_run_has_no_result_or_report_writes(self):
        code, batch, report, _, _ = self.invoke('--dry-run')
        self.assertEqual(code, 0)
        batch.assert_not_called()
        report.assert_not_called()
        self.assertFalse(self.output_root.exists())

    def test_complete_batch_and_real_report_share_one_directory(self):
        self.config.converter.write_text(
            "import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n")
        self.config.solver.write_text(
            "#!/usr/bin/env python3\nimport json\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True)),flush=True)\n"
            "print(json.dumps(dict(status='sat',direction='min',objective_value='8',elapsed_seconds=.002,model_validated=True,termination='target_beaten')))\n")
        baseline = self.root / 'competitor.csv'
        with baseline.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['文件名', 'z3状态', 'z3目标值', 'z3时间（秒）',
                             'optimathsat状态', 'optimathsat目标值', 'optimathsat时间（秒）'])
            writer.writerow(['QF_NIA/input.smt2', 'B', '10', '.1', 'B', '12', '.2'])
        chosen = self.root / 'NIA_fixture'
        real_run = subprocess.run

        def report_with_fixture_baseline(command, **kwargs):
            return real_run([*command, '--competitor', str(baseline)], **kwargs,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        with patch.object(self.runner, 'collect_inputs', return_value=[self.case]), \
             patch.object(self.runner, 'Config', return_value=self.config), \
             patch.object(self.runner.subprocess, 'run', side_effect=report_with_fixture_baseline), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = self.runner.main(['--output', str(chosen)])
        self.assertEqual(code, 0)
        for filename in ('results.csv', 'manifest.json', 'summary.json',
                         'nia_ls.csv', 'combined.csv', 'summary.md'):
            self.assertTrue((chosen / filename).is_file(), filename)
        self.assertTrue((chosen / 'cases').is_dir())
        with (chosen / 'nia_ls.csv').open(encoding='utf-8-sig') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['状态'], rows[0]['目标值']), ('B', '8'))
        self.assertIn('全部实例：1 例', (chosen / 'summary.md').read_text())


if __name__ == '__main__':
    unittest.main()
