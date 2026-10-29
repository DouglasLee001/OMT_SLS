#!/usr/bin/env python3
"""Same-node native-IR-only compiler-profile diagnostic. Never submits jobs.

prepare --parent-run RUN --candidate EXE [--output DIRECTORY]
creates a frozen diagnostic and submit.sbatch. The caller checks resources and
submits one shared 80-CPU job with an explicit node and proportional --mem.
The default output is a new NIA_timestamp sibling; --output may instead name a
new parent/diagnostics/compiler-profile-* directory without changing old files.
"""
from __future__ import annotations

from collections import Counter
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time

VARIANTS = ('baseline', 'embedded20', 'candidate17')
JOBS_PER_VARIANT = 26
ALLOCATION_CPUS = 80
TIMEOUT = 1200


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def verify_hashes(files):
    for filename, expected in files.items():
        path = Path(filename)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError(f'frozen diagnostic artifact changed: {filename}')


def module(path):
    spec = importlib.util.spec_from_file_location('focus_frozen_helpers', path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


def load_plan(output):
    output = Path(output).resolve()
    if digest(output / 'manifest.json') != (output / 'manifest.sha256').read_text().strip():
        raise ValueError('diagnostic manifest changed')
    plan = read_json(output / 'manifest.json')
    if plan['output'] != str(output):
        raise ValueError('diagnostic was relocated')
    return plan


def helpers(plan):
    path = plan['helper']
    verify_hashes({path: plan['files'][str(Path(path).relative_to(plan['output']))]})
    return module(path)


def verify_plan(output, plan):
    verify_hashes({str(Path(output) / name): sha for name, sha in plan['files'].items()})
    helper = helpers(plan)
    parent = Path(plan['parent_run'])
    if digest(parent / 'manifest.json') != plan['parent_manifest_sha256']:
        raise ValueError('parent manifest changed')
    original = helper.load_plan(parent)
    helper.verify_files(parent, original['files'])
    verify_hashes({plan['python']: plan['python_sha256'], **plan['benchmark_hashes']})


def select_focus(parent, baseline, embedded, result_state):
    indices = parent['regression_indices']
    if set(baseline) != set(indices) or set(embedded) != set(indices):
        raise ValueError('parent paired regression is incomplete')
    return [i for i in indices if result_state(baseline[i]) in ('B', 'C')
            and result_state(embedded[i]) in ('B', 'C')]


def sbatch_text(python, launcher, output):
    return ('#!/bin/bash\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n'
            '#SBATCH --cpus-per-task=80\n#SBATCH --time=35\n'
            '# Set --nodelist and proportional --mem explicitly when submitting.\n'
            'set -euo pipefail\nexport PYTHONDONTWRITEBYTECODE=1\nexec ' +
            shlex.join([str(python), str(launcher), '_execute', str(output)]) + '\n')


def validate_candidate(candidate, embedded20_sha256):
    candidate = Path(candidate)
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError('candidate executable is missing')
    if digest(candidate) == embedded20_sha256:
        raise ValueError('candidate executable is identical to parent embedded20; wait for the new build')
    flags = candidate.parent / 'src/shell/nia_ls/CMakeFiles/nia_ls_embedded.dir/flags.make'
    if not flags.is_file(): raise ValueError('candidate SLS flags.make is missing')
    tokens = flags.read_text().split()
    if '-std=gnu++17' not in tokens: raise ValueError('candidate SLS flags do not select GNU C++17')
    if any(flag in tokens for flag in ('-fPIC', '-fpic')): raise ValueError('candidate SLS flags still enable PIC')
    return flags


def prepare(parent_run, candidate, output=None):
    parent_run, candidate = Path(parent_run).resolve(), Path(candidate).resolve()
    parent = read_json(parent_run / 'manifest.json')
    if digest(parent_run / 'manifest.json') != (parent_run / 'manifest.sha256').read_text().strip():
        raise ValueError('parent manifest changed')
    launcher = Path(parent['launcher'])
    verify_hashes({str(launcher): parent['files'][str(launcher.relative_to(parent_run))]})
    helper = module(launcher)
    helper.verify_files(parent_run, parent['files'])
    baseline = helper.aggregate(parent_run, parent, 'regression', 'baseline')
    embedded = helper.aggregate(parent_run, parent, 'regression', 'embedded')
    if baseline is None or embedded is None:
        raise ValueError('parent regression has not completed')
    indices = select_focus(parent, baseline, embedded, helper.result_state)
    if len(indices) != 15:
        raise ValueError(f'expected the 15 both-B/C parent cases, found {len(indices)}')
    flags = validate_candidate(candidate, parent['variants']['embedded']['executable_sha256'])
    output = Path(output or parent_run.parent /
                  ('NIA_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f'))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    copy = lambda source, name: helper.freeze_file(source, output, 'source/' + name, files)
    frozen_helper = copy(launcher, 'embedding_experiment.py')
    frozen_script = copy(Path(__file__), 'focus_profiles.py')
    candidate_exe = copy(candidate, 'candidate17/solver')
    candidate_lib = output / 'source/candidate17/lib'; candidate_lib.mkdir(parents=True)
    _, reference = helper._reference_modules(parent['reference_script'])
    snapshot = __import__('slurm_snapshot')
    for name, path in sorted(snapshot.native_libraries(candidate).items()):
        copy(path, 'candidate17/lib/' + name)
    # Record available build evidence without mistaking an older receipt for
    # this candidate. The diagnostic identity is always its executable hash.
    tree = Path(__file__).resolve().parents[1]
    receipt = tree / 'embedding-build.json'
    if receipt.is_file() and read_json(receipt).get('executable_sha256') == digest(candidate):
        copy(receipt, 'candidate17/embedding-build.json')
    copy(flags, 'candidate17/flags.make')
    if (tree / 'sls/CMakeLists.txt').is_file(): copy(tree / 'sls/CMakeLists.txt', 'candidate17/CMakeLists.txt')
    variants = dict(baseline=parent['variants']['baseline'], embedded20=parent['variants']['embedded'],
                    candidate17=dict(executable=candidate_exe, executable_sha256=digest(candidate_exe),
                                     library_dir=str(candidate_lib)))
    cases, inputs = [], {}
    for index in indices:
        case = parent['cases'][index]
        paths = []
        for variant in ('baseline', 'embedded'):
            task = next(t for t in parent['tasks'] if t['phase'] == 'regression'
                        and t['variant'] == variant and index in t['indices'])
            local = task['indices'].index(index)
            name = f'{local:04d}-' + hashlib.sha256(case['source'].encode()).hexdigest()[:12]
            paths.append(parent_run / 'tasks' / task['id'] / 'run/cases' / name / 'input.nia')
        if digest(paths[0]) != digest(paths[1]):
            raise ValueError('parent paired native inputs differ')
        native = copy(paths[0], f'inputs/{index:04d}.nia')
        inputs[case['source']] = dict(path=native, sha256=digest(native), global_index=index)
        cases.append(case)
    config = dict(parent['config'], jobs=JOBS_PER_VARIANT, timeout=float(TIMEOUT),
                  use_target=False, stop_at_known_optimum=False, selection_complete=False)
    helper.durable_json(output / 'source/config.json', config, exclusive=True)
    files['source/config.json'] = digest(output / 'source/config.json')
    plan = dict(schema=1, output=str(output), parent_run=str(parent_run),
                parent_manifest_sha256=digest(parent_run / 'manifest.json'),
                evidence_role='same-node native-IR-only compiler-profile diagnostic; not end-to-end full',
                variants=variants, cases=cases, global_indices=indices, inputs=inputs, config=config,
                seed_defaults=parent['seed_defaults'], converter=parent['converter'],
                converter_sha256=parent['converter_sha256'], reference_script=parent['reference_script'],
                python=parent['python'], python_sha256=parent['python_sha256'],
                benchmark_hashes={c['source']: parent['benchmark_hashes'][c['source']] for c in cases},
                helper=frozen_helper, launcher=frozen_script, files=files,
                allocation_cpus=ALLOCATION_CPUS, jobs_per_variant=JOBS_PER_VARIANT,
                maximum_case_concurrency=min(len(cases), JOBS_PER_VARIANT) * len(VARIANTS))
    (output / 'submit.sbatch').write_text(sbatch_text(plan['python'], frozen_script, output))
    copy(output / 'submit.sbatch', 'submit.sbatch')
    helper.durable_json(output / 'manifest.json', plan, exclusive=True)
    (output / 'manifest.sha256').write_text(digest(output / 'manifest.json') + '\n')
    verify_plan(output, plan)
    return output


def stage_adapter(original, variant, executable, converter, inputs):
    def stage(command, deadline, stop, stdout, stderr, *args, **kwargs):
        if len(command) >= 5 and str(command[1]) == str(converter):
            if stop.is_set(): raise InterruptedError('diagnostic interrupted')
            if time.monotonic() >= deadline: raise TimeoutError('per-case wallclock limit reached')
            if command[3] != '-o' or any(x != '--keep-definitions' for x in command[5:]):
                raise ValueError('unexpected reference converter command')
            source = inputs[str(command[2])]
            verify_hashes({source['path']: source['sha256']})
            shutil.copyfile(source['path'], command[4])
            verify_hashes({str(command[4]): source['sha256']})
            Path(stdout).write_bytes(b''); Path(stderr).write_bytes(b'')
            if time.monotonic() >= deadline: raise TimeoutError('per-case IR-copy deadline reached')
            return 0
        if len(command) < 3 or command[2] != '--optimize':
            raise ValueError('unexpected native diagnostic command')
        translated = command if variant == 'baseline' else [str(executable), '--nia-ls', '--native-input', *command[1:]]
        return original(translated, deadline, stop, stdout, stderr, *args, **kwargs)
    return stage


def validate_allocation(environment, host):
    try:
        good = (int(environment['SLURM_CPUS_PER_TASK']) == ALLOCATION_CPUS
                and int(environment['SLURM_NTASKS']) == 1 and int(environment['SLURM_MEM_PER_NODE']) > 0
                and environment['SLURMD_NODENAME'] == host
                and host in ('server', 'node1', 'node2', 'node3', 'node4', 'node5')
                and environment['SLURM_JOB_ID'].isdigit())
    except (KeyError, ValueError):
        good = False
    if not good: raise ValueError('expected one shared 80-CPU Slurm allocation with explicit memory on an allowed node')


def execute(output):
    output = Path(output).resolve(); plan = load_plan(output); verify_plan(output, plan)
    host = socket.gethostname().split('.')[0]; validate_allocation(os.environ, host)
    claim = output / 'execute_started.json'
    descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(dict(job_id=os.environ['SLURM_JOB_ID'], host=host), handle)
        handle.flush(); os.fsync(handle.fileno())
    processes, handles, codes = [], [], []
    began = time.monotonic()
    previous_term = signal.getsignal(signal.SIGTERM)
    stop_requested = False
    def interrupted(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True  # Never interrupt Popen before its child is registered.
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for variant in VARIANTS:
            if stop_requested: raise InterruptedError('profile coordinator received SIGTERM')
            out = (output / (variant + '.stdout')).open('wb')
            err = (output / (variant + '.stderr')).open('wb'); handles.extend([out, err])
            processes.append(subprocess.Popen([plan['python'], plan['launcher'], '_worker', str(output), variant],
                                              stdout=out, stderr=err, start_new_session=True))
            if stop_requested: raise InterruptedError('profile coordinator received SIGTERM')
        for process in processes:
            while True:
                if stop_requested: raise InterruptedError('profile coordinator received SIGTERM')
                remaining = TIMEOUT + 90 - (time.monotonic() - began)
                if remaining <= 0: raise subprocess.TimeoutExpired('profile workers', TIMEOUT + 90)
                try:
                    codes.append(process.wait(timeout=min(.2, remaining))); break
                except subprocess.TimeoutExpired:
                    continue
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            for process in processes:
                if process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        process.wait()
        finally:
            for handle in handles: handle.close()
            signal.signal(signal.SIGTERM, previous_term)
    if any(code != 0 for code in codes): return 2
    report(output)
    return 0


def worker(output, variant):
    if variant not in VARIANTS: raise ValueError('unknown profile variant')
    output = Path(output).resolve(); plan = load_plan(output); verify_plan(output, plan)
    host = socket.gethostname().split('.')[0]; validate_allocation(os.environ, host)
    execution = read_json(output / 'execute_started.json')
    if execution != dict(job_id=os.environ['SLURM_JOB_ID'], host=host):
        raise ValueError('worker is not in the coordinator allocation')
    helper = helpers(plan); runtime = plan['variants'][variant]
    helper.durable_json(output / (variant + '.started.json'), execution, exclusive=True)
    os.environ['LD_LIBRARY_PATH'] = runtime['library_dir']
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['OMP_NUM_THREADS'] = os.environ['OPENBLAS_NUM_THREADS'] = '1'
    runner, launcher = helper._reference_modules(plan['reference_script'])
    runner._stage = stage_adapter(runner._stage, variant, runtime['executable'], plan['converter'], plan['inputs'])
    config = runner.Config(**plan['config'], solver=Path(runtime['executable']), converter=Path(plan['converter']))
    run = output / variant
    code = runner.run_batch([launcher.case_from_record(c) for c in plan['cases']], config, run)
    verify_plan(output, plan)
    local = read_json(run / 'manifest.json')
    local.update(variant=variant, evidence_role=plan['evidence_role'], native_ir_only=True,
                 input_hashes={source: data['sha256'] for source, data in plan['inputs'].items()})
    helper.durable_json(run / 'manifest.json', local)
    helper.durable_json(output / (variant + '.done.json'), dict(returncode=code, **execution,
                        results_sha256=digest(run / 'results.csv')))
    return code


def variant_rows(output, plan, variant, helper):
    done = output / (variant + '.done.json')
    if not done.exists(): return None
    done = read_json(done); run = output / variant
    if done['returncode'] != 0 or digest(run / 'results.csv') != done['results_sha256']:
        raise ValueError('profile worker failed or results changed')
    local = read_json(run / 'manifest.json')
    expected = dict(helper.config_manifest_values(plan['config']),
                    solver_sha256=plan['variants'][variant]['executable_sha256'],
                    converter_sha256=plan['converter_sha256'], cases=plan['cases'],
                    native_ir_only=True, variant=variant,
                    input_hashes={s: d['sha256'] for s, d in plan['inputs'].items()})
    for key, value in expected.items():
        if local.get(key) != value: raise ValueError(f'profile manifest mismatch: {key}')
    with (run / 'results.csv').open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    lookup = {helper._identity(c): i for i, c in zip(plan['global_indices'], plan['cases'])}
    indexed = {}
    for row in rows:
        index = lookup.get(helper._identity(row))
        if index is None or index in indexed: raise ValueError('foreign/duplicate diagnostic result')
        indexed[index] = row
    if set(indexed) != set(plan['global_indices']): raise ValueError('incomplete diagnostic results')
    return indexed


def report(output):
    output = Path(output).resolve(); plan = load_plan(output); verify_plan(output, plan); helper = helpers(plan)
    rows = {v: variant_rows(output, plan, v, helper) for v in VARIANTS}
    if any(value is None for value in rows.values()): return dict(complete=False)
    paired = {v: helper.paired_summary(rows['baseline'], rows[v]) for v in VARIANTS}
    states = {v: Counter(helper.result_state(r) for r in rows[v].values()) for v in VARIANTS}
    cyclic = []
    for local, (index, case) in enumerate(zip(plan['global_indices'], plan['cases'])):
        if case['category'] != 'cyclic': continue
        detail = dict(index=index, filename=case['filename'], variants={})
        for variant in VARIANTS:
            artifact = output / variant / 'cases' / (f'{local:04d}-' + hashlib.sha256(case['source'].encode()).hexdigest()[:12])
            events = []
            for line in (artifact / 'native.stdout').read_text().splitlines():
                try: event = json.loads(line)
                except json.JSONDecodeError: continue
                if isinstance(event, dict) and event.get('event') == 'incumbent': events.append(event)
            stderr = (artifact / 'native.stderr').read_text().splitlines()
            detail['variants'][variant] = dict(result=rows[variant][index],
                initialization=[line for line in stderr if line.startswith(('progressive_stage ', 'progressive_handoff '))],
                incumbent_origins=Counter(e.get('origin') for e in events), first=events[:3], last=events[-3:])
        cyclic.append(detail)
    result = dict(complete=True, evidence_role=plan['evidence_role'], cases=len(plan['cases']),
                  states=states, versus_baseline=paired, cyclic_details=cyclic)
    helper.durable_json(output / 'comparison.json', result)
    lines = ['# Native-IR-only compiler-profile diagnostic', '',
             'All variants shared one 80-CPU Slurm job; no conversion was performed.', '',
             '| Variant | A (A1+A2) | B | C | Solved | W | T | L vs baseline |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for variant in VARIANTS:
        state, pair = states[variant], paired[variant]; solved = state['A1'] + state['A2']
        lines.append('| ' + ' | '.join(map(str, [variant, solved, state['B'], state['C'], solved,
                                              pair['wins'], pair['ties'], pair['losses']])) + ' |')
    lines.extend(['', '| Cyclic case | Baseline | Embedded C++20 | Candidate C++17 |', '| --- | ---: | ---: | ---: |'])
    for detail in cyclic:
        lines.append('| ' + ' | '.join([detail['filename'], *[detail['variants'][v]['result']['best_value'] or 'NA' for v in VARIANTS]]) + ' |')
    (output / 'summary.md').write_text('\n'.join(lines) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--parent-run', type=Path, required=True)
    prep.add_argument('--candidate', type=Path, required=True); prep.add_argument('--output', type=Path)
    for name in ('_execute', '_worker', 'report'):
        cmd = sub.add_parser(name); cmd.add_argument('output', type=Path)
        if name == '_worker': cmd.add_argument('variant', choices=VARIANTS)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare': print(prepare(args.parent_run, args.candidate, args.output)); return 0
        if args.command == '_execute': return execute(args.output)
        if args.command == '_worker': return worker(args.output, args.variant)
        print(json.dumps(report(args.output), indent=2)); return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'focus profiles: {error}', file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
