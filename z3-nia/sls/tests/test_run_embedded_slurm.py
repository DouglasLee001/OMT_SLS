#!/usr/bin/env python3
"""Embedded Slurm launcher checks; no solver or scheduler is executed."""
from contextlib import redirect_stderr, redirect_stdout
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TREE = Path(__file__).resolve().parents[2]
MODULE = TREE / 'experiment/run_embedded_slurm.py'


def load(path, name):
    assert path.is_file(), f'missing implementation: {path.name}'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EmbeddedSlurmTests(unittest.TestCase):
    def setUp(self):
        self.m = load(MODULE, 'embedded_slurm_under_test')
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def case(self):
        runner, _, _ = self.m.reference_modules()
        return runner.InputCase(self.root / 'case.smt2', 'case.smt2', 'original', 'original', 'min')

    def test_default_full_configuration_matches_embedded_experiment(self):
        args = self.m.make_parser().parse_args([])
        config = self.m.make_config(args)
        self.assertEqual(args.nodes, 'server,node1,node2,node3,node4,node5')
        self.assertEqual(args.memory_mb, 53493)
        self.assertFalse(args.queue_workers)
        self.assertEqual(config.solver, TREE / 'build/z3')
        self.assertEqual((config.jobs, config.timeout, config.subset), (80, 1200, 'all'))
        self.assertFalse(config.use_target)
        self.assertFalse(config.stop_at_known_optimum)
        self.assertEqual((config.large_target, config.unbounded_threshold), (1000000, 1000000))
        self.assertTrue(config.prove_unbounded and config.prove_optimal)
        self.assertEqual((config.optimality_stall, config.optimality_timeout), (10, 30))
        self.assertTrue(config.optimality_resume and config.optimality_burst)
        self.assertTrue(config.optimality_cutset and config.optimality_balanced)
        self.assertEqual((config.initial_z3_after, config.initial_z3_timeout), (300, 120))

    def test_worker_sbatch_requests_one_available_node_and_310_minutes(self):
        state = dict(nodes=list(self.m.DEFAULT_NODES), jobs_per_node=80, selected=6310,
                     timeout=1200, partition='all', job_name='z3_nia', memory_mb=53493)
        state.update(shards=[list(range(rank, 6310, 6)) for rank in range(6)],
                     python=sys.executable, snapshot=dict(launcher=str(MODULE)))
        command = self.m.make_worker_sbatch_command(self.root, state, 0, 'node3')
        for option in ('--nodes=1', '--ntasks=1', '--ntasks-per-node=1', '--cpus-per-task=80',
                       '--mem=53493M', '--time=310', '--nodelist=node3'):
            self.assertIn(option, command)
        self.assertNotIn('--exclusive', command)
        self.assertNotIn('--mem=0', command)

    def test_dry_run_never_prepares_or_executes_commands(self):
        output = self.root / 'untouched'
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', side_effect=AssertionError('no preparation')), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no command')), \
             redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.m.main(['--dry-run', '--output', str(output)]), 0)
        self.assertFalse(output.exists())
        self.assertIn('no snapshot, submission, build, or solver', printed.getvalue())

    def test_direct_python_entry_does_not_write_reference_bytecode(self):
        experiment = self.root / 'experiment'; experiment.mkdir()
        copied = experiment / MODULE.name; shutil.copy2(MODULE, copied)
        reference = self.root / 'reference/script'; reference.mkdir(parents=True)
        (reference / 'run_optimize.py').write_text('')
        (reference / 'run_optimize_slurm.py').write_text('import run_optimize as runner\n')
        (reference / 'slurm_snapshot.py').write_text('')
        environment = dict(os.environ)
        environment.pop('PYTHONDONTWRITEBYTECODE', None)
        probe = 'import runpy,sys; runpy.run_path(sys.argv[1])["reference_modules"]()'
        subprocess.run([sys.executable, '-c', probe, str(copied)], check=True, capture_output=True, env=environment)
        self.assertFalse((reference / '__pycache__').exists())

    def test_complete_selection_checks_both_suite_counts_before_limit(self):
        runner, _, _ = self.m.reference_modules()
        original = self.case()
        generated = runner.replace(original, suite='new')
        full = [original] * 5744 + [generated] * 566
        with patch.object(runner, 'collect_inputs', return_value=full) as collect:
            selected = self.m.select_cases(2)
        self.assertEqual(len(selected), 2)
        collect.assert_called_once_with('all', subset='all')
        with patch.object(runner, 'collect_inputs', return_value=full[:-1]):
            with self.assertRaisesRegex(ValueError, '6310'):
                self.m.select_cases(None)

    def selection_fixture(self):
        runner, _, _ = self.m.reference_modules()
        original = self.case()
        cases = [runner.replace(original, filename=f'QF_NIA/case-{i}.smt2') for i in range(5744)]
        cases += [runner.replace(original, filename=f'case-{i}.smt2', suite='new', category='generated')
                  for i in range(566)]
        selected = cases[:414] + [cases[-1]]
        path = self.root / 'nia_ls.csv'
        rows = []
        for case in cases:
            name = (case.filename if case.suite == 'original' else
                    f'new_benchmark/{case.category}/instance/{case.filename}')
            rows.append({'文件名': name, '状态': 'B' if case in selected else 'A2',
                         '目标值': '0', '时间（秒）': '1'})
        rows[-1]['状态'] = 'C'
        self.write_selection(path, rows)
        return runner, cases, selected, path, rows

    def write_selection(self, path, rows):
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['文件名', '状态', '目标值', '时间（秒）'])
            writer.writeheader()
            writer.writerows(rows)

    def test_selection_from_uses_canonical_original_and_generated_names(self):
        runner, cases, expected, selection, _ = self.selection_fixture()
        with patch.object(runner, 'collect_inputs', return_value=cases):
            self.assertEqual(self.m.select_cases(None, selection), expected)
        args = self.m.make_parser().parse_args(['--selection-from', str(selection)])
        self.assertFalse(self.m.make_config(args).selection_complete)

    def test_selection_from_rejects_empty_foreign_duplicate_and_no_unsolved_cases(self):
        runner, cases, _, selection, rows = self.selection_fixture()
        for changed, message in (([], 'empty'),
                                 ([dict(rows[0], 文件名='')], 'name'),
                                 ([dict(rows[0], 文件名='unknown.smt2')], 'unknown'),
                                 ([rows[0], rows[0]], 'duplicate'),
                                 ([dict(row, 状态='A2') for row in rows], 'B/C')):
            with self.subTest(message=message):
                self.write_selection(selection, changed)
                with patch.object(runner, 'collect_inputs', return_value=cases), \
                     self.assertRaisesRegex(ValueError, message):
                    self.m.select_cases(None, selection)

    def test_selection_from_is_mutually_exclusive_with_limit(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.m.make_parser().parse_args(['--selection-from', '/baseline/nia_ls.csv', '--limit', '1'])

    def test_solver_option_allowlist_rejects_stopping_options_and_invalid_timings_before_selection(self):
        for option in ('--target=1', '--timeout=2400', '--core-neighborhood-stall=0',
                       '--core-neighborhood-budget=nan', '--core-neighborhood-budget=inf',
                       '--core-neighborhood-stall=-1', '--convex-exchange=true', '--convex-exchange=',
                       '--smt-exchange', '--smt-exchange=', '--smt-exchange=off',
                       '--smt-exchange=unknown', '--smt-exchange=Backend', '--smt-exchange=hints '):
            argv = ['--dry-run', '--solver-option=--core-neighborhood', '--solver-option=' + option]
            with self.subTest(option=option), \
                 patch.object(self.m, 'select_cases', side_effect=AssertionError('no selection')), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(self.m.main(argv), 2)
        for options in (['--core-neighborhood-stall=60'], ['--core-neighborhood', '--core-neighborhood'],
                        ['--convex-exchange', '--convex-exchange'],
                        ['--convex-exchange', '--core-neighborhood-budget=60'],
                        ['--smt-exchange=backend', '--smt-exchange=backend'],
                        ['--smt-exchange=backend', '--smt-exchange=hints'],
                        ['--smt-exchange=hints', '--smt-exchange=backend'],
                        ['--smt-exchange=hints', '--core-neighborhood-budget=60']):
            with self.subTest(options=options), \
                 patch.object(self.m, 'select_cases', side_effect=AssertionError('no selection')), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(self.m.main(['--dry-run', *['--solver-option=' + x for x in options]]), 2)

    def test_selection_from_accepts_a_small_nonempty_subset(self):
        runner, cases, _, selection, rows = self.selection_fixture()
        self.write_selection(selection, [rows[-1]])
        with patch.object(runner, 'collect_inputs', return_value=cases):
            self.assertEqual(self.m.select_cases(None, selection), [cases[-1]])

    def freeze_fixture(self, build_hash=None, solver_options=(), help_text=None,
                       case_count=1, nodes=None, queue_workers=False,
                       cases_per_shard=None, jobs=80):
        runner, launcher, snapshot = self.m.reference_modules()
        solver = self.root / 'z3'; solver.write_text('fake embedded binary\n'); solver.chmod(0o755)
        record = self.root / 'embedding-build.json'
        record.write_text(json.dumps(dict(executable_sha256=build_hash or hashlib.sha256(solver.read_bytes()).hexdigest())))
        baseline = self.root / 'competitor.csv'; baseline.write_text('filename\n')
        args = self.m.make_parser().parse_args(['--output', str(self.root / 'run'), '--solver', str(solver), '--limit', str(case_count),
                                              '--jobs', str(jobs),
                                              *(['--nodes', nodes] if nodes else []),
                                              *(['--queue-workers'] if queue_workers else []),
                                              *(['--cases-per-shard', str(cases_per_shard)] if cases_per_shard is not None else []),
                                              *['--solver-option=' + option for option in solver_options]])
        config = self.m.make_config(args)
        original_snapshot = snapshot.create_snapshot

        def fake_snapshot(output, repo_root, solver, converter, competitor):
            self.assertEqual(competitor, baseline)
            source = output / 'source'; source.mkdir()
            files = {}
            def copy(path, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                files[destination.relative_to(output).as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
                return str(destination)
            frozen_solver = copy(solver, source / 'nia_ls')
            frozen_converter = copy(converter, source / 'converter/omt_to_ls.py')
            frozen_baseline = copy(competitor, source / 'result/competitor.csv')
            for filename in ('run_optimize.py', 'run_optimize_slurm.py', 'slurm_snapshot.py', 'smtlib.py', 'report_results.py'):
                copy(TREE / 'reference/script' / filename, source / 'script' / filename)
            (source / 'lib').mkdir()
            return dict(solver=frozen_solver, converter=frozen_converter, baseline=frozen_baseline,
                        launcher=str(source / 'script/run_optimize_slurm.py'), library_dir=str(source / 'lib'),
                        files=files, solver_sha256=files['source/nia_ls'],
                        converter_sha256=files['source/converter/omt_to_ls.py'])
        with patch.object(self.m, 'BUILD_RECORD', record), patch.object(self.m, 'COMPETITOR', baseline), \
             patch.object(snapshot, 'create_snapshot', side_effect=fake_snapshot), \
             patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess(
                 [], 0, '--core-neighborhood --core-neighborhood-stall=SECONDS --core-neighborhood-budget=SECONDS'
                 if help_text is None else help_text, '')) as preflight:
            state = self.m.prepare([runner.replace(self.case(), filename=f'case-{i}.smt2')
                                   for i in range(case_count)], config, args)
        preflight.assert_called_once()
        self.assertEqual(preflight.call_args.args[0], [state['snapshot']['solver'], '--nia-ls', '--help'])
        self.assertTrue(preflight.call_args.kwargs['check'])
        self.assertIs(snapshot.create_snapshot, original_snapshot)
        return args.output, state

    def test_cases_per_shard_full_set_preserves_coverage_and_40_parallel(self):
        self.assertTrue(hasattr(self.m.make_parser().parse_args([]), 'cases_per_shard'))
        output, state = self.freeze_fixture(case_count=6310, jobs=40, cases_per_shard=40,
            nodes='server,node1,node2,node4,node5')
        self.assertEqual(len(state['shards']), 158)
        indices = [i for chunk in state['shards'] for i in chunk]
        self.assertEqual(sorted(indices), list(range(6310)))
        self.assertTrue(all(0 < len(chunk) <= 40 for chunk in state['shards']))
        self.assertEqual(state['jobs_per_node'], 40)
        self.assertEqual(state['cases_per_shard'], 40)
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['cases_per_shard'], 40)
        self.assertEqual(manifest['shard_count'], 158)
        self.assertEqual(manifest['jobs'], 200)
        command = self.m.make_worker_sbatch_command(output, state, 0, 'node2')
        self.assertIn('--cpus-per-task=40', command)
        self.assertIn('--time=50', command)

    def test_cases_per_shard_default_keeps_node_sized_partitioning(self):
        self.assertTrue(hasattr(self.m.make_parser().parse_args([]), 'cases_per_shard'))
        output, state = self.freeze_fixture(case_count=83, jobs=40,
            nodes='server,node1,node2,node4,node5')
        self.assertEqual(len(state['shards']), 5)
        self.assertEqual(sorted(i for chunk in state['shards'] for i in chunk), list(range(83)))

    def test_cases_per_shard_tail_and_small_selection_are_complete(self):
        self.assertTrue(hasattr(self.m.make_parser().parse_args([]), 'cases_per_shard'))
        output, state = self.freeze_fixture(case_count=83, jobs=40, cases_per_shard=40,
            nodes='server,node1,node2,node4,node5')
        self.assertEqual(len(state['shards']), 3)
        self.assertTrue(all(0 < len(chunk) <= 40 for chunk in state['shards']))
        self.assertEqual(sorted(i for chunk in state['shards'] for i in chunk), list(range(83)))

    def test_cases_per_shard_rejects_nonpositive_before_collecting(self):
        self.assertTrue(hasattr(self.m.make_parser().parse_args([]), 'cases_per_shard'))
        for size in ('0', '-1'):
            with self.subTest(size=size), redirect_stderr(io.StringIO()), \
                 patch.object(self.m, 'select_cases') as collect:
                self.assertEqual(self.m.main(['--dry-run', '--cases-per-shard', size]), 2)
                collect.assert_not_called()

    def test_queue_workers_freezes_boolean_mode_in_all_run_records(self):
        output, state = self.freeze_fixture(queue_workers=True, case_count=415, nodes='server,node1,node2')
        self.assertIs(state['queue_workers'], True)
        for relative in ('manifest.json', 'embedded_receipt.json', 'source/embedded_receipt.json',
                         'source/scheduler_options.json'):
            self.assertIs(json.loads((output / relative).read_text())['queue_workers'], True)
        self.assertIn('source/scheduler_options.json', state['snapshot']['files'])
        self.assertTrue(self.m.check_queue_workers(output, state))
        for changed in (False, 'true', 1):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'queue'):
                self.m.check_queue_workers(output, dict(state, queue_workers=changed))
        self.assertEqual(len(state['shards']), 3)

    def test_default_queue_mode_is_frozen_false_and_legacy_runs_remain_false(self):
        output, state = self.freeze_fixture()
        self.assertIs(state['queue_workers'], False)
        self.assertFalse(self.m.check_queue_workers(output, state))
        with self.assertRaisesRegex(ValueError, 'queue'):
            self.m.check_queue_workers(output, {key: value for key, value in state.items() if key != 'queue_workers'})
        legacy = self.root / 'legacy'; legacy.mkdir()
        (legacy / 'manifest.json').write_text('{}')
        old_state = dict(snapshot=dict(files={'source/old.py': 'oldhash'}))
        self.assertFalse(self.m.check_queue_workers(legacy, old_state))
        with self.assertRaisesRegex(ValueError, 'queue'):
            self.m.check_queue_workers(legacy, dict(old_state, queue_workers=True))

    def test_legacy_queue_fallback_rejects_receipt_only_mode_fields(self):
        legacy = self.root / 'legacy-receipt'; (legacy / 'source').mkdir(parents=True)
        (legacy / 'manifest.json').write_text('{}')
        receipt = legacy / 'source/embedded_receipt.json'
        for mode in (True, False):
            with self.subTest(mode=mode):
                receipt.write_text(json.dumps(dict(queue_workers=mode)))
                state = dict(snapshot=dict(files={'source/embedded_receipt.json': hashlib.sha256(receipt.read_bytes()).hexdigest()}))
                with self.assertRaisesRegex(ValueError, 'queue'):
                    self.m.check_queue_workers(legacy, state)
        receipt.write_text('{"solver_kind": "z3-nia"}')
        state['snapshot']['files']['source/embedded_receipt.json'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
        self.assertFalse(self.m.check_queue_workers(legacy, state))

    def test_frozen_queue_mode_file_is_checked_before_coordinator(self):
        output, state = self.freeze_fixture(queue_workers=True)
        frozen = load(Path(state['snapshot']['launcher']), 'tampered_queue_mode_under_test')
        (output / 'source/scheduler_options.json').write_text('{"queue_workers": false}')
        with redirect_stderr(io.StringIO()), \
             patch.object(frozen, 'reference_modules', return_value=self.m.reference_modules()), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no external scheduler commands')):
            self.assertEqual(frozen.main(['_schedule', str(output)]), 2)

    def test_queue_workers_dry_run_has_no_writes_or_solver_scheduler_commands(self):
        output = self.root / 'untouched-queued'
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', side_effect=AssertionError('no preparation')), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no command')), \
             patch.object(self.m.subprocess, 'Popen', side_effect=AssertionError('no daemon')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(['--dry-run', '--queue-workers', '--output', str(output)]), 0)
        self.assertFalse(output.exists())
        parser = self.m.make_parser()
        self.assertEqual(self.m.make_config(parser.parse_args(['--queue-workers'])), self.m.make_config(parser.parse_args([])))

    def test_three_node_pilot_can_fill_120_parallel_cases_per_worker(self):
        output, state = self.freeze_fixture(case_count=415, nodes='server,node1,node2')
        self.assertEqual(len(state['shards']), 3)
        self.assertEqual(sorted(map(len, state['shards'])), [138, 138, 139])
        self.assertEqual(sorted(i for shard in state['shards'] for i in shard), list(range(415)))
        self.assertEqual(json.loads((output / 'manifest.json').read_text())['shard_count'], 3)

    def test_solver_options_are_frozen_in_state_manifest_and_hashed_metadata(self):
        options = ['--core-neighborhood', '--core-neighborhood-stall=60', '--core-neighborhood-budget=60']
        output, state = self.freeze_fixture(solver_options=options)
        manifest = json.loads((output / 'manifest.json').read_text())
        receipt = json.loads((output / 'embedded_receipt.json').read_text())
        self.assertEqual(state['solver_options'], options)
        self.assertEqual(manifest['solver_options'], options)
        self.assertEqual(receipt['solver_options'], options)
        self.assertIn('source/solver_options.json', state['snapshot']['files'])
        self.m.check_solver_options(output, state)
        changed = dict(state, solver_options=[])
        with self.assertRaisesRegex(ValueError, 'solver options'):
            self.m.check_solver_options(output, changed)

    def test_candidate_help_must_advertise_every_selected_solver_option(self):
        with self.assertRaisesRegex(ValueError, 'does not support.*--core-neighborhood'):
            self.freeze_fixture(solver_options=['--core-neighborhood'], help_text='regular solver help')

    def test_frozen_solver_options_file_is_checked_before_worker(self):
        output, state = self.freeze_fixture(solver_options=['--core-neighborhood'])
        frozen = load(Path(state['snapshot']['launcher']), 'tampered_options_under_test')
        (output / 'source/solver_options.json').write_text('{"solver_options": []}')
        with redirect_stderr(io.StringIO()), patch.object(frozen, 'reference_modules', return_value=self.m.reference_modules()):
            self.assertEqual(frozen.main(['_worker', str(output)]), 2)

    def test_runtime_adapter_appends_validated_options_only_to_native_optimization(self):
        adapter = load(MODULE.with_name('runtime_adapter.py'), 'options_adapter_under_test')
        options = ['--core-neighborhood', '--core-neighborhood-stall=60', '--core-neighborhood-budget=60']
        imported = adapter.translate_command([sys.executable, '/converter.py', '/case.smt2', '-o', '/input.nia'],
                                             'embedded', '/z3', '/converter.py', options)
        self.assertEqual(imported, ['/z3', '--nia-ls', '/case.smt2', '--import-only', '--export-native=/input.nia'])
        optimized = adapter.translate_command(['/reference', '/input.nia', '--optimize', '--timeout=1200'],
                                              'embedded', '/z3', '/converter.py', options)
        self.assertEqual(optimized, ['/z3', '--nia-ls', '--native-input', '/input.nia', '--optimize', '--timeout=1200', *options])
        with self.assertRaisesRegex(ValueError, 'solver option'):
            adapter.translate_command(['/reference', '/input.nia', '--optimize'],
                                      'embedded', '/z3', '/converter.py', ['--target=1'])

    def test_convex_exchange_can_run_alone_or_with_core_and_only_changes_native_options(self):
        adapter = load(MODULE.with_name('runtime_adapter.py'), 'convex_adapter_under_test')
        baseline = self.m.make_config(self.m.make_parser().parse_args([]))
        for options in (['--convex-exchange'],
                        ['--core-neighborhood', '--convex-exchange'],
                        ['--convex-exchange', '--core-neighborhood', '--core-neighborhood-budget=60']):
            with self.subTest(options=options):
                args = self.m.make_parser().parse_args(['--solver-option=' + option for option in options])
                self.assertEqual(self.m.make_config(args), baseline)
                self.assertEqual(adapter.validated_solver_options(options), tuple(options))
                imported = adapter.translate_command([sys.executable, '/converter.py', '/case.smt2', '-o', '/input.nia'],
                                                     'embedded', '/z3', '/converter.py', options)
                self.assertEqual(imported, ['/z3', '--nia-ls', '/case.smt2', '--import-only', '--export-native=/input.nia'])
                optimized = adapter.translate_command(['/reference', '/input.nia', '--optimize'],
                                                      'embedded', '/z3', '/converter.py', options)
                self.assertEqual(optimized, ['/z3', '--nia-ls', '--native-input', '/input.nia', '--optimize', *options])

    def test_convex_exchange_uses_the_existing_frozen_option_metadata(self):
        options = ['--convex-exchange']
        output, state = self.freeze_fixture(solver_options=options, help_text='--convex-exchange')
        self.assertEqual(state['solver_options'], options)
        for relative in ('manifest.json', 'embedded_receipt.json', 'source/embedded_receipt.json',
                         'source/solver_options.json'):
            self.assertEqual(json.loads((output / relative).read_text())['solver_options'], options)
        self.m.check_solver_options(output, state)

    def test_convex_exchange_still_requires_support_in_candidate_help(self):
        with self.assertRaisesRegex(ValueError, 'does not support.*--convex-exchange'):
            self.freeze_fixture(solver_options=['--convex-exchange'], help_text='--core-neighborhood')

    def test_smt_exchange_modes_only_append_to_native_commands_and_preserve_proof_config(self):
        adapter = load(MODULE.with_name('runtime_adapter.py'), 'smt_exchange_adapter_under_test')
        baseline = self.m.make_config(self.m.make_parser().parse_args([]))
        for mode in ('backend', 'hints'):
            for other in ([], ['--core-neighborhood'], ['--convex-exchange'],
                          ['--core-neighborhood', '--convex-exchange']):
                options = [*other, '--smt-exchange=' + mode]
                with self.subTest(options=options):
                    args = self.m.make_parser().parse_args(['--solver-option=' + option for option in options])
                    self.assertEqual(self.m.make_config(args), baseline)
                    self.assertEqual(adapter.validated_solver_options(options), tuple(options))
                    imported = adapter.translate_command([sys.executable, '/converter.py', '/case.smt2', '-o', '/input.nia'],
                                                         'embedded', '/z3', '/converter.py', options)
                    self.assertEqual(imported, ['/z3', '--nia-ls', '/case.smt2', '--import-only', '--export-native=/input.nia'])
                    optimized = adapter.translate_command(['/reference', '/input.nia', '--optimize'],
                                                          'embedded', '/z3', '/converter.py', options)
                    self.assertEqual(optimized, ['/z3', '--nia-ls', '--native-input', '/input.nia', '--optimize', *options])

    def test_smt_exchange_reuses_frozen_metadata_and_help_prefix(self):
        options = ['--smt-exchange=hints']
        output, state = self.freeze_fixture(solver_options=options, help_text='--smt-exchange=backend|hints')
        self.assertEqual(state['solver_options'], options)
        for relative in ('manifest.json', 'embedded_receipt.json', 'source/embedded_receipt.json',
                         'source/solver_options.json'):
            self.assertEqual(json.loads((output / relative).read_text())['solver_options'], options)
        self.m.check_solver_options(output, state)

    def test_smt_exchange_preflight_rejects_a_different_help_flag_prefix(self):
        with self.assertRaisesRegex(ValueError, 'does not support.*--smt-exchange'):
            self.freeze_fixture(solver_options=['--smt-exchange=backend'], help_text='--smt-exchange-hints')

    def test_preparation_freezes_wrapper_adapter_build_record_and_embedded_receipt(self):
        output, state = self.freeze_fixture()
        _, _, snapshot = self.m.reference_modules()
        self.assertEqual(state['snapshot']['launcher'], str(output / 'source/script/run_embedded_slurm.py'))
        for relative in ('source/script/run_embedded_slurm.py', 'source/script/runtime_adapter.py',
                         'source/script/independent_scheduler.py', 'source/embedding-build.json'):
            self.assertIn(relative, state['snapshot']['files'])
        snapshot.check_snapshot(output, state['snapshot'])
        receipt = json.loads((output / 'embedded_receipt.json').read_text())
        self.assertEqual(receipt['solver_kind'], 'z3-nia')
        self.assertEqual(receipt['solver_sha256'], state['snapshot']['solver_sha256'])
        self.assertTrue(receipt['build_record_matches_solver'])
        self.assertEqual(state['scheduler_mode'], 'independent')
        self.assertEqual(state['shards'], [[0]])
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['shard_count'], 1)
        (output / 'source/script/runtime_adapter.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            snapshot.check_snapshot(output, state['snapshot'])

    def test_rebuilt_binary_freezes_with_explicit_unmatched_old_build_record(self):
        try:
            output, state = self.freeze_fixture(build_hash='old-build-hash')
        except ValueError as error:
            self.fail(f'rebuilt solver should be accepted: {error}')
        receipt = json.loads((output / 'embedded_receipt.json').read_text())
        self.assertFalse(receipt['build_record_matches_solver'])
        self.assertEqual(receipt['solver_sha256'], state['snapshot']['solver_sha256'])
        preserved = json.loads((output / 'source/embedding-build.json').read_text())
        self.assertEqual(preserved['executable_sha256'], 'old-build-hash')

    def test_frozen_worker_installs_adapter_before_delegation_and_uses_local_tmp(self):
        output, state = self.freeze_fixture()
        frozen = load(Path(state['snapshot']['launcher']), 'frozen_embedded_slurm_under_test')
        runner, launcher, snapshot = self.m.reference_modules()
        captured = []
        def stage(command, *args, **kwargs):
            captured.append(command)
            return 0
        def worker(directory):
            self.assertEqual(os.environ['TMPDIR'], '/tmp')
            converter, solver = state['config']['converter'], state['config']['solver']
            runner._stage([sys.executable, converter, '/case.smt2', '-o', '/input.nia'])
            runner._stage([solver, '/input.nia', '--optimize', '--timeout=1200'])
            return 7
        with patch.object(frozen, 'reference_modules', return_value=(runner, launcher, snapshot)), \
             patch.object(runner, '_stage', side_effect=stage), \
             patch.object(launcher, 'worker', side_effect=worker), patch.dict(os.environ, {'TMPDIR': '/bad/nfs'}):
            self.assertEqual(frozen.main(['_worker', str(output)]), 7)
        solver = state['snapshot']['solver']
        self.assertEqual(captured[0], [solver, '--nia-ls', '/case.smt2', '--import-only', '--export-native=/input.nia'])
        self.assertEqual(captured[1], [solver, '--nia-ls', '--native-input', '/input.nia', '--optimize', '--timeout=1200'])

    def test_prepare_only_does_not_start_controller_and_normal_command_starts_one(self):
        args = ['--output', str(self.root / 'run'), '--limit', '1', '--queue-workers']
        state = dict(nodes=list(self.m.DEFAULT_NODES), jobs_per_node=80, selected=1,
                     timeout=1200, partition='all', job_name='z3_nia', memory_mb=53493,
                     python=sys.executable, shards=[[0], [], [], [], [], []],
                     snapshot=dict(launcher='/frozen/run_embedded_slurm.py'))
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', return_value=state), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no submission')), \
             patch.object(self.m.subprocess, 'Popen', side_effect=AssertionError('no controller')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(args + ['--prepare-only']), 0)
        args[1] = str(self.root)
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', return_value=state), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no foreground submission')), \
             patch.object(self.m.subprocess, 'Popen') as start, \
             patch.object(self.m.shutil, 'which', return_value='/bin/tool'), redirect_stdout(io.StringIO()):
            start.return_value.pid = 123
            self.assertEqual(self.m.main(args), 0)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0], [sys.executable, '/frozen/run_embedded_slurm.py', '_schedule', str(self.root)])
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        self.assertEqual(start.call_args.kwargs.get('stdin'), subprocess.DEVNULL)
        self.assertEqual(json.loads((self.root / 'controller_launch.json').read_text())['pid'], 123)

    def test_explicit_shard_worker_uses_actual_assignment_and_records_cleanup_failure(self):
        output, state = self.freeze_fixture()
        frozen = load(Path(state['snapshot']['launcher']), 'independent_worker_under_test')
        runner, launcher, snapshot = self.m.reference_modules()
        shard = output / 'shards/00'; shard.mkdir()
        assignment = dict(rank=0, node='node3', comment=f'z3nia:{output.name}:00')
        (shard / 'assignment.json').write_text(json.dumps(assignment))
        environment = dict(SLURMD_NODENAME='node3', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                           SLURM_LOCALID='0', SLURM_JOB_ID='123', SLURM_MEM_PER_NODE='53493', SLURM_PROCID='999')
        with patch.object(frozen, 'reference_modules', return_value=(runner, launcher, snapshot)), \
             patch.object(frozen.socket, 'gethostname', return_value='node3'), patch.dict(os.environ, environment), \
             patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
             patch.object(runner, 'run_batch', side_effect=OSError('cleanup failed')), redirect_stderr(io.StringIO()):
            self.assertEqual(frozen.main(['_worker_shard', str(output), '0']), 2)
        done = json.loads((shard / 'worker_done.json').read_text())
        self.assertEqual(done['returncode'], 2)
        self.assertIn('cleanup failed', done['error'])
        actual = json.loads((shard / 'assignment.json').read_text())
        self.assertEqual((actual['actual_node'], actual['job_id']), ('node3', '123'))
        identity = json.loads((output / 'shards/worker-00.json').read_text())
        self.assertEqual(identity['rank'], 0)
        self.assertEqual(launcher.shard_directory(output, 0, state), shard)

    def test_explicit_worker_rejects_wrong_host_or_allocation(self):
        state = dict(nodes=list(self.m.DEFAULT_NODES), jobs_per_node=80, memory_mb=53493)
        assignment = dict(rank=0, node='node3')
        environment = dict(SLURMD_NODENAME='node3', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                           SLURM_LOCALID='0', SLURM_JOB_ID='123', SLURM_MEM_PER_NODE='53493')
        self.m.validate_assignment(state, assignment, 0, environment, 'node3')
        for host, changes in [('node2', {}), ('node3', dict(SLURM_CPUS_PER_TASK='79')),
                              ('node3', dict(SLURM_MEM_PER_NODE='53492')), ('node3', dict(SLURM_NTASKS='2'))]:
            with self.subTest(host=host, changes=changes), self.assertRaises(ValueError):
                self.m.validate_assignment(state, assignment, 0, dict(environment, **changes), host)

    def test_invalid_budget_and_memory_fail_before_selection(self):
        for args in (['--memory-mb', '0'], ['--timeout', '1201'], ['--jobs', '0'], ['--limit', '0']):
            with self.subTest(args=args), patch.object(self.m, 'select_cases', side_effect=AssertionError('no selection')), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(self.m.main(args), 2)


if __name__ == '__main__':
    unittest.main()
