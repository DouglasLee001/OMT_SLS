#!/usr/bin/env python3
"""Frozen paired HEAD/embedded experiments, with shared-node Slurm admission.

Importing this module has no scheduler or solver side effects. `prepare` copies
and hashes artifacts; only `watch` submits jobs, and only `_worker` runs cases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import importlib
import io
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
import tarfile
import time

COMMIT = '6614f3c1ea13539918b6f641c64446b753e6db0e'
NODES = ('server', 'node1', 'node2', 'node3', 'node4', 'node5')
WORKERS = 80
TIMEOUT = 1200
TREE = Path(__file__).resolve().parents[1]
WORKSPACE = TREE.parent
OLD_FULL = Path('/data/shared/lbh/OMT/result/NIA_20261028_011635_736862')
RESULTS = Path('/data/shared/lbh/OMT/result')
FULL_CONFIG = dict(jobs=80, timeout=1200.0, use_target=False, subset='all',
                   stop_at_known_optimum=False, large_target=1000000,
                   selection_suite='all', selection_complete=False,
                   prove_unbounded=True, unbounded_threshold=1000000,
                   prove_optimal=True, optimality_stall=10.0, optimality_timeout=30.0,
                   optimality_search_window=0.0, optimality_resume=True,
                   optimality_burst=True, optimality_cutset=True, optimality_balanced=True,
                   initial_z3_after=300.0, initial_z3_timeout=120.0)
EVALUATION_POLICY = dict(
    scope='one fresh paired full 6310-case run at the frozen 1200-second budget',
    aggregate_criteria=['no new embedded error/interrupted cases',
                        'validated feasible-model count does not decrease',
                        'A1+A2 solved count does not decrease',
                        'paired objective wins are at least paired losses'],
    stronger_diagnostic='zero individual objective losses',
    limitation='Observed aggregate comparison; completion alone is not a noninferiority claim.')


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def durable_json(path, value, exclusive=False):
    """Persist a local record and its directory before an external action."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path if exclusive else path.with_name(path.name + '.tmp-' + str(os.getpid()))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(value, handle, indent=2); handle.write('\n')
        handle.flush(); os.fsync(handle.fileno())
    if not exclusive:
        os.replace(target, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def freeze_file(original, output, relative, files):
    original, output = Path(original), Path(output)
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'source':
        raise ValueError('invalid frozen artifact destination')
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not original.is_file() or destination.exists():
        raise ValueError(f'missing source or duplicate frozen artifact: {original}')
    shutil.copy2(original, destination, follow_symlinks=True)
    files[relative.as_posix()] = digest(destination)
    return str(destination)


def verify_files(output, files):
    output = Path(output)
    for relative, expected in files.items():
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'source':
            raise ValueError('frozen artifact escapes source directory')
        path = output
        for part in relative.parts:
            path /= part
            if path.is_symlink():
                raise ValueError(f'live symlink in frozen artifacts: {relative}')
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f'frozen artifact changed: {relative}')


def verify_plan(output, plan, indices=None):
    verify_files(output, plan['files'])
    if digest(plan['python']) != plan['python_sha256']:
        raise ValueError('Python interpreter changed')
    selected = plan['cases'] if indices is None else [plan['cases'][i] for i in indices]
    for case in selected:
        if digest(case['source']) != plan['benchmark_hashes'][case['source']]:
            raise ValueError(f'benchmark changed: {case["source"]}')


def load_plan(output):
    output = Path(output).resolve()
    plan = read_json(output / 'manifest.json')
    if digest(output / 'manifest.json') != (output / 'manifest.sha256').read_text().strip():
        raise ValueError('experiment manifest changed')
    if Path(plan['output']) != output:
        raise ValueError('experiment was relocated')
    return plan


def admit_node(text, expected):
    if expected not in NODES:
        return None
    values = dict(re.findall(r'(\w+)=([^\s]+)', text))
    try:
        if values.get('NodeName') != expected or values.get('State') not in ('IDLE', 'MIXED'):
            return None
        total, effective, allocated, memory, used_memory = (
            int(values[key]) for key in ('CPUTot', 'CPUEfctv', 'CPUAlloc', 'RealMemory', 'AllocMem'))
        capacity = min(total, effective)
        if capacity < WORKERS or allocated < 0 or memory <= 0 or used_memory < 0:
            return None
        requested = (memory * WORKERS + capacity - 1) // capacity
        if capacity - allocated < WORKERS or memory - used_memory < requested:
            return None
        return dict(node=expected, workers=WORKERS, capacity=capacity,
                    free_cpus=capacity - allocated, memory_mb=requested,
                    real_memory_mb=memory, alloc_memory_mb=used_memory, observed_utc=utc())
    except (KeyError, ValueError, OverflowError):
        return None


def query_node(node):
    try:
        result = subprocess.run(['scontrol', 'show', 'node', node, '-o'],
                                capture_output=True, text=True, check=True, timeout=15)
        return admit_node(result.stdout, node)
    except (OSError, subprocess.SubprocessError):
        return None


