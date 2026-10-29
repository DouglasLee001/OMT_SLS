#!/usr/bin/env python3
"""Submit one NIA_LS experiment to Slurm, with one local worker per node."""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys

if __package__:
    from . import run_optimize as runner
    from .slurm_snapshot import create_snapshot, check_snapshot, snapshot_environment
else:
    import run_optimize as runner
    from slurm_snapshot import create_snapshot, check_snapshot, snapshot_environment

DEFAULT_NODES = ('server', 'node1', 'node2')


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def split_indices(count, nodes):
    if count < 0 or nodes < 1:
        raise ValueError('Invalid case or node count')
    return [list(range(rank, count, nodes)) for rank in range(nodes)]


def artifact_name(index, case):
    return f'{index:04d}-{hashlib.sha256(str(case.source).encode()).hexdigest()[:12]}'


def case_record(case):
    record = asdict(case)
    record['source'] = str(case.source)
    for key in ('z3', 'optimathsat', 'known_optimum'):
        record[key] = None if record[key] is None else str(record[key])
    record['known_optimum_value'] = record.pop('known_optimum')
    return record


def case_from_record(record):
    return runner.InputCase(Path(record['source']), record['filename'], record['category'],
                            record['suite'], record['direction'],
                            *(None if record.get(key) is None else int(record[key])
                              for key in ('z3', 'optimathsat', 'known_optimum_value')),
                            record.get('known_optimum_source', ''))


def config_record(config):
    return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(config).items()}


def config_from_record(record):
    values = dict(record)
    for key in ('solver', 'converter'):
        values[key] = Path(values[key])
    config = runner.Config(**values)
    runner.validate_budget(config.jobs, config.timeout)
    return config


def make_manifest(cases, config):
    return dict(solver=str(config.solver.resolve()), converter=str(config.converter.resolve()),
                subset=config.subset, selection_suite=config.selection_suite,
                selection_complete=config.selection_complete, jobs=config.jobs, timeout=config.timeout,
                scalar_target_enabled=config.use_target and not config.prove_unbounded,
                known_optimum_stop_enabled=config.stop_at_known_optimum,
                prove_optimal=config.prove_optimal, optimality_stall_seconds=config.optimality_stall,
                optimality_timeout_seconds=config.optimality_timeout,
                optimality_search_window_seconds=config.optimality_search_window, optimality_resume=config.optimality_resume,
                optimality_burst=config.optimality_burst,
                optimality_cutset=config.optimality_cutset,
                optimality_balanced=config.optimality_balanced, search_exchange=config.search_exchange,
                initial_z3_after_seconds=config.initial_z3_after, initial_z3_timeout_seconds=config.initial_z3_timeout,
                large_target_magnitude=None if config.large_target is None else str(config.large_target),
                prove_unbounded=config.prove_unbounded,
                unbounded_threshold=str(config.unbounded_threshold) if config.prove_unbounded else None,
                started_utc=datetime.now(timezone.utc).isoformat(), cases=[case_record(c) for c in cases])


def shard_directory(output, rank, state):
    return output / 'shards' / f'{rank:02d}-{state["nodes"][rank]}'


def make_sbatch_command(output, state):
    nodes, jobs = state['nodes'], state['jobs_per_node']
    maximum_shard = math.ceil(state['selected'] / len(nodes))
    minutes = math.ceil(math.ceil(maximum_shard / jobs) * state['timeout'] / 60) + 30
    return ['sbatch', '--parsable', f'--partition={state["partition"]}',
            f'--nodes={len(nodes)}', f'--ntasks={len(nodes)}', '--ntasks-per-node=1',
            f'--cpus-per-task={jobs}', '--nodelist=' + ','.join(nodes), '--exclusive', '--mem=0',
            f'--time={minutes}', f'--job-name={state["job_name"]}', '--export=ALL',
            f'--chdir={output}', f'--output={output}/slurm-%j.out', f'--error={output}/slurm-%j.err',
            str(output / 'submit.sbatch')]


def make_srun_command(output, state):
    nodes = state['nodes']
    return ['srun', f'--nodes={len(nodes)}', f'--ntasks={len(nodes)}', '--ntasks-per-node=1',
            f'--cpus-per-task={state["jobs_per_node"]}', '--nodelist=' + ','.join(nodes),
            '--exact', '--cpu-bind=none', '--kill-on-bad-exit=1', '--mpi=none', '--label',
            f'--output={output}/node-%N.out', f'--error={output}/node-%N.err',
            state['python'], state['snapshot']['launcher'], '_worker', str(output)]


