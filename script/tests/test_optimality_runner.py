"""Contract tests for opt-in finite optimality reports; no real solver calls."""
import csv
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))


def final_record(**changes):
    result = dict(event='final', status='sat', direction='min', objective_value='8',
                  elapsed_seconds=.02, time_to_best_seconds=.001, model_validated=True,
                  is_optimal=True, termination='optimality_proved', prove_optimal=True,
                  optimality_stall_seconds=.01, optimality_timeout_seconds=.03,
                  optimality_attempts=1, optimality_seconds=.002, optimality_status='proved',
                  time_to_optimality_seconds=.01, optimality_proved_value='8')
    return result | changes


class OptimalityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = importlib.import_module('run_optimize')
        self.verifier = importlib.import_module('verify_optimize')
        self.reporter = importlib.import_module('report_results')
        self.tmp = tempfile.TemporaryDirectory(prefix='nia-optimality-protocol-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def config_and_case(self, record=None, body=None, timeout=.5):
        self.assertIn('prove_optimal', self.runner.Config.__dataclass_fields__)
        source = self.root / 'input.smt2'
        source.write_text('(minimize x)\n(check-sat)\n')
        converter = self.root / 'converter.py'
        converter.write_text("import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n")
        native = self.root / 'native'
        native.write_text('#!/usr/bin/env python3\n' + (body or
            "import json,pathlib,sys\n"
            "assert '--prove-optimal' in sys.argv\n"
            "assert '--optimality-stall=0.01' in sys.argv\n"
            "assert '--optimality-timeout=0.03' in sys.argv\n"
            "assert not any(x.startswith('--target=') for x in sys.argv)\n"
            "query=next(x.split('=',1)[1] for x in sys.argv if x.startswith('--optimality-query-output='))\n"
            "pathlib.Path(query).write_text('(check-sat)\\n')\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
            'print(json.dumps(' + repr(record or final_record()) + '),flush=True)\n'))
        native.chmod(0o755)
        config = self.runner.Config(jobs=1, timeout=timeout, solver=native, converter=converter,
                                    prove_optimal=True, optimality_stall=.01, optimality_timeout=.03)
        case = self.runner.InputCase(source, 'QF_NIA/input.smt2', 'original', 'original', 'min',
                                     8, 10, 8, 'z3')
        return config, case

    def test_cli_defaults_and_config_disable_historical_stopping(self):
        args = self.runner.make_parser({}).parse_args(['--prove-optimal'])
        self.assertTrue(args.prove_optimal)
        self.assertEqual((args.optimality_stall, args.optimality_timeout), (50, 30))
        config, _ = self.config_and_case()
        self.assertFalse(config.use_target)
        self.assertFalse(config.stop_at_known_optimum)
        for field in ('optimality_stall', 'optimality_timeout'):
            for value in (0, -1, float('nan'), float('inf'), True):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    replace(config, **{field: value})

    def test_search_window_is_optional_nonnegative_and_requires_proof_when_nonzero(self):
        parser = self.runner.make_parser({})
        self.assertEqual(parser.parse_args([]).optimality_search_window, 0)
        self.assertEqual(parser.parse_args(['--optimality-search-window', '17']).optimality_search_window, 17)
        self.assertEqual(self.runner.Config().optimality_search_window, 0)
        for invalid in (-1, float('nan'), float('inf'), True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.runner.Config(prove_optimal=True, optimality_search_window=invalid)
        with self.assertRaises(ValueError):
            self.runner.Config(optimality_search_window=17)

    def test_nonzero_search_window_reaches_native_and_batch_metadata(self):
        config, case = self.config_and_case(timeout=.25, body=
            "import json,sys,time\n"
            "assert '--optimality-search-window=0.125' in sys.argv\n"
            "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
            'time.sleep(20)\n')
        config = replace(config, optimality_search_window=.125)
        output = self.root / 'windowed-run'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual((row['termination'], row['best_value']), ('wall_timeout', '8'))
        for name in ('manifest.json', 'summary.json'):
            metadata = json.loads((output / name).read_text())
            self.assertEqual(metadata['optimality_search_window_seconds'], .125)

    def test_custom_optimality_timing_requires_opt_in(self):
        for settings in ({'optimality_stall': .01}, {'optimality_timeout': .03}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.runner.Config(**settings)

    def test_valid_native_proof_sets_flag_and_retains_query_and_times(self):
        config, case = self.config_and_case()
        artifacts = self.root / 'artifacts'
        result = self.runner.run_case(case, config, threading.Event(), artifacts)
        self.assertEqual((result['status'], result['termination'], result['is_optimal']),
                         ('sat', 'optimality_proved', 'true'), result.get('error'))
        self.assertEqual(result['optimality_status'], 'proved')
        self.assertEqual(result['optimality_proved_value'], '8')
        self.assertGreater(float(result['time_to_optimality_seconds']), float(result['time_to_best_seconds']))
        self.assertTrue((artifacts / 'optimality.smt2').is_file())

    def test_known_optimum_match_does_not_stop_independent_mode(self):
        body = ("import json,time\n"
                "print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,is_optimal=False)),flush=True)\n"
                "time.sleep(10)\n")
        config, case = self.config_and_case(body=body, timeout=.2)
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((result['termination'], result['is_optimal']), ('wall_timeout', 'false'))
        self.assertEqual(result['best_value'], '8')

    def test_native_optimality_requires_complete_consistent_metadata(self):
        self.assertTrue(hasattr(self.verifier, 'validate_optimality_metadata'))
        validate = self.verifier.validate_optimality_metadata
        self.assertTrue(validate(final_record(), True, .01, .03, 8, 'min', .5, .001))
        for changes in ({'is_optimal': False}, {'optimality_status': 'unknown'},
                        {'optimality_proved_value': '7'}, {'objective_value': '7'},
                        {'optimality_attempts': 0}, {'model_validated': False},
                        {'termination': 'timeout'}, {'prove_optimal': False},
                        {'optimality_stall_seconds': .02}, {'optimality_timeout_seconds': .04},
                        {'time_to_optimality_seconds': 2}, {'time_to_optimality_seconds': 0},
                        {'optimality_seconds': float('nan')}, {'status': 'unknown'},
                        {'is_unbounded': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(final_record(**changes), True, .01, .03, 8, 'min', .5, .001)
        incomplete = final_record()
        del incomplete['optimality_attempts']
        with self.assertRaises(ValueError):
            validate(incomplete, True, .01, .03, 8, 'min', .5, .001)

    def test_runner_rejects_forged_proof_but_keeps_incumbent(self):
        config, case = self.config_and_case(final_record(optimality_proved_value='7'))
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((result['status'], result['is_optimal'], result['best_value']), ('error', 'false', '8'))
        self.assertIn('optimality', result['error'])

    def test_other_native_metadata_error_cannot_leave_optimal_flag_true(self):
        config, case = self.config_and_case(final_record(large_target_attempts=1))
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['is_optimal'], 'false')

    def test_batch_records_opt_in_configuration(self):
        config, case = self.config_and_case()
        output = self.root / 'run'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        for filename in ('manifest.json', 'summary.json'):
            metadata = json.loads((output / filename).read_text())
            self.assertTrue(metadata['prove_optimal'])
            self.assertEqual((metadata['optimality_stall_seconds'], metadata['optimality_timeout_seconds']), (.01, .03))
            self.assertFalse(metadata['scalar_target_enabled'])
            self.assertFalse(metadata['known_optimum_stop_enabled'])

    def save_report_fixture(self, proven=False, **changes):
        config, case = self.config_and_case()
        directory = self.root / 'run'
        directory.mkdir(exist_ok=True)
        raw = dict.fromkeys(self.runner.FIELDS, '')
        raw.update(filename=case.filename, category=case.category, suite=case.suite, direction='min',
                   status='sat', termination='optimality_proved' if proven else 'wall_timeout',
                   best_value='8', time_to_best_seconds='.1', wall_seconds='.5',
                   model_validated='true', is_optimal=str(proven).lower(),
                   known_optimum_value='8', known_optimum_reached='true',
                   prove_optimal='true', optimality_stall_seconds='.01', optimality_timeout_seconds='.03',
                   optimality_attempts='1' if proven else '0', optimality_seconds='.02' if proven else '0',
                   optimality_status='proved' if proven else 'waiting',
                   optimality_proved_value='8' if proven else '', time_to_optimality_seconds='.2' if proven else '')
        raw.update(changes)
        with (directory / 'results.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=raw)
            writer.writeheader(); writer.writerow(raw)
        (directory / 'manifest.json').write_text(json.dumps(dict(
            cases=[dict(filename=case.filename, category=case.category, suite=case.suite, direction='min', known_optimum_value='8')],
            prove_optimal=True, optimality_stall_seconds=.01, optimality_timeout_seconds=.03,
            scalar_target_enabled=False, known_optimum_stop_enabled=False, timeout=.5)))
        (directory / 'summary.json').write_text(json.dumps(dict(selected=1, completed=1, statuses={})))
        return directory

    def test_report_independent_mode_does_not_borrow_known_optimum(self):
        rows, _ = self.reporter.load_nia_run(self.save_report_fixture())
        self.assertEqual(rows['QF_NIA/input.smt2']['状态'], 'B')

    def test_report_validates_own_proof_and_preserves_best_time(self):
        rows, _ = self.reporter.load_nia_run(self.save_report_fixture(proven=True))
        result = rows['QF_NIA/input.smt2']
        self.assertEqual((result['状态'], result['时间（秒）']), ('A2', '.1'))
        for changes in ({'optimality_status': 'unknown'}, {'optimality_proved_value': '7'},
                        {'time_to_optimality_seconds': '.6'}, {'optimality_attempts': '0'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reporter.load_nia_run(self.save_report_fixture(proven=True, **changes))

    def test_adaptive_metadata_allows_same_value_retries_and_cumulative_budget(self):
        record = final_record(optimality_timeout_seconds=30, optimality_attempts=3,
                              optimality_seconds=95, elapsed_seconds=180,
                              time_to_optimality_seconds=170,
                              optimality_schedule='adaptive_v1',
                              optimality_next_timeout_seconds=120,
                              optimality_sat_models_accepted=0)
        self.assertTrue(self.verifier.validate_optimality_metadata(
            record, True, .01, 30, 8, 'min', 1200, .001))
        for changes in ({'optimality_schedule': 'bogus'},
                        {'optimality_next_timeout_seconds': 0},
                        {'optimality_next_timeout_seconds': float('inf')},
                        {'optimality_sat_models_accepted': True},
                        {'optimality_sat_models_accepted': -1},
                        {'optimality_sat_models_accepted': 4}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.verifier.validate_optimality_metadata(
                    record | changes, True, .01, 30, 8, 'min', 1200, .001)
        del record['optimality_next_timeout_seconds']
        with self.assertRaises(ValueError):
            self.verifier.validate_optimality_metadata(record, True, .01, 30, 8, 'min', 1200, .001)

    def test_adaptive_sat_incumbent_is_retained_without_claiming_optimal(self):
        record = final_record(objective_value='6', time_to_best_seconds=.002,
                              is_optimal=False, termination='timeout', optimality_status='budget',
                              optimality_attempts=2, optimality_seconds=.005,
                              time_to_optimality_seconds=None, optimality_proved_value=None,
                              optimality_schedule='adaptive_v1',
                              optimality_next_timeout_seconds=.06,
                              optimality_sat_models_accepted=1)
        body = (
            "import json,pathlib,sys\n"
            "model=pathlib.Path(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--model-output=')))\n"
            "for value,elapsed in [('8',.001),('6',.002)]:\n"
            " model.write_text('x\\t'+value+'\\n')\n"
            " print(json.dumps(dict(event='incumbent',status='sat',direction='min',objective_value=value,elapsed_seconds=elapsed,model_validated=True,is_optimal=False)),flush=True)\n"
            'print(json.dumps(' + repr(record) + '),flush=True)\n')
        config, case = self.config_and_case(body=body)
        artifacts = self.root / 'adaptive-sat'
        result = self.runner.run_case(case, config, threading.Event(), artifacts)
        self.assertEqual((result['status'], result['best_value'], result['is_optimal']), ('sat', '6', 'false'))
        self.assertEqual((result['optimality_schedule'], result['optimality_next_timeout_seconds'],
                          result['optimality_sat_models_accepted']), ('adaptive_v1', '0.06', '1'))
        progress = [json.loads(line) for line in (artifacts / 'progress.jsonl').read_text().splitlines()]
        self.assertEqual([p['objective_value'] for p in progress], ['8', '6'])
        self.assertEqual((artifacts / 'model.tsv').read_text(), 'x\t6\n')
        self.assertEqual(result['known_optimum_reached'], 'false')

    def test_report_adaptive_metadata_does_not_relax_own_proof_requirements(self):
        metadata = dict(optimality_schedule='adaptive_v1', optimality_next_timeout_seconds='.06',
                        optimality_sat_models_accepted='1', optimality_attempts='2', optimality_seconds='.1')
        rows, _ = self.reporter.load_nia_run(self.save_report_fixture(proven=True, **metadata))
        self.assertEqual(rows['QF_NIA/input.smt2']['状态'], 'A2')
        for changes in ({'optimality_sat_models_accepted': '3'},
                        {'optimality_next_timeout_seconds': 'nan'},
                        {'optimality_proved_value': '7'}, {'optimality_status': 'sat'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reporter.load_nia_run(self.save_report_fixture(proven=True, **(metadata | changes)))
        with self.assertRaises(ValueError):
            self.reporter.load_nia_run(self.save_report_fixture(
                proven=False, optimality_attempts='', optimality_seconds='', optimality_status='',
                optimality_schedule='adaptive_v1', optimality_next_timeout_seconds='.06',
                optimality_sat_models_accepted='1'))


if __name__ == '__main__':
    unittest.main()
