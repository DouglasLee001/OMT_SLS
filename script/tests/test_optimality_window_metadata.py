"""Windowed scheduling must retain the independent UNSAT proof contract."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_optimality_runner as fixtures
import test_verify_optimize as audit_fixtures


class WindowMetadataTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.OptimalityRunnerTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.runner, self.verifier, self.reporter = self.base.runner, self.base.verifier, self.base.reporter

    def record(self, **changes):
        return fixtures.final_record(optimality_schedule='windowed_v1',
            optimality_next_timeout_seconds=.03, optimality_sat_models_accepted=0,
            optimality_search_window_seconds=.125, **changes)

    def test_adaptive_rejects_positive_native_window_even_with_legacy_configuration(self):
        record = self.record()
        record['optimality_schedule'] = 'adaptive_v1'
        with self.assertRaises(ValueError):
            self.verifier.validate_optimality_metadata(record, True, .01, .03, 8, 'min', .5, .001)

    def test_windowed_requires_matching_positive_finite_window(self):
        record = self.record()
        self.assertTrue(self.verifier.validate_optimality_metadata(
            record, True, .01, .03, 8, 'min', .5, .001, search_window=.125))
        for change in ({'optimality_search_window_seconds': 0},
                       {'optimality_search_window_seconds': -.1},
                       {'optimality_search_window_seconds': float('nan')},
                       {'optimality_search_window_seconds': float('inf')},
                       {'optimality_search_window_seconds': True},
                       {'optimality_search_window_seconds': '.125'},
                       {'optimality_search_window_seconds': .25},
                       {'optimality_schedule': 'adaptive_v1'},
                       {'optimality_schedule': 'invented'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verifier.validate_optimality_metadata(
                    record | change, True, .01, .03, 8, 'min', .5, .001, search_window=.125)
        del record['optimality_search_window_seconds']
        with self.assertRaises(ValueError):
            self.verifier.validate_optimality_metadata(
                record, True, .01, .03, 8, 'min', .5, .001, search_window=.125)

    def test_historical_adaptive_and_pre_adaptive_records_still_validate(self):
        for record in (fixtures.final_record(), self.record() | {
                'optimality_schedule': 'adaptive_v1', 'optimality_search_window_seconds': 0}):
            self.assertTrue(self.verifier.validate_optimality_metadata(
                record, True, .01, .03, 8, 'min', .5, .001))

    def test_runner_accepts_windowed_final_and_rejects_wrong_configuration(self):
        config, case = self.base.config_and_case(self.record())
        config = replace(config, optimality_search_window=.125)
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((result['status'], result['is_optimal']), ('sat', 'true'), result)
        self.assertEqual(result['optimality_schedule'], 'windowed_v1')
        self.assertEqual(float(result['optimality_search_window_seconds']), .125)
        result = self.runner.run_case(case, replace(config, optimality_search_window=.25), threading.Event())
        self.assertEqual((result['status'], result['is_optimal']), ('error', 'false'))

    def test_batch_writes_windowed_final_to_csv_and_reporter_accepts_it(self):
        config, case = self.base.config_and_case(self.record())
        config = replace(config, optimality_search_window=.125)
        path = self.base.root / 'windowed-batch'
        self.assertEqual(self.runner.run_batch([case], config, path), 0)
        with (path / 'results.csv').open(newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(float(row['optimality_search_window_seconds']), .125)
        rows, _ = self.reporter.load_nia_run(path)
        self.assertEqual(rows['QF_NIA/input.smt2']['状态'], 'A2')

    def test_batch_without_final_keeps_configured_window_in_csv(self):
        config, case = self.base.config_and_case(timeout=.25, body=
            "import time\ntime.sleep(20)\n")
        config = replace(config, optimality_search_window=.125)
        path = self.base.root / 'windowed-timeout'
        self.assertEqual(self.runner.run_batch([case], config, path), 0)
        with (path / 'results.csv').open(newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row['termination'], 'wall_timeout')
        self.assertEqual(float(row['optimality_search_window_seconds']), .125)
        rows, _ = self.reporter.load_nia_run(path)
        self.assertEqual(rows['QF_NIA/input.smt2']['状态'], 'C')

    def report_fixture(self):
        path = self.base.save_report_fixture(proven=True, optimality_schedule='windowed_v1',
            optimality_next_timeout_seconds='.03', optimality_sat_models_accepted='0',
            optimality_search_window_seconds='.125')
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['optimality_search_window_seconds'] = .125
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def test_report_windowed_is_A2_only_with_matching_configuration_and_proof(self):
        path = self.report_fixture()
        rows, _ = self.reporter.load_nia_run(path)
        self.assertEqual(rows['QF_NIA/input.smt2']['状态'], 'A2')
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for value in (0, .25, True, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manifest_path.write_text(json.dumps(manifest | {'optimality_search_window_seconds': value}))
                self.reporter.load_nia_run(path)
        manifest_path.write_text(json.dumps(manifest))
        with (path / 'results.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle)); fields = list(rows[0])
        rows[0]['optimality_proved_value'] = '7'
        with (path / 'results.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        with self.assertRaises(ValueError):
            self.reporter.load_nia_run(path)

    def test_artifact_verifier_matches_windowed_native_csv_and_manifest(self):
        base = audit_fixtures.OptimizationVerifierTests(); base.setUp(); self.addCleanup(base.doCleanups)
        base.manifest.update(prove_optimal=True, optimality_stall_seconds=.05,
            optimality_timeout_seconds=.03, optimality_search_window_seconds=.125,
            known_optimum_stop_enabled=False)
        metadata = self.record() | dict(optimality_stall_seconds=.05,
            optimality_seconds=.02, time_to_optimality_seconds=.2)
        native_fields = {key: value for key, value in metadata.items()
            if key in base.verifier.OPTIMALITY_FIELDS + base.verifier.OPTIMALITY_ADAPTIVE_FIELDS
            or key == 'optimality_search_window_seconds'}
        base.row.update(termination='optimality_proved', is_optimal='true', wall_seconds='.4',
            **{key: str(value).lower() if isinstance(value, bool) else str(value)
               for key, value in native_fields.items()})
        base.row['time_to_optimality_seconds'] = '.25'
        base.write_artifacts(final='optimality_proved')
        path = base.artifact / 'native.stdout'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1].update(native_fields, is_optimal=True, elapsed_seconds=.3)
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        report = base.verify()
        self.assertTrue(report['ok'], report)
        base.manifest['optimality_search_window_seconds'] = .25
        self.assertFalse(base.verify()['ok'])


if __name__ == '__main__':
    unittest.main()