def load_state(output):
    state = json.loads((output / 'slurm_state.json').read_text())
    if state.get('manifest_sha256'):
        actual = hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()
        if actual != state['manifest_sha256']:
            raise ValueError('The global manifest changed after preparation')
    return state


def prepare_run(cases, config, output, nodes, partition='all', job_name='nia_omt'):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'shards').mkdir()
    snapshot = create_snapshot(output, runner.ROOT, config.solver, config.converter,
                               runner.ROOT / 'result/competitor.csv')
    actual = runner.replace(config, solver=Path(snapshot['solver']), converter=Path(snapshot['converter']))
    manifest = make_manifest(cases, actual)
    manifest.update(scheduler='slurm', nodes=list(nodes), node_count=len(nodes),
                    jobs_per_node=config.jobs, jobs=len(nodes) * config.jobs,
                    solver_sha256=snapshot['solver_sha256'], converter_sha256=snapshot['converter_sha256'])
    write_json(output / 'manifest.json', manifest)
    state = dict(nodes=list(nodes), partition=partition, job_name=job_name, jobs_per_node=config.jobs,
                 timeout=config.timeout, selected=len(cases), python=sys.executable,
                 config=config_record(actual), snapshot=snapshot, shards=split_indices(len(cases), len(nodes)),
                 manifest_sha256=hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest())
    write_json(output / 'slurm_state.json', state)
    command = [sys.executable, snapshot['launcher'], '_execute', str(output)]
    (output / 'submit.sbatch').write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                                        'export PYTHONDONTWRITEBYTECODE=1\nexec ' + shlex.join(command) + '\n')
    return state


def worker(output):
    state = load_state(output)
    rank = int(os.environ['SLURM_PROCID'])
    host = os.environ.get('SLURMD_NODENAME', socket.gethostname().split('.')[0])
    if not 0 <= rank < len(state['nodes']) or host != state['nodes'][rank]:
        raise ValueError(f'Unexpected Slurm placement: rank={rank}, host={host}, nodes={state["nodes"]}')
    if os.environ.get('SLURM_LOCALID', '0') != '0':
        raise ValueError('Expected exactly one worker on this node')
    claim = output / 'shards' / f'.claim-{rank}'
    descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.close(descriptor)
    check_snapshot(output, state['snapshot'])
    os.environ.update(snapshot_environment(state['snapshot']))
    config = runner.replace(config_from_record(state['config']), selection_complete=False)
    manifest = json.loads((output / 'manifest.json').read_text())
    cases = [case_from_record(manifest['cases'][i]) for i in state['shards'][rank]]
    shard = shard_directory(output, rank, state)
    identity = dict(rank=rank, node=host, job_id=os.environ.get('SLURM_JOB_ID'),
                    global_indices=state['shards'][rank], jobs=config.jobs,
                    solver_sha256=state['snapshot']['solver_sha256'])
    write_json(output / 'shards' / f'worker-{rank}.json', identity)
    # Native libraries differ across hosts: fail before consuming a benchmark.
    subprocess.run([str(config.solver), '--help'], check=True, capture_output=True, timeout=20)
    probe = 'import runpy,sys; z=runpy.run_path(sys.argv[1])["load_z3"](); print(z.get_version_string())'
    subprocess.run([state['python'], '-c', probe, str(config.converter)],
                   check=True, capture_output=True, timeout=20)
    if cases:
        code = runner.run_batch(cases, config, shard)
    else:
        shard.mkdir(); (shard / 'cases').mkdir()
        empty = make_manifest([], config)
        empty.update(solver_sha256=state['snapshot']['solver_sha256'],
                     converter_sha256=state['snapshot']['converter_sha256'])
        write_json(shard / 'manifest.json', empty)
        with (shard / 'results.csv').open('w', newline='') as handle:
            csv.DictWriter(handle, fieldnames=runner.FIELDS).writeheader()
        code = 0
    write_json(shard / 'worker_done.json', dict(identity, returncode=code))
    return code


