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
            strictly_better_than_best=int(wins), strict_win_rate=float(wins), over_80_percent=wins,
            **({'large_target_magnitude': self.manifest['large_target_magnitude'],
                'large_target_reached': int(self.row.get('large_target_reached') == 'true')}
               if 'large_target_magnitude' in self.manifest else {}))))

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

    def test_independent_finite_proof_checks_native_metadata_and_exact_retained_model(self):
        self.manifest.update(prove_optimal=True, optimality_stall_seconds=.05,
                             optimality_timeout_seconds=.03, known_optimum_stop_enabled=False)
        metadata = dict(prove_optimal=True, optimality_stall_seconds=.05,
                        optimality_timeout_seconds=.03, optimality_attempts=1,
                        optimality_seconds=.02, optimality_status='proved',
                        time_to_optimality_seconds=.2, optimality_proved_value='8')
        self.row.update(termination='optimality_proved', is_optimal='true', wall_seconds='.4',
                        **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in metadata.items()})
        self.row['time_to_optimality_seconds'] = '.25'
        self.write_artifacts(final='optimality_proved')
        path = self.artifact / 'native.stdout'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1].update(metadata, is_optimal=True, elapsed_seconds=.3)
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['verified_is_optimal'], 1)
        events[-1]['optimality_proved_value'] = '7'
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        self.assert_rejected('proved value')
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

    def prepare_large_target(self, magnitude='8', direction='max', final=True):
        self.manifest['large_target_magnitude'] = magnitude
        self.case.update(direction=direction, optimathsat='6' if direction == 'max' else '10')
        self.source.write_text(f'(declare-const x Int)\n({"maximize" if direction == "max" else "minimize"} x)\n(check-sat)\n')
        self.write_tables(oms=self.case['optimathsat'])
        self.row.update(direction=direction, optimathsat_best_value=self.case['optimathsat'])
        reached = magnitude is not None and (8 >= int(magnitude) if direction == 'max' else 8 <= -int(magnitude))
        native_metadata = dict(large_target_magnitude=magnitude, large_target_attempts=1 if magnitude else 0,
            large_target_slices=2 if magnitude else 0, large_target_seconds=.02 if magnitude else 0,
            large_target_reached=reached,
            large_target_status='disabled' if magnitude is None else 'reached' if reached else 'searching')
        self.row.update({key: (str(value).lower() if isinstance(value, bool) else str(value))
                         for key, value in native_metadata.items()})
        self.row['large_target_magnitude'] = '' if magnitude is None else magnitude
        self.write_artifacts(final='timeout' if final else None)
        if final:
            self.row['termination'] = 'timeout'
            path = self.artifact / 'native.stdout'
            events = [json.loads(line) for line in path.read_text().splitlines()]
            events[-1].update(native_metadata)
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        else:
            for key in native_metadata:
                if key != 'large_target_magnitude':
                    self.row[key] = ''
            if reached:
                self.row['large_target_reached'] = 'true'

    def test_large_target_checks_exact_threshold_native_counters_and_retained_model(self):
        self.prepare_large_target()
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['cases'][0]['verified_large_target_reached'])
        self.assertEqual(report['verified_large_target_reached'], 1)
        self.row['is_optimal'] = 'true'
        self.assert_rejected('unproved optimality')

    def test_large_target_subthreshold_and_disabled_final_are_not_claimed_as_reached(self):
        self.prepare_large_target(direction='min')
        self.assertTrue(self.verify()['ok'])
        self.prepare_large_target(magnitude=None)
        self.assertTrue(self.verify()['ok'])

    def test_large_target_no_final_only_derives_positive_hit_and_leaves_accounting_unknown(self):
        self.prepare_large_target(final=False)
        self.assertTrue(self.verify()['ok'])
        self.row['large_target_attempts'] = '0'
        self.assert_rejected('large_target')
        self.prepare_large_target(magnitude='10', final=False)
        self.row['termination'] = 'wall_timeout'
        self.assertTrue(self.verify()['ok'])
        self.row['large_target_reached'] = 'false'
        self.assert_rejected('large_target')

    def test_large_target_rejects_bad_magnitudes_forged_flags_and_counters(self):
        for field, value in [('large_target_magnitude', '9'), ('large_target_reached', 'false'),
                             ('large_target_attempts', '-1'), ('large_target_slices', '1.5'),
                             ('large_target_seconds', 'nan'), ('large_target_seconds', '100')]:
            with self.subTest(field=field, value=value):
                self.prepare_large_target()
                self.row[field] = value
                self.assert_rejected('large_target')
        for magnitude in [True, 1.5, '0', '-1', str(2**127), str(2**127 + 1), '1e10']:
            with self.subTest(magnitude=magnitude):
                self.prepare_large_target()
                self.manifest['large_target_magnitude'] = magnitude
                self.assert_rejected('large_target')

    def test_large_target_signed_128_max_uses_exact_signed_model_value_at_both_boundaries(self):
        magnitude = 2**127 - 1
        self.assertEqual(self.verifier.large_target_magnitude(str(magnitude)), magnitude)
        for oversized in [2**127, 2**127 + 1]:
            with self.subTest(oversized=oversized), self.assertRaises(self.verifier.VerificationError):
                self.verifier.large_target_magnitude(str(oversized))
        for direction, value, reached in [('min', -magnitude, True), ('max', magnitude, True),
                                           ('min', -magnitude + 1, False), ('max', magnitude - 1, False)]:
            with self.subTest(direction=direction, value=value):
                self.prepare_large_target(magnitude=str(magnitude), direction=direction)
                self.case.update(z3=None, optimathsat=None, known_optimum_value=None, known_optimum_source='')
                self.row.update(best_value=str(value), z3_best_value='', optimathsat_best_value='',
                    reference_best_value='', vs_z3='no_reference', vs_optimathsat='no_reference',
                    vs_best='no_reference', strictly_better_than_best='false', known_optimum_value='',
                    known_optimum_source='', known_optimum_reached='false', large_target_reached=str(reached).lower(),
                    large_target_status='reached' if reached else 'waiting')
                self.write_tables(z3='', z3_proven='false', oms='', oms_proven='false')
                self.write_artifacts(final='timeout', value=str(value))
                if direction == 'min':
                    native = self.artifact / 'input.nia'
                    native.write_text(native.read_text().replace('-1 1 0 1', '1 1 0 1'))
                path = self.artifact / 'native.stdout'
                events = [json.loads(line) for line in path.read_text().splitlines()]
                events[-1].update(large_target_magnitude=str(magnitude), large_target_attempts=0,
                    large_target_slices=0, large_target_seconds=0, large_target_reached=reached,
                    large_target_status='reached' if reached else 'waiting')
                self.row.update(large_target_attempts='0', large_target_slices='0', large_target_seconds='0')
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                report = self.verify()
                self.assertTrue(report['ok'], report)
                self.assertEqual(report['cases'][0]['verified_large_target_reached'], reached)

    def test_large_target_rejects_forged_native_accounting_even_when_csv_agrees(self):
        for field, value in [('large_target_magnitude', '9'), ('large_target_reached', False),
                             ('large_target_attempts', True), ('large_target_slices', -1),
                             ('large_target_seconds', float('inf')), ('large_target_seconds', 100)]:
            with self.subTest(field=field, value=value):
                self.prepare_large_target()
                path = self.artifact / 'native.stdout'
                events = [json.loads(line) for line in path.read_text().splitlines()]
                events[-1][field] = value
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                self.row[field] = str(value).lower()
                self.assert_rejected('large_target')

    def test_large_target_status_cannot_claim_infinity_or_disagree_with_reached(self):
        for status, magnitude in [('unbounded', '8'), ('searching', '8'), ('reached', '10'),
                                  ('disabled', '10')]:
            with self.subTest(status=status, magnitude=magnitude):
                self.prepare_large_target(magnitude=magnitude)
                path = self.artifact / 'native.stdout'
                events = [json.loads(line) for line in path.read_text().splitlines()]
                events[-1]['large_target_status'] = status
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                self.row['large_target_status'] = status
                self.assert_rejected('large_target')

    def test_native_large_target_metadata_requires_manifest_configuration(self):
        self.prepare_large_target()
        del self.manifest['large_target_magnitude']
        for field in list(self.row):
            if field.startswith('large_target_'):
                del self.row[field]
        self.assert_rejected('large_target')

    def prepare_excluded(self, extras=1):
        self.manifest.update(subset='excluded', selection_suite='original', selection_complete=False)
        self.write_tables()
        self.ordinary_final()
        for suite in ['original', 'new']:
            root = self.root / 'result' / suite
            (root / f'{suite}.csv').write_text((root / f'{suite}_bound.csv').read_text())
            # All full rows, including a.smt2, are outside the bounded subset in this fixture.
            (root / f'{suite}_bound.csv').write_text((root / f'{suite}_bound.csv').read_text().splitlines()[0] + '\n')
        listing = self.root / 'result/original/original.csv'
        for index in range(1, extras):
            name = f'excluded-{index}.smt2'
            (self.source.parent / name).write_text('(minimize x)\n(check-sat)\n')
            with listing.open('a') as handle:
                handle.write(f'{name},-10000000001,false,,false\n')

    def test_excluded_limited_run_independently_checks_full_minus_bound_and_proof_metadata(self):
        self.prepare_excluded(extras=2)
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertFalse(report['canonical_selection_checked'])
        self.assertTrue(report['historical_selection_checked'])
        self.assertEqual(len(report['known_optimum_tables']), 4)
        bound = self.root / 'result/original/original_bound.csv'
        bound.write_text((self.root / 'result/original/original.csv').read_text())
        self.assert_rejected('historical')

    def test_excluded_complete_selection_is_audited_using_recorded_suite(self):
        self.prepare_excluded()
        self.manifest['selection_complete'] = True
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['canonical_selection_checked'])
        self.prepare_excluded(extras=2)
        self.manifest['selection_complete'] = True
        self.assert_rejected('complete')

    def test_excluded_checks_duplicates_missing_sources_and_bound_membership_outside_requested_keys(self):
        self.prepare_excluded(extras=2)
        full = self.root / 'result/original/original.csv'
        original = full.read_text()
        full.write_text(original + original.splitlines()[-1] + '\n')
        self.assert_rejected('duplicate')
        full.write_text(original)
        (self.source.parent / 'excluded-1.smt2').unlink()
        self.assert_rejected('source')
        (self.source.parent / 'excluded-1.smt2').write_text('(minimize x)\n(check-sat)\n')
        foreign = self.source.parent / 'foreign.smt2'
        foreign.write_text('(minimize x)\n(check-sat)\n')
        bound = self.root / 'result/original/original_bound.csv'
        bound.write_text(bound.read_text() + 'foreign.smt2,0,false,,false\n')
        self.assert_rejected('bounded')

    def prepare_all(self, extras=1):
        self.prepare_excluded(extras=extras)
        self.manifest['subset'] = 'all'
        # Full selection must not need either filtered table.
        for suite in ('original', 'new'):
            (self.root / 'result' / suite / f'{suite}_bound.csv').unlink()

    def add_full_new_case(self):
        source = self.root / 'new_benchmark/design/instance/b.smt2'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('(minimize x)\n(check-sat)\n')
        with (self.root / 'result/new/new.csv').open('a') as handle:
            handle.write('b.smt2,design,8,true,10,false\n')
        return source

    def test_all_historical_selection_reads_full_tables_without_filtered_tables(self):
        self.prepare_all(extras=2)
        self.add_full_new_case()
        selection, tables = self.verifier.historical_selection('all', include_proofs=True)
        self.assertEqual(len(selection), 3)
        self.assertEqual({Path(table['path']).name for table in tables}, {'original.csv', 'new.csv'})
        self.assertEqual(selection[('original', 'original', 'a.smt2')]['known_optimum_value'], '8')
        self.assertEqual(selection[('new', 'design', 'b.smt2')]['known_optimum_source'], 'z3')

    def test_all_canonical_selection_requires_6310_cases(self):
        selected = {index: {} for index in range(6310)}
        with mock.patch.object(self.verifier, 'historical_selection', return_value=(selected, [])) as historical:
            self.assertEqual(len(self.verifier.canonical_selection('all')[0]), 6310)
            historical.assert_called_once_with('all')
            selected.pop(0)
            with self.assertRaises(self.verifier.VerificationError):
                self.verifier.canonical_selection('all')

    def test_all_complete_original_selection_checks_full_source_coverage(self):
        self.prepare_all()
        self.add_full_new_case()
        self.manifest['selection_complete'] = True
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['canonical_selection_checked'])
        self.assertEqual(report['canonical_cases'], 1)
        self.manifest['selection_suite'] = 'all'
        self.assert_rejected('complete')

    def test_all_complete_new_selection_uses_generated_source_identity(self):
        self.prepare_all()
        self.source = self.add_full_new_case()
        self.case.update(source=str(self.source), suite='new', category='design', filename='b.smt2')
        self.row.update(suite='new', category='design', filename='b.smt2')
        artifact = self.artifact.parent / ('0000-' + hashlib.sha256(str(self.source).encode()).hexdigest()[:12])
        self.artifact.rename(artifact)
        self.artifact = artifact
        self.write_artifacts(final='timeout')
        self.manifest.update(selection_suite='new', selection_complete=True)
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['canonical_cases'], 1)
        self.case['source'] = str(self.root / 'benchmarks/a.smt2')
        self.assert_rejected('source')

    def test_all_partial_selection_checks_sources_and_duplicates_outside_selected_keys(self):
        self.prepare_all(extras=2)
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertFalse(report['canonical_selection_checked'])
        self.assertEqual(len(report['known_optimum_tables']), 2)
        full = self.root / 'result/original/original.csv'
        original = full.read_text()
        full.write_text(original + original.splitlines()[-1] + '\n')
        self.assert_rejected('duplicate')
        full.write_text(original)
        (self.source.parent / 'excluded-1.smt2').unlink()
        self.assert_rejected('source')

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
