"""Exercise strict-bound selection and CLI behavior using frozen on-disk runs."""
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


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
GROUPS = ('proved-equal', 'unproved-tie', 'unproved-better')
RAW_FIELDS = (
    'filename', 'category', 'suite', 'direction', 'status', 'termination',
    'best_value', 'time_to_best_seconds', 'wall_seconds', 'model_validated',
    'is_optimal', 'known_optimum_reached', 'is_unbounded', 'unbounded_status',
    'time_to_unbounded_seconds', 'optimality_seconds', 'optimality_attempts',
)
BASE_FIELDS = (
    '文件名', 'z3状态', 'z3目标值', 'z3时间（秒）',
    'optimathsat状态', 'optimathsat目标值', 'optimathsat时间（秒）',
)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


class StrictBoundGroupsTests(unittest.TestCase):
    def setUp(self):
        self.runner = importlib.import_module('run_strict_bound')
        self.tmp = tempfile.TemporaryDirectory(prefix='omt-strict-groups-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'source-run'
        (self.run / 'source/result').mkdir(parents=True)
        self.cases, self.raw, self.opponents = [], [], []
        self.huge = '1' + '0' * 80
        next_huge = '1' + '0' * 79 + '1'
        second_huge = '1' + '0' * 79 + '2'
        self.add_case('proved-min', 'min', '5', ('A2', '5'), ('B', '6'))
        self.add_case('proved-max', 'max', '9', ('B', '8'), ('A2', '9'))
        self.add_case('proved-both', 'min', '-3', ('A2', '(- 3)'), ('A2', '-3'))
        self.add_case('tie-min', 'min', '5', ('B', '5'), ('B', '6'))
        self.add_case('tie-max', 'max', '9', ('B', '8'), ('B', '9'))
        self.add_case('tie-one-missing', 'min', '-2', ('C', 'NA'), ('B', '(- 2)'))
        self.add_case('better-min', 'min', '4', ('B', '5'), ('B', '6'))
        self.add_case('better-max', 'max', '10', ('B', '9'), ('C', 'NA'))
        self.add_case('only-nia', 'min', '7', ('C', 'NA'), ('C', 'NA'))
        self.add_case('huge-min', 'min', self.huge, ('B', next_huge), ('B', second_huge))
        self.add_case('huge-max', 'max', '-' + self.huge,
                      ('B', '-' + next_huge), ('B', '-' + second_huge))
        self.add_case('behind-min', 'min', '6', ('B', '5'), ('B', '7'))
        self.add_case('behind-max', 'max', '8', ('B', '9'), ('B', '7'))
        self.add_case('opponent-unbounded-min', 'min', '0', ('A1', '-oo'), ('B', '1'))
        self.add_case('opponent-unbounded-max', 'max', '0', ('B', '-1'), ('A1', '+oo'))
        self.add_case('opponent-proved-different', 'min', '4', ('A2', '5'), ('B', '6'))
        self.add_case('nia-proved', 'min', '5', ('A2', '5'), ('B', '6'), nia='A2')
        self.add_case('nia-missing', 'min', '', ('C', 'NA'), ('C', 'NA'), nia='C')
        self.add_case('nia-unbounded', 'min', '-99', ('B', '-99'), ('C', 'NA'), nia='A1')
        self.save_run()
        self.expected = {
            'proved-equal': {'proved-min', 'proved-max', 'proved-both'},
            'unproved-tie': {'tie-min', 'tie-max', 'tie-one-missing'},
            'unproved-better': {'better-min', 'better-max', 'only-nia', 'huge-min', 'huge-max'},
        }

    def add_case(self, name, direction, best, z3, opti, *, nia='B'):
        filename = 'QF_NIA/' + name + '.smt2'
        source = self.root / (name + '.smt2')
        objective = 'minimize' if direction == 'min' else 'maximize'
        source.write_text('(set-logic QF_NIA)\n(declare-const cost Int)\n'
                          f'(assert (>= cost (- {"1" + "0" * 90})))\n'
                          f'({objective} cost)\n(check-sat)\n(exit)\n')
        self.cases.append(dict(filename=filename, category='original', suite='original',
                               direction=direction, source=str(source)))
        self.raw.append(dict(
            filename=filename, category='original', suite='original', direction=direction,
            status='sat' if nia != 'C' else 'unknown',
            termination='unbounded_proved' if nia == 'A1' else 'wall_timeout',
            best_value=best, time_to_best_seconds='1.25' if best else '',
            wall_seconds='1200', model_validated='true' if best else 'false',
            is_optimal='true' if nia == 'A2' else 'false', known_optimum_reached='false',
            is_unbounded='true' if nia == 'A1' else 'false',
            unbounded_status='proved' if nia == 'A1' else '',
            time_to_unbounded_seconds='2' if nia == 'A1' else '',
            optimality_seconds='0.75', optimality_attempts='2',
        ))
        self.opponents.append(dict(zip(BASE_FIELDS, (
            filename, z3[0], z3[1], '2.5', opti[0], opti[1], '3.75',
        ))))

    def save_run(self):
        (self.run / 'manifest.json').write_text(json.dumps({'cases': self.cases}))
        (self.run / 'summary.json').write_text(json.dumps({
            'selected': len(self.cases), 'completed': len(self.cases), 'complete': True,
        }))
        # Reversed baseline order makes positional joins fail visibly.
        write_csv(self.run / 'results.csv', RAW_FIELDS, self.raw)
        write_csv(self.run / 'source/result/competitor.csv', BASE_FIELDS,
                  list(reversed(self.opponents)))

    @staticmethod
    def short_names(cases):
        return {Path(case['filename']).stem for case in cases}

    def invoke_cli(self, *args):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT / 'run_strict_bound.py'), *map(str, args)],
            capture_output=True, text=True, timeout=15,
        )

    def test_default_selection_retains_proved_equal_without_old_127_count(self):
        default = self.runner.select_cases(self.run)
        explicit = self.runner.select_cases(self.run, 3)
        self.assertEqual(self.short_names(default), self.expected['proved-equal'])
        self.assertEqual(default, explicit)
        self.assertEqual({row['selection_group'] for row in default}, {'proved-equal'})
        proved = {Path(row['filename']).stem: row for row in default}
        self.assertEqual(proved['proved-both']['proved_by'], ['z3', 'optimathsat'])
        self.assertEqual(proved['proved-min']['proved_by'], ['z3'])
        self.assertEqual(proved['proved-max']['proved_by'], ['optimathsat'])

    def test_three_groups_are_disjoint_and_compare_best_opponent_by_direction(self):
        selections = {}
        for group in GROUPS:
            with self.subTest(group=group):
                cases = self.runner.select_cases(self.run, groups=(group,))
                self.assertEqual(self.short_names(cases), self.expected[group])
                self.assertEqual({case['selection_group'] for case in cases}, {group})
                if group != 'proved-equal':
                    self.assertTrue(all(case['proved_by'] == [] for case in cases))
                selections[group] = {case['filename'] for case in cases}
        combined = self.runner.select_cases(self.run, groups=GROUPS)
        expected_union = set().union(*selections.values())
        self.assertEqual({case['filename'] for case in combined}, expected_union)
        self.assertEqual(len(combined), sum(map(len, selections.values())))
        for index, group in enumerate(GROUPS):
            for other in GROUPS[index + 1:]:
                self.assertFalse(selections[group] & selections[other])

    def test_exact_values_and_reference_metadata_survive_selection_and_preparation(self):
        selected = self.runner.select_cases(self.run, groups=GROUPS)
        by_name = {Path(row['filename']).stem: row for row in selected}
        self.assertEqual(by_name['huge-min']['M'], self.huge)
        self.assertEqual(by_name['huge-max']['M'], '-' + self.huge)
        self.assertEqual(by_name['tie-one-missing']['z3_reference_value'], 'NA')
        self.assertEqual(by_name['tie-one-missing']['optimathsat_reference_value'], '-2')
        self.assertEqual(by_name['proved-both']['z3_reference_value'], '-3')
        self.assertEqual(by_name['proved-min']['z3_reference_time_seconds'], '2.5')
        self.assertEqual(by_name['proved-min']['optimathsat_reference_time_seconds'], '3.75')
        prepared = self.root / 'queries'
        manifest = self.runner.prepare_queries(selected, prepared, self.run)
        loaded = self.runner.load_prepared(prepared)
        self.assertEqual(loaded['instances'], manifest['instances'])
        for case in loaded['instances']:
            self.assertIn(case['selection_group'], GROUPS)
            self.assertIn('z3_reference_value', case)
            self.assertIn('optimathsat_reference_value', case)
            query = (prepared / case['query_file']).read_text()
            comparison = '<' if case['direction'] == 'min' else '>'
            number = case['M']
            literal = f'(- {number[1:]})' if number.startswith('-') else number
            self.assertIn(f'(assert ({comparison} cost {literal}))', query)
            self.assertEqual(query.count('(check-sat)'), 1)
            self.assertNotIn('minimize', query)
            self.assertNotIn('maximize', query)

    def test_only_nia_feasible_is_better_and_a1_losses_and_nia_a_c_are_excluded(self):
        cases = self.runner.select_cases(self.run, groups=GROUPS)
        names = self.short_names(cases)
        self.assertIn('only-nia', names)
        only = next(case for case in cases if case['filename'].endswith('/only-nia.smt2'))
        self.assertEqual(only['selection_group'], 'unproved-better')
        for name in ('behind-min', 'behind-max', 'opponent-unbounded-min',
                     'opponent-unbounded-max', 'opponent-proved-different',
                     'nia-proved', 'nia-missing', 'nia-unbounded'):
            self.assertNotIn(name, names)

    def test_expected_count_is_optional_but_checked_when_supplied(self):
        cases = self.runner.select_cases(self.run, groups=GROUPS)
        self.assertEqual(len(cases), 11)
        self.assertEqual(len(self.runner.select_cases(self.run, 11, groups=GROUPS)), 11)
        with self.assertRaises(ValueError):
            self.runner.select_cases(self.run, 12, groups=GROUPS)

    def test_prepare_only_cli_accepts_three_groups_3600_seconds_and_180_jobs(self):
        output = self.root / 'cli-prepared'
        completed = self.invoke_cli(
            '--run', self.run, '--groups', *GROUPS, '--timeout', '3600',
            '--jobs', '180', '--prepare-only', '--output', output,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = self.runner.load_prepared(output)
        self.assertEqual(manifest['selected'], 11)
        self.assertEqual({case['selection_group'] for case in manifest['instances']}, set(GROUPS))
        self.assertFalse((output / 'logs').exists())

    def test_prepare_only_cli_defaults_to_proved_equal_and_count_is_opt_in(self):
        output = self.root / 'cli-default'
        completed = self.invoke_cli('--run', self.run, '--prepare-only', '--output', output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = self.runner.load_prepared(output)
        self.assertEqual(manifest['selected'], 3)
        self.assertEqual({case['selection_group'] for case in manifest['instances']}, {'proved-equal'})

    def test_cli_rejects_invalid_limits_groups_and_expected_count_before_output(self):
        for index, bad in enumerate((
            ('--timeout', '0'), ('--timeout', 'nan'),
            ('--timeout', 'inf'), ('--jobs', '181'), ('--jobs', '0'),
            ('--expected-count', '0'), ('--expected-count', '-1'),
            ('--expected-count', '4'), ('--groups', 'bogus'),
        )):
            with self.subTest(args=bad):
                output = self.root / ('invalid-' + str(index))
                completed = self.invoke_cli('--run', self.run, '--prepare-only',
                                            '--output', output, *bad)
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertFalse(output.exists())

    def make_sat_solver(self):
        solver = self.root / 'fake-z3'
        solver.write_text(
            f'#!{sys.executable}\n'
            'from pathlib import Path\n'
            'import sys\n'
            'if sys.argv[1:] == ["--version"]:\n'
            '    print("Z3 version 5.1.0 - test CLI")\n'
            'else:\n'
            '    assert len(sys.argv) == 3 and sys.argv[1] == "-smt2", sys.argv\n'
            '    query = Path(sys.argv[2]).read_text()\n'
            '    assert "(check-sat)" in query\n'
            '    assert "minimize" not in query and "maximize" not in query\n'
            '    print("sat")\n'
        )
        solver.chmod(0o755)
        return solver

    def test_sat_is_a_normal_result_for_unproved_groups_and_failure_for_proved_equal(self):
        solver = self.make_sat_solver()
        for group, expected_code in (('unproved-tie', 0), ('unproved-better', 0),
                                     ('proved-equal', 1)):
            with self.subTest(group=group):
                prepared = self.root / ('prepared-' + group)
                # A genuine prepared file also checks query hashes and metadata on the runner path.
                cases = self.runner.select_cases(self.run, groups=(group,))[:1]
                self.runner.prepare_queries(cases, prepared, self.run)
                output = self.root / ('result-' + group)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = self.runner.run_experiment(prepared, output, solver, 1, 3)
                self.assertEqual(code, expected_code, stdout.getvalue())
                rows = read_csv(output / 'results.csv')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'sat')
                self.assertEqual(rows[0]['selection_group'], group)
                self.assertEqual(rows[0]['z3_reference_value'], cases[0]['z3_reference_value'])
                self.assertEqual(rows[0]['optimathsat_reference_value'], cases[0]['optimathsat_reference_value'])
                summary = json.loads((output / 'summary.json').read_text())
                self.assertEqual(summary['statuses'], {'sat': 1})
                self.assertTrue(summary['complete'])

    def test_prepared_cli_does_not_require_count_and_rejects_a_new_selection(self):
        cases = self.runner.select_cases(self.run, groups=('unproved-tie',))[:1]
        prepared = self.root / 'cli-reuse-prepared'
        self.runner.prepare_queries(cases, prepared, self.run)
        solver = self.make_sat_solver()
        output = self.root / 'cli-reuse-result'
        completed = self.invoke_cli('--prepared', prepared, '--solver', solver,
                                    '--jobs', '1', '--timeout', '3', '--output', output)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(read_csv(output / 'results.csv')[0]['selection_group'], 'unproved-tie')
        rejected = self.root / 'cli-rejected-result'
        completed = self.invoke_cli('--prepared', prepared, '--groups', 'proved-equal',
                                    '--solver', solver, '--output', rejected)
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertFalse(rejected.exists())

    def test_legacy_prepared_case_without_group_still_treats_sat_as_counterexample(self):
        cases = self.runner.select_cases(self.run, 3)
        legacy_case = dict(cases[0])
        legacy_case.pop('selection_group')
        prepared = self.root / 'legacy-prepared'
        self.runner.prepare_queries([legacy_case], prepared, self.run)
        output = self.root / 'legacy-result'
        completed = self.invoke_cli('--prepared', prepared, '--solver', self.make_sat_solver(),
                                    '--jobs', '1', '--timeout', '3', '--output', output)
        self.assertEqual(completed.returncode, 1, completed.stderr + completed.stdout)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['selection_groups']['proved-equal']['statuses'], {'sat': 1})

    def test_summary_separates_three_groups_without_claiming_unproved_rows_are_oms_proofs(self):
        output = self.root / 'summary-only'
        output.mkdir()
        manifest = dict(selected=3, source_run=str(self.run), solver='fake-z3',
                        solver_version='5.1.0', jobs=180, timeout=3600)
        rows = [
            dict(filename='proved.smt2', selection_group='proved-equal',
                 proved_by=['optimathsat'], z3_reference_state='B',
                 optimathsat_reference_state='A2', status='unsat'),
            dict(filename='tie.smt2', selection_group='unproved-tie',
                 proved_by=[], z3_reference_state='B',
                 optimathsat_reference_state='B', status='sat'),
            dict(filename='better.smt2', selection_group='unproved-better',
                 proved_by=[], z3_reference_state='C',
                 optimathsat_reference_state='C', status='unknown'),
        ]
        manifest['instances'] = rows
        summary = self.runner.write_summary(output, manifest, rows, True)
        self.assertEqual(summary['selection_groups'], {
            'proved-equal': {'selected': 1, 'completed': 1, 'statuses': {'unsat': 1}},
            'unproved-tie': {'selected': 1, 'completed': 1, 'statuses': {'sat': 1}},
            'unproved-better': {'selected': 1, 'completed': 1, 'statuses': {'unknown': 1}},
        })
        report = (output / 'summary.md').read_text()
        table = [line for line in report.splitlines() if line.startswith('|')]
        for group, expected_status in (('proved-equal', 'UNSAT'), ('unproved-tie', 'SAT'),
                                       ('unproved-better', 'unknown')):
            with self.subTest(group=group):
                matching = [line for line in table if group in line]
                self.assertEqual(len(matching), 1, report)
                # selected, UNSAT, SAT, unknown, timeout, error, interrupted
                expected = {'UNSAT': [1, 1, 0, 0, 0, 0, 0],
                            'SAT': [1, 0, 1, 0, 0, 0, 0],
                            'unknown': [1, 0, 0, 1, 0, 0, 0]}[expected_status]
                numeric = [int(value.strip()) for value in matching[0].split('|')[2:-1]]
                self.assertEqual(numeric, expected, report)
        for line in table:
            if '仅OptiMathSAT原已证明最优' in line:
                self.assertEqual(int(line.split('|')[2].strip()), 1, report)
        self.assertNotIn('1200秒', report)


if __name__ == '__main__':
    unittest.main()