def finalize_run(output, step_code=0, report=True):
    state = load_state(output)
    check_snapshot(output, state['snapshot'])
    manifest = json.loads((output / 'manifest.json').read_text())
    config = config_from_record(state['config'])
    cases = [case_from_record(c) for c in manifest['cases']]
    rows, moves, errors = {}, [], []
    identity = lambda r: (r['suite'], r['category'], r['filename'])
    for rank, indices in enumerate(state['shards']):
        shard = shard_directory(output, rank, state)
        done_path = shard / 'worker_done.json'
        if not done_path.is_file() or json.loads(done_path.read_text()).get('returncode') != 0:
            errors.append(f'Worker {rank} did not finish successfully')
        if not (shard / 'results.csv').is_file():
            continue
        local = json.loads((shard / 'manifest.json').read_text())
        expected = [case_record(cases[i]) for i in indices]
        if local['cases'] != expected:
            raise ValueError(f'Worker {rank}: case manifest differs from its assigned shard')
        for key in ('solver_sha256', 'converter_sha256'):
            if local.get(key) != manifest.get(key):
                raise ValueError(f'Worker {rank}: mismatched {key}')
        expected_config = make_manifest([], runner.replace(config, selection_complete=False))
        for key, value in expected_config.items():
            if key not in ('cases', 'started_utc', 'solver', 'converter') and (
                    local.get(key) != value
                    or (key in ('optimality_burst', 'optimality_cutset', 'optimality_balanced')
                        and type(local.get(key)) is not bool)):
                raise ValueError(f'Worker {rank}: mismatched configuration {key}')
        lookup = {identity(manifest['cases'][i]): (local_index, i) for local_index, i in enumerate(indices)}
        with (shard / 'results.csv').open(newline='') as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != runner.FIELDS:
                raise ValueError(f'Worker {rank}: incompatible CSV columns')
            for row in reader:
                key = identity(row)
                if key not in lookup:
                    raise ValueError(f'Worker {rank}: foreign result {key}')
                local_index, global_index = lookup[key]
                if global_index in rows:
                    raise ValueError(f'Duplicate result for global case {global_index}')
                if row['direction'] != cases[global_index].direction:
                    raise ValueError(f'Worker {rank}: inconsistent direction')
                source = shard / 'cases' / artifact_name(local_index, cases[global_index])
                target = output / 'cases' / artifact_name(global_index, cases[global_index])
                if source.is_dir() and target.exists():
                    raise ValueError(f'Duplicate artifact directory for global case {global_index}')
                if not source.is_dir() and not target.is_dir():
                    raise ValueError(f'Missing retained artifacts for global case {global_index}')
                rows[global_index] = row
                if source.is_dir(): moves.append((source, target))
    (output / 'cases').mkdir(exist_ok=True)
    for source, target in moves:
        source.rename(target)
    ordered = [rows[i] for i in sorted(rows)]
    with (output / 'results.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=runner.FIELDS)
        writer.writeheader(); writer.writerows(ordered)
    summary = runner._summary(cases, ordered, config)
    complete = (step_code == 0 and not errors and len(rows) == len(cases)
                and all(r['status'] != 'interrupted' for r in ordered))
    summary.update(parallel=manifest['jobs'], scheduler='slurm', nodes=state['nodes'],
                   jobs_per_node=config.jobs, node_count=len(state['nodes']),
                   complete=complete, slurm_step_returncode=step_code, worker_errors=errors)
    write_json(output / 'summary.json', summary)
    if not complete:
        print(f'Incomplete Slurm run: {len(rows)}/{len(cases)} results retained in {output}', file=sys.stderr)
        return 2
    if report:
        command = [state['python'], str(Path(state['snapshot']['launcher']).with_name('report_results.py')),
                   str(output), '--competitor', state['snapshot']['baseline'], '--output', str(output)]
        return subprocess.run(command, check=False).returncode
    return 0


def execute(output):
    state = load_state(output)
    check_snapshot(output, state['snapshot'])
    hosts = subprocess.run(['scontrol', 'show', 'hostnames', os.environ['SLURM_JOB_NODELIST']],
                           text=True, capture_output=True, check=True).stdout.split()
    if len(hosts) != len(state['nodes']) or set(hosts) != set(state['nodes']):
        raise ValueError(f'Slurm allocated unexpected nodes: {hosts}')
    state['nodes'] = hosts  # Slurm rank order, which can differ from the request order.
    manifest = json.loads((output / 'manifest.json').read_text())
    manifest.update(nodes=hosts, slurm_job_id=os.environ.get('SLURM_JOB_ID'))
    write_json(output / 'manifest.json', manifest)
    state['manifest_sha256'] = hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()
    write_json(output / 'slurm_state.json', state)
    process = subprocess.Popen(make_srun_command(output, state), start_new_session=True)
    previous = {}
    def stop(signum, frame):
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, stop)
        code = process.wait()
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)
    return finalize_run(output, code)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in ('_worker', '_execute', '_finalize'):
            if len(argv) != 2: raise ValueError('Internal command requires one run directory')
            output = Path(argv[1]).resolve()
            return {'_worker': worker, '_execute': execute, '_finalize': finalize_run}[argv[0]](output)
        parser = runner.make_parser()
        parser.description = __doc__
        parser.add_argument('--nodes', default=','.join(DEFAULT_NODES), help='comma-separated Slurm nodes')
        parser.add_argument('--partition', default='all')
        parser.add_argument('--job-name', default='nia_omt')
        parser.add_argument('--input-list', type=Path, help='optional file with one benchmark path per line')
        args = parser.parse_args(argv)
        runner.validate_budget(args.jobs, args.timeout)
        runner.validate_optimality_settings(args.prove_optimal, args.optimality_stall, args.optimality_timeout,
                                           args.optimality_search_window, args.optimality_resume,
                                           args.optimality_burst, args.optimality_cutset, args.optimality_balanced)
        runner.validate_initial_z3_settings(args.prove_optimal, args.initial_z3_after, args.initial_z3_timeout)
        runner.validate_search_exchange(args.prove_optimal, args.search_exchange)
        if args.unbounded_threshold is not None and not args.prove_unbounded:
            raise ValueError('--unbounded-threshold requires --prove-unbounded')
        nodes = [n.strip() for n in args.nodes.split(',')]
        if any(not n or not re_node(n) for n in nodes) or len(set(nodes)) != len(nodes):
            raise ValueError('--nodes requires unique simple node names')
        if args.limit is not None and args.limit < 1: raise ValueError('--limit must be positive')
        cases = runner.collect_inputs(args.suite, subset=args.subset)
        requested = {p.resolve() for p in (args.input or [])}
        if args.input_list:
            requested.update(Path(line.strip()).resolve() for line in args.input_list.read_text().splitlines()
                             if line.strip() and not line.lstrip().startswith('#'))
            if not requested:
                raise ValueError('--input-list contains no benchmark paths')
        if requested:
            missing = requested - {c.source for c in cases}
            if missing: raise ValueError(f'Requested inputs are outside the selected subset: {sorted(map(str, missing))[:3]}')
            cases = [c for c in cases if c.source in requested]
        if args.limit: cases = cases[:args.limit]
        if not cases: raise ValueError('No benchmarks selected')
        config = runner.Config(jobs=args.jobs, timeout=args.timeout, subset=args.subset,
            use_target=not args.no_target and not args.prove_unbounded,
            stop_at_known_optimum=not args.no_known_optimum_stop, large_target=args.large_target,
            selection_suite=args.suite, selection_complete=not requested and args.limit is None,
            prove_optimal=args.prove_optimal, optimality_stall=args.optimality_stall,
            optimality_timeout=args.optimality_timeout,
            optimality_search_window=args.optimality_search_window, optimality_resume=args.optimality_resume,
            optimality_burst=args.optimality_burst,
            optimality_cutset=args.optimality_cutset,
            optimality_balanced=args.optimality_balanced, search_exchange=args.search_exchange,
            initial_z3_after=args.initial_z3_after, initial_z3_timeout=args.initial_z3_timeout,
            prove_unbounded=args.prove_unbounded, unbounded_threshold=args.unbounded_threshold or 1000000)
        print(f'Slurm: {",".join(nodes)}; {args.jobs} cases/node; total {len(nodes)*args.jobs}; '
              f'{len(cases)} selected; {args.timeout:g}s/case', flush=True)
        if args.dry_run:
            print('Dry run: no build, snapshot, submission, or solver execution.')
            return 0
        for executable in ('sbatch', 'srun', 'scontrol'):
            if not shutil.which(executable): raise ValueError(f'Missing Slurm command: {executable}')
        subprocess.run(['make', '-C', str(runner.ROOT / 'NIA_LS/src'), '-j' + os.environ.get('BUILD_JOBS', '8')], check=True)
        output = (args.output or runner.default_output_directory()).resolve()
        state = prepare_run(cases, config, output, nodes, args.partition, args.job_name)
        command = make_sbatch_command(output, state)
        submitted = subprocess.run(command, text=True, capture_output=True, check=False)
        if submitted.returncode:
            write_json(output / 'submission.json', {'command': command, 'error': submitted.stderr,
                                                    'returncode': submitted.returncode})
            raise ValueError(f'Slurm submission failed; retained setup: {output}: {submitted.stderr.strip()}')
        job = submitted.stdout.strip().split(';')[0]
        if not job.isdigit(): raise ValueError(f'Unexpected sbatch response: {submitted.stdout!r}')
        write_json(output / 'submission.json', {'job_id': job, 'command': command})
        print(f'Submitted Slurm job {job}\nResults: {output}\nStatus: squeue -j {job}\nCancel: scancel {job}')
        return 0
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or '').strip()
        print(f'run_optimize_slurm: {error}' + (f'\n{detail}' if detail else ''), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'run_optimize_slurm: {error}', file=sys.stderr)
        return 2


def re_node(value):
    return all(c.isalnum() or c in '._-' for c in value)


if __name__ == '__main__':
    raise SystemExit(main())
