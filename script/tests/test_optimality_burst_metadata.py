"""Burst results cannot be silently interpreted as legacy finite proofs."""
import csv
import importlib
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_persistent_optimality_metadata as persistent


class BurstMetadataTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('verify_optimize')

    def validate(self, record, *, burst=True, resume=True, window=0):
        self.assertIn('burst', inspect.signature(self.module.validate_optimality_metadata).parameters)
        return self.module.validate_optimality_metadata(
            record, True, .05, .03, 8, 'min', 1, search_window=window, resume=resume, burst=burst)

    def test_burst_preserves_existing_schedule_identifiers(self):
        for resume, window, schedule in ((True, 0, 'persistent_v1'),
                                         (False, .05, 'windowed_v1'),
                                         (False, 0, 'adaptive_v1')):
            record = persistent.final_record(window, optimality_resume=resume,
                                             optimality_burst=True, optimality_schedule=schedule)
            with self.subTest(schedule=schedule):
                self.assertTrue(self.validate(record, resume=resume, window=window))

    def test_burst_native_requires_exact_boolean_and_cannot_be_downgraded(self):
        for value in (False, None, 0, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(persistent.final_record(optimality_burst=value))
        with self.assertRaises(ValueError):
            self.validate(persistent.final_record())
        with self.assertRaises(ValueError):
            self.validate(persistent.final_record(optimality_burst=True), burst=False)
        for value in (0, 1, None, 'true'):
            with self.subTest(configured=value), self.assertRaises(ValueError):
                self.validate(persistent.final_record(optimality_burst=True), burst=value)

    def test_legacy_native_may_omit_burst_only_when_disabled(self):
        self.assertTrue(self.validate(persistent.final_record(), burst=False))
        self.assertTrue(self.validate(persistent.final_record(optimality_burst=False), burst=False))

    def test_burst_still_rejects_wrong_value_late_and_unproved_claims(self):
        for changes in ({'optimality_proved_value': '7'}, {'time_to_optimality_seconds': 1.1},
                        {'model_validated': False}, {'optimality_status': 'unknown'},
                        {'optimality_schedule': 'burst_v1'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(persistent.final_record(optimality_burst=True, **changes))


class BurstRetainedRunTests(unittest.TestCase):
    flag = 'optimality_burst'

    def setUp(self):
        self.base = persistent.PersistentRetainedRunTests(methodName='runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.configure()
        self.fixture = self.base.fixture

    def verify(self, *, manifest=True, row='true', native=True, summary=True):
        f = self.fixture
        for record, value in ((f.manifest, manifest), (f.row, row)):
            if value == 'missing':
                record.pop(self.flag, None)
            else:
                record[self.flag] = value
        path = f.artifact / 'native.stdout'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        if native == 'missing':
            events[-1].pop(self.flag, None)
        else:
            events[-1][self.flag] = native
        path.write_text(''.join(json.dumps(e) + '\n' for e in events))
        f.save_run()
        path = f.run / 'summary.json'
        record = json.loads(path.read_text())
        if summary != 'missing':
            record[self.flag] = summary
        path.write_text(json.dumps(record))
        return f.verifier.verify_run(f.run, expected_cases=1)

    def test_retained_native_csv_manifest_and_summary_match(self):
        report = self.verify()
        self.assertTrue(report['ok'], report)
        self.assertIs(report.get('optimality_burst'), True)
        self.assertEqual(report['verified_is_optimal'], 1)

    def test_any_burst_layer_disagreement_prevents_proof_acceptance(self):
        for changes in ({'manifest': False}, {'manifest': 'missing'}, {'row': 'false'},
                        {'row': 'missing'}, {'native': False}, {'native': 'missing'},
                        {'summary': False}, {'summary': 'missing'}):
            with self.subTest(changes=changes):
                report = self.verify(**changes)
                self.assertFalse(report['ok'], report)
                errors = report['errors'] + [c.get('error', '') for c in report.get('cases', [])]
                self.assertIn('burst', '\n'.join(errors))

    def test_manifest_and_summary_burst_require_boolean(self):
        for layer in ('manifest', 'summary'):
            for value in (0, 1, None, 'true'):
                with self.subTest(layer=layer, value=value):
                    report = self.verify(**{layer: value})
                    self.assertFalse(report['ok'], report)


class BurstReportTests(unittest.TestCase):
    flag = 'optimality_burst'

    def setUp(self):
        self.base = persistent.PersistentReportCsvTests(methodName='runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    def load(self, *, manifest=True, row='true', summary=True, proofs=True, window=0):
        self.base.write_run(window=window)
        path = self.base.run / 'results.csv'
        with path.open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        if row != 'missing':
            rows[0][self.flag] = row
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        for name, value in (('manifest.json', manifest), ('summary.json', summary)):
            path = self.base.run / name
            record = json.loads(path.read_text())
            if value != 'missing':
                record[self.flag] = value
            if name == 'manifest.json':
                record['prove_optimal'] = proofs
            path.write_text(json.dumps(record))
        return self.base.module.load_nia_run(self.base.run)

    def test_valid_burst_csv_exports_independently_proven_a2(self):
        rows, _ = self.load()
        self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')

    def test_csv_manifest_and_summary_mismatches_cannot_be_old_mode_a2(self):
        for changes in ({'manifest': False}, {'manifest': 'missing'}, {'manifest': 1},
                        {'row': 'false'}, {'row': 'missing'}, {'row': 'TRUE'}, {'row': ''},
                        {'summary': False}, {'summary': 'missing'}, {'summary': 1},
                        {'proofs': False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(**changes)

    def test_historical_report_without_any_burst_fields_stays_compatible(self):
        rows, _ = self.load(manifest='missing', row='missing', summary='missing')
        self.assertEqual(rows['QF_NIA/test.smt2']['状态'], 'A2')


if __name__ == '__main__':
    unittest.main()