def sbatch_command(output, task_id, node, count, plan_id, partition='all', launcher=None):
    output = Path(output)
    minutes = math.ceil(math.ceil(count / WORKERS) * TIMEOUT / 60) + 15
    task = output / 'tasks' / task_id
    return ['sbatch', '--parsable', '--partition=' + partition, '--nodes=1', '--ntasks=1',
            '--cpus-per-task=80', '--nodelist=' + node['node'], f'--mem={node["memory_mb"]}M',
            f'--time={minutes}', '--job-name=z3nia_' + task_id,
            '--comment=z3nia:' + plan_id + ':' + task_id, '--export=ALL',
            '--chdir=' + str(output), '--output=' + str(task / 'slurm-%j.out'),
            '--error=' + str(task / 'slurm-%j.err'), str(task / 'submit.sbatch')]


def regression_indices(cases, per_stratum=2):
    if per_stratum < 1:
        raise ValueError('regression samples per stratum must be positive')
    groups = defaultdict(list)
    for index, case in enumerate(cases):
        reference = ('known' if case.get('known_optimum_value') is not None else
                     'finite' if case.get('z3') is not None or case.get('optimathsat') is not None else 'unknown')
        parts = Path(case['filename']).parts
        family = parts[1] if case['suite'] == 'original' and len(parts) > 1 and parts[0] == 'QF_NIA' else case['category']
        groups[(case['suite'], family, case['direction'], reference)].append(index)
    selected = []
    for indices in groups.values():
        indices.sort(key=lambda i: hashlib.sha256(cases[i]['source'].encode()).hexdigest())
        selected.extend(indices[:per_stratum])
    return sorted(selected)


def forced_regression_indices(cases):
    portfolio = [i for i, c in enumerate(cases) if c['suite'] == 'new' and c['category'] == 'portfolio'
                 and c['filename'] == 'gram-m12-q6-s1058--a34080af91dd.smt2']
    leipzig = [i for i, c in enumerate(cases) if c['suite'] == 'original' and c['filename'].startswith('QF_NIA/leipzig/')]
    if len(portfolio) != 1:
        raise ValueError('required portfolio 1058 case is absent or duplicated in manifest')
    if not leipzig:
        raise ValueError('required Leipzig representative is absent from manifest')
    return sorted([portfolio[0], min(leipzig, key=lambda i: hashlib.sha256(cases[i]['source'].encode()).hexdigest())])


def result_state(row):
    valid = row.get('model_validated') == 'true' and bool(row.get('best_value'))
    if not valid:
        return 'C'
    if row.get('is_unbounded') == 'true':
        return 'A1'
    return 'A2' if row.get('is_optimal') == 'true' else 'B'


def result_score(row):
    if result_state(row) == 'C':
        return (0, 0)
    if row.get('is_unbounded') == 'true':
        return (2, 0)
    if row['direction'] not in ('min', 'max') or not re.fullmatch(r'-?[0-9]+', row['best_value']):
        raise ValueError('invalid exact result objective')
    value = int(Decimal(row['best_value']))
    return (1, -value if row['direction'] == 'min' else value)


def compare_pair(baseline, embedded):
    if baseline['direction'] != embedded['direction']:
        raise ValueError('paired result directions differ')
    a, b = result_score(baseline), result_score(embedded)
    return 'win' if b > a else 'loss' if b < a else 'tie'


def gate_from_comparison(comparison):
    if not comparison or not comparison.get('complete'):
        return dict(state='waiting', reason='paired regression is incomplete')
    passed = comparison['losses'] == 0 and comparison['errors'] == 0 and comparison['solved_delta'] >= 0
    return dict(state='passed' if passed else 'failed', comparison=comparison,
                reason='no objective loss, no errors, no solved-count loss' if passed else
                       'regression reported an objective loss, error or solved-count loss')


def full_evaluation(comparison):
    """Separate evidence completeness from the predeclared aggregate criteria."""
    result = dict(policy=EVALUATION_POLICY, state='incomplete',
                  meets_aggregate_criteria=None, strict_zero_objective_losses=None)
    if not comparison or not comparison.get('complete') or comparison.get('cases') != 6310:
        return result
    checks = dict(no_new_errors=comparison['new_error_count'] == 0,
                  feasible_count_not_lower=comparison['model_delta'] >= 0,
                  solved_count_not_lower=comparison['solved_delta'] >= 0,
                  objective_wins_at_least_losses=comparison['wins'] >= comparison['losses'])
    meets = all(checks.values())
    result.update(state='meets_aggregate_criteria' if meets else 'does_not_meet_aggregate_criteria',
                  meets_aggregate_criteria=meets, checks=checks,
                  strict_zero_objective_losses=comparison['losses'] == 0)
    return result


def _reference_modules(directory):
    directory = str(Path(directory))
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module('run_optimize'), importlib.import_module('run_optimize_slurm')


