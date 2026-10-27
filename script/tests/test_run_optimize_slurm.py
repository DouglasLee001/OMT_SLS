"""Slurm partitioning, allocation and shared-result merge contracts."""
import contextlib
import csv
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from script.run_optimize import Config, InputCase, FIELDS


class SlurmOptimizeTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('script.run_optimize_slurm')

    def test_worker_preflight_preserves_missing_library_diagnostic(self):
        failure = subprocess.CalledProcessError(127, ['nia_ls', '--help'],
            stderr='error while loading shared libraries: libz3.so.4: cannot open shared object file')
        error = io.StringIO()
        with patch.object(self.module, 'worker', side_effect=failure), contextlib.redirect_stderr(error):
            code = self.module.main(['_worker', '/shared/example'])
        self.assertEqual(code, 2)
        self.assertIn('libz3.so.4', error.getvalue())

    def test_search_window_survives_frozen_worker_config_and_manifest(self):
        config = Config(prove_optimal=True, optimality_search_window=17)
        record = self.module.config_record(config)
        self.assertEqual(record['optimality_search_window'], 17)
        restored = self.module.config_from_record(json.loads(json.dumps(record)))
        self.assertEqual(restored.optimality_search_window, 17)
        self.assertFalse(restored.use_target)
        self.assertFalse(restored.stop_at_known_optimum)
        self.assertEqual(self.module.make_manifest([], restored)['optimality_search_window_seconds'], 17)

    def test_cli_search_window_is_forwarded_before_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = self.cli_cases(Path(directory))
            real_config = self.module.runner.Config
            with patch.object(self.module.runner, 'collect_inputs', return_value=cases), \
                 patch.object(self.module.runner, 'Config', wraps=real_config) as configured, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = self.module.main(['all', '--subset', 'all', '--dry-run',
                    '--prove-optimal', '--optimality-search-window', '17'])
            self.assertEqual(code, 0)
            self.assertEqual(configured.call_args.kwargs['optimality_search_window'], 17)

    def test_partition_is_disjoint_complete_and_allows_empty_workers(self):
        self.assertEqual(self.module.split_indices(7, 3), [[0, 3, 6], [1, 4], [2, 5]])
        self.assertEqual(self.module.split_indices(2, 3), [[0], [1], []])
        for total in (1, 92, 6310):
            shards = self.module.split_indices(total, 3)
            self.assertEqual(sorted(i for shard in shards for i in shard), list(range(total)))
            self.assertLessEqual(max(map(len, shards)) - min(map(len, shards)), 1)

    def test_slurm_requests_one_worker_and_180_cpus_per_node(self):
        state = dict(nodes=['node1', 'node2', 'server'], partition='all',
                     job_name='nia_omt', jobs_per_node=180, timeout=1200, selected=6310,
                     python='/usr/bin/python3', snapshot={'launcher': '/shared/source/script/run_optimize_slurm.py'})
        command = self.module.make_sbatch_command(Path('/shared/run'), state)
        for arg in ('--nodes=3', '--ntasks=3', '--ntasks-per-node=1', '--cpus-per-task=180',
                    '--nodelist=node1,node2,server', '--partition=all', '--parsable'):
            self.assertIn(arg, command)
        limit = next(x.split('=', 1)[1] for x in command if x.startswith('--time='))
        self.assertGreater(int(limit), 20)  # Whole experiment must outlive one case.
        step = self.module.make_srun_command(Path('/shared/run'), state)
        self.assertIn('--kill-on-bad-exit=1', step)
        self.assertIn('--ntasks-per-node=1', step)
        self.assertIn('--cpus-per-task=180', step)

    def fixture(self, directory):
        root = Path(directory)
        cases = [InputCase(root / f'{i}.smt2', f'{i}.smt2', 'original', 'original', 'min')
                 for i in range(5)]
        config = Config(jobs=2, timeout=1, use_target=False, stop_at_known_optimum=False)
        manifest = self.module.make_manifest(cases, config)
        manifest.update(scheduler='slurm', nodes=['node1', 'server'], node_count=2,
                        jobs_per_node=2, jobs=4, solver_sha256='solver', converter_sha256='converter')
        (root / 'manifest.json').write_text(json.dumps(manifest))
        state = dict(nodes=['node1', 'server'], jobs_per_node=2, config=self.module.config_record(config),
                     snapshot={'solver_sha256': 'solver', 'converter_sha256': 'converter'},
                     shards=self.module.split_indices(5, 2))
        (root / 'slurm_state.json').write_text(json.dumps(state))
        for rank, indices in enumerate(state['shards']):
            shard = self.module.shard_directory(root, rank, state)
            (shard / 'cases').mkdir(parents=True)
            local = self.module.make_manifest([cases[i] for i in indices], config)
            local.update(solver_sha256='solver', converter_sha256='converter')
            (shard / 'manifest.json').write_text(json.dumps(local))
            rows = []
            for index, global_index in enumerate(indices):
                case = cases[global_index]
                artifact = shard / 'cases' / self.module.artifact_name(index, case)
                artifact.mkdir(); (artifact / 'marker').write_text(str(global_index))
                row = dict.fromkeys(FIELDS, '')
                row.update(filename=case.filename, category='original', suite='original', direction='min',
                           status='timeout', termination='wall_timeout', is_optimal='false',
                           model_validated='false', strictly_better_than_best='false',
                           vs_z3='no_reference', vs_optimathsat='no_reference', vs_best='no_reference')
                rows.append(row)
            with (shard / 'results.csv').open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader();writer.writerows(reversed(rows))
            (shard / 'worker_done.json').write_text(json.dumps({'returncode': 0}))
        return root, cases, state

    def test_merge_preserves_global_identity_and_does_not_overwrite_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root, cases, state = self.fixture(directory)
            with patch.object(self.module, 'check_snapshot'):
                self.assertEqual(self.module.finalize_run(root, 0, report=False), 0)
                self.assertEqual(self.module.finalize_run(root, 0, report=False), 0)
            summary = json.loads((root / 'summary.json').read_text())
            self.assertEqual((summary['selected'], summary['completed'], summary['parallel']), (5, 5, 4))
            self.assertIs(summary['complete'], True)
            for index, case in enumerate(cases):
                self.assertEqual((root / 'cases' / self.module.artifact_name(index, case) / 'marker').read_text(), str(index))

    def test_missing_worker_keeps_run_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, state = self.fixture(directory)
            shard = self.module.shard_directory(root, 1, state)
            (shard / 'worker_done.json').unlink()
            with patch.object(self.module, 'check_snapshot'):
                self.assertNotEqual(self.module.finalize_run(root, 1, report=False), 0)
            summary = json.loads((root / 'summary.json').read_text())
            self.assertIs(summary['complete'], False)
            self.assertEqual(summary['completed'], 5)  # Honest rows; failure isn't a fake omission.
            self.assertFalse((root / 'summary.md').exists())

    def test_duplicate_foreign_or_missing_artifact_cannot_complete(self):
        for failure in ('duplicate', 'foreign', 'artifact', 'fingerprint'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root, cases, state = self.fixture(directory)
                shard = self.module.shard_directory(root, 0, state)
                if failure in ('duplicate', 'foreign'):
                    with (shard / 'results.csv').open() as handle:rows=list(csv.DictReader(handle))
                    extra = dict(rows[0])
                    if failure=='foreign':extra['filename']='foreign.smt2'
                    with (shard / 'results.csv').open('a', newline='') as handle:
                        csv.DictWriter(handle, fieldnames=FIELDS).writerow(extra)
                elif failure=='artifact':
                    artifact=shard/'cases'/self.module.artifact_name(0,cases[0])
                    (artifact/'marker').unlink();artifact.rmdir()
                else:
                    m=json.loads((shard/'manifest.json').read_text());m['solver_sha256']='wrong'
                    (shard/'manifest.json').write_text(json.dumps(m))
                with patch.object(self.module, 'check_snapshot'):
                    with self.assertRaises(ValueError):self.module.finalize_run(root, 0, report=False)

    def cli_cases(self, root, count=3):
        cases = []
        for index in range(count):
            source = root / f'{index}.smt2'
            source.write_text('(set-logic QF_NIA)\n(declare-const x Int)\n(minimize x)\n(check-sat)\n')
            cases.append(InputCase(source.resolve(), f'QF_NIA/{index}.smt2',
                                   'original', 'original', 'min'))
        return cases

    def test_cli_default_dry_run_has_no_build_snapshot_submission_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cli_cases(root)
            output = root / 'must-not-exist'
            stdout = io.StringIO()
            real_config = self.module.runner.Config
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(self.module.runner, 'collect_inputs', return_value=cases), \
                 patch.object(self.module.runner, 'Config', wraps=real_config) as configured, \
                 patch.object(self.module, 'prepare_run', side_effect=AssertionError('prepared real run')) as prepared, \
                 patch.object(self.module, 'create_snapshot', side_effect=AssertionError('created snapshot')) as snapshot, \
                 patch.object(self.module.subprocess, 'run', side_effect=AssertionError('external command')) as command, \
                 patch.object(self.module.runner, 'run_batch', side_effect=AssertionError('solver worker')) as batch, \
                 contextlib.redirect_stdout(stdout):
                code = self.module.main(['all', '--subset', 'all', '--dry-run', '--output', str(output)])
            self.assertEqual(code, 0)
            self.assertEqual((configured.call_args.kwargs['jobs'], configured.call_args.kwargs['timeout']),
                             (180, 1200))
            self.assertIn('540', stdout.getvalue())
            self.assertIn('3 selected', stdout.getvalue())
            self.assertFalse(output.exists())
            for guarded in (prepared, snapshot, command, batch):
                guarded.assert_not_called()

    def test_cli_input_list_unions_with_explicit_inputs_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cli_cases(root)
            listing = root / 'inputs.txt'
            listing.write_text(f'# selected cases\n{cases[1].source}\n\n{cases[1].source}\n')
            output = root / 'chosen'
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(self.module.runner, 'collect_inputs', return_value=cases), \
                 patch.object(self.module.shutil, 'which', return_value='/mock/slurm'), \
                 patch.object(self.module, 'prepare_run', return_value={}) as prepared, \
                 patch.object(self.module, 'make_sbatch_command', return_value=['sbatch', '--parsable']), \
                 patch.object(self.module, 'write_json') as written, \
                 patch.object(self.module.subprocess, 'run', side_effect=[
                     subprocess.CompletedProcess(['make'], 0),
                     subprocess.CompletedProcess(['sbatch'], 0, '12345\n', '')]) as command, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = self.module.main(['all', '--subset', 'all', '--input-list', str(listing),
                                         '--input', str(cases[2].source), '--output', str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(prepared.call_args.args[0], cases[1:])
            config = prepared.call_args.args[1]
            self.assertEqual((config.jobs, config.timeout), (180, 1200))
            self.assertFalse(config.selection_complete)
            self.assertEqual(prepared.call_args.args[2], output)
            self.assertEqual(command.call_count, 2)
            self.assertEqual(written.call_args.args[1]['job_id'], '12345')

    def test_cli_empty_or_comment_only_input_list_cannot_select_everything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cli_cases(root)
            listing = root / 'inputs.txt'
            for contents in ('', '\n   \n# no benchmarks\n'):
                with self.subTest(contents=contents):
                    listing.write_text(contents)
                    with patch.object(self.module.runner, 'collect_inputs', return_value=cases), \
                         patch.object(self.module, 'prepare_run', side_effect=AssertionError('created run')), \
                         patch.object(self.module, 'create_snapshot', side_effect=AssertionError('snapshot')), \
                         patch.object(self.module.subprocess, 'run', side_effect=AssertionError('external command')), \
                         contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        code = self.module.main(['--dry-run', '--input-list', str(listing)])
                    self.assertEqual(code, 2)

    def test_cli_foreign_input_list_is_rejected_before_external_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cli_cases(root)
            listing = root / 'inputs.txt'
            listing.write_text(str(root / 'not-selected.smt2') + '\n')
            with patch.object(self.module.runner, 'collect_inputs', return_value=cases), \
                 patch.object(self.module, 'prepare_run', side_effect=AssertionError('created run')), \
                 patch.object(self.module.subprocess, 'run', side_effect=AssertionError('external command')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = self.module.main(['--dry-run', '--input-list', str(listing)])
            self.assertEqual(code, 2)

    def test_worker_rejects_wrong_host_rank_or_localid_before_starting_work(self):
        state = dict(nodes=['server', 'node1', 'node2'])
        for rank, host, localid in (('0', 'wrong-host', '0'), ('0', 'server', '1'),
                                    ('3', 'node2', '0'), ('-1', 'server', '0')):
            with self.subTest(rank=rank, host=host, localid=localid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = {'SLURM_PROCID': rank, 'SLURMD_NODENAME': host, 'SLURM_LOCALID': localid}
                with patch.dict(os.environ, env, clear=True), \
                     patch.object(self.module, 'load_state', return_value=state), \
                     patch.object(self.module, 'check_snapshot', side_effect=AssertionError('preflight started')) as snapshot, \
                     patch.object(self.module.subprocess, 'run', side_effect=AssertionError('external command')) as command, \
                     patch.object(self.module.runner, 'run_batch', side_effect=AssertionError('solver worker')) as batch, \
                     contextlib.redirect_stderr(io.StringIO()):
                    code = self.module.main(['_worker', str(root)])
                self.assertEqual(code, 2)
                self.assertFalse((root / 'shards').exists())
                for guarded in (snapshot, command, batch):
                    guarded.assert_not_called()

    def test_empty_worker_writes_success_without_calling_run_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cli_cases(root, count=2)
            config = Config(jobs=180, timeout=1200, use_target=False, stop_at_known_optimum=False)
            state = dict(nodes=['server', 'node1', 'node2'], python=sys.executable,
                         config=self.module.config_record(config), shards=self.module.split_indices(2, 3),
                         snapshot=dict(solver_sha256='solver', converter_sha256='converter'))
            (root / 'shards').mkdir()
            (root / 'manifest.json').write_text(json.dumps(self.module.make_manifest(cases, config)))
            env = {'SLURM_PROCID': '2', 'SLURMD_NODENAME': 'node2', 'SLURM_LOCALID': '0'}
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(self.module, 'load_state', return_value=state), \
                 patch.object(self.module, 'check_snapshot'), \
                 patch.object(self.module, 'snapshot_environment', return_value={}), \
                 patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as preflight, \
                 patch.object(self.module.runner, 'run_batch', side_effect=AssertionError('empty solver batch')) as batch:
                code = self.module.worker(root)
            self.assertEqual(code, 0)
            batch.assert_not_called()
            self.assertEqual(preflight.call_count, 2)
            shard = self.module.shard_directory(root, 2, state)
            done = json.loads((shard / 'worker_done.json').read_text())
            self.assertEqual((done['returncode'], done['global_indices']), (0, []))
            self.assertEqual(json.loads((shard / 'manifest.json').read_text())['cases'], [])
            with (shard / 'results.csv').open() as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, list(FIELDS))
                self.assertEqual(list(reader), [])
            self.assertTrue((shard / 'cases').is_dir())
            self.assertFalse(any((shard / 'cases').iterdir()))
