"""Persistent Z3 scheduling keeps the finite-UNSAT acceptance contract strict."""
import csv
import importlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_verify_optimize as retained_fixture


def final_record(window=0, **changes):
    record = dict(event='final', status='sat', direction='min', objective_value='8',
                  elapsed_seconds=.3, time_to_best_seconds=.1, model_validated=True,
                  is_optimal=True, is_unbounded=False, termination='optimality_proved',
                  prove_optimal=True, optimality_stall_seconds=.05,
                  optimality_timeout_seconds=.03, optimality_search_window_seconds=window,
                  optimality_resume=True, optimality_attempts=3, optimality_seconds=.15,
                  optimality_status='proved', optimality_schedule='persistent_v1',
                  optimality_next_timeout_seconds=.12, optimality_sat_models_accepted=0,
                  time_to_optimality_seconds=.25, optimality_proved_value='8')
    return record | changes


class PersistentOptimalityMetadataTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('verify_optimize')

    def test_accepts_persistent_proof_with_or_without_search_window(self):
        self.assertIn('resume', inspect.signature(self.module.validate_optimality_metadata).parameters)
        for window in (0, .05):
            with self.subTest(window=window):
                self.assertTrue(self.module.validate_optimality_metadata(
                    final_record(window), True, .05, .03, 8, 'min', 1,
                    search_window=window, resume=True))

    def test_rejects_resume_schedule_flag_disagreements_and_invalid_types(self):
        self.assertIn('resume', inspect.signature(self.module.validate_optimality_metadata).parameters)
        for changes in ({'optimality_resume': False}, {'optimality_resume': 1},
                        {'optimality_resume': 'true'}, {'optimality_schedule': 'adaptive_v1'},
                        {'optimality_schedule': 'windowed_v1'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.validate_optimality_metadata(
                    final_record(**changes), True, .05, .03, 8, 'min', 1, resume=True)
        missing = final_record()
        del missing['optimality_resume']
        with self.assertRaises(ValueError):
            self.module.validate_optimality_metadata(missing, True, .05, .03, 8, 'min', 1, resume=True)
        with self.assertRaises(ValueError):
            self.module.validate_optimality_metadata(final_record(), True, .05, .03, 8, 'min', 1)

    def test_persistent_mode_does_not_accept_unproved_late_or_wrong_value_claims(self):
        self.assertIn('resume', inspect.signature(self.module.validate_optimality_metadata).parameters)
        for changes in ({'optimality_status': 'sat'}, {'optimality_status': 'unknown'},
                        {'optimality_proved_value': '7'}, {'model_validated': False},
                        {'time_to_optimality_seconds': 1.1}, {'elapsed_seconds': 1.1},
                        {'optimality_seconds': .4}, {'optimality_attempts': 0},
                        {'termination': 'wall_timeout'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.validate_optimality_metadata(
                    final_record(**changes), True, .05, .03, 8, 'min', 1, resume=True)


class PersistentRetainedRunTests(unittest.TestCase):
    def setUp(self):
        self.fixture = retained_fixture.OptimizationVerifierTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configure(self, window=0):
        f = self.fixture
        f.manifest.update(prove_optimal=True, optimality_stall_seconds=.05,
                          optimality_timeout_seconds=.03, optimality_search_window_seconds=window,
                          optimality_resume=True, known_optimum_stop_enabled=False)
        final = final_record(window)
        final.pop('is_unbounded')
        f.row.update(termination='optimality_proved', is_optimal='true', wall_seconds='.4',
                     **{k: str(v).lower() if isinstance(v, bool) else str(v)
                        for k,v in final.items() if k.startswith('optimality_') or k == 'prove_optimal'})
        f.row.update(time_to_optimality_seconds='.30')
        f.write_artifacts(final='optimality_proved')
        path = f.artifact / 'native.stdout'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1].update(final)
        path.write_text(''.join(json.dumps(e) + '\n' for e in events))

    def test_persistent_native_csv_manifest_and_retained_model_are_audited(self):
        for window in (0, .05):
            with self.subTest(window=window):
                self.configure(window)
                report = self.fixture.verify()
                self.assertTrue(report['ok'], report)
                self.assertEqual(report['verified_is_optimal'], 1)
        self.fixture.row['optimality_resume'] = 'false'
        self.fixture.assert_rejected('resume')

    def test_manifest_resume_requires_bool_and_enabled_proofs(self):
        self.configure()
        for invalid in (1, 'true', None):
            with self.subTest(invalid=invalid):
                self.fixture.manifest['optimality_resume'] = invalid
                self.fixture.assert_rejected('resume')
        self.fixture.manifest.update(optimality_resume=True, prove_optimal=False)
        self.fixture.assert_rejected('resume')

    def test_persistent_claim_still_checks_exact_model_and_deadline(self):
        self.configure()
        (self.fixture.artifact / 'model.tsv').write_text('x\t7\n')
        self.fixture.assert_rejected('differs from accepted CSV best')
        (self.fixture.artifact / 'model.tsv').write_text('x\t8\n')
        self.fixture.manifest['timeout'] = .2
        self.fixture.assert_rejected()


class PersistentReportCsvTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('report_results')
        self.tmp = tempfile.TemporaryDirectory(prefix='omt-persistent-report-')
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)

    def write_run(self, window=0, manifest_resume=True, row_resume='true', **row_changes):
        row = dict(filename='QF_NIA/test.smt2', category='original', suite='original',
                   direction='min', status='sat', termination='optimality_proved',
                   best_value='8', model_validated='true', is_optimal='true',
                   time_to_best_seconds='.1', wall_seconds='.4',
                   **{k: str(v).lower() if isinstance(v, bool) else str(v)
                        for k,v in final_record(window).items() if k.startswith('optimality_') or k == 'prove_optimal'})
        row.update(optimality_resume=row_resume, time_to_optimality_seconds='.25')
        row.update(row_changes)
        with (self.run / 'results.csv').open('w', newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerow(row)
        manifest=dict(cases=[{k:row[k] for k in ('filename','category','suite','direction')}],
                      prove_optimal=True, optimality_resume=manifest_resume,
                      optimality_stall_seconds=.05, optimality_timeout_seconds=.03,
                      optimality_search_window_seconds=window,
                      scalar_target_enabled=False, known_optimum_stop_enabled=False, timeout=1)
        (self.run / 'manifest.json').write_text(json.dumps(manifest))
        (self.run / 'summary.json').write_text(json.dumps(dict(selected=1,completed=1,complete=True)))

    def test_real_persistent_csv_exports_a2_for_both_window_modes(self):
        for window in (0,.05):
            self.write_run(window)
            rows,_=self.module.load_nia_run(self.run)
            self.assertEqual(rows['QF_NIA/test.smt2']['状态'],'A2')

    def test_real_csv_rejects_resume_mismatch_invalid_manifest_and_fake_proof(self):
        for kwargs in ({'row_resume':'false'}, {'row_resume':''}, {'row_resume':'TRUE'},
                       {'manifest_resume':False}, {'manifest_resume':1}, {'manifest_resume':'true'},
                       {'optimality_schedule':'adaptive_v1'}, {'model_validated':'false'},
                       {'optimality_status':'unknown'}, {'optimality_proved_value':'7'},
                       {'time_to_optimality_seconds':'1.1'}):
            with self.subTest(kwargs=kwargs):
                self.write_run(**kwargs)
                with self.assertRaises(ValueError):self.module.load_nia_run(self.run)


if __name__ == '__main__':
    unittest.main()
