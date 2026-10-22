"""Independent retained-artifact and historical-optimum verifier contracts."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_verifier():
    spec = importlib.util.spec_from_file_location('verify_optimize', ROOT / 'script/verify_optimize.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OptimizationVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = load_verifier()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for name, value in [('ROOT', self.root), ('ORIGINAL_ROOT', self.root / 'benchmarks')]:
            patch = mock.patch.object(self.verifier, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.source = self.root / 'benchmarks/a.smt2'
        self.source.parent.mkdir()
        self.source.write_text('(declare-const x Int)\n(assert (>= x 0))\n(minimize x)\n(check-sat)\n')
        self.run = self.root / 'run'
        self.artifact = self.run / 'cases' / ('0000-' + hashlib.sha256(str(self.source).encode()).hexdigest()[:12])
        self.artifact.mkdir(parents=True)
        self.case = dict(source=str(self.source), suite='original', category='original',
                         filename='a.smt2', direction='min', z3='8', optimathsat='10',
                         known_optimum_value='8', known_optimum_source='z3')
        self.row = dict(filename='a.smt2', category='original', suite='original', direction='min',
                        status='sat', termination='known_optimum_reached', best_value='8',
                        time_to_first_seconds='0.15', time_to_best_seconds='0.15', wall_seconds='0.2',
                        model_validated='true', is_optimal='false', z3_best_value='8',
                        optimathsat_best_value='10', reference_best_value='8', vs_z3='tie',
                        vs_optimathsat='better', vs_best='tie', strictly_better_than_best='false',
                        known_optimum_value='8', known_optimum_source='z3', known_optimum_reached='true', error='')
        self.manifest = dict(cases=[self.case], subset='bounded', timeout=1, jobs=1,
                             scalar_target_enabled=False, known_optimum_stop_enabled=True)
        self.write_tables()
        self.write_artifacts()

    def write_tables(self, z3='8', z3_proven='true', oms='10', oms_proven='false', subset='bounded'):
        suffix = 'bound' if subset == 'bounded' else subset
        for suite in ('original', 'new'):
            path = self.root / 'result' / suite / f'{suite}_{suffix}.csv'
            path.parent.mkdir(parents=True, exist_ok=True)
            fields = ['filename', 'z3_best_value', 'z3_is_optimal',
                      'optimathsat_best_value', 'optimathsat_is_optimal']
            if suite == 'new':
                fields.insert(1, 'category')
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                if suite == 'original':
                    writer.writerow(dict(filename='a.smt2', z3_best_value=z3, z3_is_optimal=z3_proven,
                                         optimathsat_best_value=oms, optimathsat_is_optimal=oms_proven))

    def native(self):
        return ('NIA_LS_OMT 1\nSTATUS ready\nSOURCE ' + str(self.source).encode().hex() + '\n'
                'ORIGINAL_OBJECTIVE 78\nDIRECTION ' + self.case['direction'] + '\n'
                'VARIABLES 1\n0 I 78\nLITERALS 1\n1 L 0 1\n-1 1 0 1\n'
                'CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n')

    def write_artifacts(self, final=None, value='8'):
        event = dict(event='incumbent', status='sat', direction=self.case['direction'],
                     objective_value=value, elapsed_seconds=0.1, model_validated=True, is_optimal=False)
        events = [event]
        if final is not None:
            events.append(dict(event, event='final', elapsed_seconds=0.2,
                               time_to_best_seconds=0.1, termination=final))
        (self.artifact / 'native.stdout').write_text(''.join(json.dumps(e) + '\n' for e in events))
        progress = dict(objective_value=value, elapsed_seconds=0.1, time_seconds=0.15, model_validated=True)
        (self.artifact / 'progress.jsonl').write_text(json.dumps(progress) + '\n')
        (self.artifact / 'input.nia').write_text(self.native())
        (self.artifact / 'model.tsv').write_text('x\t' + value + '\n')

    def save_run(self):
        (self.run / 'manifest.json').write_text(json.dumps(self.manifest))
        with (self.run / 'results.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerow(self.row)
        wins = self.row['strictly_better_than_best'] == 'true'
        (self.run / 'summary.json').write_text(json.dumps(dict(selected=1, completed=1,
            strictly_better_than_best=int(wins), strict_win_rate=float(wins), over_80_percent=wins)))

    def verify(self):
        self.save_run()
        return self.verifier.verify_run(self.run, expected_cases=1)

    def assert_rejected(self, fragment=None):
        report = self.verify()
        self.assertFalse(report['ok'], report)
        if fragment:
            errors = report['errors'] + [c.get('error', '') for c in report['cases']]
            self.assertIn(fragment, '\n'.join(errors), report)

    def ordinary_final(self):
        self.row.update(termination='timeout', known_optimum_reached='true')
        self.write_artifacts(final='timeout')

    def test_known_optimum_accepts_external_stop_without_native_final(self):
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['cases'][0]['model_checked'])
        self.assertTrue(report['cases'][0]['verified_known_optimum_reached'])

    def test_known_optimum_accepts_different_native_final_termination(self):
        self.write_artifacts(final='timeout')
        self.assertTrue(self.verify()['ok'])

    def test_known_optimum_maximize_reads_source_direction(self):
        self.source.write_text('(declare-const x Int)\n(maximize x)\n(check-sat)\n')
        self.case.update(direction='max', optimathsat='6')
        self.row.update(direction='max', optimathsat_best_value='6')
        self.write_tables(oms='6')
        self.write_artifacts()
        self.assertTrue(self.verify()['ok'])

    def test_finite_proven_source_can_ignore_other_solver_missing_value(self):
        self.case.update(z3=None, optimathsat='8', known_optimum_source='optimathsat')
        self.row.update(z3_best_value='', optimathsat_best_value='8', vs_z3='no_reference',
                        vs_optimathsat='tie', known_optimum_source='optimathsat')
        self.write_tables(z3='', z3_proven='true', oms='8', oms_proven='true')
        self.assertTrue(self.verify()['ok'])

    def test_both_proven_sources_use_exact_unary_negative_values(self):
        value = '-99999999999999999999999999999999999999999999999999999999999'
        self.case.update(z3=value, optimathsat=value, known_optimum_value=value,
                         known_optimum_source='z3+optimathsat')
        self.row.update(best_value=value, z3_best_value=value, optimathsat_best_value=value,
                        reference_best_value=value, vs_optimathsat='tie', known_optimum_value=value,
                        known_optimum_source='z3+optimathsat')
        self.write_tables(z3=f'(- {value[1:]})', z3_proven='TRUE', oms=value, oms_proven='true')
        self.write_artifacts(value=value)
        # The synthetic model's hard constraint must also permit this negative value.
        path = self.artifact / 'input.nia'
        path.write_text(path.read_text().replace('-1 1 0 1', '1 1 0 1'))
        self.assertTrue(self.verify()['ok'])

    def test_unproven_reference_cannot_authorize_known_optimum(self):
        self.ordinary_final()
        self.write_tables(z3_proven='false')
        self.assert_rejected('known optimum')

    def test_historical_optimum_value_and_sources_are_checked_independently(self):
        self.ordinary_final()
        for z3, z3_proven, oms, oms_proven in [('7', 'true', '10', 'false'),
                ('8', 'false', '8', 'true'), ('8', 'true', '9', 'true'),
                ('8', 'true', '7', 'false'), ('', 'true', '10', 'false')]:
            with self.subTest(z3=z3, oms=oms, z3_proven=z3_proven, oms_proven=oms_proven):
                self.write_tables(z3, z3_proven, oms, oms_proven)
                self.assert_rejected()

    def test_manifest_direction_and_source_must_match_historical_case(self):
        self.ordinary_final()
        self.source.write_text('(maximize x)\n(check-sat)\n')
        self.assert_rejected('direction')

    def test_forged_csv_value_source_and_hit_flag_are_rejected(self):
        self.ordinary_final()
        for field, value in [('known_optimum_value', '7'), ('known_optimum_source', 'optimathsat'),
                             ('known_optimum_reached', 'false'), ('known_optimum_reached', 'TRUE')]:
            with self.subTest(field=field):
                old = self.row[field]
                self.row[field] = value
                self.assert_rejected('known optimum')
                self.row[field] = old

    def test_natural_completion_and_disabled_stopping_can_report_equality(self):
        self.ordinary_final()
        self.manifest['known_optimum_stop_enabled'] = False
        self.assertTrue(self.verify()['ok'])

    def test_known_stop_requires_enabled_setting_and_true_hit_flag(self):
        self.write_artifacts(final='known_optimum_reached')
        self.manifest['known_optimum_stop_enabled'] = False
        self.assert_rejected('known optimum')
        self.manifest['known_optimum_stop_enabled'] = True
        self.row['known_optimum_reached'] = 'false'
        self.assert_rejected('known optimum')

    def test_known_stop_requires_exact_best_equal_optimum(self):
        self.write_artifacts(final='known_optimum_reached', value='7')
        self.row.update(best_value='7', vs_z3='better', vs_optimathsat='better',
                        vs_best='better', strictly_better_than_best='true')
        self.assert_rejected('known optimum')

    def test_known_stop_cannot_hide_native_error_final(self):
        for termination, error in [('error', None), ('timeout', 'native failure')]:
            with self.subTest(termination=termination, error=error):
                self.write_artifacts(final=termination)
                path = self.artifact / 'native.stdout'
                events = [json.loads(line) for line in path.read_text().splitlines()]
                if error is not None:
                    events[-1]['error'] = error
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                self.assert_rejected('known optimum')

    def test_known_stop_still_checks_retained_model(self):
        self.write_artifacts(final='known_optimum_reached')
        for contents in [None, 'x\t-1\n', 'x\t7\n']:
            with self.subTest(model=contents):
                path = self.artifact / 'model.tsv'
                if contents is None:
                    path.unlink()
                else:
                    path.write_text(contents)
                self.assert_rejected()

    def test_known_stop_still_checks_raw_events_progress_budget_and_native_proof_flag(self):
        for mutation in ['raw_objective', 'late_progress', 'missing_progress', 'native_proof']:
            with self.subTest(mutation=mutation):
                self.write_artifacts(final='known_optimum_reached')
                if mutation in ('raw_objective', 'native_proof'):
                    path = self.artifact / 'native.stdout'
                    events = [json.loads(line) for line in path.read_text().splitlines()]
                    if mutation == 'raw_objective':
                        events[0]['objective_value'] = '9'
                    else:
                        events[0]['is_optimal'] = True
                    path.write_text(''.join(json.dumps(e) + '\n' for e in events))
                else:
                    path = self.artifact / 'progress.jsonl'
                    record = json.loads(path.read_text())
                    record['time_seconds'] = 1.1
                    path.write_text('' if mutation == 'missing_progress' else json.dumps(record) + '\n')
                self.assert_rejected()

    def test_legacy_runs_need_no_historical_proof_lookup(self):
        self.ordinary_final()
        del self.manifest['known_optimum_stop_enabled']
        for key in ('known_optimum_value', 'known_optimum_source'):
            del self.case[key]
        for key in ('known_optimum_value', 'known_optimum_source', 'known_optimum_reached'):
            del self.row[key]
        import shutil
        shutil.rmtree(self.root / 'result')
        self.assertTrue(self.verify()['ok'])

    def test_legacy_termination_rules_are_preserved(self):
        self.ordinary_final()
        del self.manifest['known_optimum_stop_enabled']
        for key in ('known_optimum_value', 'known_optimum_source'):
            del self.case[key]
        for key in ('known_optimum_value', 'known_optimum_source', 'known_optimum_reached'):
            del self.row[key]
        self.row['termination'] = 'target_beaten'
        self.write_artifacts(final='timeout')
        self.assert_rejected('termination mismatch')
        self.row['termination'] = 'wall_timeout'
        self.assertTrue(self.verify()['ok'])
        self.row['status'] = 'unknown'
        self.assert_rejected('status')

    def test_known_stop_cannot_omit_new_metadata(self):
        self.write_artifacts(final='known_optimum_reached')
        del self.manifest['known_optimum_stop_enabled']
        for key in ('known_optimum_value', 'known_optimum_source'):
            del self.case[key]
        for key in ('known_optimum_value', 'known_optimum_source', 'known_optimum_reached'):
            del self.row[key]
        self.assert_rejected('known optimum')

    def populate_canonical(self, subset, count, blank_reference=False):
        suffix = 'bound' if subset == 'bounded' else subset
        self.write_tables(subset=subset)
        path = self.root / 'result/original' / f'original_{suffix}.csv'
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['filename', 'z3_best_value', 'optimathsat_best_value'])
            for index in range(count):
                name = f'{index}.smt2'
                (self.source.parent / name).write_text('(minimize x)\n(check-sat)\n')
                writer.writerow([name, '' if blank_reference else '8', ''])

    def test_default_canonical_selection_preserves_legacy_fields_and_denominator(self):
        self.populate_canonical('unsolved_bounded_sol', 473)
        selection, tables = self.verifier.canonical_selection()
        self.assertEqual(len(selection), 473)
        self.assertEqual(set(next(iter(selection.values()))), {'source', 'direction', 'z3', 'optimathsat'})
        self.assertTrue(all('unsolved_bounded_sol.csv' in table['path'] for table in tables))

    def test_bounded_canonical_selection_allows_empty_references_and_checks_denominator(self):
        self.populate_canonical('bounded', 4262, blank_reference=True)
        selection, tables = self.verifier.canonical_selection(subset='bounded')
        self.assertEqual(len(selection), 4262)
        self.assertTrue(all(case['z3'] is None and case['optimathsat'] is None for case in selection.values()))
        self.assertTrue(all('_bound.csv' in table['path'] for table in tables))
        path = self.root / 'result/original/original_bound.csv'
        path.write_text(path.read_text().rsplit('\n', 2)[0] + '\n')
        with self.assertRaises(self.verifier.VerificationError):
            self.verifier.canonical_selection(subset='bounded')

    def test_bounded_limit_473_uses_limited_proof_lookup_not_unsolved_canonical(self):
        cases, rows, proofs = [], [], {}
        for index in range(473):
            case = dict(self.case)
            if index:
                case.update(filename=f'{index}.smt2', source=str(self.source.parent / f'{index}.smt2'))
            row = dict(self.row, filename=case['filename'])
            key = case['suite'], case['category'], case['filename']
            proofs[key] = {field: case[field] for field in ('source', 'direction', 'z3', 'optimathsat',
                                                           'known_optimum_value', 'known_optimum_source')}
            cases.append(case)
            rows.append(row)
            artifact = self.run / 'cases' / (f'{index:04d}-' + hashlib.sha256(case['source'].encode()).hexdigest()[:12])
            artifact.mkdir(exist_ok=True)
        self.manifest['cases'] = cases
        self.save_run()
        with (self.run / 'results.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerows(rows)
        (self.run / 'summary.json').write_text(json.dumps(dict(selected=473, completed=473,
            strictly_better_than_best=0, strict_win_rate=0, over_80_percent=False)))
        # This test isolates provenance routing; model and event checks have separate artifact tests.
        with mock.patch.object(self.verifier, 'canonical_selection',
                               side_effect=AssertionError('bounded limit must not use unsolved canonical')) as canonical, \
             mock.patch.object(self.verifier, 'historical_selection', return_value=(proofs, [])) as historical, \
             mock.patch.object(self.verifier, 'verify_case', return_value=dict(ok=True, model_checked=True)):
            report = self.verifier.verify_run(self.run, expected_cases=473)
        self.assertTrue(report['ok'], report)
        self.assertFalse(report['canonical_selection_checked'])
        self.assertTrue(report['known_optimum_metadata_checked'])
        canonical.assert_not_called()
        historical.assert_called_once_with('bounded', set(proofs), include_proofs=True)


if __name__ == '__main__':
    unittest.main()
