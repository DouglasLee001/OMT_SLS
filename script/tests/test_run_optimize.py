"""Contract tests for the native optimization benchmark runner."""
import csv
from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    path = ROOT / 'script/run_optimize.py'
    assert path.is_file(), 'optimization runner has not been implemented'
    spec = importlib.util.spec_from_file_location('run_optimize', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OptimizationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def executable(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o755)
        return path

    def setup_case(self, native_body=None, converter_body=None, timeout=2, direction='min', z3=10, oms=12):
        source = self.root / 'input.smt2'
        source.write_text('(minimize x)\n(check-sat)\n')
        converter = self.executable('converter.py', converter_body or
            "import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n")
        native = self.executable('nia_ls', native_body or
            "import json,sys\nassert '--optimize' in sys.argv\nassert '--target=10' in sys.argv\n"
            "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.002,'model_validated':True,'is_optimal':False,'termination':'target_beaten'}))\n")
        config = self.runner.Config(jobs=180, timeout=timeout, solver=native, converter=converter)
        case = self.runner.InputCase(source, 'input.smt2', 'custom', 'custom', direction, z3, oms)
        return config, case

    def input_tables(self):
        originals = self.root / 'benchmarks'
        source = originals / 'QF_NIA/group/a.smt2'
        source.parent.mkdir(parents=True)
        source.write_text('; (maximize ignored)\n(minimize x)\n(check-sat)\n')
        listing = self.root / 'result/original/original_unsolved_bounded_sol.csv'
        listing.parent.mkdir(parents=True)
        listing.write_text('filename,z3_best_value,optimathsat_best_value\nQF_NIA/group/a.smt2,(- 34),12\n')
        generated = self.root / 'new_benchmark/design/instance/b.smt2'
        generated.parent.mkdir(parents=True)
        generated.write_text('(set-info :notes "(minimize ignored)")\n(maximize x)\n(check-sat)\n')
        listing = self.root / 'result/new/new_unsolved_bounded_sol.csv'
        listing.parent.mkdir(parents=True)
        listing.write_text('filename,category,z3_best_value,optimathsat_best_value\nb.smt2,design,,81\n')
        excluded = generated.parent / 'excluded.smt2'
        excluded.write_text('(minimize x)\n(check-sat)\n')
        return originals, source, generated

    def test_defaults_and_budget_cap(self):
        args = self.runner.make_parser({}).parse_args([])
        self.assertEqual((args.suite, args.jobs, args.timeout), ('all', 180, 1200))
        with self.assertRaises(ValueError):
            self.runner.validate_budget(1, 1200.1)
        with self.assertRaises(ValueError):
            self.runner.validate_budget(0, 1)
        with self.assertRaises(ValueError):
            self.runner.validate_budget(181, 1)

    def test_bounded_selection_and_full_budget_cli_options(self):
        defaults = self.runner.make_parser({}).parse_args([])
        self.assertIn('subset', vars(defaults))
        self.assertEqual(defaults.subset, 'unsolved_bounded_sol')
        args = self.runner.make_parser({}).parse_args(
            ['all', '--subset', 'bounded', '--jobs', '180', '--timeout', '1200', '--no-target'])
        self.assertEqual((args.subset, args.jobs, args.timeout, args.no_target),
                         ('bounded', 180, 1200, True))

    def test_all_selection_reads_complete_original_and_new_tables(self):
        option = next(a for a in self.runner.make_parser({})._actions if a.dest == 'subset')
        self.assertIn('all', option.choices)
        args = self.runner.make_parser({}).parse_args(
            ['all', '--subset', 'all', '--prove-unbounded', '--jobs', '180', '--timeout', '1200'])
        self.assertEqual((args.suite, args.subset, args.jobs, args.timeout), ('all', 'all', 180, 1200))
        originals, source, generated = self.input_tables()
        extra = originals / 'QF_NIA/group/extra.smt2'
        extra.write_text('(maximize x)\n(check-sat)\n')
        extra_new = generated.parent / 'excluded.smt2'
        (self.root / 'result/original/original.csv').write_text(
            'filename,z3_best_value,optimathsat_best_value\n'
            'QF_NIA/group/a.smt2,(- 34),12\nQF_NIA/group/extra.smt2,,91\n')
        (self.root / 'result/new/new.csv').write_text(
            'filename,category,z3_best_value,optimathsat_best_value\n'
            'b.smt2,design,,81\nexcluded.smt2,design,,\n')
        # Complete selection must not depend on any filtered listing.
        for path in self.root.glob('result/*/*_unsolved_bounded_sol.csv'):
            path.unlink()
        cases = self.runner.collect_inputs('all', self.root, originals, subset='all')
        self.assertEqual({case.source for case in cases}, {source, extra, generated, extra_new})
        self.assertEqual([(case.direction, case.z3, case.optimathsat) for case in cases],
                         [('min', -34, 12), ('max', None, 91), ('max', None, 81), ('min', None, None)])
        for suite, expected in [('original', {source, extra}), ('new', {generated, extra_new})]:
            self.assertEqual({case.source for case in self.runner.collect_inputs(
                suite, self.root, originals, subset='all')}, expected)

    def test_all_selection_is_recorded_in_results(self):
        config, case = self.setup_case()
        config = self.runner.replace(config, subset='all')
        output = self.root / 'all-results'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        for name in ['manifest.json', 'summary.json']:
            self.assertEqual(json.loads((output / name).read_text())['subset'], 'all')

    def test_bounded_selection_includes_rows_outside_unsolved_subset(self):
        import inspect
        self.assertIn('subset', inspect.signature(self.runner.collect_inputs).parameters)
        originals, source, generated = self.input_tables()
        extra = originals / 'QF_NIA/group/extra.smt2'
        extra.write_text('(maximize x)\n(check-sat)\n')
        extra_new = generated.parent / 'excluded.smt2'
        (self.root / 'result/original/original_bound.csv').write_text(
            'filename,z3_best_value,optimathsat_best_value\n'
            'QF_NIA/group/a.smt2,(- 34),12\nQF_NIA/group/extra.smt2,,91\n')
        (self.root / 'result/new/new_bound.csv').write_text(
            'filename,category,z3_best_value,optimathsat_best_value\n'
            'b.smt2,design,,81\nexcluded.smt2,design,,\n')
        cases = self.runner.collect_inputs('all', self.root, originals, subset='bounded')
        self.assertEqual({c.source for c in cases}, {source, generated, extra, extra_new})
        self.assertEqual([(c.direction, c.z3, c.optimathsat) for c in cases],
                         [('min', -34, 12), ('max', None, 91), ('max', None, 81), ('min', None, None)])
        self.assertEqual(len(self.runner.collect_inputs('original', self.root, originals, subset='bounded')), 2)
        self.assertEqual(len(self.runner.collect_inputs('new', self.root, originals, subset='bounded')), 2)
        self.assertEqual(len(self.runner.collect_inputs('all', self.root, originals)), 2)

    def test_bounded_selection_is_recorded_in_results(self):
        self.assertIn('subset', self.runner.Config.__dataclass_fields__)
        config, case = self.setup_case()
        config = self.runner.replace(config, subset='bounded')
        output = self.root / 'bounded-results'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        for name in ['manifest.json', 'summary.json']:
            self.assertEqual(json.loads((output / name).read_text())['subset'], 'bounded')

    def test_large_target_cli_and_exact_magnitude_validation(self):
        args = self.runner.make_parser({}).parse_args(['--large-target', '10000000000'])
        self.assertEqual(args.large_target, 10**10)
        self.assertIsNone(self.runner.make_parser({}).parse_args([]).large_target)
        maximum = 2**127 - 1
        self.assertEqual(self.runner.Config(large_target=maximum).large_target, maximum)
        self.assertEqual(self.runner.make_parser({}).parse_args(['--large-target', str(maximum)]).large_target, maximum)
        for value in [0, -1, True, False, 1.5, maximum + 1, maximum + 2, '100', '1e10']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.Config(large_target=value)
        for value in ['0', '-1', '1.5', '1e10', 'true', str(maximum + 1), str(maximum + 2)]:
            with self.subTest(cli=value), self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    self.runner.make_parser({}).parse_args(['--large-target', value])

    def test_large_target_signed_128_max_worker_and_manifest_preserve_exact_signed_thresholds(self):
        magnitude = 2**127 - 1
        for direction, value, reached in [('min', -magnitude, True), ('max', magnitude, True),
                                           ('min', -magnitude + 1, False), ('max', magnitude - 1, False)]:
            with self.subTest(direction=direction, value=value):
                record = dict(status='sat', direction=direction, objective_value=str(value),
                    elapsed_seconds=.002, model_validated=True, termination='timeout',
                    large_target_magnitude=str(magnitude), large_target_attempts=0,
                    large_target_slices=0, large_target_seconds=0,
                    large_target_reached=reached, large_target_status='reached' if reached else 'waiting')
                config, case = self.setup_case(direction=direction, native_body=
                    'import json,sys\nassert ' + repr('--large-target=' + str(magnitude))
                    + ' in sys.argv\nprint(json.dumps(' + repr(record) + '),flush=True)\n')
                config = self.runner.replace(config, large_target=magnitude, use_target=False)
                output = self.root / f'exact-target-{direction}-{int(reached)}'
                self.assertEqual(self.runner.run_batch([case], config, output), 0)
                with (output / 'results.csv').open(newline='') as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual((row['status'], row['best_value'], row['large_target_magnitude'],
                                  row['large_target_reached']),
                                 ('sat', str(value), str(magnitude), str(reached).lower()))
                for name in ['manifest.json', 'summary.json']:
                    self.assertEqual(json.loads((output / name).read_text())['large_target_magnitude'], str(magnitude))

    def test_large_target_is_independent_of_historical_targets_and_recorded(self):
        config, case = self.setup_case(native_body=
            "import json,sys\nassert '--large-target=10000000000' in sys.argv\n"
            "assert not any(x.startswith('--target=') for x in sys.argv)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'-10000000001','elapsed_seconds':.002,'model_validated':True,'termination':'timeout','large_target_magnitude':'10000000000','large_target_attempts':2,'large_target_slices':3,'large_target_seconds':.001,'large_target_reached':True,'large_target_status':'reached'}),flush=True)\n")
        config = self.runner.replace(config, large_target=10**10, use_target=False,
                                     stop_at_known_optimum=False)
        output = self.root / 'large-target'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual((row['status'], row['large_target_magnitude'], row['large_target_reached']),
                         ('sat', '10000000000', 'true'))
        self.assertEqual((row['large_target_attempts'], row['large_target_slices'],
                          row['large_target_status']), ('2', '3', 'reached'))
        self.assertAlmostEqual(float(row['large_target_seconds']), .001)
        for name in ['manifest.json', 'summary.json']:
            self.assertEqual(json.loads((output / name).read_text())['large_target_magnitude'], '10000000000')

    def test_default_native_command_does_not_enable_large_target(self):
        config, case = self.setup_case(native_body=
            "import json,sys\nassert not any(x.startswith('--large-target=') for x in sys.argv)\n"
            "print(json.dumps({'status':'unknown','termination':'timeout'}),flush=True)\n")
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(row.get('large_target_magnitude'), '')
        self.assertEqual(row.get('large_target_reached'), '')

    def test_large_target_timeout_preserves_unknown_probe_state_and_exact_reached(self):
        for direction, value, reached in [('min', '-10000000000', 'true'),
                                           ('max', '10000000001', 'true'),
                                           ('min', '10000000001', ''),
                                           ('max', '-10000000000', '')]:
            with self.subTest(direction=direction, value=value):
                config, case = self.setup_case(timeout=.2, direction=direction, native_body=
                    "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':"
                    + repr(direction) + ",'objective_value':" + repr(value)
                    + ",'elapsed_seconds':.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
                config = self.runner.replace(config, large_target=10**10)
                row = self.runner.run_case(case, config, threading.Event())
                self.assertEqual(row['status'], 'sat')
                self.assertEqual(row['termination'], 'wall_timeout')
                self.assertEqual(row.get('large_target_reached'), reached)
                for key in ['attempts', 'slices', 'seconds', 'status']:
                    self.assertEqual(row.get('large_target_' + key), '')

    def test_large_target_rejects_inconsistent_or_invalid_native_metadata(self):
        base = dict(status='sat', direction='min', objective_value='8', elapsed_seconds=.002,
                    model_validated=True, termination='timeout', large_target_magnitude='10000000000',
                    large_target_attempts=0, large_target_slices=0, large_target_seconds=0,
                    large_target_reached=False, large_target_status='not_triggered')
        for change in [dict(large_target_magnitude='100'), dict(large_target_attempts=True),
                       dict(large_target_slices=-1), dict(large_target_seconds=float('inf')),
                       dict(large_target_reached=True), dict(large_target_status='unbounded'),
                       dict(large_target_status='reached'), dict(large_target_status='disabled')]:
            with self.subTest(change=change):
                record = dict(base, **change)
                # JSON text keeps non-finite payloads available to the reader for validation.
                config, case = self.setup_case(native_body='import json\nprint(' + repr(json.dumps(record)) + ',flush=True)\n')
                config = self.runner.replace(config, large_target=10**10)
                row = self.runner.run_case(case, config, threading.Event())
                self.assertEqual(row['status'], 'error')
                self.assertIn('large_target', row['error'])

    def test_large_target_final_accounting_survives_timeout_interrupt_and_native_exit_error(self):
        record = dict(event='final', status='sat', direction='min', objective_value='8',
                      elapsed_seconds=.002, model_validated=True, termination='timeout',
                      large_target_magnitude='10000000000', large_target_attempts=1,
                      large_target_slices=2, large_target_seconds=.001,
                      large_target_reached=False, large_target_status='searching')
        for mode in ['timeout', 'interrupt', 'exit']:
            with self.subTest(mode=mode):
                config, case = self.setup_case(timeout=.3, native_body=
                    'import json,time,sys\nprint(json.dumps(' + repr(record) + '),flush=True)\n'
                    + ('sys.exit(7)\n' if mode == 'exit' else 'time.sleep(30)\n'))
                config = self.runner.replace(config, large_target=10**10)
                stop = threading.Event()
                timer = threading.Timer(.15, stop.set) if mode == 'interrupt' else None
                if timer:
                    timer.start()
                try:
                    row = self.runner.run_case(case, config, stop)
                finally:
                    if timer:
                        timer.cancel()
                self.assertEqual(row['termination'], {'timeout':'wall_timeout', 'interrupt':'interrupted', 'exit':'error'}[mode])
                self.assertEqual((row['large_target_attempts'], row['large_target_slices'],
                                  row['large_target_status'], row['large_target_reached']),
                                 ('1', '2', 'searching', 'false'))
                self.assertIn({'timeout':'wallclock', 'interrupt':'interrupted', 'exit':'exit 7'}[mode], row['error'])

    def test_large_target_bad_cleanup_metadata_does_not_hide_existing_native_error(self):
        record = dict(status='sat', direction='min', objective_value='8', elapsed_seconds=.002,
                      model_validated=True, termination='timeout', large_target_magnitude='10000000000',
                      large_target_attempts=1, large_target_slices=-1, large_target_seconds=0,
                      large_target_reached=False, large_target_status='searching')
        config, case = self.setup_case(native_body='import json,sys\nprint(json.dumps(' + repr(record) + '),flush=True)\nsys.exit(7)\n')
        config = self.runner.replace(config, large_target=10**10)
        row = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((row['status'], row['termination']), ('error', 'error'))
        self.assertIn('exit 7', row['error'])
        self.assertIn('large_target_slices', row['error'])

    def excluded_tables(self):
        originals, source, generated = self.input_tables()
        original_extra = source.parent / 'extra.smt2'
        original_extra.write_text('(minimize x)\n(check-sat)\n')
        other = self.root / 'new_benchmark/other/instance/b.smt2'
        other.parent.mkdir(parents=True)
        other.write_text('(maximize x)\n(check-sat)\n')
        (self.root / 'result/original/original.csv').write_text(
            'filename,z3_best_value,z3_is_optimal,optimathsat_best_value,optimathsat_is_optimal\n'
            'QF_NIA/group/a.smt2,-34,false,12,false\n'
            'QF_NIA/group/extra.smt2,-10000000001,false,-10000000002,true\n')
        (self.root / 'result/original/original_bound.csv').write_text(
            'filename,z3_best_value,optimathsat_best_value\nQF_NIA/group/a.smt2,-34,12\n')
        (self.root / 'result/new/new.csv').write_text(
            'filename,category,z3_best_value,optimathsat_best_value\n'
            'b.smt2,design,,81\nexcluded.smt2,design,-10000000001,\nb.smt2,other,10000000001,\n')
        (self.root / 'result/new/new_bound.csv').write_text(
            'filename,category,z3_best_value,optimathsat_best_value\nb.smt2,design,,81\n')
        return originals, original_extra, generated.parent / 'excluded.smt2', other

    def test_excluded_selection_is_full_minus_bound_with_source_identity(self):
        originals, extra, generated_extra, other = self.excluded_tables()
        args = self.runner.make_parser({}).parse_args(['--subset', 'excluded'])
        self.assertEqual(args.subset, 'excluded')
        cases = self.runner.collect_inputs('all', self.root, originals, subset='excluded')
        self.assertEqual([c.source for c in cases], [extra, generated_extra, other])
        self.assertEqual((cases[0].z3, cases[0].optimathsat, cases[0].known_optimum,
                          cases[0].known_optimum_source),
                         (-10000000001, -10000000002, -10000000002, 'optimathsat'))
        self.assertEqual([c.category for c in cases], ['original', 'design', 'other'])
        self.assertEqual(len(self.runner.collect_inputs('original', self.root, originals, subset='excluded')), 1)
        self.assertEqual(len(self.runner.collect_inputs('new', self.root, originals, subset='excluded')), 2)

    def test_excluded_selection_rejects_duplicates_even_in_subtracted_rows(self):
        originals, _, _, _ = self.excluded_tables()
        for suffix in ['', '_bound']:
            listing = self.root / f'result/original/original{suffix}.csv'
            original = listing.read_text()
            listing.write_text(original + original.splitlines()[1] + '\n')
            with self.subTest(table=listing.name), self.assertRaisesRegex(ValueError, 'Duplicate'):
                self.runner.collect_inputs('original', self.root, originals, subset='excluded')
            listing.write_text(original)

    def test_excluded_selection_requires_bounded_rows_to_belong_to_full_table(self):
        originals, _, _, _ = self.excluded_tables()
        extra = originals / 'QF_NIA/group/foreign.smt2'
        extra.write_text('(minimize x)\n(check-sat)\n')
        listing = self.root / 'result/original/original_bound.csv'
        listing.write_text(listing.read_text() + 'QF_NIA/group/foreign.smt2,0,1\n')
        with self.assertRaisesRegex(ValueError, 'bounded.*full|Bounded.*full'):
            self.runner.collect_inputs('original', self.root, originals, subset='excluded')

    def test_excluded_selection_checks_source_paths_in_both_tables(self):
        originals, extra, _, _ = self.excluded_tables()
        extra.unlink()
        with self.assertRaisesRegex(ValueError, 'Invalid benchmark'):
            self.runner.collect_inputs('original', self.root, originals, subset='excluded')
        extra.write_text('(minimize x)\n(check-sat)\n')
        listing = self.root / 'result/new/new_bound.csv'
        listing.write_text('filename,category,z3_best_value,optimathsat_best_value\nb.smt2,../design,0,1\n')
        with self.assertRaisesRegex(ValueError, 'Invalid generated category'):
            self.runner.collect_inputs('new', self.root, originals, subset='excluded')

    def test_excluded_selection_rejects_parent_category_even_when_external_source_exists(self):
        originals, _, _, _ = self.excluded_tables()
        external = self.root / 'instance/b.smt2'
        external.parent.mkdir()
        external.write_text('(minimize x)\n(check-sat)\n')
        listing = self.root / 'result/new/new_bound.csv'
        listing.write_text('filename,category,z3_best_value,optimathsat_best_value\nb.smt2,..,0,1\n')
        with self.assertRaisesRegex(ValueError, 'Invalid generated category'):
            self.runner.collect_inputs('new', self.root, originals, subset='excluded')

    def test_no_target_retains_later_improvement_after_beating_reference(self):
        config, case = self.setup_case(timeout=1, native_body=
            "import json,sys,time\nassert not any(x.startswith('--target=') for x in sys.argv)\n"
            "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':.001,'model_validated':True}),flush=True)\n"
            "time.sleep(.15)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'7','elapsed_seconds':.16,'model_validated':True,'termination':'timeout'}),flush=True)\n")
        config = self.runner.replace(config, use_target=False)
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((result['status'], result['best_value'], result['vs_best']), ('sat', '7', 'better'))
        self.assertGreater(float(result['wall_seconds']), .12)

    def test_exact_integer_parsing_and_rejection(self):
        for value, expected in [('0',0), ('+12',12), ('-0012',-12), ('(- 34)',-34), ('(- (- 9))',9), ('',None), (None,None), ('999999999999999999999999999999999',999999999999999999999999999999999)]:
            self.assertEqual(self.runner.parse_integer(value), expected)
        for value in ['1.5', 'oo', '(/ 1 2)', 'nan', 'true']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.parse_integer(value)

    def test_comparison_direction_strict_ties_and_missing_reference(self):
        for direction, value, z3, oms, expected in [
            ('min',8,10,12,('better','better','better')),
            ('min',10,10,12,('tie','better','tie')),
            ('min',11,10,12,('worse','better','worse')),
            ('max',13,10,12,('better','better','better')),
            ('max',12,10,12,('better','tie','tie')),
            ('max',11,None,12,('no_reference','worse','worse')),
            ('min',8,None,None,('no_reference','no_reference','no_reference')),
            ('min',None,10,12,('no_incumbent','no_incumbent','no_incumbent')),
        ]:
            with self.subTest(direction=direction,value=value,z3=z3,oms=oms):
                result=self.runner.comparisons(value,z3,oms,direction)
                self.assertEqual((result['vs_z3'],result['vs_optimathsat'],result['vs_best']),expected)
                self.assertEqual(result['strictly_better_than_best'],str(expected[2]=='better').lower())

    def test_selection_uses_only_subset_rows_and_source_direction(self):
        originals, source, generated = self.input_tables()
        cases=self.runner.collect_inputs('all',self.root,originals)
        self.assertEqual([(c.source,c.filename,c.direction,c.z3,c.optimathsat) for c in cases],[(source,'QF_NIA/group/a.smt2','min',-34,12),(generated,'b.smt2','max',None,81)])
        self.assertEqual([c.suite for c in cases],['original','new'])

    def test_selection_reads_consistent_proven_optima_from_either_solver(self):
        originals, source, _ = self.input_tables()
        listing = self.root / 'result/original/original_unsolved_bounded_sol.csv'
        for direction, z3, z3_optimal, oms, oms_optimal, optimum, provenance in [
            ('min', '(- 34)', 'true', '12', 'false', -34, 'z3'),
            ('min', '12', 'false', '(- 34)', 'TRUE', -34, 'optimathsat'),
            ('max', '34', 'true', '12', 'false', 34, 'z3'),
            ('max', '12', 'false', '34', 'true', 34, 'optimathsat'),
            ('min', '34', 'true', '34', 'true', 34, 'z3+optimathsat'),
            ('max', '99999999999999999999999999999', 'true', '', 'false',
             99999999999999999999999999999, 'z3'),
        ]:
            with self.subTest(direction=direction, z3=z3, oms=oms, provenance=provenance):
                source.write_text(f'({"minimize" if direction == "min" else "maximize"} x)\n(check-sat)\n')
                listing.write_text('filename,z3_best_value,z3_is_optimal,optimathsat_best_value,optimathsat_is_optimal\n'
                                   f'QF_NIA/group/a.smt2,{z3},{z3_optimal},{oms},{oms_optimal}\n')
                case = self.runner.collect_inputs('original', self.root, originals)[0]
                self.assertEqual(getattr(case, 'known_optimum', None), optimum)
                self.assertEqual(case.known_optimum_source, provenance)

    def test_selection_disables_unproved_missing_or_inconsistent_optima(self):
        originals, source, _ = self.input_tables()
        listing = self.root / 'result/original/original_unsolved_bounded_sol.csv'
        for direction, z3, z3_optimal, oms, oms_optimal in [
            ('min', '10', 'false', '12', 'false'),
            ('min', '', 'true', '12', 'false'),
            ('min', '10', 'true', '12', 'true'),
            ('min', '10', 'true', '8', 'false'),
            ('min', '8', 'false', '10', 'true'),
            ('max', '10', 'true', '12', 'false'),
            ('max', '12', 'false', '10', 'true'),
            ('min', '10', 'yes', '12', 'false'),
        ]:
            with self.subTest(direction=direction, z3=z3, oms=oms):
                source.write_text(f'({"minimize" if direction == "min" else "maximize"} x)\n(check-sat)\n')
                listing.write_text('filename,z3_best_value,z3_is_optimal,optimathsat_best_value,optimathsat_is_optimal\n'
                                   f'QF_NIA/group/a.smt2,{z3},{z3_optimal},{oms},{oms_optimal}\n')
                case = self.runner.collect_inputs('original', self.root, originals)[0]
                self.assertIsNone(getattr(case, 'known_optimum', None))
                self.assertEqual(getattr(case, 'known_optimum_source', None), '')

    def test_selection_without_optimality_columns_has_no_known_optimum(self):
        originals, _, _ = self.input_tables()
        for case in self.runner.collect_inputs('all', self.root, originals):
            self.assertIsNone(getattr(case, 'known_optimum', None))
            self.assertEqual(getattr(case, 'known_optimum_source', None), '')

    def test_generated_category_and_name_cannot_escape(self):
        originals,_,_=self.input_tables()
        listing=self.root/'result/new/new_unsolved_bounded_sol.csv'
        for name,category in [('b.smt2','../design'),('../b.smt2','design'),('b.smt2','/tmp')]:
            listing.write_text('filename,category,z3_best_value,optimathsat_best_value\n'+name+','+category+',0,1\n')
            with self.subTest(name=name,category=category), self.assertRaises(ValueError):
                self.runner.collect_inputs('new',self.root,originals)

    def test_selection_rejects_missing_duplicate_and_escaping_rows(self):
        for row in ['../escape.smt2,0,1\n','QF_NIA/missing.smt2,0,1\n','QF_NIA/group/a.smt2,0,1\nQF_NIA/group/a.smt2,0,1\n']:
            with self.subTest(row=row):
                originals,_,_=self.input_tables()
                (self.root/'result/original/original_unsolved_bounded_sol.csv').write_text('filename,z3_best_value,optimathsat_best_value\n'+row)
                with self.assertRaises(ValueError): self.runner.collect_inputs('original',self.root,originals)
                import shutil
                shutil.rmtree(originals); shutil.rmtree(self.root/'result'); shutil.rmtree(self.root/'new_benchmark')

    def test_result_retains_first_and_best_validated_progress(self):
        config,case=self.setup_case(native_body=
            "import json,sys\nassert '--target=10' in sys.argv\n"
            "for value,t in [('12',0.001),('8',0.002),('9',0.003)]: print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':value,'elapsed_seconds':t,'model_validated':True}),flush=True)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.004,'model_validated':True,'termination':'timeout','is_optimal':False}))\n")
        artifacts=self.root/'artifacts'; artifacts.mkdir()
        result=self.runner.run_case(case,config,threading.Event(),artifacts)
        self.assertEqual((result['status'],result['best_value'],result['termination']),('sat','8','timeout'))
        self.assertEqual(result['vs_best'],'better')
        self.assertEqual(result['model_validated'],'true')
        self.assertGreater(float(result['time_to_best_seconds']),float(result['time_to_first_seconds']))
        progress=[json.loads(line) for line in (artifacts/'progress.jsonl').read_text().splitlines()]
        self.assertEqual([x['objective_value'] for x in progress],['12','8'])

    def test_final_record_recovers_actual_incumbent_time(self):
        config,case=self.setup_case(native_body=
            "import json,time\ntime.sleep(.12)\nprint(json.dumps({'event':'final','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':.12,'time_to_best_seconds':.001,'model_validated':True,'termination':'timeout'}))\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['best_value'],'8')
        self.assertGreater(float(result['wall_seconds'])-float(result['time_to_best_seconds']),.09)

    def test_maximize_passes_better_reference_target(self):
        config,case=self.setup_case(direction='max',native_body=
            "import json,sys\nassert '--target=12' in sys.argv\nprint(json.dumps({'status':'unknown','direction':'max','termination':'timeout'}))\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'unknown')

    def test_target_is_optional_and_missing_refs_do_not_supply_one(self):
        body="import json,sys\nassert not any(x.startswith('--target=') for x in sys.argv)\nprint(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n"
        for use_target,z3,oms in [(False,10,12),(True,None,None)]:
            config,case=self.setup_case(native_body=body,z3=z3,oms=oms)
            config=self.runner.replace(config,use_target=use_target)
            self.assertEqual(self.runner.run_case(case,config,threading.Event())['status'],'unknown')

    def test_streamed_validated_incumbent_survives_external_timeout(self):
        config,case=self.setup_case(timeout=0.3,native_body=
            "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
        started=time.monotonic();result=self.runner.run_case(case,config,threading.Event())
        self.assertLess(time.monotonic()-started,1.5)
        self.assertEqual((result['status'],result['termination'],result['best_value']),('sat','wall_timeout','8'))
        self.assertEqual(result['vs_best'],'better')

    def test_timeout_restores_last_published_model_before_unpublished_overwrite(self):
        config, case = self.setup_case(timeout=.25, native_body=
            'import json,os,pathlib,sys,time\n'
            "model=pathlib.Path(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--model-output=')))\n"
            "model.write_text('x\\t8\\n')\n"
            "record=dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,model_inode=model.stat().st_ino)\n"
            'print(json.dumps(record),flush=True)\n'
            "os.link(model,str(model)+'.prev')\n"
            "temporary=pathlib.Path(str(model)+'.tmp')\n"
            "temporary.write_text('x\\t7\\n')\n"
            'os.replace(temporary,model)\n'
            'time.sleep(30)\n')
        artifacts = self.root / 'transaction-race'
        result = self.runner.run_case(case, config, threading.Event(), artifacts)
        self.assertEqual((result['termination'], result['best_value']), ('wall_timeout', '8'))
        self.assertEqual((artifacts / 'model.tsv').read_text(), 'x\t8\n')
        self.assertEqual(result['model_validated'], 'true')
        progress = [json.loads(line) for line in (artifacts / 'progress.jsonl').read_text().splitlines()]
        self.assertEqual([record['objective_value'] for record in progress], ['8'])

    def test_missing_published_model_identity_is_an_explicit_error(self):
        config, case = self.setup_case(timeout=.25, native_body=
            'import json,pathlib,sys,time\n'
            "model=pathlib.Path(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--model-output=')))\n"
            "model.write_text('x\\t8\\n')\n"
            "record=dict(event='incumbent',status='sat',direction='min',objective_value='8',elapsed_seconds=.001,model_validated=True,model_inode=model.stat().st_ino)\n"
            'print(json.dumps(record),flush=True)\n'
            'model.unlink()\n'
            'time.sleep(30)\n')
        artifacts = self.root / 'lost-published-model'
        result = self.runner.run_case(case, config, threading.Event(), artifacts)
        self.assertEqual(result['status'], 'error')
        self.assertIn('published model', result['error'])
        self.assertEqual(result['model_validated'], 'false')

    def test_known_optimum_equality_stops_without_target_and_retains_model(self):
        for direction, first, provenance in [('min', '12', 'z3'), ('max', '8', 'optimathsat')]:
            with self.subTest(direction=direction):
                pidfile = self.root / f'{direction}-child.pid'
                config, case = self.setup_case(timeout=.7, direction=direction, native_body=
                    'import json,pathlib,subprocess,sys,time\n'
                    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                    f'pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n'
                    "assert not any(x.startswith('--target=') for x in sys.argv)\n"
                    "model=pathlib.Path(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--model-output=')))\n"
                    f"for value,t in [({first!r},.001),('10',.002)]:\n"
                    " model.write_text('x\\t'+value+'\\n')\n"
                    f" print(json.dumps({{'event':'incumbent','status':'sat','direction':{direction!r},'objective_value':value,'elapsed_seconds':t,'model_validated':True}}),flush=True)\n"
                    'time.sleep(30)\n')
                case = self.runner.replace(case, known_optimum=10, known_optimum_source=provenance)
                config = self.runner.replace(config, use_target=False)
                artifacts = self.root / f'artifacts-{direction}'
                started = time.monotonic()
                result = self.runner.run_case(case, config, threading.Event(), artifacts)
                self.assertEqual((result['status'], result['termination'], result['best_value']),
                                 ('sat', 'known_optimum_reached', '10'))
                self.assertLess(time.monotonic() - started, .5)
                self.assertEqual(result['error'], '')
                self.assertEqual(result['model_validated'], 'true')
                self.assertEqual(result['is_optimal'], 'false')
                self.assertEqual((result['known_optimum_value'], result['known_optimum_source'],
                                  result['known_optimum_reached']), ('10', provenance, 'true'))
                self.assertGreater(float(result['time_to_best_seconds']), float(result['time_to_first_seconds']))
                self.assertEqual((artifacts / 'model.tsv').read_text(), 'x\t10\n')
                progress = [json.loads(line) for line in (artifacts / 'progress.jsonl').read_text().splitlines()]
                self.assertEqual([record['objective_value'] for record in progress], [first, '10'])
                pid = int(pidfile.read_text())
                for _ in range(20):
                    state = Path(f'/proc/{pid}/stat')
                    if not state.exists() or state.read_text().split()[2] == 'Z':
                        break
                    time.sleep(.03)
                else:
                    self.fail('known-optimum stop left a live child process')

    def test_known_optimum_stop_is_default_on_and_has_independent_opt_out(self):
        self.assertIs(getattr(self.runner.Config(), 'stop_at_known_optimum', None), True)
        parser = self.runner.make_parser({})
        self.assertFalse(parser.parse_args(['--no-target']).no_known_optimum_stop)
        self.assertTrue(parser.parse_args(['--no-known-optimum-stop']).no_known_optimum_stop)
        config, case = self.setup_case(timeout=.25, native_body=
            "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'10','elapsed_seconds':.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
        case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
        config = self.runner.replace(config, use_target=False, stop_at_known_optimum=False)
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual((result['status'], result['termination'], result['best_value']), ('sat', 'wall_timeout', '10'))
        self.assertEqual(result['known_optimum_reached'], 'true')

    def test_known_optimum_requires_equality_and_unproved_best_uses_full_budget(self):
        for direction, optimum, value in [('min', 10, '11'), ('max', 10, '9'), ('min', None, '8')]:
            with self.subTest(direction=direction, optimum=optimum):
                record = {'event':'incumbent', 'status':'sat', 'direction':direction,
                          'objective_value':value, 'elapsed_seconds':.001, 'model_validated':True}
                config, case = self.setup_case(timeout=.25, direction=direction, native_body=
                    'import json,time\nprint(json.dumps(' + repr(record) + '),flush=True)\ntime.sleep(30)\n')
                case = self.runner.replace(case, known_optimum=optimum, known_optimum_source='z3' if optimum else '')
                config = self.runner.replace(config, use_target=False)
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((result['termination'], result['best_value']), ('wall_timeout', value))
                self.assertEqual(result.get('known_optimum_reached'), 'false')
                self.assertGreater(float(result['wall_seconds']), .20)

    def test_known_optimum_stop_drains_fast_updates_and_detects_mismatch(self):
        for direction, better in [('min', '9'), ('max', '11')]:
            with self.subTest(direction=direction):
                config, case = self.setup_case(direction=direction, native_body=
                    'import json,sys,time\n'
                    f"records=[{{'event':'incumbent','status':'sat','direction':{direction!r},'objective_value':value,'elapsed_seconds':t,'model_validated':True}} for value,t in [('10',.001),({better!r},.002)]]\n"
                    "sys.stdout.write('\\n'.join(map(json.dumps,records))+'\\n');sys.stdout.flush()\ntime.sleep(30)\n")
                case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((result['status'], result['termination'], result['best_value']), ('error', 'error', better))
                self.assertIn('known optimum', result['error'].lower())
                self.assertEqual(result['known_optimum_reached'], 'false')

    def test_known_optimum_stop_drains_updates_published_during_cleanup(self):
        config, case = self.setup_case(native_body=
            'import json,pathlib,signal,sys,time\n'
            "model=pathlib.Path(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--model-output=')))\n"
            'def emit(value,t):\n'
            " model.write_text('x\\t'+value+'\\n')\n"
            " print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':value,'elapsed_seconds':t,'model_validated':True}),flush=True)\n"
            "def finish(sig,frame):\n emit('9',.002)\n sys.exit(0)\n"
            "signal.signal(signal.SIGTERM,finish)\nemit('10',.001)\ntime.sleep(30)\n")
        case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
        artifacts = self.root / 'cleanup-artifacts'
        result = self.runner.run_case(case, config, threading.Event(), artifacts)
        self.assertEqual((result['status'], result['best_value']), ('error', '9'))
        self.assertIn('known optimum', result['error'].lower())
        self.assertEqual(result['known_optimum_reached'], 'false')
        self.assertEqual((artifacts / 'model.tsv').read_text(), 'x\t9\n')
        self.assertEqual(len((artifacts / 'progress.jsonl').read_text().splitlines()), 2)

    def test_known_optimum_final_record_and_batch_metadata(self):
        config, case = self.setup_case(native_body=
            "import json\nprint(json.dumps({'status':'sat','direction':'min','objective_value':'10','elapsed_seconds':.002,'time_to_best_seconds':.001,'model_validated':True,'termination':'timeout'}),flush=True)\n")
        case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3+optimathsat')
        output = self.root / 'known-results'
        self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            result = next(csv.DictReader(handle))
        self.assertEqual((result['status'], result['termination'], result.get('known_optimum_reached')),
                         ('sat', 'timeout', 'true'))
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertIs(manifest['known_optimum_stop_enabled'], True)
        self.assertEqual(manifest['cases'][0]['known_optimum_value'], '10')
        self.assertEqual(manifest['cases'][0]['known_optimum_source'], 'z3+optimathsat')
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['known_optimum_reached'], 1)

    def test_known_optimum_never_accepts_invalid_progress_or_hides_native_failure(self):
        base = {'event':'incumbent', 'status':'sat', 'direction':'min',
                'objective_value':'10', 'elapsed_seconds':.001, 'model_validated':True}
        for changed in [{'model_validated':False}, {'objective_value':'10.0'},
                        {'direction':'max'}, {'elapsed_seconds':99999}]:
            with self.subTest(changed=changed):
                record = dict(base, **changed)
                config, case = self.setup_case(native_body='import json\nprint(json.dumps(' + repr(record) + '),flush=True)\n')
                case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((result['status'], result['best_value']), ('error', ''))
                self.assertEqual(result.get('known_optimum_reached'), 'false')
        for final in [{'status':'error', 'error':'native fault'}, {'status':'unsat'}]:
            with self.subTest(final=final):
                config, case = self.setup_case(native_body=
                    'import json,sys\n'
                    + 'sys.stdout.write(json.dumps(' + repr(base) + ")+ '\\n' + json.dumps(" + repr(final) + ")+ '\\n');sys.stdout.flush()\n")
                case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((result['status'], result['best_value']), ('error', '10'))
                self.assertEqual(result.get('known_optimum_reached'), 'false')

    def test_invalid_records_never_become_incumbents(self):
        for changed in [{'model_validated':False},{'objective_value':'1.5'},{'objective_value':8},{'direction':'max'},{'elapsed_seconds':-1},{'elapsed_seconds':99999}]:
            record={'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True};record.update(changed)
            config,case=self.setup_case(native_body='import json\nprint(json.dumps('+repr(record)+'))\n')
            result=self.runner.run_case(case,config,threading.Event())
            self.assertEqual(result['status'],'error',changed)
            self.assertEqual(result['best_value'],'',changed)

    def test_native_failure_retains_prior_validated_value_and_honest_error(self):
        config,case=self.setup_case(native_body=
            "import json,sys\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\nprint(json.dumps({'status':'error','error':'native fault'}))\nsys.exit(7)\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual((result['status'],result['best_value']),('error','8'))
        self.assertIn('native fault',result['error'])

    def test_failed_conversion_never_starts_search(self):
        marker=self.root/'started'
        config,case=self.setup_case(converter_body="import sys\nprint('bad conversion',file=sys.stderr)\nsys.exit(2)\n",native_body=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'error');self.assertIn('bad conversion',result['error']);self.assertFalse(marker.exists())

    def test_conversion_and_search_share_total_budget(self):
        config,case=self.setup_case(timeout=0.3,converter_body="import pathlib,sys,time\ntime.sleep(.20)\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n",native_body="import time\ntime.sleep(30)\n")
        started=time.monotonic();result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'timeout');self.assertLess(time.monotonic()-started,1)

    def test_timeout_cleans_up_descendants_even_after_parent_exit(self):
        pidfile=self.root/'child.pid'
        config,case=self.setup_case(timeout=.3,native_body=
            "import subprocess,sys,pathlib,json\nchild=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
            + f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
            + "print(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n")
        self.runner.run_case(case,config,threading.Event())
        pid=int(pidfile.read_text())
        for _ in range(20):
            state=Path(f'/proc/{pid}/stat')
            if not state.exists() or state.read_text().split()[2]=='Z': break
            time.sleep(.03)
        else: self.fail('solver left a live child process')

    def test_interruption_retains_progress_and_cleans_up(self):
        config,case=self.setup_case(timeout=2,native_body=
            "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
        stop=threading.Event()
        timer=threading.Timer(.2,stop.set);timer.start()
        try:
            result=self.runner.run_case(case,config,stop)
        finally:
            timer.cancel()
        self.assertEqual((result['status'],result['termination'],result['best_value']),('interrupted','interrupted','8'))

    def test_parallel_jobs_bound_live_solver_processes(self):
        lock=self.root/'count.lock';state=self.root/'count';journal=self.root/'active'
        body=("import fcntl,pathlib,time,json\n"
              +f"state=pathlib.Path({str(state)!r})\n"
              +f"with open({str(lock)!r},'a') as lock:\n"
              +" fcntl.flock(lock,fcntl.LOCK_EX)\n count=int(state.read_text()) if state.exists() else 0\n state.write_text(str(count+1))\n"
              +f" with open({str(journal)!r},'a') as journal: journal.write(str(count+1)+'\\n')\n"
              +"time.sleep(.1)\n"
              +f"with open({str(lock)!r},'a') as lock:\n"
              +" fcntl.flock(lock,fcntl.LOCK_EX)\n state.write_text(str(int(state.read_text())-1))\n"
              +"print(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n")
        config,case=self.setup_case(native_body=body)
        config=self.runner.replace(config,jobs=2)
        cases=[self.runner.replace(case,filename=f'{i}.smt2') for i in range(5)]
        self.assertEqual(self.runner.run_batch(cases,config,self.root/'parallel'),0)
        self.assertEqual(max(map(int,journal.read_text().splitlines())),2)
        self.assertEqual(state.read_text(),'0')

    def test_known_optimum_stop_preserves_native_cleanup_error_and_exit_code(self):
        for code, report in [
            (2, {'event':'final', 'status':'sat', 'direction':'min', 'objective_value':'10',
                 'elapsed_seconds':.002, 'model_validated':True, 'termination':'error', 'error':'cleanup fault'}),
            (0, {'event':'final', 'status':'sat', 'direction':'min', 'objective_value':'10',
                 'elapsed_seconds':.002, 'model_validated':True, 'termination':'error', 'error':'cleanup fault'}),
            (7, None),
        ]:
            with self.subTest(code=code, report=report):
                config, case = self.setup_case(native_body=
                    'import json,signal,sys,time\n'
                    'def finish(sig,frame):\n'
                    + (' print(json.dumps(' + repr(report) + '),flush=True)\n' if report is not None else '')
                    + f' sys.exit({code})\n'
                    + "signal.signal(signal.SIGTERM,finish)\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'10','elapsed_seconds':.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
                case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual((result['status'], result['termination'], result['best_value']), ('error', 'error', '10'))
                self.assertEqual(result['known_optimum_reached'], 'false')
                self.assertIn('cleanup fault' if report is not None else 'exit 7', result['error'])

    def test_worker_failure_retains_known_optimum_metadata(self):
        from unittest.mock import patch
        config, case = self.setup_case()
        case = self.runner.replace(case, known_optimum=10, known_optimum_source='z3')
        output = self.root / 'worker-error'
        with patch.object(self.runner, 'run_case', side_effect=RuntimeError('worker failed')):
            self.assertEqual(self.runner.run_batch([case], config, output), 0)
        with (output / 'results.csv').open(newline='') as handle:
            result = next(csv.DictReader(handle))
        self.assertEqual((result['status'], result['termination']), ('error', 'worker_error'))
        self.assertEqual((result['known_optimum_value'], result['known_optimum_source'],
                          result['known_optimum_reached']), ('10', 'z3', 'false'))

    def test_summary_counts_all_selected_including_failures(self):
        config,case=self.setup_case()
        rows=[{'status':'error','vs_z3':'no_incumbent','vs_optimathsat':'no_incumbent','vs_best':'no_incumbent','strictly_better_than_best':'false'},
              {'status':'sat','vs_z3':'better','vs_optimathsat':'better','vs_best':'better','strictly_better_than_best':'true'}]
        summary=self.runner._summary([case]*3,rows,config)
        self.assertEqual(summary['strict_win_rate'],1/3)
        self.assertFalse(summary['over_80_percent'])

    def test_batch_preserves_existing_outputs_and_records_summary(self):
        config,case=self.setup_case();output=self.root/'results'
        self.assertEqual(self.runner.run_batch([case],config,output),0)
        with (output/'results.csv').open(newline='') as handle:
            rows=list(csv.DictReader(handle))
        self.assertEqual(rows[0]['best_value'],'8')
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual((summary['selected'],summary['strictly_better_than_best'],summary['strict_win_rate']), (1,1,1.0))
        self.assertEqual(len(list((output/'cases').iterdir())),1)
        artifact=next((output/'cases').iterdir())
        self.assertEqual((artifact/'input.nia').read_text(),'converted')
        with self.assertRaises(FileExistsError): self.runner.run_batch([case],config,output)

if __name__=='__main__':unittest.main()