def _baseline_source_archive(workspace, reference, destination):
    archive = subprocess.run(['git', 'archive', '--format=tar', COMMIT, 'NIA_LS', 'script', 'result'],
                             cwd=workspace, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as packed:
        for member in packed.getmembers():
            if not member.isfile():
                continue
            path = Path(reference) / member.name
            if not path.is_file() or path.read_bytes() != packed.extractfile(member).read():
                raise ValueError(f'reference does not match pinned HEAD: {member.name}')
    Path(destination).write_bytes(archive)


def _source_archive(tree, destination):
    with tarfile.open(destination, 'w:gz') as packed:
        for directory in ('source', 'sls', 'experiment'):
            base = tree / directory
            for path in sorted(base.rglob('*')):
                if not path.is_file() or path.is_symlink() or any(
                        part in ('__pycache__', '.git', 'build') for part in path.relative_to(base).parts):
                    continue
                packed.add(path, arcname=str(path.relative_to(tree)), recursive=False)


def prepare(args):
    tree, workspace = args.tree.resolve(), args.workspace.resolve()
    output = (args.output or RESULTS / ('NIA_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f'))).resolve()
    reference = tree / 'reference'
    receipt = read_json(args.baseline_build_record)
    if receipt.get('git_commit') != COMMIT or receipt.get('executable_sha256') != digest(args.baseline):
        raise ValueError('baseline build receipt does not identify the pinned HEAD executable')
    embedded_receipt = read_json(tree / 'embedding-build.json')
    if embedded_receipt.get('baseline_commit') != COMMIT or embedded_receipt.get('executable_sha256') != digest(args.embedded):
        raise ValueError('embedded build receipt does not identify the current executable')
    for executable in (args.baseline, args.embedded):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f'executable is missing: {executable}')
    old = read_json(args.case_manifest)
    cases = old['cases']
    if Counter(c['suite'] for c in cases) != {'original': 5744, 'new': 566}:
        raise ValueError('full manifest must contain exactly 5744 original and 566 new cases')
    if len({(c['suite'], c['category'], c['filename']) for c in cases}) != 6310 or len({c['source'] for c in cases}) != 6310:
        raise ValueError('duplicate source or case identity in full manifest')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'source').mkdir()
    files = {}
    archive = output / 'source/baseline-source.tar'
    _baseline_source_archive(workspace, reference, archive)
    files['source/baseline-source.tar'] = digest(archive)
    for path in sorted((reference / 'script').glob('*.py')):
        freeze_file(path, output, 'source/reference/script/' + path.name, files)
    for path in sorted((reference / 'result').rglob('*.csv')):
        if path.name == 'competitor.csv':
            continue
        freeze_file(path, output, 'source/reference/result/' + str(path.relative_to(reference / 'result')), files)
    freeze_file(workspace / 'result/competitor.csv', output, 'source/reference/result/competitor.csv', files)
    freeze_file(args.case_manifest, output, 'source/input-manifest.json', files)
    freeze_file(args.baseline_build_record, output, 'source/baseline-build-record.json', files)
    for name in ('embedding-build.json', 'build.sh'):
        freeze_file(tree / name, output, 'source/' + name, files)
    provenance = sorted((tree / 'toolchain').glob('*/provenance.json'))
    if not provenance:
        raise ValueError('toolchain provenance is missing')
    for path in provenance:
        freeze_file(path, output, 'source/' + str(path.relative_to(tree)), files)
    for name in ('embedding_experiment.py', 'runtime_adapter.py'):
        freeze_file(Path(__file__).parent / name, output, 'source/harness/' + name, files)
    runner, _ = _reference_modules(output / 'source/reference/script')
    snapshot = importlib.import_module('slurm_snapshot')
    variants = {}
    for variant, executable in [('baseline', args.baseline), ('embedded', args.embedded)]:
        frozen = freeze_file(executable, output, 'source/' + variant + '/solver', files)
        lib = output / 'source' / variant / 'lib'
        lib.mkdir()
        libraries = snapshot.native_libraries(executable)
        for soname, path in sorted(libraries.items()):
            freeze_file(path, output, 'source/' + variant + '/lib/' + soname, files)
        variants[variant] = dict(executable=frozen, executable_sha256=digest(frozen), library_dir=str(lib))
    converter = freeze_file(reference / 'NIA_LS/converter/omt_to_ls.py', output,
                            'source/baseline/converter/omt_to_ls.py', files)
    sdk = args.sdk.resolve() / 'build'
    for path in sorted((sdk / 'python/z3').glob('*.py')):
        freeze_file(path, output, 'source/baseline/converter/z3-5.1.0/build/python/z3/' + path.name, files)
    freeze_file(sdk / 'libz3.so', output, 'source/baseline/converter/z3-5.1.0/build/libz3.so', files)
    if not (output / 'source/baseline/converter/z3-5.1.0/build/python/z3/__init__.py').is_file():
        raise ValueError('baseline converter SDK is missing')
    native_z3 = [p for n, p in snapshot.native_libraries(args.baseline).items() if n.startswith('libz3.so')]
    if len(native_z3) != 1 or digest(native_z3[0]) != digest(sdk / 'libz3.so'):
        raise ValueError('baseline converter and native solver use different Z3 libraries')
    embedded_archive = output / 'source/embedded-source.tar.gz'
    _source_archive(tree, embedded_archive)
    files['source/embedded-source.tar.gz'] = digest(embedded_archive)
    benchmark_hashes = {c['source']: digest(c['source']) for c in cases}
    forced = forced_regression_indices(cases)
    regression = sorted(set(regression_indices(cases, args.regression_per_stratum)) | set(forced))
    tasks = []
    for phase, indices in [('regression', regression), ('full', list(range(len(cases))))]:
        phase_shards = 1 if phase == 'regression' else args.shards
        for rank in range(phase_shards):
            shard = indices[rank::phase_shards]
            if not shard:
                continue
            for variant in (('baseline', 'embedded') if rank % 2 == 0 else ('embedded', 'baseline')):
                tasks.append(dict(id=f'{phase}-{variant}-{rank:02d}', phase=phase, variant=variant, indices=shard))
    plan = dict(schema=1, id=output.name.removeprefix('NIA_'), created_utc=utc(), output=str(output),
                baseline_commit=COMMIT, baseline_reuse=False, case_manifest=str(args.case_manifest.resolve()),
                workers=WORKERS, timeout=TIMEOUT, nodes=list(NODES), partition=args.partition,
                config=FULL_CONFIG, seed_defaults=dict(explicit_seed=None, mt=0, lia_seed=0),
                variants=variants, converter=converter, converter_sha256=digest(converter),
                files=files, benchmark_hashes=benchmark_hashes,
                python=str(Path(sys.executable).resolve()), python_sha256=digest(sys.executable),
                launcher=str(output / 'source/harness/embedding_experiment.py'),
                reference_script=str(output / 'source/reference/script'), cases=cases,
                regression_indices=regression, forced_regression_indices=forced, forced_case_count=len(forced), tasks=tasks,
                evidence_role='fresh paired HEAD versus embedded native-entry comparison',
                evaluation_policy=EVALUATION_POLICY,
                converter_budget='conversion and optimization share the same 1200-second wallclock deadline')
    runner.Config(**FULL_CONFIG, solver=Path(variants['baseline']['executable']), converter=Path(converter))
    for task in tasks:
        where = output / 'tasks' / task['id']; where.mkdir(parents=True)
        command = [plan['python'], plan['launcher'], '_worker', str(output), task['id']]
        script = '#!/bin/bash\nset -euo pipefail\nexport PYTHONDONTWRITEBYTECODE=1\nexec ' + shlex.join(command) + '\n'
        (where / 'submit.sbatch').write_text(script)
        # Submission scripts are part of the immutable frozen hash list too.
        relative = 'source/jobs/' + task['id'] + '.sbatch'
        freeze_file(where / 'submit.sbatch', output, relative, files)
    durable_json(output / 'manifest.json', plan, exclusive=True)
    (output / 'manifest.sha256').write_text(digest(output / 'manifest.json') + '\n')
    verify_plan(output, plan)
    durable_json(output / 'state.json', dict(state='prepared', created_utc=utc(), regression='pending', full='pending'))
    return output


