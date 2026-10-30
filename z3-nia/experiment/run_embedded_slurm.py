#!/usr/bin/env python3
"""Run only embedded z3-nia through the frozen reference Slurm harness."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--limit', type=int, help='optional small prefix for diagnostics')
    selection.add_argument('--selection-from', type=Path,
                           help='canonical baseline nia_ls.csv; select only B/C cases')
    parser.add_argument('--solver-option', action='append', default=[], metavar='OPTION',
                        help='repeat with =--core-neighborhood or its positive stall/budget seconds')
    parser.add_argument('--queue-workers', action='store_true',
                        help='queue independent workers on healthy nodes with sufficient total CPU/memory capacity')
    parser.add_argument('--cases-per-shard', type=int,
                        help='optional positive maximum cases per worker; parallel cases/node is unchanged')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='inspect selection without writes or external commands')
    mode.add_argument('--prepare-only', action='store_true', help='freeze the run without submitting it')
    return parser


def make_config(args):
    runner, _, _ = reference_modules()
    runner.validate_budget(args.jobs, args.timeout)
    return runner.Config(solver=args.solver.resolve(), converter=TREE / 'reference/NIA_LS/converter/omt_to_ls.py',
                         jobs=args.jobs, timeout=args.timeout, subset='all', selection_suite='all',
                         selection_complete=args.limit is None and args.selection_from is None,
                         use_target=False, stop_at_known_optimum=False,
                         large_target=1000000, prove_unbounded=True, unbounded_threshold=1000000,
                         prove_optimal=True, optimality_stall=10, optimality_timeout=30,
                         optimality_resume=True, optimality_burst=True, optimality_cutset=True,
                         optimality_balanced=True, initial_z3_after=300, initial_z3_timeout=120)


def runtime_adapter():
    spec = importlib.util.spec_from_file_location('_embedded_runtime_adapter',
                                                  Path(__file__).with_name('runtime_adapter.py'))
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def select_cases(limit, selection_from=None):
    runner, _, _ = reference_modules()
    cases = runner.collect_inputs('all', subset='all')
    if len(cases) != 6310 or Counter(c.suite for c in cases) != {'original': 5744, 'new': 566}:
        raise ValueError('expected the complete 6310 cases: 5744 original and 566 new')
    if selection_from is None:
        return cases if limit is None else cases[:limit]
    if limit is not None:
        raise ValueError('--selection-from and --limit are mutually exclusive')
    reporter = importlib.import_module('report_results')
    canonical = {}
    for case in cases:
        name = reporter.case_name(dict(suite=case.suite, category=case.category, filename=case.filename))
        if name in canonical:
            raise ValueError(f'duplicate canonical collection name: {name}')
        canonical[name] = case
    with Path(selection_from).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not {'文件名', '状态'}.issubset(reader.fieldnames or []):
            raise ValueError('selection CSV requires canonical 文件名 and 状态 columns')
        rows = list(reader)
    if not rows:
        raise ValueError('selection CSV is empty')
    seen, selected = set(), set()
    for row in rows:
        name = row.get('文件名')
        if not isinstance(name, str) or not name or None in row:
            raise ValueError('selection CSV has an empty or malformed case name')
        if name in seen:
            raise ValueError(f'duplicate selection case name: {name}')
        seen.add(name)
        if name not in canonical:
            raise ValueError(f'unknown canonical selection case name: {name}')
        if row.get('状态') not in ('A1', 'A2', 'B', 'C'):
            raise ValueError(f'unknown selection status for {name}: {row.get("状态")}')
        if row['状态'] in ('B', 'C'):
            selected.add(name)
    if not selected:
        raise ValueError('selection CSV contains no B/C cases')
    return [case for name, case in canonical.items() if name in selected]


def check_solver_options(output, state):
    """Bind mutable coordinator state to the hashed experiment configuration."""
    options = list(runtime_adapter().validated_solver_options(state.get('solver_options', [])))
    manifest = json.loads((Path(output) / 'manifest.json').read_text())
    relative = 'source/solver_options.json'
    recorded = relative in state['snapshot']['files']
    if not recorded:
        if options or 'solver_options' in state or 'solver_options' in manifest:
            raise ValueError('solver options have no frozen metadata')
        return options  # Runs prepared before optional flags existed.
    metadata = json.loads((Path(output) / relative).read_text())
    receipt = json.loads((Path(output) / 'source/embedded_receipt.json').read_text())
    if (metadata.get('solver_options') != options or manifest.get('solver_options') != options
            or receipt.get('solver_options') != options):
        raise ValueError('solver options differ from frozen metadata or manifest')
    return options


def check_queue_workers(output, state):
    """Bind scheduler admission mode to the copied and hashed run metadata."""
    queue_workers = state.get('queue_workers', False)
    if type(queue_workers) is not bool:
        raise ValueError('queue workers mode must be a Boolean')
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text())
    receipt_path = output / 'source/embedded_receipt.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    if not isinstance(manifest, dict) or not isinstance(receipt, dict):
        raise ValueError('queue workers run metadata must be JSON objects')
    relative = 'source/scheduler_options.json'
    if relative not in state['snapshot']['files']:
        if any('queue_workers' in record for record in (state, manifest, receipt)):
            raise ValueError('queue workers mode has no frozen metadata')
        return False  # Older frozen runs retain their original idle admission.
    metadata = json.loads((output / relative).read_text())
    if 'queue_workers' not in state or any(not isinstance(record, dict) or record.get('queue_workers') is not queue_workers
                                           for record in (metadata, manifest, receipt)):
        raise ValueError('queue workers mode differs from frozen metadata or manifest')
    return queue_workers


def probe_solver_options(solver, options, environment=None):
    answer = subprocess.run([str(solver), '--nia-ls', '--help'], check=True, capture_output=True,
                            text=True, timeout=20, env=environment)
    help_text = (answer.stdout or '') + '\n' + (answer.stderr or '')
    for option in options:
        name = option.split('=', 1)[0]
        if not re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', help_text):
            raise ValueError(f'candidate help does not support solver option: {name}')


def make_sbatch_command(output, state):
    _, launcher, _ = reference_modules()
    command = launcher.make_sbatch_command(output, state)
    command = [arg for arg in command if arg not in ('--exclusive', '--mem=0')]
    command.insert(-1, f'--mem={state["memory_mb"]}M')
    return command


def make_worker_sbatch_command(output, state, rank, node):
    """One shard claims resources on one node selected by the coordinator."""
    output = Path(output)
    shard = output / 'shards' / f'{rank:02d}'
    shard.mkdir(parents=True, exist_ok=True)
    command = [state['python'], state['snapshot']['launcher'], '_worker_shard', str(output), str(rank)]
    script = shard / 'submit.sbatch'
    script.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexport TMPDIR=/tmp\n'
                      'export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1\n'
                      'exec ' + shlex.join(command) + '\n')
    minutes = math.ceil(math.ceil(len(state['shards'][rank]) / state['jobs_per_node']) * state['timeout'] / 60) + 30
    return ['sbatch', '--parsable', f'--partition={state["partition"]}', '--nodes=1', '--ntasks=1',
            '--ntasks-per-node=1', f'--cpus-per-task={state["jobs_per_node"]}', f'--nodelist={node}',
            f'--mem={state["memory_mb"]}M', f'--time={minutes}', f'--job-name={state["job_name"]}_{rank:02d}',
            '--export=ALL', f'--chdir={output}', f'--output={shard}/slurm-%j.out',
            f'--error={shard}/slurm-%j.err', str(script)]


def prepare(cases, config, args):
    runner, launcher, snapshot = reference_modules()
    if args.cases_per_shard is not None and args.cases_per_shard < 1:
        raise ValueError('--cases-per-shard must be positive')
    options = list(runtime_adapter().validated_solver_options(args.solver_option))
    if type(args.queue_workers) is not bool:
        raise ValueError('queue workers mode must be a Boolean')
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
    copy(Path(__file__).with_name('independent_scheduler.py'), 'source/script/independent_scheduler.py')
    copy(BUILD_RECORD, 'source/embedding-build.json')
    launcher.write_json(output / 'source/solver_options.json', dict(solver_options=options))
    files['source/solver_options.json'] = hashlib.sha256((output / 'source/solver_options.json').read_bytes()).hexdigest()
    launcher.write_json(output / 'source/scheduler_options.json', dict(queue_workers=args.queue_workers))
    files['source/scheduler_options.json'] = hashlib.sha256((output / 'source/scheduler_options.json').read_bytes()).hexdigest()
    selection = None
    if args.selection_from is not None:
        selection = dict(path=str(args.selection_from.resolve()), frozen='source/selection_from.csv')
        copy(args.selection_from, selection['frozen'])
        selection['sha256'] = files[selection['frozen']]
    solver_hash = state['snapshot']['solver_sha256']
    build = json.loads((output / 'source/embedding-build.json').read_text())
    receipt = dict(solver_kind='z3-nia', solver=str(config.solver), solver_sha256=solver_hash,
                   frozen_solver=state['snapshot']['solver'], build_record='source/embedding-build.json',
                   build_record_matches_solver=build.get('executable_sha256') == solver_hash,
                   actual_converter='embedded C++ importer', actual_converter_sha256=solver_hash,
                   converter_budget='import and optimization share the same per-case wallclock deadline',
                   solver_options=options, selection_from=selection, queue_workers=args.queue_workers)
    launcher.write_json(output / 'source/embedded_receipt.json', receipt)
    files['source/embedded_receipt.json'] = hashlib.sha256((output / 'source/embedded_receipt.json').read_bytes()).hexdigest()
    launcher.write_json(output / 'embedded_receipt.json', receipt)
    shard_count = ((len(cases) + args.cases_per_shard - 1) // args.cases_per_shard
                   if args.cases_per_shard is not None else min(len(state['nodes']), len(cases)))
    state.update(memory_mb=args.memory_mb, solver_kind='z3-nia', scheduler_mode='independent',
                 solver_options=options, selection_from=selection, queue_workers=args.queue_workers,
                 shards=launcher.split_indices(len(cases), shard_count))
    manifest = json.loads((output / 'manifest.json').read_text())
    manifest.update(solver_kind='z3-nia', actual_converter='embedded C++ importer',
                    actual_converter_sha256=solver_hash, embedded_receipt='source/embedded_receipt.json',
                    scheduler_mode='independent', shard_count=shard_count, node_pool=list(state['nodes']),
                    solver_options=options, selection_from=selection, queue_workers=args.queue_workers)
    if args.cases_per_shard is not None:
        state['cases_per_shard'] = manifest['cases_per_shard'] = args.cases_per_shard
    launcher.write_json(output / 'manifest.json', manifest)
    state['manifest_sha256'] = hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()
    launcher.write_json(output / 'slurm_state.json', state)
    command = [state['python'], state['snapshot']['launcher'], '_schedule', str(output)]
    (output / 'submit.sbatch').write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                                        'export PYTHONDONTWRITEBYTECODE=1\nexport TMPDIR=/tmp\n'
                                        'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1\nexec ' + shlex.join(command) + '\n')
    snapshot.check_snapshot(output, state['snapshot'])
    check_solver_options(output, state)
    check_queue_workers(output, state)
    probe_solver_options(state['snapshot']['solver'], options,
                         dict(snapshot.snapshot_environment(state['snapshot']), TMPDIR='/tmp'))
    args.output = output
    return state


def validate_assignment(state, assignment, rank, environment, actual_host):
    try:
        valid = (assignment['rank'] == rank and assignment['node'] in state['nodes']
                 and assignment['node'] == actual_host == environment['SLURMD_NODENAME']
                 and int(environment['SLURM_CPUS_PER_TASK']) == state['jobs_per_node']
                 and int(environment['SLURM_MEM_PER_NODE']) == state['memory_mb']
                 and int(environment['SLURM_NTASKS']) == 1
                 and int(environment.get('SLURM_NNODES', '1')) == 1
                 and environment.get('SLURM_LOCALID', '0') == '0'
                 and environment['SLURM_JOB_ID'].isdigit()
                 and assignment.get('job_id', environment['SLURM_JOB_ID']) == environment['SLURM_JOB_ID'])
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('worker host/allocation does not match its shard assignment')


def independent_worker(output, rank):
    runner, launcher, snapshot = reference_modules()
    state = launcher.load_state(output)
    if not 0 <= rank < len(state['shards']):
        raise ValueError('invalid shard rank')
    shard = output / 'shards' / f'{rank:02d}'
    code, error, identity, claimed = 2, '', {}, False
    staging = shard / 'run'
    try:
        assignment = json.loads((shard / 'assignment.json').read_text())
        host = socket.gethostname().split('.')[0]
        validate_assignment(state, assignment, rank, os.environ, host)
        descriptor = os.open(output / 'shards' / f'.claim-{rank:02d}', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(descriptor)
        claimed = True
        snapshot.check_snapshot(output, state['snapshot'])
        options = check_solver_options(output, state)
        check_queue_workers(output, state)
        identity = dict(rank=rank, node=host, job_id=os.environ['SLURM_JOB_ID'],
                        global_indices=state['shards'][rank], jobs=state['jobs_per_node'],
                        solver_sha256=state['snapshot']['solver_sha256'])
        assignment.update(actual_node=host, job_id=identity['job_id'])
        launcher.write_json(shard / 'assignment.json', assignment)
        launcher.write_json(shard / 'worker_started.json', dict(identity, utc=datetime.now(timezone.utc).isoformat()))
        launcher.write_json(output / 'shards' / f'worker-{rank:02d}.json', identity)
        os.environ.update(snapshot.snapshot_environment(state['snapshot']))
        config = runner.replace(launcher.config_from_record(state['config']), selection_complete=False)
        manifest = json.loads((output / 'manifest.json').read_text())
        cases = [launcher.case_from_record(manifest['cases'][i]) for i in state['shards'][rank]]
        probe_solver_options(config.solver, options)
        code = runner.run_batch(cases, config, staging)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        print(f'shard {rank}: {error}', file=sys.stderr, flush=True)
    finally:
        # run_batch requires a new directory; assignments already occupy shard.
        # Promote retained output even after failure so the standard finalizer
        # can retain every available row and artifact.
        if claimed and staging.is_dir():
            try:
                for child in staging.iterdir():
                    child.rename(shard / child.name)
                staging.rmdir()
            except Exception as exc:
                code, error = 2, error + f'; output cleanup failed: {exc}'
        if claimed or not (output / 'shards' / f'.claim-{rank:02d}').exists():
            shard.mkdir(parents=True, exist_ok=True)
            launcher.write_json(shard / 'worker_done.json', dict(identity, rank=rank, returncode=code, error=error,
                                utc=datetime.now(timezone.utc).isoformat()))
    return code


def internal(command, output, rank=None):
    runner, launcher, snapshot = reference_modules()
    state = launcher.load_state(output)
    snapshot.check_snapshot(output, state['snapshot'])
    options = check_solver_options(output, state)
    check_queue_workers(output, state)
    frozen = Path(state['snapshot']['launcher'])
    if Path(__file__).resolve() != frozen:
        extra = [] if rank is None else [str(rank)]
        os.execv(state['python'], [state['python'], str(frozen), command, str(output), *extra])
    os.environ.update(TMPDIR='/tmp', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    tempfile.tempdir = '/tmp'
    if state.get('scheduler_mode') == 'independent':
        launcher.shard_directory = lambda directory, index, _state: Path(directory) / 'shards' / f'{index:02d}'
    if command == '_schedule':
        spec = importlib.util.spec_from_file_location('_independent_scheduler', Path(__file__).with_name('independent_scheduler.py'))
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        return scheduler.schedule(output, sys.modules[__name__])
    if command in ('_worker', '_worker_shard'):
        runtime_adapter().install(runner, 'embedded', state['config']['solver'], state['config']['converter'], options)
    if command == '_worker_shard':
        return independent_worker(output, rank)
    # Z3's regular --help is supported by shell/main.cpp, so the existing
    # worker preflight remains valid without changing the frozen reference.
    return {'_worker': launcher.worker, '_execute': launcher.execute, '_finalize': launcher.finalize_run}[command](output)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in ('_worker', '_execute', '_finalize', '_schedule', '_worker_shard'):
            expected = 3 if argv[0] == '_worker_shard' else 2
            if len(argv) != expected:
                raise ValueError('internal command requires one run directory')
            return internal(argv[0], Path(argv[1]).resolve(), int(argv[2]) if expected == 3 else None)
        args = make_parser().parse_args(argv)
        if args.memory_mb < 1:
            raise ValueError('--memory-mb must be positive')
        if args.limit is not None and args.limit < 1:
            raise ValueError('--limit must be positive')
        if args.cases_per_shard is not None and args.cases_per_shard < 1:
            raise ValueError('--cases-per-shard must be positive')
        args.solver_option = list(runtime_adapter().validated_solver_options(args.solver_option))
        runner, launcher, _ = reference_modules()
        nodes = [node.strip() for node in args.nodes.split(',')]
        if any(not node or not launcher.re_node(node) for node in nodes) or len(set(nodes)) != len(nodes):
            raise ValueError('--nodes requires unique simple node names')
        args.nodes = ','.join(nodes)
        config = make_config(args)
        cases = select_cases(args.limit, args.selection_from)
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
        command = [state['python'], state['snapshot']['launcher'], '_schedule', str(args.output)]
        if args.prepare_only:
            print(f'Prepared: {args.output}\nResume coordinator: {shlex.join(command)}')
            return 0
        log = args.output / 'controller.log'
        with log.open('ab', buffering=0) as handle:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, cwd=args.output,
                                       start_new_session=True, env=dict(os.environ, TMPDIR='/tmp', PYTHONDONTWRITEBYTECODE='1'))
        launcher.write_json(args.output / 'controller_launch.json', dict(pid=process.pid, command=command, log=str(log)))
        print(f'Controller PID: {process.pid}\nResults: {args.output}\nLog: {log}\n'
              f'Fixed shards: {len(state["shards"])}; workers start as pool resources become available.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, csv.Error, subprocess.SubprocessError) as error:
        print(f'run_embedded_slurm: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
