#!/usr/bin/env python3
"""Embedded Slurm launcher checks; no solver or scheduler is executed."""
from contextlib import redirect_stderr, redirect_stdout
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

    def test_shared_sbatch_requests_exact_six_node_capacity_and_310_minutes(self):
        state = dict(nodes=list(self.m.DEFAULT_NODES), jobs_per_node=80, selected=6310,
                     timeout=1200, partition='all', job_name='z3_nia', memory_mb=53493)
        command = self.m.make_sbatch_command(self.root, state)
        for option in ('--nodes=6', '--ntasks=6', '--ntasks-per-node=1', '--cpus-per-task=80',
                       '--mem=53493M', '--time=310', '--nodelist=server,node1,node2,node3,node4,node5'):
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

    def freeze_fixture(self, build_hash=None):
        runner, launcher, snapshot = self.m.reference_modules()
        solver = self.root / 'z3'; solver.write_text('fake embedded binary\n'); solver.chmod(0o755)
        record = self.root / 'embedding-build.json'
        record.write_text(json.dumps(dict(executable_sha256=build_hash or hashlib.sha256(solver.read_bytes()).hexdigest())))
        baseline = self.root / 'competitor.csv'; baseline.write_text('filename\n')
        args = self.m.make_parser().parse_args(['--output', str(self.root / 'run'), '--solver', str(solver), '--limit', '1'])
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
             patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as preflight:
            state = self.m.prepare([self.case()], config, args)
        preflight.assert_called_once()
        self.assertEqual(preflight.call_args.args[0], [state['snapshot']['solver'], '--nia-ls', '--help'])
        self.assertTrue(preflight.call_args.kwargs['check'])
        self.assertIs(snapshot.create_snapshot, original_snapshot)
        return args.output, state

    def test_preparation_freezes_wrapper_adapter_build_record_and_embedded_receipt(self):
        output, state = self.freeze_fixture()
        _, _, snapshot = self.m.reference_modules()
        self.assertEqual(state['snapshot']['launcher'], str(output / 'source/script/run_embedded_slurm.py'))
        for relative in ('source/script/run_embedded_slurm.py', 'source/script/runtime_adapter.py', 'source/embedding-build.json'):
            self.assertIn(relative, state['snapshot']['files'])
        snapshot.check_snapshot(output, state['snapshot'])
        receipt = json.loads((output / 'embedded_receipt.json').read_text())
        self.assertEqual(receipt['solver_kind'], 'z3-nia')
        self.assertEqual(receipt['solver_sha256'], state['snapshot']['solver_sha256'])
        self.assertTrue(receipt['build_record_matches_solver'])
        self.assertIn('export TMPDIR=/tmp', (output / 'submit.sbatch').read_text())
        self.assertIn('run_embedded_slurm.py', (output / 'submit.sbatch').read_text())
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

    def test_prepare_only_does_not_submit_and_normal_command_submits_once(self):
        args = ['--output', str(self.root / 'run'), '--limit', '1']
        state = dict(nodes=list(self.m.DEFAULT_NODES), jobs_per_node=80, selected=1,
                     timeout=1200, partition='all', job_name='z3_nia', memory_mb=53493)
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', return_value=state), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no submission')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(args + ['--prepare-only']), 0)
        args[1] = str(self.root)
        answer = subprocess.CompletedProcess([], 0, '123;cluster\n', '')
        with patch.object(self.m, 'select_cases', return_value=[self.case()]), \
             patch.object(self.m, 'prepare', return_value=state), \
             patch.object(self.m.subprocess, 'run', return_value=answer) as submit, \
             patch.object(self.m.shutil, 'which', return_value='/bin/tool'), redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(args), 0)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args.args[0][0], 'sbatch')

    def test_invalid_budget_and_memory_fail_before_selection(self):
        for args in (['--memory-mb', '0'], ['--timeout', '1201'], ['--jobs', '0'], ['--limit', '0']):
            with self.subTest(args=args), patch.object(self.m, 'select_cases', side_effect=AssertionError('no selection')), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(self.m.main(args), 2)


if __name__ == '__main__':
    unittest.main()
