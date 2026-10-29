#!/usr/bin/env python3
"""Run only embedded z3-nia through the frozen reference Slurm harness."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

TREE = Path(__file__).resolve().parents[1]
WORKSPACE = TREE.parent
BUILD_RECORD = TREE / 'embedding-build.json'
COMPETITOR = WORKSPACE / 'result/competitor.csv'
DEFAULT_NODES = ('server', 'node1', 'node2', 'node3', 'node4', 'node5')


def reference_modules():
    """Frozen workers import their sibling copies, never the live script tree."""
    directory = Path(__file__).resolve().parent
    if not (directory / 'run_optimize_slurm.py').is_file():
        directory = TREE / 'reference/script'
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    launcher = importlib.import_module('run_optimize_slurm')
    snapshot = importlib.import_module('slurm_snapshot')
    for module in (launcher, launcher.runner, snapshot):
        if Path(module.__file__).resolve().parent != directory:
            raise ValueError('reference module was imported from another runtime')
    return launcher.runner, launcher, snapshot


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodes', default=','.join(DEFAULT_NODES))
    parser.add_argument('--partition', default='all')
    parser.add_argument('--job-name', default='z3_nia')
    parser.add_argument('--jobs', type=int, default=80, help='parallel cases on each node')
    parser.add_argument('--timeout', type=float, default=1200, help='total seconds per case, including import')
    parser.add_argument('--memory-mb', type=int, default=53493, help='positive memory allocation per node')
    parser.add_argument('--solver', type=Path, default=TREE / 'build/z3')
    parser.add_argument('--output', type=Path, help='new run directory; default: result/NIA_<timestamp>')
    parser.add_argument('--limit', type=int, help='optional small prefix for diagnostics')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='inspect selection without writes or external commands')
    mode.add_argument('--prepare-only', action='store_true', help='freeze the run without submitting it')
    return parser


def make_config(args):
    runner, _, _ = reference_modules()
    runner.validate_budget(args.jobs, args.timeout)
    return runner.Config(solver=args.solver.resolve(), converter=TREE / 'reference/NIA_LS/converter/omt_to_ls.py',
                         jobs=args.jobs, timeout=args.timeout, subset='all', selection_suite='all',
                         selection_complete=args.limit is None, use_target=False, stop_at_known_optimum=False,
                         large_target=1000000, prove_unbounded=True, unbounded_threshold=1000000,
                         prove_optimal=True, optimality_stall=10, optimality_timeout=30,
                         optimality_resume=True, optimality_burst=True, optimality_cutset=True,
                         optimality_balanced=True, initial_z3_after=300, initial_z3_timeout=120)


def select_cases(limit):
    runner, _, _ = reference_modules()
    cases = runner.collect_inputs('all', subset='all')
    if len(cases) != 6310 or Counter(c.suite for c in cases) != {'original': 5744, 'new': 566}:
        raise ValueError('expected the complete 6310 cases: 5744 original and 566 new')
    return cases if limit is None else cases[:limit]


def make_sbatch_command(output, state):
    _, launcher, _ = reference_modules()
    command = launcher.make_sbatch_command(output, state)
    command = [arg for arg in command if arg not in ('--exclusive', '--mem=0')]
    command.insert(-1, f'--mem={state["memory_mb"]}M')
    return command


def prepare(cases, config, args):
    runner, launcher, snapshot = reference_modules()
    if not config.solver.is_file() or not os.access(config.solver, os.X_OK):
        raise ValueError(f'embedded executable is missing: {config.solver}')
    output = (args.output or runner.default_output_directory()).resolve()
    # The reference root supplies clean scripts/tables; competitor.csv is live
    # workspace data copied into this snapshot, not a file in the reference tree.
    original_create = launcher.create_snapshot
    def create_snapshot(run, root, solver, converter, baseline):
        return snapshot.create_snapshot(run, root, solver, converter, COMPETITOR)
    launcher.create_snapshot = create_snapshot
    try:
        state = launcher.prepare_run(cases, config, output, args.nodes.split(','), args.partition, args.job_name)
    finally:
        launcher.create_snapshot = original_create
    files = state['snapshot']['files']
    def copy(original, relative):
        destination = output / relative
        shutil.copy2(original, destination)
        files[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
        return str(destination)
    state['snapshot']['launcher'] = copy(Path(__file__).resolve(), 'source/script/run_embedded_slurm.py')
    copy(Path(__file__).with_name('runtime_adapter.py'), 'source/script/runtime_adapter.py')
    copy(BUILD_RECORD, 'source/embedding-build.json')
    solver_hash = state['snapshot']['solver_sha256']
    build = json.loads((output / 'source/embedding-build.json').read_text())
    receipt = dict(solver_kind='z3-nia', solver=str(config.solver), solver_sha256=solver_hash,
                   frozen_solver=state['snapshot']['solver'], build_record='source/embedding-build.json',
                   build_record_matches_solver=build.get('executable_sha256') == solver_hash,
                   actual_converter='embedded C++ importer', actual_converter_sha256=solver_hash,
                   converter_budget='import and optimization share the same per-case wallclock deadline')
    launcher.write_json(output / 'source/embedded_receipt.json', receipt)
    files['source/embedded_receipt.json'] = hashlib.sha256((output / 'source/embedded_receipt.json').read_bytes()).hexdigest()
    launcher.write_json(output / 'embedded_receipt.json', receipt)
    state.update(memory_mb=args.memory_mb, solver_kind='z3-nia')
    manifest = json.loads((output / 'manifest.json').read_text())
    manifest.update(solver_kind='z3-nia', actual_converter='embedded C++ importer',
                    actual_converter_sha256=solver_hash, embedded_receipt='source/embedded_receipt.json')
    launcher.write_json(output / 'manifest.json', manifest)
    state['manifest_sha256'] = hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()
    launcher.write_json(output / 'slurm_state.json', state)
    command = [state['python'], state['snapshot']['launcher'], '_execute', str(output)]
    (output / 'submit.sbatch').write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                                        'export PYTHONDONTWRITEBYTECODE=1\nexport TMPDIR=/tmp\n'
                                        'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1\nexec ' + shlex.join(command) + '\n')
    snapshot.check_snapshot(output, state['snapshot'])
    subprocess.run([state['snapshot']['solver'], '--nia-ls', '--help'], check=True,
                   capture_output=True, timeout=20,
                   env=dict(snapshot.snapshot_environment(state['snapshot']), TMPDIR='/tmp'))
    args.output = output
    return state


def internal(command, output):
    runner, launcher, snapshot = reference_modules()
    state = launcher.load_state(output)
    snapshot.check_snapshot(output, state['snapshot'])
    frozen = Path(state['snapshot']['launcher'])
    if Path(__file__).resolve() != frozen:
        os.execv(state['python'], [state['python'], str(frozen), command, str(output)])
    os.environ.update(TMPDIR='/tmp', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    tempfile.tempdir = '/tmp'
    if command == '_worker':
        adapter_path = Path(__file__).with_name('runtime_adapter.py')
        spec = importlib.util.spec_from_file_location('_embedded_runtime_adapter', adapter_path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        adapter.install(runner, 'embedded', state['config']['solver'], state['config']['converter'])
    # Z3's regular --help is supported by shell/main.cpp, so the existing
    # worker preflight remains valid without changing the frozen reference.
    return {'_worker': launcher.worker, '_execute': launcher.execute, '_finalize': launcher.finalize_run}[command](output)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in ('_worker', '_execute', '_finalize'):
            if len(argv) != 2:
                raise ValueError('internal command requires one run directory')
            return internal(argv[0], Path(argv[1]).resolve())
        args = make_parser().parse_args(argv)
        if args.memory_mb < 1:
            raise ValueError('--memory-mb must be positive')
        if args.limit is not None and args.limit < 1:
            raise ValueError('--limit must be positive')
        runner, launcher, _ = reference_modules()
        nodes = [node.strip() for node in args.nodes.split(',')]
        if any(not node or not launcher.re_node(node) for node in nodes) or len(set(nodes)) != len(nodes):
            raise ValueError('--nodes requires unique simple node names')
        args.nodes = ','.join(nodes)
        config = make_config(args)
        cases = select_cases(args.limit)
        print(f'z3-nia Slurm: {args.nodes}; {config.jobs} cases/node; total {len(nodes) * config.jobs}; '
              f'{len(cases)} selected; {config.timeout:g}s/case', flush=True)
        if args.dry_run:
            print('Dry run: no snapshot, submission, build, or solver execution.')
            return 0
        if not args.prepare_only:
            for executable in ('sbatch', 'srun', 'scontrol'):
                if not shutil.which(executable):
                    raise ValueError(f'missing Slurm command: {executable}')
        state = prepare(cases, config, args)
        command = make_sbatch_command(args.output, state)
        if args.prepare_only:
            print(f'Prepared: {args.output}\nSubmit: {shlex.join(command)}')
            return 0
        answer = subprocess.run(command, capture_output=True, text=True, check=False)
        record = dict(command=command, returncode=answer.returncode, stdout=answer.stdout, stderr=answer.stderr)
        job = answer.stdout.strip().split(';')[0]
        if answer.returncode == 0 and job.isdigit():
            record['job_id'] = job
        launcher.write_json(args.output / 'submission.json', record)
        if 'job_id' not in record:
            raise ValueError(f'sbatch failed or returned an uncertain receipt; inspect {args.output}/submission.json')
        print(f'Submitted Slurm job {job}\nResults: {args.output}\nStatus: squeue -j {job}\nCancel: scancel {job}')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'run_embedded_slurm: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
