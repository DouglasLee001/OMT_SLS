import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'experiment/deepmix_compare.py'
NIA_FIELDS = ['文件名', '状态', '目标值', '时间（秒）']
COMBINED_FIELDS = ['文件名', 'z3状态', 'z3目标值', 'z3时间（秒）',
                   'optimathsat状态', 'optimathsat目标值', 'optimathsat时间（秒）',
                   'NIA_LS状态', 'NIA_LS目标值', 'NIA_LS时间（秒）']


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fixture_run(root, name, entries, *, complete=False):
    directory = root / name
    directory.mkdir()
    cases, nia, combined, raw = [], [], [], []
    for entry in entries:
        case = dict(suite=entry.get('suite', 'original'),
                    category=entry.get('category', 'original'),
                    filename=entry['name'], direction=entry.get('direction', 'min'))
        canonical = (case['filename'] if case['suite'] == 'original' else
                     f'new_benchmark/{case["category"]}/instance/{case["filename"]}')
        cases.append(case)
        state, value = entry.get('state', 'B'), entry.get('value', '5')
        nia.append(dict(zip(NIA_FIELDS, [canonical, state, value, '1.25'])))
        combined.append(dict(zip(COMBINED_FIELDS, [canonical,
                            entry.get('z3_state', 'B'), entry.get('z3_value', '8'), '1',
                            entry.get('omt_state', 'B'), entry.get('omt_value', '7'), '1',
                            state, value, '1.25'])))
        raw.append({**case, 'status': entry.get('native_status', 'sat'),
                    'termination': entry.get('termination', 'wall_timeout'),
                    'error': entry.get('error', '')})
    (directory / 'manifest.json').write_text(json.dumps({
        'cases': cases, 'selection_complete': complete, 'selection_suite': 'all',
        'nodes': ['server', 'node1', 'node2', 'node4', 'node5'],
        'node_count': 5, 'jobs_per_node': 40, 'jobs': 200,
        'timeout': 1200, 'scalar_target_enabled': False,
        'known_optimum_stop_enabled': False}), encoding='utf-8')
    write_csv(directory / 'nia_ls.csv', NIA_FIELDS, nia)
    write_csv(directory / 'combined.csv', COMBINED_FIELDS, combined)
    write_csv(directory / 'results.csv', ['suite', 'category', 'filename', 'direction',
              'status', 'termination', 'error'], raw)
    # This deliberately disagrees with canonical tables. It must never be used.
    (directory / 'summary.json').write_text('{"solved": 999999, "comparisons": {}}')
    return directory


class DeepmixCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='deepmix-compare-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'deepmix comparison entry point is missing')
        spec = importlib.util.spec_from_file_location('deepmix_compare_under_test', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_writes_only_candidate_and_uses_canonical_tables(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2', state='B')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2', state='A2')])
        before = {p.name: p.read_bytes() for p in baseline.iterdir()}
        result = subprocess.run([sys.executable, str(SCRIPT), '--baseline-run', str(baseline),
                                 '--candidate-run', str(candidate)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((candidate / 'deepmix_compare.json').read_text())
        self.assertEqual(report['baseline']['all']['solved']['NIA_LS'], 0)
        self.assertEqual(report['candidate']['all']['solved']['NIA_LS'], 1)
        self.assertFalse(report['goal']['full_goal_achieved'])
        self.assertEqual({p.name: p.read_bytes() for p in baseline.iterdir()}, before)
        self.assertTrue((candidate / 'deepmix_compare.md').is_file())
        self.assertTrue((candidate / 'deepmix_cases.csv').is_file())

    def test_exact_large_integer_min_max_infinity_and_na(self):
        giant = '9' * 5000
        base_entries = [
            dict(name='QF_NIA/min.smt2', value='9007199254740993', z3_value='9007199254740992', omt_value='9007199254740993'),
            dict(name='max.smt2', suite='new', category='design', direction='max', value='7', z3_value='8', omt_value='6'),
            dict(name='QF_NIA/inf.smt2', state='A1', value='-oo', z3_value=giant, omt_value='-1'),
            dict(name='QF_NIA/na.smt2', state='C', value='NA', z3_state='C', z3_value='NA', omt_state='C', omt_value='NA'),
        ]
        candidates = [dict(e) for e in base_entries]
        candidates[0]['value'] = '9007199254740992'
        candidates[1]['value'] = '9'
        baseline = fixture_run(self.root, 'base', base_entries)
        candidate = fixture_run(self.root, 'candidate', candidates)
        report, rows = self.module().compare_runs(baseline, candidate)
        self.assertEqual(report['baseline']['all']['nia_wdl']['best_competitor'], {'win': 1, 'tie': 1, 'loss': 2})
        self.assertEqual(report['candidate']['all']['nia_wdl']['best_competitor'], {'win': 2, 'tie': 2, 'loss': 0})
        self.assertEqual(report['common']['candidate_vs_baseline'], {'win': 2, 'tie': 2, 'loss': 0})
        self.assertEqual(report['common_unsolved']['instances'], 3)
        self.assertEqual(len(rows), 4)
        self.assertEqual(report['provenance']['baseline']['nia_ls.csv']['sha256'].__len__(), 64)

    def test_subset_reports_coverage_without_claiming_full_goal(self):
        entries = [dict(name=f'QF_NIA/{i}.smt2', state='B') for i in range(55)]
        baseline = fixture_run(self.root, 'base', entries, complete=True)
        candidate = fixture_run(self.root, 'candidate', [dict(e, state='A2') for e in entries[:50]])
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertEqual(report['coverage']['common_instances'], 50)
        self.assertEqual(len(report['coverage']['missing_candidate']), 5)
        self.assertEqual(report['common']['success_delta'], 50)
        self.assertFalse(report['goal']['full_goal_achieved'])
        self.assertFalse(report['coverage']['full_6310_coverage'])

    def test_complete_6310_target_uses_updated_gain_45_and_loss_cap(self):
        entries = [dict(name=f'QF_NIA/{i}.smt2', state='B') for i in range(5744)]
        entries += [dict(name=f'{i}.smt2', suite='new', category='design', state='B') for i in range(566)]
        baseline = fixture_run(self.root, 'base', entries, complete=True)
        candidate = fixture_run(self.root, 'candidate', [dict(e, state='A2') if i < 45 else e
                                      for i, e in enumerate(entries)], complete=True)
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertTrue(report['goal']['full_goal_achieved'])
        self.assertEqual(report['goal']['required_success'], 45)
        self.assertEqual(report['goal']['maximum_best_competitor_losses'], 0)
        self.assertTrue(report['coverage']['full_6310_coverage'])
        below = fixture_run(self.root, 'below-target', [dict(e, state='A2') if i < 44 else e
                                  for i, e in enumerate(entries)], complete=True)
        report, _ = self.module().compare_runs(baseline, below)
        self.assertFalse(report['goal']['observed_success_condition'])
        self.assertFalse(report['goal']['full_goal_achieved'])

    def test_full_goal_requires_current_five_node_40_protocol(self):
        entries = [dict(name=f'QF_NIA/{i}.smt2', state='B') for i in range(5744)]
        entries += [dict(name=f'{i}.smt2', suite='new', category='design', state='B') for i in range(566)]
        baseline = fixture_run(self.root, 'base', entries, complete=True)
        candidate = fixture_run(self.root, 'candidate', [dict(e, state='A2') if i < 50 else e
                                      for i, e in enumerate(entries)], complete=True)
        # The fixed historical baseline used six nodes at 80 cases per node.
        path = baseline / 'manifest.json'
        historical = json.loads(path.read_text())
        historical.update(nodes=['server', 'node1', 'node2', 'node3', 'node4', 'node5'],
                          node_count=6, jobs_per_node=80, jobs=480)
        path.write_text(json.dumps(historical))
        # Node order records pool/rank ordering and is not part of the contract.
        path = candidate / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['nodes'].reverse()
        path.write_text(json.dumps(manifest))
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertTrue(report['goal']['eligible_full_protocol'])
        self.assertTrue(report['goal']['full_goal_achieved'])
        # Only the candidate needs current resources; an old 3 x 120 candidate
        # remains readable but cannot establish the current full goal.
        path = candidate / 'manifest.json'
        old = dict(manifest, nodes=['server', 'node1', 'node2'],
                   node_count=3, jobs_per_node=120, jobs=360)
        path.write_text(json.dumps(old))
        try:
            report, _ = self.module().compare_runs(baseline, candidate)
            self.assertFalse(report['goal']['eligible_full_protocol'])
            self.assertFalse(report['goal']['full_goal_achieved'])
        finally:
            path.write_text(json.dumps(manifest))
        # Historical resource metadata does not waive the shared protocol.
        path = baseline / 'manifest.json'
        for field, value in [('selection_complete', False), ('timeout', 1199),
                             ('scalar_target_enabled', True), ('known_optimum_stop_enabled', True)]:
            with self.subTest(invalid_historical_protocol=field):
                path.write_text(json.dumps(dict(historical, **{field: value})))
                try:
                    report, _ = self.module().compare_runs(baseline, candidate)
                    self.assertFalse(report['goal']['eligible_full_protocol'])
                    self.assertFalse(report['goal']['full_goal_achieved'])
                finally:
                    path.write_text(json.dumps(historical))

    def test_native_errors_and_missing_error_evidence_block_full_claim(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2', native_status='error', error='failed')])
        report, rows = self.module().compare_runs(baseline, candidate)
        self.assertEqual(report['candidate']['native']['errors'], 1)
        self.assertEqual(rows[0]['candidate_native_status'], 'error')
        (candidate / 'results.csv').unlink()
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertFalse(report['candidate']['native']['available'])
        self.assertFalse(report['goal']['full_goal_achieved'])

    def test_sat_proof_diagnostic_is_reported_without_counting_as_native_error(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2',
                               error='optimization proof returned unknown')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2')])
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertEqual(report['baseline']['native']['errors'], 0)
        self.assertEqual(report['baseline']['native']['diagnostics'], 1)
        self.assertEqual(report['baseline']['native']['diagnostic_instances'], ['QF_NIA/a.smt2'])

    def test_rejects_duplicate_unknown_names_and_nia_combined_disagreement(self):
        for failure in ('duplicate', 'unknown', 'disagreement', 'direction', 'opponent'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                root = Path(tmp)
                baseline = fixture_run(root, 'base', [dict(name='QF_NIA/a.smt2')])
                candidate = fixture_run(root, 'candidate', [dict(name='QF_NIA/a.smt2')])
                if failure == 'direction':
                    manifest = json.loads((candidate / 'manifest.json').read_text())
                    manifest['cases'][0]['direction'] = 'max'
                    (candidate / 'manifest.json').write_text(json.dumps(manifest))
                else:
                    path = candidate / ('nia_ls.csv' if failure != 'opponent' else 'combined.csv')
                    with path.open(encoding='utf-8-sig', newline='') as handle:
                        rows = list(csv.DictReader(handle))
                    fields = list(rows[0])
                    if failure == 'duplicate':
                        rows.append(dict(rows[0]))
                    elif failure == 'unknown':
                        rows[0]['文件名'] = 'QF_NIA/alien.smt2'
                    elif failure == 'disagreement':
                        rows[0]['目标值'] = '6'
                    else:
                        rows[0]['z3目标值'] = '11'
                    write_csv(path, fields, rows)
                with self.assertRaises(ValueError):
                    self.module().compare_runs(baseline, candidate)

    def test_cli_rejects_same_run_to_protect_baseline(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2')])
        before = {p.name: p.read_bytes() for p in baseline.iterdir()}
        result = subprocess.run([sys.executable, str(SCRIPT), '--baseline-run', str(baseline),
                                 '--candidate-run', str(baseline)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual({p.name: p.read_bytes() for p in baseline.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
