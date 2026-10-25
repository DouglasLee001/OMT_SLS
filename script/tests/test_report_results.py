import contextlib
import csv
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
RAW_FIELDS = ['filename', 'category', 'suite', 'direction', 'status', 'termination',
              'best_value', 'time_to_best_seconds', 'wall_seconds', 'model_validated',
              'is_optimal', 'known_optimum_value', 'known_optimum_reached',
              'is_unbounded', 'unbounded_status', 'time_to_unbounded_seconds']
BASE_FIELDS = ['文件名', 'z3状态', 'z3目标值', 'z3时间（秒）',
               'optimathsat状态', 'optimathsat目标值', 'optimathsat时间（秒）']


def write_csv(path, fields, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


class ReportResultsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPT / 'report_results.py').is_file(),
                        'Reusable report entry point is missing')
        self.module = importlib.import_module('report_results')
        self.tmp = tempfile.TemporaryDirectory(prefix='omt-report-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'runs' / 'example'
        self.run.mkdir(parents=True)
        common = dict(status='sat', termination='wall_timeout', best_value='0',
                      time_to_best_seconds='1.25', wall_seconds='1200.1',
                      model_validated='true', is_optimal='false', known_optimum_value='',
                      known_optimum_reached='false', is_unbounded='false',
                      unbounded_status='', time_to_unbounded_seconds='')
        self.raw = [
            dict(common, filename='QF_NIA/min.smt2', category='original', suite='original',
                 direction='min', best_value='-4', known_optimum_value='-4',
                 known_optimum_reached='true', termination='known_optimum_reached'),
            dict(common, filename='QF_NIA/unbounded.smt2', category='original', suite='original',
                 direction='min', best_value='-1000000', is_unbounded='true',
                 unbounded_status='proved', termination='unbounded_proved',
                 time_to_unbounded_seconds='0.75'),
            dict(common, filename='max.smt2', category='design', suite='new',
                 direction='max', best_value='9', time_to_best_seconds='2.5'),
            dict(common, filename='missing.smt2', category='packing', suite='new',
                 direction='min', status='error', best_value='', time_to_best_seconds='',
                 model_validated='false', wall_seconds='42.5'),
        ]
        self.cases = [{k: row[k] for k in ('filename', 'category', 'suite', 'direction')}
                      | {'known_optimum_value': row['known_optimum_value'] or None}
                      for row in self.raw]
        self.save_run()
        names = ['QF_NIA/min.smt2', 'QF_NIA/unbounded.smt2',
                 'new_benchmark/design/instance/max.smt2',
                 'new_benchmark/packing/instance/missing.smt2', 'QF_NIA/unused.smt2']
        values = [('-4', '-3'), ('-10000', '-oo'), ('8', '10'), ('NA', 'NA'), ('NA', 'NA')]
        self.baseline_rows = []
        for name, (z3, opti) in zip(names, values):
            self.baseline_rows.append(dict(zip(BASE_FIELDS, [
                name, '找到最优解并证明最优' if name.endswith('/min.smt2') else
                ('未找到可行解' if z3 == 'NA' else '找到解但未证明最优性'), z3, '0.1',
                '证明无界' if opti == '-oo' else
                ('未找到可行解' if opti == 'NA' else '找到解但未证明最优性'), opti, '0.2'])))
        self.baseline = self.root / 'competitor.csv'
        write_csv(self.baseline, BASE_FIELDS, list(reversed(self.baseline_rows)))
        self.out = self.root / 'out'

    def save_run(self, directory=None, started='2026-10-23T07:16:53+00:00', completed=None):
        directory = directory or self.run
        directory.mkdir(parents=True, exist_ok=True)
        write_csv(directory / 'results.csv', RAW_FIELDS, self.raw)
        (directory / 'manifest.json').write_text(json.dumps({
            'started_utc': started, 'cases': self.cases}))
        (directory / 'summary.json').write_text(json.dumps({
            'selected': len(self.raw), 'completed': len(self.raw) if completed is None else completed,
            'statuses': {}}))

    def invoke(self, *extra, run=True):
        args = ([str(self.run)] if run else []) + [
            '--competitor', str(self.baseline), '--output', str(self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_exports_four_statuses_times_and_name_join_without_changing_baseline(self):
        baseline_bytes = self.baseline.read_bytes()
        code, text, error = self.invoke()
        self.assertEqual(code, 0, error)
        nia = {r['文件名']: r for r in read_csv(self.out / 'nia_ls.csv')}
        self.assertEqual(len(nia), 4)
        self.assertEqual(nia['QF_NIA/min.smt2']['状态'], 'A2')
        self.assertEqual(nia['QF_NIA/unbounded.smt2']['目标值'], '-oo')
        self.assertEqual(nia['QF_NIA/unbounded.smt2']['时间（秒）'], '0.75')
        self.assertEqual(nia['new_benchmark/design/instance/max.smt2']['状态'], 'B')
        missing = nia['new_benchmark/packing/instance/missing.smt2']
        self.assertEqual((missing['状态'], missing['目标值'], missing['时间（秒）']), ('C', 'NA', '42.5'))
        combined = read_csv(self.out / 'combined.csv')
        self.assertEqual(len(combined), 4)
        self.assertEqual(len(combined[0]), 10)
        joined = {r['文件名']: r for r in combined}
        self.assertEqual(joined['QF_NIA/min.smt2']['z3状态'], 'A2')
        self.assertEqual(joined['QF_NIA/unbounded.smt2']['optimathsat状态'], 'A1')
        self.assertEqual(self.baseline.read_bytes(), baseline_bytes)
        report = (self.out / 'summary.md').read_text()
        self.assertIn(report, text)
        self.assertIn('A1', report)
        self.assertIn('A2', report)
        stats = self.module.compute_statistics(combined, {
            'QF_NIA/min.smt2': 'min', 'QF_NIA/unbounded.smt2': 'min',
            'new_benchmark/design/instance/max.smt2': 'max',
            'new_benchmark/packing/instance/missing.smt2': 'min'})
        self.assertEqual(stats['all']['nia_wdl']['best_competitor'], {'win': 0, 'tie': 3, 'loss': 1})
        self.assertEqual(stats['all_unsolved']['instances'], 2)

    def test_default_selects_latest_complete_run_and_skips_newer_partial_run(self):
        self.save_run(self.run.parent / 'newer-incomplete', '2026-10-24T00:00:00+00:00', completed=2)
        with patch.object(self.module, 'OPTIMIZE_ROOT', self.run.parent):
            code, text, error = self.invoke(run=False)
        self.assertEqual(code, 0, error)
        self.assertIn(str(self.run), text)

    def test_default_discovery_uses_shared_result_root_without_legacy_fallback(self):
        self.assertEqual(self.module.OPTIMIZE_ROOT, Path('/data/shared/lbh/OMT/result'))
        shared = self.root / 'shared-result'
        shared.mkdir()
        self.save_run(self.root / 'result/optimize/legacy')
        with patch.object(self.module, 'ROOT', self.root), \
             patch.object(self.module, 'OPTIMIZE_ROOT', shared):
            code, _, error = self.invoke(run=False)
        self.assertEqual(code, 2)
        self.assertIn(str(shared), error)
        self.assertFalse(self.out.exists())

    def test_default_output_is_the_explicit_historical_run_and_preserves_inputs(self):
        original = {name: (self.run / name).read_bytes()
                    for name in ('results.csv', 'manifest.json', 'summary.json')}
        # Confine the previous default destination during the RED test too.
        with patch.object(self.module, 'REPORT_ROOT', self.root / 'old-reports', create=True), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = self.module.main([str(self.run / 'results.csv'), '--competitor', str(self.baseline)])
        self.assertEqual(code, 0)
        for name in ('nia_ls.csv', 'combined.csv', 'summary.md'):
            self.assertTrue((self.run / name).is_file(), name)
        for name, contents in original.items():
            self.assertEqual((self.run / name).read_bytes(), contents, name)
        self.assertFalse((self.root / 'old-reports').exists())

    def test_package_import_works_without_adding_script_directory_to_sys_path(self):
        code = ('import sys; sys.path.insert(0, ' + repr(str(SCRIPT.parent)) + '); '
                'from script import report_results; '
                "assert str(report_results.OPTIMIZE_ROOT) == '/data/shared/lbh/OMT/result'")
        process = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                                 cwd=self.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_rejects_missing_or_duplicate_competitor_instances_before_writing(self):
        for rows in (self.baseline_rows[1:], self.baseline_rows + self.baseline_rows[:1]):
            with self.subTest(count=len(rows)):
                write_csv(self.baseline, BASE_FIELDS, rows)
                code, _, _ = self.invoke()
                self.assertEqual(code, 2)
                self.assertFalse(self.out.exists())

    def test_rejects_incomplete_run_or_duplicate_run_rows(self):
        self.save_run(completed=2)
        self.assertEqual(self.invoke()[0], 2)
        self.assertFalse(self.out.exists())
        self.raw[-1] = dict(self.raw[0])
        self.save_run()
        self.assertEqual(self.invoke()[0], 2)
        self.assertFalse(self.out.exists())

    def test_rejects_claimed_known_optimum_mismatch_and_unvalidated_model(self):
        for changes in ({'known_optimum_value': '-3'}, {'model_validated': 'false'},
                        {'time_to_best_seconds': 'nan'}):
            with self.subTest(changes=changes):
                original = dict(self.raw[0])
                self.raw[0].update(changes)
                self.save_run()
                self.assertEqual(self.invoke()[0], 2)
                self.assertFalse(self.out.exists())
                self.raw[0] = original

    def test_results_csv_path_is_accepted_and_export_can_be_repeated(self):
        code, _, error = self.invoke()
        self.assertEqual(code, 0, error)
        before = (self.out / 'combined.csv').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = self.module.main([str(self.run / 'results.csv'), '--competitor', str(self.baseline),
                                     '--output', str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual((self.out / 'combined.csv').read_bytes(), before)

    def test_unbounded_proof_takes_priority_over_known_value_hit(self):
        self.raw[1]['known_optimum_reached'] = 'true'
        self.raw[1]['known_optimum_value'] = self.raw[1]['best_value']
        self.cases[1]['known_optimum_value'] = self.raw[1]['best_value']
        self.save_run()
        code, _, error = self.invoke()
        self.assertEqual(code, 0, error)
        nia = {r['文件名']: r for r in read_csv(self.out / 'nia_ls.csv')}
        row = nia['QF_NIA/unbounded.smt2']
        self.assertEqual((row['状态'], row['目标值'], row['时间（秒）']), ('A1', '-oo', '0.75'))

    def test_rejects_unaccepted_proofs_and_times_after_the_actual_run(self):
        for index, changes in ((1, {'termination': 'wall_timeout'}),
                               (1, {'time_to_unbounded_seconds': '1300'}),
                               (1, {'wall_seconds': '0.1'}),
                               (1, {'wall_seconds': 'nan'}),
                               (2, {'time_to_best_seconds': '1300'})):
            with self.subTest(index=index, changes=changes):
                original = dict(self.raw[index])
                self.raw[index].update(changes)
                self.save_run()
                self.assertEqual(self.invoke()[0], 2)
                self.assertFalse(self.out.exists())
                self.raw[index] = original

    def test_exports_integer_values_beyond_python_string_conversion_limit(self):
        huge = '1' + '0' * 4300
        self.raw[2]['best_value'] = huge
        self.save_run()
        code, _, error = self.invoke()
        self.assertEqual(code, 0, error)
        nia = {r['文件名']: r for r in read_csv(self.out / 'nia_ls.csv')}
        self.assertEqual(nia['new_benchmark/design/instance/max.smt2']['目标值'], huge)


if __name__ == '__main__':
    unittest.main()
