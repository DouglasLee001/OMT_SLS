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


def fixture_run(root, name, entries, *, complete=False,
                nodes=('server', 'node1', 'node2', 'node4', 'node5'), jobs_per_node=40):
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
        'nodes': list(nodes), 'node_count': len(nodes),
        'jobs_per_node': jobs_per_node, 'jobs': len(nodes) * jobs_per_node,
        'timeout': 1200, 'scalar_target_enabled': False,
        'known_optimum_stop_enabled': False}), encoding='utf-8')
    write_csv(directory / 'nia_ls.csv', NIA_FIELDS, nia)
    write_csv(directory / 'combined.csv', COMBINED_FIELDS, combined)
    write_csv(directory / 'results.csv', ['suite', 'category', 'filename', 'direction',
              'status', 'termination', 'error'], raw)
    # This deliberately disagrees with canonical tables. It must never be used.
    (directory / 'summary.json').write_text('{"solved": 999999, "comparisons": {}}')
    return directory


def round2_entries(*, gain=0, extra_losses=0):
    entries = [dict(name=f'QF_NIA/{i}.smt2') for i in range(5744)]
    entries += [dict(name=f'{i}.smt2', suite='new', category='design') for i in range(566)]
    return [dict(entry, state='A2' if i < 5942 + gain else 'B',
                 value='9' if i < 23 + extra_losses else '5')
            for i, entry in enumerate(entries)]


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

    def test_round2_requires_gain_50_against_explicit_5942_success_baseline(self):
        module = self.module()
        nodes = ('node1', 'node2', 'node3')
        baseline = fixture_run(self.root, 'fixed-005', round2_entries(), complete=True)
        before = {p.name: p.read_bytes() for p in baseline.iterdir()}
        for gain in (49, 50):
            with self.subTest(gain=gain):
                candidate = fixture_run(self.root, f'candidate-{gain}', round2_entries(gain=gain),
                                        complete=True, nodes=nodes, jobs_per_node=180)
                report, _ = module.compare_runs(baseline, candidate, required_gain=50,
                    expected_nodes=nodes, expected_jobs_per_node=180)
                self.assertEqual(report['baseline']['all']['solved']['NIA_LS'], 5942)
                self.assertEqual(report['goal']['required_gain'], 50)
                self.assertEqual(report['goal']['required_success'], 5992)
                self.assertEqual(report['goal']['maximum_best_competitor_losses'], 23)
                self.assertTrue(report['goal']['eligible_full_protocol'])
                self.assertEqual(report['goal']['observed_success_condition'], gain == 50)
                self.assertTrue(report['goal']['observed_loss_condition'])
                self.assertEqual(report['goal']['full_goal_achieved'], gain == 50)
                self.assertEqual(report['protocol'], dict(expected_nodes=list(nodes),
                    expected_node_count=3, expected_jobs_per_node=180, expected_jobs=540,
                    timeout_seconds=1200))
                rendered = module.render_report(report)
                self.assertIn('gain >= 50', rendered)
                self.assertIn('node1/node2/node3', rendered)
                self.assertIn('180 jobs per node (540 total)', rendered)
        self.assertEqual({p.name: p.read_bytes() for p in baseline.iterdir()}, before)

    def test_round2_gain_does_not_waive_loss_cap(self):
        baseline = fixture_run(self.root, 'fixed-005', round2_entries(), complete=True)
        candidate = fixture_run(self.root, 'more-losses', round2_entries(gain=50, extra_losses=1),
                                complete=True, nodes=('node3', 'node2', 'node1'), jobs_per_node=180)
        report, _ = self.module().compare_runs(baseline, candidate, required_gain=50,
            expected_nodes=('node1', 'node2', 'node3'), expected_jobs_per_node=180)
        self.assertTrue(report['goal']['eligible_full_protocol'])
        self.assertTrue(report['goal']['observed_success_condition'])
        self.assertFalse(report['goal']['observed_loss_condition'])
        self.assertFalse(report['goal']['full_goal_achieved'])

    def test_round2_candidate_resources_must_match_each_explicit_expectation(self):
        module = self.module()
        baseline = fixture_run(self.root, 'fixed-005', round2_entries(), complete=True)
        candidate = fixture_run(self.root, 'candidate', round2_entries(gain=50),
                                complete=True, nodes=('node1', 'node2', 'node3'), jobs_per_node=180)
        path = candidate / 'manifest.json'
        valid = json.loads(path.read_text())
        for changes in [dict(jobs_per_node=40, jobs=120),
                        dict(nodes=['node1', 'node2', 'node4']),
                        dict(node_count=5), dict(jobs=200)]:
            with self.subTest(changes=changes):
                path.write_text(json.dumps(dict(valid, **changes)))
                report, _ = module.compare_runs(baseline, candidate, required_gain=50,
                    expected_nodes=('node1', 'node2', 'node3'), expected_jobs_per_node=180)
                self.assertTrue(report['goal']['observed_success_condition'])
                self.assertFalse(report['goal']['eligible_full_protocol'])
                self.assertFalse(report['goal']['full_goal_achieved'])

    def test_report_defaults_record_original_gain_and_resource_protocol(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2')])
        report, _ = self.module().compare_runs(baseline, candidate)
        self.assertEqual(report['goal']['required_gain'], 45)
        self.assertEqual(report['protocol'], dict(expected_nodes=['server', 'node1', 'node2', 'node4', 'node5'],
            expected_node_count=5, expected_jobs_per_node=40, expected_jobs=200, timeout_seconds=1200))

    def test_explicit_protocol_keeps_objective_ties_despite_proof_status_and_time(self):
        entries = [dict(name='QF_NIA/tie.smt2', state='A2', value='5', z3_value='5')]
        baseline = fixture_run(self.root, 'base', entries)
        candidate = fixture_run(self.root, 'candidate', [dict(entries[0], state='B')])
        for filename, field in [('nia_ls.csv', '时间（秒）'), ('combined.csv', 'NIA_LS时间（秒）')]:
            path = candidate / filename
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            rows[0][field] = '1199'
            write_csv(path, list(rows[0]), rows)
        report, rows = self.module().compare_runs(baseline, candidate, required_gain=50,
            expected_nodes=('node1', 'node2', 'node3'), expected_jobs_per_node=180)
        self.assertEqual(report['common']['candidate_vs_baseline'], dict(win=0, tie=1, loss=0))
        self.assertEqual(rows[0]['candidate_vs_baseline'], 'tie')
        self.assertEqual(rows[0]['candidate_vs_best_competitor'], 'tie')
        self.assertEqual(report['common']['success_delta'], -1)

    def test_compare_rejects_invalid_goal_and_resource_configuration(self):
        module = self.module()
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2')])
        invalid = {
            'required_gain': [True, False, None, '50', 50.0, 50.5, float('nan'), float('inf'), -1],
            'expected_jobs_per_node': [True, False, None, '180', 180.0, 1.5, float('nan'), float('inf'), 0, -1],
            'expected_nodes': [None, 'node1', [], (), ['node1', 'node1'], [''], [' '],
                               [' node1'], ['node1 '], ['node 1'], ['node\t1'], ['node\n1'],
                               ['node1', 2], {'node1': 1}],
        }
        for keyword, values in invalid.items():
            for value in values:
                with self.subTest(keyword=keyword, value=value), self.assertRaises(ValueError):
                    module.compare_runs(baseline, candidate, **{keyword: value})
        report, _ = module.compare_runs(baseline, candidate, required_gain=0,
                                       expected_nodes=['node1'], expected_jobs_per_node=1)
        self.assertEqual(report['goal']['required_success'], 0)
        self.assertEqual(report['protocol']['expected_jobs'], 1)

    def test_cli_accepts_explicit_round2_goal_and_resource_configuration(self):
        baseline = fixture_run(self.root, 'fixed-005', round2_entries(), complete=True)
        candidate = fixture_run(self.root, 'candidate', round2_entries(gain=50),
                                complete=True, nodes=('node1', 'node2'), jobs_per_node=180)
        before = {p.name: p.read_bytes() for p in baseline.iterdir()}
        result = subprocess.run([sys.executable, str(SCRIPT), '--baseline-run', str(baseline),
            '--candidate-run', str(candidate), '--required-gain', '50',
            '--expected-nodes', 'node1,node2', '--expected-jobs-per-node', '180'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((candidate / 'deepmix_compare.json').read_text())
        self.assertTrue(report['goal']['full_goal_achieved'])
        self.assertEqual(report['goal']['required_success'], 5992)
        self.assertEqual(report['protocol']['expected_jobs'], 360)
        self.assertEqual({p.name: p.read_bytes() for p in baseline.iterdir()}, before)

    def test_cli_configuration_errors_return_2_without_writing_either_run(self):
        baseline = fixture_run(self.root, 'base', [dict(name='QF_NIA/a.smt2')])
        candidate = fixture_run(self.root, 'candidate', [dict(name='QF_NIA/a.smt2')])
        before = {directory: {p.name: p.read_bytes() for p in directory.iterdir()}
                  for directory in (baseline, candidate)}
        invalid = [('--required-gain', '-1'), ('--required-gain', 'NaN'),
                   ('--required-gain', '50.0'), ('--expected-jobs-per-node', '0'),
                   ('--expected-jobs-per-node', 'inf'), ('--expected-nodes', ''),
                   ('--expected-nodes', 'node1,node1'), ('--expected-nodes', 'node1, node2'),
                   ('--expected-nodes', 'node1,,node2')]
        for option, value in invalid:
            with self.subTest(option=option, value=value):
                result = subprocess.run([sys.executable, str(SCRIPT), '--baseline-run', str(baseline),
                    '--candidate-run', str(candidate), option, value], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual({directory: {p.name: p.read_bytes() for p in directory.iterdir()}
                                 for directory in (baseline, candidate)}, before)


if __name__ == '__main__':
    unittest.main()