def queue_jobs():
    result = subprocess.run(['squeue', '--noheader', '--format=%i|%k|%T|%N', '--user=' + str(os.getuid())],
                            capture_output=True, text=True, check=True, timeout=15)
    jobs = []
    for line in result.stdout.splitlines():
        pieces = line.split('|')
        if len(pieces) == 4:
            jobs.append(dict(zip(('job_id', 'comment', 'state', 'nodes'), pieces)))
    return jobs


def recover_receipt(output, plan, task, jobs):
    where = Path(output) / 'tasks' / task['id']
    if not (where / 'intent.json').exists() or (where / 'receipt.json').exists():
        return
    intent = read_json(where / 'intent.json')
    matches = [j for j in jobs if j['comment'] == intent['comment']]
    if len(matches) > 1:
        raise ValueError('duplicate scheduler jobs for one durable intent')
    started = where / 'worker_started.json'
    job = matches[0]['job_id'] if matches else (read_json(started)['job_id'] if started.exists() else None)
    if job:
        durable_json(where / 'receipt.json', dict(job_id=job, recovered_utc=utc(),
                                                recovered_from='queue' if matches else 'worker_started'), exclusive=True)


def record_terminal_jobs(output, plan, jobs):
    """Accounted terminal jobs without a completion record fail closed."""
    active = {j['job_id'] for j in jobs}
    terminal = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY',
                'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}
    for task in plan['tasks']:
        where = Path(output) / 'tasks' / task['id']
        if not (where / 'receipt.json').exists() or (where / 'worker_done.json').exists():
            continue
        job = read_json(where / 'receipt.json')['job_id']
        if job in active:
            continue
        try:
            answer = subprocess.run(['sacct', '--noheader', '--parsable2', '-j', job,
                                     '--format=JobIDRaw,State,ExitCode'],
                                    capture_output=True, text=True, check=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue  # Accounting lag or failure never permits a retry.
        for line in answer.stdout.splitlines():
            fields = line.split('|')
            if len(fields) >= 3 and fields[0] == job and fields[1].split()[0].rstrip('+') in terminal:
                durable_json(where / 'worker_done.json', dict(returncode=2, job_id=job,
                            scheduler_state=fields[1], exit_code=fields[2], utc=utc(),
                            reason='scheduler terminated the job without a worker completion record'), exclusive=True)
                break


def attempt_submit(output, plan, task, node):
    output = Path(output)
    where = output / 'tasks' / task['id']
    if (where / 'intent.json').exists() or query_node(node) is None:
        return False
    verify_plan(output, plan, task['indices'])
    # Verify the actual Slurm script, not just its frozen copy.
    script = where / 'submit.sbatch'
    frozen_script = output / 'source/jobs' / (task['id'] + '.sbatch')
    if frozen_script.exists() and digest(script) != digest(frozen_script):
        raise ValueError('submission script changed')
    # Last external action before sbatch: a fresh fail-closed resource check.
    # Between this and sbatch there are only local file writes/fsyncs.
    resource = query_node(node)
    if resource is None:
        return False
    command = sbatch_command(output, task['id'], resource, len(task['indices']), plan['id'],
                             plan.get('partition', 'all'))
    intent = dict(plan_id=plan['id'], plan_sha256=digest(output / 'manifest.json') if (output / 'manifest.json').exists() else '',
                  task_id=task['id'], node=node, workers=WORKERS, memory_mb=resource['memory_mb'],
                  resource=resource, comment='z3nia:' + plan['id'] + ':' + task['id'],
                  command=command, created_utc=utc())
    try:
        durable_json(where / 'intent.json', intent, exclusive=True)
    except FileExistsError:
        return False
    try:
        answer = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        job_id = answer.stdout.strip().split(';')[0]
        if not re.fullmatch(r'[0-9]+', job_id):
            raise ValueError('sbatch returned an uncertain job receipt')
        durable_json(where / 'receipt.json', dict(job_id=job_id, submitted_utc=utc(),
                                                stdout=answer.stdout, stderr=answer.stderr), exclusive=True)
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        durable_json(where / 'submission_uncertain.json', dict(reason=str(error), utc=utc()))
        return False


def validate_worker(intent, environment, actual_host):
    try:
        valid = (actual_host == intent['node'] == environment['SLURMD_NODENAME']
                 and int(environment['SLURM_CPUS_PER_TASK']) == intent['workers'] == WORKERS
                 and int(environment['SLURM_NTASKS']) == 1
                 and int(environment['SLURM_MEM_PER_NODE']) == intent['memory_mb']
                 and environment.get('SLURM_LOCALID', '0') == '0'
                 and re.fullmatch(r'[0-9]+', environment['SLURM_JOB_ID']))
    except (KeyError, ValueError):
        valid = False
    if not valid:
        raise ValueError('worker placement/allocation does not match durable submission intent')


def worker(output, task_id):
    output = Path(output).resolve()
    plan = load_plan(output)
    task = next(t for t in plan['tasks'] if t['id'] == task_id)
    where = output / 'tasks' / task_id
    intent = read_json(where / 'intent.json')
    if intent['plan_id'] != plan['id'] or intent['plan_sha256'] != digest(output / 'manifest.json') or intent['task_id'] != task_id:
        raise ValueError('worker intent does not match frozen plan')
    validate_worker(intent, os.environ, socket.gethostname().split('.')[0])
    verify_plan(output, plan, task['indices'])
    durable_json(where / 'worker_started.json', dict(job_id=os.environ['SLURM_JOB_ID'], node=intent['node'],
                                                   task_id=task_id, plan_sha256=intent['plan_sha256'], utc=utc()), exclusive=True)
    runtime = plan['variants'][task['variant']]
    os.environ['LD_LIBRARY_PATH'] = runtime['library_dir']
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    runner, launcher = _reference_modules(plan['reference_script'])
    from runtime_adapter import install
    install(runner, task['variant'], runtime['executable'], plan['converter'])
    config = runner.Config(**plan['config'], solver=Path(runtime['executable']), converter=Path(plan['converter']))
    cases = [launcher.case_from_record(plan['cases'][i]) for i in task['indices']]
    code = runner.run_batch(cases, config, where / 'run')
    verify_plan(output, plan, task['indices'])
    local = read_json(where / 'run/manifest.json')
    local.update(variant=task['variant'], plan_id=plan['id'], global_indices=task['indices'],
                 actual_converter='embedded C++ importer' if task['variant'] == 'embedded' else 'HEAD Python converter',
                 actual_converter_sha256=runtime['executable_sha256'] if task['variant'] == 'embedded' else digest(plan['converter']),
                 seed_defaults=plan['seed_defaults'])
    durable_json(where / 'run/manifest.json', local)
    durable_json(where / 'worker_done.json', dict(returncode=code, job_id=os.environ['SLURM_JOB_ID'],
                                                task_id=task_id, utc=utc(),
                                                results_sha256=digest(where / 'run/results.csv')))
    return code


def _identity(record):
    return record['suite'], record['category'], record['filename']


def config_manifest_values(config):
    aliases = dict(optimality_stall='optimality_stall_seconds',
                   optimality_timeout='optimality_timeout_seconds',
                   optimality_search_window='optimality_search_window_seconds',
                   initial_z3_after='initial_z3_after_seconds', initial_z3_timeout='initial_z3_timeout_seconds',
                   stop_at_known_optimum='known_optimum_stop_enabled',
                   use_target='scalar_target_enabled')
    expected = {aliases.get(key, key): value for key, value in config.items() if key not in ('large_target', 'unbounded_threshold')}
    expected['large_target_magnitude'] = str(config['large_target']) if config['large_target'] is not None else None
    expected['unbounded_threshold'] = str(config['unbounded_threshold']) if config['prove_unbounded'] else None
    return expected


def validate_local_manifest(plan, task, local):
    expected = config_manifest_values(plan['config'])
    expected.update(solver_sha256=plan['variants'][task['variant']]['executable_sha256'],
                    converter_sha256=plan['converter_sha256'],
                    actual_converter_sha256=(plan['variants']['embedded']['executable_sha256']
                                             if task['variant'] == 'embedded' else plan['converter_sha256']),
                    seed_defaults=plan['seed_defaults'])
    for key, value in expected.items():
        if local.get(key) != value or (type(value) is bool and type(local.get(key)) is not bool):
            raise ValueError(f'worker manifest configuration/runtime mismatch: {key}')


def aggregate(output, plan, phase, variant):
    tasks = [t for t in plan['tasks'] if t['phase'] == phase and t['variant'] == variant]
    rows = {}
    for task in tasks:
        where = Path(output) / 'tasks' / task['id']
        if not (where / 'worker_done.json').is_file():
            return None
        done = read_json(where / 'worker_done.json')
        if done['returncode'] != 0 or digest(where / 'run/results.csv') != done['results_sha256']:
            raise ValueError(f'worker failed or results changed: {task["id"]}')
        local = read_json(where / 'run/manifest.json')
        validate_local_manifest(plan, task, local)
        expected = [plan['cases'][i] for i in task['indices']]
        if local['cases'] != expected or local['global_indices'] != task['indices'] or local['variant'] != variant:
            raise ValueError('worker case assignment differs from frozen plan')
        with (where / 'run/results.csv').open(newline='') as handle:
            actual = list(csv.DictReader(handle))
        lookup = {_identity(plan['cases'][i]): i for i in task['indices']}
        if len(actual) != len(lookup):
            raise ValueError('worker did not record every assigned case')
        for row in actual:
            index = lookup.get(_identity(row))
            if index is None or index in rows or row['direction'] != plan['cases'][index]['direction']:
                raise ValueError('duplicate, foreign or inconsistent worker result')
            rows[index] = row
    return rows


def write_reference_report(run, competitor):
    """Reuse the frozen reference's proof metadata and A1/A2/B/C checks."""
    reporter = importlib.import_module('report_results')
    nia, directions = reporter.load_nia_run(run)
    opponents = reporter.load_competitors(competitor)
    if nia.keys() - opponents.keys():
        raise ValueError('run contains cases missing from frozen competitor.csv')
    combined = []
    for name in sorted(nia):
        row = dict(opponents[name])
        row.update({'NIA_LS' + field: nia[name][field] for field in reporter.NIA_FIELDS[1:]})
        combined.append(row)
    stats = reporter.compute_statistics(combined, directions)
    reporter.atomic_write(run / 'nia_ls.csv', reporter.csv_text(reporter.NIA_FIELDS, [nia[n] for n in sorted(nia)]), 'utf-8-sig')
    reporter.atomic_write(run / 'combined.csv', reporter.csv_text(reporter.COMBINED_FIELDS, combined), 'utf-8-sig')
    reporter.atomic_write(run / 'summary.md', reporter.render_report(stats, run))
    durable_json(run / 'reference_statistics.json', stats)
    return stats


def paired_summary(baseline, embedded):
    if set(baseline) != set(embedded):
        raise ValueError('paired case sets differ')
    outcomes, unsolved = Counter(), Counter()
    states = {v: Counter() for v in ('baseline', 'embedded')}
    errors = 0
    errors_by_variant = Counter(baseline=0, embedded=0)
    new_error_count = 0
    for i in baseline:
        a, b = baseline[i], embedded[i]
        outcome = compare_pair(a, b); outcomes[outcome] += 1
        if result_state(a) in ('B', 'C') and result_state(b) in ('B', 'C'):
            unsolved[outcome] += 1
        for variant, row in [('baseline', a), ('embedded', b)]:
            states[variant][result_state(row)] += 1
            is_error = row.get('status') in ('error', 'interrupted')
            errors += is_error
            errors_by_variant[variant] += is_error
        new_error_count += (b.get('status') in ('error', 'interrupted') and
                            a.get('status') not in ('error', 'interrupted'))
    solved = {v: sum(states[v][s] for s in ('A1', 'A2')) for v in states}
    return dict(complete=True, cases=len(baseline), wins=outcomes['win'], ties=outcomes['tie'],
                losses=outcomes['loss'], errors=errors, errors_by_variant=dict(errors_by_variant),
                new_error_count=new_error_count, states=states, solved=solved,
                solved_delta=solved['embedded'] - solved['baseline'],
                model_delta=sum(n for s, n in states['embedded'].items() if s != 'C') -
                            sum(n for s, n in states['baseline'].items() if s != 'C'),
                both_unsolved_paired=dict(cases=sum(unsolved.values()), wins=unsolved['win'],
                                          ties=unsolved['tie'], losses=unsolved['loss']))


def render_top_summary(comparisons):
    lines = ['# Embedding performance comparison', '', 'WTL is from the embedded executable\'s perspective.', '']
    for phase, comparison in comparisons.items():
        lines.extend(['## ' + phase.capitalize(), ''])
        if not comparison.get('complete'):
            lines.extend(['Pending.', '']); continue
        lines.extend(['| Variant | A1 | A2 | B | C | Solved |', '| --- | ---: | ---: | ---: | ---: | ---: |'])
        for variant in ('baseline', 'embedded'):
            state = comparison['states'][variant]
            cells = [variant, *(state.get(s, 0) for s in ('A1', 'A2', 'B', 'C')), comparison['solved'][variant]]
            lines.append('| ' + ' | '.join(map(str, cells)) + ' |')
        lines.extend(['', '| Paired subset | Cases | Wins | Ties | Losses |', '| --- | ---: | ---: | ---: | ---: |'])
        for label, values in [('All', comparison), ('Both B/C', comparison['both_unsolved_paired'])]:
            lines.append('| ' + ' | '.join(map(str, [label, *(values[k] for k in ('cases', 'wins', 'ties', 'losses'))])) + ' |')
        lines.append('')
    evaluation = full_evaluation(comparisons.get('full'))
    lines.extend(['## Full evaluation', '', 'State: `' + evaluation['state'] + '`.', '',
                  'The regression gate remains separate. Finishing an evaluation does not itself establish noninferiority.', '',
                  'Predeclared aggregate criteria: no new error/interrupted cases; no decrease in validated feasible models or A1+A2; objective wins >= losses.', ''])
    if evaluation['meets_aggregate_criteria'] is not None:
        lines.extend(['Aggregate criteria met: `' + str(evaluation['meets_aggregate_criteria']).lower() + '`.', '',
                      'Stronger diagnostic, zero individual objective losses: `' +
                      str(evaluation['strict_zero_objective_losses']).lower() + '`.', ''])
    return '\n'.join(lines)


def report(output, plan=None):
    output = Path(output).resolve()
    plan = plan or load_plan(output)
    comparisons = {}
    runner, _ = _reference_modules(plan['reference_script'])
    for phase in ('regression', 'full'):
        variants = {v: aggregate(output, plan, phase, v) for v in ('baseline', 'embedded')}
        if any(value is None for value in variants.values()):
            comparisons[phase] = dict(complete=False)
            continue
        baseline, embedded = variants['baseline'], variants['embedded']
        comparison = paired_summary(baseline, embedded)
        details = []
        for index in sorted(baseline):
            a, b = baseline[index], embedded[index]
            outcome = compare_pair(a, b)
            details.append(dict(index=index, suite=a['suite'], category=a['category'], filename=a['filename'],
                                direction=a['direction'], baseline_state=result_state(a), embedded_state=result_state(b),
                                baseline_value=a['best_value'], embedded_value=b['best_value'], outcome=outcome,
                                baseline_wall=a['wall_seconds'], embedded_wall=b['wall_seconds'],
                                baseline_first=a['time_to_first_seconds'], embedded_first=b['time_to_first_seconds'],
                                baseline_best=a['time_to_best_seconds'], embedded_best=b['time_to_best_seconds']))
        comparison['wall_seconds'] = {v: str(sum((Decimal(r['wall_seconds']) for r in variants[v].values()), Decimal(0)))
                                      for v in variants}
        comparisons[phase] = comparison
        directory = output / 'reports' / phase; directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'paired.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(details[0]))
            writer.writeheader(); writer.writerows(details)
        official = {}
        for variant, indexed in variants.items():
            run = directory / variant; run.mkdir(exist_ok=True)
            ordered = sorted(indexed)
            cases = [plan['cases'][i] for i in ordered]
            case_objects = []
            # The reference launcher restores exact large integer values.
            _, launcher = _reference_modules(plan['reference_script'])
            case_objects = [launcher.case_from_record(c) for c in cases]
            config = runner.Config(**dict(plan['config'], selection_complete=phase == 'full'),
                                   solver=Path(plan['variants'][variant]['executable']), converter=Path(plan['converter']))
            rows = [indexed[i] for i in ordered]
            local = launcher.make_manifest(case_objects, config)
            local.update(variant=variant, seed_defaults=plan['seed_defaults'])
            durable_json(run / 'manifest.json', local)
            durable_json(run / 'summary.json', runner._summary(case_objects, rows, config))
            with (run / 'results.csv').open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=runner.FIELDS)
                writer.writeheader(); writer.writerows(rows)
            official[variant] = write_reference_report(run, output / 'source/reference/result/competitor.csv')
        comparison['reference_statistics'] = official
        durable_json(directory / 'comparison.json', comparison)
    gate = gate_from_comparison(comparisons['regression'])
    durable_json(output / 'regression_gate.json', gate)
    durable_json(output / 'full_evaluation.json', full_evaluation(comparisons['full']))
    durable_json(output / 'comparison.json', comparisons)
    (output / 'summary.md').write_text(render_top_summary(comparisons))
    return comparisons, gate


