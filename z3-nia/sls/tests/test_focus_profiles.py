#!/usr/bin/env python3
"""Pure/mock profile diagnostic tests; no solver or scheduler is executed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'experiment/focus_profiles.py'


def subject():
    assert SCRIPT.is_file(), 'missing focus_profiles.py implementation'
    spec = importlib.util.spec_from_file_location('focus_profiles_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.m = subject()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_selects_exact_both_unsolved_intersection(self):
        parent = dict(regression_indices=list(range(52)))
        a = {i: dict(state='B' if i < 15 else 'A2') for i in range(52)}
        b = {i: dict(state='C' if i < 15 else 'B') for i in range(52)}
        self.assertEqual(self.m.select_focus(parent, a, b, lambda r: r['state']), list(range(15)))
        b.pop(0)
        with self.assertRaises(ValueError): self.m.select_focus(parent, a, b, lambda r: r['state'])

    def test_three_independent_worker_budgets_never_exceed_80(self):
        self.assertEqual(self.m.VARIANTS, ('baseline', 'embedded20', 'candidate17'))
        self.assertEqual(self.m.JOBS_PER_VARIANT, 26)
        self.assertLessEqual(3 * self.m.JOBS_PER_VARIANT, 78)

    def test_candidate_requires_new_hash_and_actual_cxx17_without_pic_flags(self):
        self.assertTrue(hasattr(self.m, 'validate_candidate'), 'missing compiler-profile fail-fast check')
        candidate = self.root / 'solver'; candidate.write_bytes(b'old embedded20'); candidate.chmod(0o755)
        old = self.m.digest(candidate)
        with self.assertRaisesRegex(ValueError, 'identical'):
            self.m.validate_candidate(candidate, old)
        candidate.write_bytes(b'new candidate17')
        with self.assertRaisesRegex(ValueError, 'flags'):
            self.m.validate_candidate(candidate, old)
        flags = candidate.parent / 'src/shell/nia_ls/CMakeFiles/nia_ls_embedded.dir/flags.make'
        flags.parent.mkdir(parents=True)
        flags.write_text('CXX_FLAGS = -O3 -g -std=gnu++20\n')
        with self.assertRaisesRegex(ValueError, r'C\+\+17'):
            self.m.validate_candidate(candidate, old)
        flags.write_text('CXX_FLAGS = -O3 -g -fPIC -std=gnu++17\n')
        with self.assertRaisesRegex(ValueError, 'PIC'):
            self.m.validate_candidate(candidate, old)
        flags.write_text('CXX_FLAGS = -O3 -g -std=gnu++17\n')
        self.assertEqual(self.m.validate_candidate(candidate, old), flags)

    def test_converter_stage_copies_identical_frozen_ir_and_never_executes(self):
        source = self.root / 'frozen.nia'; source.write_bytes(b'NIA_LS_OMT 1\nexact frozen IR\n')
        output = self.root / 'input.nia'
        inputs = {'/source/one.smt2': dict(path=str(source), sha256=self.m.digest(source))}
        stage = self.m.stage_adapter(lambda *a, **k: self.fail('converter must not execute'),
                                     'candidate17', '/candidate', '/converter.py', inputs)
        result = stage(['python', '/converter.py', '/source/one.smt2', '-o', str(output), '--keep-definitions'],
                       time.monotonic() + 10, threading.Event(), self.root / 'out', self.root / 'err')
        self.assertEqual(result, 0)
        self.assertEqual(source.read_bytes(), output.read_bytes())
        source.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            stage(['python', '/converter.py', '/source/one.smt2', '-o', str(output)],
                  time.monotonic() + 10, threading.Event(), self.root / 'out', self.root / 'err')

    def test_native_stage_preserves_runner_callback_and_flags(self):
        captured = []
        def original(command, *args, **kwargs): captured.append((command, args, kwargs)); return 7
        callback = object()
        command = ['/temporary/copied-exe', '/input.nia', '--optimize', '--timeout=1199', '--optimality-balanced']
        for variant in self.m.VARIANTS:
            stage = self.m.stage_adapter(original, variant, '/frozen-exe', '/converter', {})
            self.assertEqual(stage(command, 123, object(), '/out', '/err', callback, stop_when=callback), 7)
            actual, args, kwargs = captured[-1]
            expected = command if variant == 'baseline' else ['/frozen-exe', '--nia-ls', '--native-input', *command[1:]]
            self.assertEqual(actual, expected)
            self.assertIs(args[-1], callback)
            self.assertIs(kwargs['stop_when'], callback)
            self.assertFalse(any(x.startswith('--target=') or x.startswith('--mode=') for x in actual))

    def test_copy_stage_respects_stop_and_total_deadline(self):
        stage = self.m.stage_adapter(lambda *a, **k: None, 'baseline', '/exe', '/converter', {})
        stop = threading.Event(); stop.set()
        args = (['python', '/converter', '/missing', '-o', '/output'],)
        with self.assertRaises(InterruptedError): stage(*args, time.monotonic() + 1, stop, '/out', '/err')
        with self.assertRaises(TimeoutError): stage(*args, time.monotonic() - 1, threading.Event(), '/out', '/err')

    def test_execute_requires_one_80_cpu_slurm_job_and_same_host(self):
        env = dict(SLURM_JOB_ID='123', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                   SLURMD_NODENAME='node3', SLURM_MEM_PER_NODE='53493')
        self.m.validate_allocation(env, 'node3')
        for changes in [dict(SLURM_CPUS_PER_TASK='240'), dict(SLURM_CPUS_PER_TASK='78'),
                        dict(SLURM_NTASKS='3'), dict(SLURM_MEM_PER_NODE='0'), dict(SLURMD_NODENAME='node4')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.m.validate_allocation(dict(env, **changes), 'node3')

    def test_sbatch_file_does_not_submit_or_claim_exclusive_resources(self):
        text = self.m.sbatch_text('/python', '/frozen/script.py', '/run')
        self.assertIn('#SBATCH --cpus-per-task=80', text)
        self.assertIn('_execute', text)
        self.assertNotIn('--exclusive', text)
        self.assertNotIn('--mem=0', text)
        self.assertNotIn('sbatch ', text)

    def test_frozen_file_change_is_rejected(self):
        path = self.root / 'exe'; path.write_bytes(b'exe')
        self.m.verify_hashes({str(path): self.m.digest(path)})
        expected = self.m.digest(path); path.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.m.verify_hashes({str(path): expected})

    def test_execute_starts_three_separate_python_workers_without_jobs(self):
        plan = dict(python=sys.executable, launcher='/frozen/focus_profiles.py')
        commands = []
        class Process:
            returncode = 0
            pid = 999999
            def __init__(self, command, **kwargs): commands.append(command)
            def wait(self, timeout=None): return 0
            def poll(self): return 0
        env = dict(SLURM_JOB_ID='123', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                   SLURMD_NODENAME='node3', SLURM_MEM_PER_NODE='53493')
        with patch.object(self.m, 'load_plan', return_value=plan), patch.object(self.m, 'verify_plan'), \
             patch.object(self.m, 'report', return_value={}), \
             patch.object(self.m.socket, 'gethostname', return_value='node3'), \
             patch.dict(self.m.os.environ, env), patch.object(self.m.subprocess, 'Popen', Process), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no scheduler/solver')):
            self.assertEqual(self.m.execute(self.root), 0)
        self.assertEqual(len(commands), 3)
        self.assertEqual({c[-1] for c in commands}, set(self.m.VARIANTS))
        self.assertTrue(all(c[:2] == [sys.executable, '/frozen/focus_profiles.py'] and c[2] == '_worker' for c in commands))

    def test_prepare_freezes_15_shared_inputs_without_mutating_parent_or_running_programs(self):
        helper_path = SCRIPT.with_name('embedding_experiment.py')
        spec = importlib.util.spec_from_file_location('profile_test_helpers', helper_path)
        real = importlib.util.module_from_spec(spec); spec.loader.exec_module(real)
        parent_dir = self.root / 'parent'; parent_dir.mkdir()
        files = {}; cases = []; hashes = {}; tasks = []
        launcher = parent_dir / 'source/harness/embedding_experiment.py'
        launcher.parent.mkdir(parents=True); launcher.write_text('# fake frozen helper\n')
        files[str(launcher.relative_to(parent_dir))] = self.m.digest(launcher)
        for i in range(52):
            source = self.root / 'benchmarks' / f'{i}.smt2'; source.parent.mkdir(exist_ok=True)
            source.write_text('(assert true)\n'); hashes[str(source)] = self.m.digest(source)
            cases.append(dict(source=str(source), suite='new', category='cyclic' if i in (5, 6) else 'toy',
                              filename=f'{i}.smt2', direction='min', z3=None, optimathsat=None,
                              known_optimum_value=None, known_optimum_source=''))
        variants = {}
        for variant in ('baseline', 'embedded'):
            exe = parent_dir / 'source' / variant / 'solver'; exe.parent.mkdir(parents=True)
            exe.write_bytes(variant.encode()); files[str(exe.relative_to(parent_dir))] = self.m.digest(exe)
            variants[variant] = dict(executable=str(exe), executable_sha256=self.m.digest(exe),
                                     library_dir=str(exe.parent / 'lib'))
            task = dict(id='regression-' + variant + '-00', phase='regression', variant=variant,
                        indices=list(range(52))); tasks.append(task)
            for i in range(15):
                name = f'{i:04d}-' + __import__('hashlib').sha256(cases[i]['source'].encode()).hexdigest()[:12]
                native = parent_dir / 'tasks' / task['id'] / 'run/cases' / name / 'input.nia'
                native.parent.mkdir(parents=True); native.write_bytes(f'IR for {i}\n'.encode())
        parent = dict(files=files, launcher=str(launcher), cases=cases, regression_indices=list(range(52)),
                      tasks=tasks, variants=variants, reference_script='/frozen/reference/script',
                      config=real.FULL_CONFIG, converter='/frozen/converter.py', converter_sha256='conv',
                      seed_defaults=dict(explicit_seed=None, mt=0, lia_seed=0), benchmark_hashes=hashes,
                      python=str(Path(sys.executable).resolve()), python_sha256=self.m.digest(sys.executable))
        (parent_dir / 'manifest.json').write_text(json.dumps(parent))
        (parent_dir / 'manifest.sha256').write_text(self.m.digest(parent_dir / 'manifest.json'))
        before = {str(p): self.m.digest(p) for p in parent_dir.rglob('*') if p.is_file()}
        rows = {i: dict(state='B' if i < 15 else 'A2') for i in range(52)}
        fake = SimpleNamespace(verify_files=real.verify_files, freeze_file=real.freeze_file,
                               durable_json=real.durable_json, load_plan=lambda _: parent,
                               aggregate=lambda *args: rows, result_state=lambda r: r['state'],
                               _reference_modules=lambda _: (None, None))
        candidate = self.root / 'candidate'; candidate.write_bytes(b'candidate17'); candidate.chmod(0o755)
        flags = candidate.parent / 'src/shell/nia_ls/CMakeFiles/nia_ls_embedded.dir/flags.make'
        flags.parent.mkdir(parents=True); flags.write_text('CXX_FLAGS = -O3 -g -std=gnu++17\n')
        lib = self.root / 'runtime.so'; lib.write_bytes(b'runtime')
        snapshot = SimpleNamespace(native_libraries=lambda _: {'libstdc++.so.6': lib})
        with patch.object(self.m, 'module', return_value=fake), \
             patch.dict(sys.modules, {'slurm_snapshot': snapshot}), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no external program')), \
             patch.object(self.m.subprocess, 'Popen', side_effect=AssertionError('no worker/solver')):
            output = self.m.prepare(parent_dir, candidate)
        plan = self.m.load_plan(output)
        self.assertFalse(output.is_relative_to(parent_dir))
        self.assertEqual(len(plan['inputs']), 15)
        self.assertEqual(plan['maximum_case_concurrency'], 45)
        self.assertEqual(plan['config']['jobs'], 26)
        self.assertFalse(plan['config']['use_target'])
        self.assertFalse(plan['config']['stop_at_known_optimum'])
        self.assertIn('native-IR-only', plan['evidence_role'])
        self.assertIn('source/candidate17/solver', plan['files'])
        self.assertIn('source/candidate17/lib/libstdc++.so.6', plan['files'])
        self.assertIn('source/candidate17/flags.make', plan['files'])
        self.assertEqual(before, {str(p): self.m.digest(p) for p in parent_dir.rglob('*') if p.is_file()})
        self.assertFalse((output / 'execute_started.json').exists())

    def test_coordinator_sigterm_cleans_up_started_workers_and_restores_handler(self):
        plan = dict(python=sys.executable, launcher='/frozen/focus_profiles.py')
        env = dict(SLURM_JOB_ID='123', SLURM_CPUS_PER_TASK='80', SLURM_NTASKS='1',
                   SLURMD_NODENAME='node3', SLURM_MEM_PER_NODE='53493')
        previous = signal.getsignal(signal.SIGTERM)
        killed = []; count = 0
        class Process:
            def __init__(self, *args, **kwargs):
                nonlocal count
                count += 1
                self.pid = 999998 + count  # The child already exists before SIGTERM delivery.
                if count == 2:
                    handler = signal.getsignal(signal.SIGTERM)
                    if not callable(handler): raise AssertionError('missing coordinator SIGTERM cleanup handler')
                    handler(signal.SIGTERM, None)
            def poll(self): return 0 if any(pid == self.pid for pid, _ in killed) else None
            def wait(self, timeout=None): return 0
        with patch.object(self.m, 'load_plan', return_value=plan), patch.object(self.m, 'verify_plan'), \
             patch.object(self.m.socket, 'gethostname', return_value='node3'), patch.dict(self.m.os.environ, env), \
             patch.object(self.m.subprocess, 'Popen', Process), \
             patch.object(self.m.os, 'killpg', side_effect=lambda pid, sig: killed.append((pid, sig))):
            with self.assertRaises(InterruptedError): self.m.execute(self.root)
        self.assertEqual(killed, [(999999, signal.SIGTERM), (1000000, signal.SIGTERM)])
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_report_compares_three_variants_and_keeps_two_cyclic_traces(self):
        spec = importlib.util.spec_from_file_location('profile_report_helpers', SCRIPT.with_name('embedding_experiment.py'))
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        cases = [dict(source=f'/source/{i}.smt2', filename=f'{i}.smt2', category='cyclic' if i < 2 else 'toy')
                 for i in range(3)]
        plan = dict(cases=cases, global_indices=[10, 20, 30], evidence_role='native-IR-only diagnostic')
        def row(value):
            return dict(status='sat', direction='min', model_validated='true', best_value=str(value),
                        is_optimal='false', is_unbounded='false')
        rows = {'baseline': {10: row(10), 20: row(20), 30: row(30)},
                'embedded20': {10: row(11), 20: row(20), 30: row(30)},
                'candidate17': {10: row(9), 20: row(20), 30: row(30)}}
        for variant in self.m.VARIANTS:
            for local, case in enumerate(cases[:2]):
                name = f'{local:04d}-' + __import__('hashlib').sha256(case['source'].encode()).hexdigest()[:12]
                path = self.root / variant / 'cases' / name; path.mkdir(parents=True)
                event = dict(event='incumbent', origin='sls', objective_value='10', elapsed_seconds=11)
                (path / 'native.stdout').write_text('7\n' + json.dumps(event) + '\n')
                (path / 'native.stderr').write_text('progressive_stage checks=6\nprogressive_handoff after=2\n')
        with patch.object(self.m, 'load_plan', return_value=plan), patch.object(self.m, 'verify_plan'), \
             patch.object(self.m, 'helpers', return_value=helper), \
             patch.object(self.m, 'variant_rows', side_effect=lambda o, p, v, h: rows[v]):
            result = self.m.report(self.root)
        self.assertEqual(len(result['cyclic_details']), 2)
        self.assertEqual(result['versus_baseline']['candidate17']['wins'], 1)
        self.assertEqual(result['versus_baseline']['embedded20']['losses'], 1)
        self.assertIn('| baseline | 0 | 3 | 0 | 0 |', (self.root / 'summary.md').read_text())


if __name__ == '__main__': unittest.main()