def status(output, plan=None, jobs=None):
    output = Path(output).resolve()
    plan = plan or load_plan(output)
    tasks = []
    for task in plan['tasks']:
        where = output / 'tasks' / task['id']
        state, node, job = 'prepared', None, None
        if (where / 'intent.json').exists():
            intent = read_json(where / 'intent.json'); node = intent['node']; state = 'submission_uncertain'
        if (where / 'receipt.json').exists():
            job = read_json(where / 'receipt.json')['job_id']; state = 'submitted'
        if (where / 'worker_started.json').exists(): state = 'running'
        if (where / 'worker_done.json').exists():
            state = 'complete' if read_json(where / 'worker_done.json')['returncode'] == 0 else 'failed'
        tasks.append(dict(id=task['id'], phase=task['phase'], variant=task['variant'], state=state, node=node, job_id=job))
    return dict(utc=utc(), tasks=tasks, counts=Counter(t['state'] for t in tasks))


def watch(output, phase='all', interval=30, once=False, collect_full_evidence=False):
    output = Path(output).resolve()
    plan = load_plan(output)
    with (output / 'watch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            verify_plan(output, plan)
            try:
                jobs = queue_jobs()
            except (OSError, subprocess.SubprocessError) as error:
                durable_json(output / 'state.json', dict(state='waiting_for_scheduler', reason=str(error), utc=utc()))
                if once: return 0
                time.sleep(interval); continue
            for task in plan['tasks']:
                recover_receipt(output, plan, task, jobs)
            record_terminal_jobs(output, plan, jobs)
            comparisons, gate = report(output, plan)
            if gate['state'] == 'failed' and not collect_full_evidence:
                durable_json(output / 'state.json', dict(state='regression_failed', gate=gate, utc=utc()))
                return 2
            current = 'regression' if not comparisons['regression']['complete'] else 'full'
            if current == 'full' and (phase == 'regression' or comparisons['full']['complete']):
                complete_state = ('regression_complete' if phase == 'regression' else
                                  'evaluation_complete' if collect_full_evidence else 'complete')
                durable_json(output / 'state.json', dict(state=complete_state,
                            collect_full_evidence=collect_full_evidence, regression_gate=gate, utc=utc()))
                return 0
            snapshot = status(output, plan)
            busy = {t['node'] for t in snapshot['tasks'] if t['node'] and t['state'] not in ('complete', 'failed')}
            submitted = 0
            for task in plan['tasks']:
                if task['phase'] != current or (output / 'tasks' / task['id'] / 'intent.json').exists():
                    continue
                for node in plan['nodes']:
                    if node in busy:
                        continue
                    if attempt_submit(output, plan, task, node):
                        submitted += 1; busy.add(node); break
                    # A timed-out sbatch may already have started. Reserve its
                    # node even without a receipt and do not try another node.
                    if (output / 'tasks' / task['id'] / 'intent.json').exists():
                        busy.add(node); break
            durable_json(output / 'state.json', dict(state='watching', phase=current, submitted_this_poll=submitted,
                                                    collect_full_evidence=collect_full_evidence,
                                                    regression_gate=gate, status=status(output, plan), utc=utc()))
            if once: return 0
            time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--tree', type=Path, default=TREE)
    prep.add_argument('--workspace', type=Path, default=WORKSPACE)
    prep.add_argument('--baseline', type=Path, required=True)
    prep.add_argument('--baseline-build-record', type=Path, required=True)
    prep.add_argument('--embedded', type=Path, default=TREE / 'build/z3')
    prep.add_argument('--sdk', type=Path, default=WORKSPACE / 'competitor/z3-5.1.0')
    prep.add_argument('--case-manifest', type=Path, default=OLD_FULL / 'manifest.json')
    prep.add_argument('--output', type=Path)
    prep.add_argument('--partition', default='all')
    prep.add_argument('--shards', type=int, default=6)
    prep.add_argument('--regression-per-stratum', type=int, default=2)
    for name in ('watch', 'status', 'report', '_worker'):
        sub = commands.add_parser(name); sub.add_argument('output', type=Path)
        if name == 'watch':
            sub.add_argument('--phase', choices=('all', 'regression', 'full'), default='all')
            sub.add_argument('--interval', type=int, default=30)
            sub.add_argument('--once', action='store_true')
            sub.add_argument('--collect-full-evidence', action='store_true',
                             help='collect the full paired evaluation even if the regression gate fails; never marks the gate passed')
        if name == '_worker': sub.add_argument('task_id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            if not 1 <= args.shards <= 6: raise ValueError('shards must be between 1 and 6')
            print(prepare(args)); return 0
        plan = load_plan(args.output)
        if args.command in ('watch', '_worker') and Path(__file__).resolve() != Path(plan['launcher']):
            verify_files(args.output, plan['files'])
            os.execv(plan['python'], [plan['python'], plan['launcher'], *(argv if argv is not None else sys.argv[1:])])
        if args.command == 'watch':
            if not 1 <= args.interval <= 60: raise ValueError('poll interval must be 1..60 seconds')
            return watch(args.output, args.phase, args.interval, args.once, args.collect_full_evidence)
        if args.command == '_worker': return worker(args.output, args.task_id)
        if args.command == 'status': print(json.dumps(status(args.output, plan), indent=2))
        if args.command == 'report': print(json.dumps(report(args.output, plan)[0], indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'embedding experiment: {error}', file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
