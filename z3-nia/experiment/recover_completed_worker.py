#!/usr/bin/env python3
"""Explicitly finish metadata after a fully recorded batch failed in temp cleanup.

`check` is read-only. `recover --apply` never runs a solver, changes results.csv,
or edits frozen inputs. A recovered worker records the original cleanup error;
its returncode=0 describes this recovery, not the original Slurm job's exit.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


COMMIT = '6614f3c1ea13539918b6f641c64446b753e6db0e'
RETURN_RULE = "130 if len(rows) < len(cases) or any(row['status'] == 'interrupted' for row in rows) else 0"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def load_helper(output):
    """Check the manifest and helper before executing any frozen Python code."""
    # Frozen helper/reference imports must stay read-only even without `-B`.
    sys.dont_write_bytecode = True
    output = Path(output).resolve()
    manifest = output / 'manifest.json'
    if digest(manifest) != (output / 'manifest.sha256').read_text().strip():
        raise ValueError('experiment manifest changed')
    plan = json.loads(manifest.read_text())
    path = output / 'source/harness/embedding_experiment.py'
    relative = str(path.relative_to(output))
    if Path(plan['launcher']) != path or path.is_symlink() or digest(path) != plan['files'].get(relative):
        raise ValueError('frozen helper mismatch')
    name = 'cleanup_recovery_helper_' + hashlib.sha256(str(output).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    helper = importlib.util.module_from_spec(spec); sys.modules[name] = helper; spec.loader.exec_module(helper)
    return helper


def queue_snapshot(helper):
    # A scheduler query failure propagates; missing observations never mean idle.
    return helper.queue_jobs()


def return_contract(source):
    tree = ast.parse(Path(source).read_text())
    function = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_batch'), None)
    outer = next((n for n in function.body if isinstance(n, ast.Try)), None) if function else None
    expected = ast.dump(ast.parse(RETURN_RULE, mode='eval').body)
    if (outer is None or not isinstance(outer.body[0], ast.With) or
            not isinstance(outer.body[-1], ast.Return) or
            ast.dump(outer.body[-1].value) != expected):
        raise ValueError('frozen runner has an unknown post-cleanup return contract')
    item = outer.body[0].items[0].context_expr
    if (not isinstance(item, ast.Call) or ast.unparse(item.func) != 'tempfile.TemporaryDirectory' or
            not any(k.arg == 'prefix' and isinstance(k.value, ast.Constant) and
                    k.value.value == 'nia-opt-run-' for k in item.keywords)):
        raise ValueError('frozen runner cleanup context differs')
    return dict(source_sha256=digest(source), expression=RETURN_RULE,
                inference='all assigned rows present and no interrupted status imply zero after cleanup')


def collect(output, task_id):
    output = Path(output).resolve(); helper = load_helper(output); plan = helper.load_plan(output)
    if plan.get('baseline_commit') != COMMIT:
        raise ValueError('only the pinned committed baseline contract is supported')
    task = next((t for t in plan['tasks'] if t['id'] == task_id), None)
    if task is None or not task['indices']:
        raise ValueError('unknown or empty task')
    where = output / 'tasks' / task_id; run = where / 'run'
    if where.resolve() != where or run.resolve() != run:
        raise ValueError('task directories must not be symlinks')
    for name in ('worker_done.json', 'cleanup_recovery.json'):
        if (where / name).exists(): raise ValueError('completion or recovery already recorded')
    for name in ('summary.json', 'manifest.before-recovery.json'):
        if (run / name).exists(): raise ValueError('task already has post-run metadata')
    helper.verify_plan(output, plan, task['indices'])
    intent = helper.read_json(where / 'intent.json'); receipt = helper.read_json(where / 'receipt.json')
    started = helper.read_json(where / 'worker_started.json'); job_id = receipt['job_id']
    expected_comment = 'z3nia:' + plan['id'] + ':' + task_id
    if (not re.fullmatch(r'[0-9]+', job_id) or intent['plan_id'] != plan['id'] or
            intent['plan_sha256'] != digest(output / 'manifest.json') or
            intent['task_id'] != task_id or intent['workers'] != plan['workers'] or
            intent['node'] not in plan['nodes'] or intent['comment'] != expected_comment or
            started['job_id'] != job_id or started['task_id'] != task_id or
            started['node'] != intent['node'] or started['plan_sha256'] != intent['plan_sha256']):
        raise ValueError('submission/start records disagree with the frozen assignment')
    jobs = queue_snapshot(helper)
    if any(j['job_id'] == job_id or j.get('comment') == expected_comment for j in jobs):
        raise ValueError('the original job or its exact submission comment is still in the queue')
    stdout, stderr = where / f'slurm-{job_id}.out', where / f'slurm-{job_id}.err'
    error = stderr.read_text()
    match = re.fullmatch("embedding experiment: \\[Errno 39\\] Directory not empty: '(/[^'\\n]*/nia-opt-run-[A-Za-z0-9_]+)'\\n?", error)
    if match is None: raise ValueError('only the exact post-run ENOTEMPTY cleanup error is recoverable')
    runtime = plan['variants'][task['variant']]
    if (digest(runtime['executable']) != runtime['executable_sha256'] or
            digest(plan['converter']) != plan['converter_sha256']):
        raise ValueError('runtime or converter differs from the frozen plan')
    reference = Path(plan['reference_script'])
    for name in ('run_optimize.py', 'run_optimize_slurm.py', 'smtlib.py', 'slurm_snapshot.py'):
        path = reference / name
        if not path.is_relative_to(output / 'source') or str(path.relative_to(output)) not in plan['files']:
            raise ValueError('reference runner dependency is not a frozen artifact')
    contract = return_contract(reference / 'run_optimize.py')
    runner, launcher = helper._reference_modules(reference)
    cases = [plan['cases'][i] for i in task['indices']]
    original = (run / 'manifest.json').read_bytes(); local = json.loads(original)
    expected = dict(helper.config_manifest_values(plan['config']), cases=cases,
                    solver=runtime['executable'], converter=plan['converter'],
                    solver_sha256=runtime['executable_sha256'], converter_sha256=plan['converter_sha256'])
    if set(local) != set(expected) | {'started_utc'}:
        raise ValueError('initial run manifest has unexpected or missing fields')
    for key, value in expected.items():
        if local[key] != value or (isinstance(value, bool) and type(local[key]) is not bool):
            raise ValueError('initial run manifest configuration mismatch: ' + key)
    if not isinstance(local['started_utc'], str) or not local['started_utc']:
        raise ValueError('run manifest lacks its start timestamp')
    with (run / 'results.csv').open(newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(runner.FIELDS): raise ValueError('result schema differs from the frozen runner')
        rows = list(reader)
    lookup = {helper._identity(c): c for c in cases}; seen = set()
    if len(rows) != len(cases) or len(lookup) != len(cases): raise ValueError('incomplete assigned case coverage')
    lines = []
    for index, row in enumerate(rows, 1):
        identity = helper._identity(row); case = lookup.get(identity)
        if (case is None or identity in seen or row['direction'] != case['direction'] or
                row['status'] == 'interrupted' or None in row or any(v is None for v in row.values())):
            raise ValueError('interrupted, duplicate, foreign or inconsistent result row')
        seen.add(identity)
        detail = (f"unbounded_proved {row['best_value']}" if plan['config']['prove_unbounded'] and row['is_unbounded'] == 'true' else
                  f"{row['status']} {row['best_value']} {row['vs_best']}")
        lines.append(f"[{index}/{len(cases)}] {detail}: {row['filename']}")
    if stdout.read_text().splitlines() != lines:
        raise ValueError('Slurm stdout does not exactly match every CSV row in completion order')
    evidence_files = dict(original_manifest=run / 'manifest.json', results_csv=run / 'results.csv',
                          slurm_stdout=stdout, slurm_stderr=stderr, intent=where / 'intent.json',
                          receipt=where / 'receipt.json', worker_started=where / 'worker_started.json')
    record = dict(schema=1, kind='post-run-cleanup-recovery', task_id=task_id, job_id=job_id,
                  node=intent['node'], utc=helper.utc(), plan_sha256=intent['plan_sha256'],
                  original_worker_outcome='post-run-cleanup-error', original_error=error.rstrip('\n'),
                  scheduler_observation='original job and exact comment absent from successful squeue query',
                  scheduler_terminal_state='not available; no normal Slurm exit is asserted', queue_snapshot=jobs,
                  cleanup_directory=match.group(1), completed_cases=len(rows), assigned_cases=len(cases),
                  post_cleanup_returncode=0, return_contract=contract,
                  files={name: dict(path=str(path), sha256=digest(path)) for name, path in evidence_files.items()},
                  recovery_tool_sha256=digest(__file__),
                  interpretation='returncode=0 belongs to the explicit recovery, not to the original job')
    return dict(helper=helper, plan=plan, task=task, local=local, original=original,
                rows=rows, runner=runner, launcher=launcher, record=record)


def check(output, task_id):
    return collect(output, task_id)['record']


def recover(output, task_id):
    output = Path(output).resolve(); data = collect(output, task_id)
    helper, plan, task = (data[k] for k in ('helper', 'plan', 'task'))
    where = output / 'tasks' / task_id; run = where / 'run'
    record = data['record']; record.update(state='validated_before_metadata_recovery')
    # Both are exclusive: an interrupted recovery requires explicit inspection.
    with (run / 'manifest.before-recovery.json').open('xb') as f:
        f.write(data['original']); f.flush(); os.fsync(f.fileno())
    helper.durable_json(where / 'cleanup_recovery.json', record, exclusive=True)
    local = dict(data['local']); runtime = plan['variants'][task['variant']]
    local.update(variant=task['variant'], plan_id=plan['id'], global_indices=task['indices'],
                 actual_converter='embedded C++ importer' if task['variant'] == 'embedded' else 'HEAD Python converter',
                 actual_converter_sha256=runtime['executable_sha256'] if task['variant'] == 'embedded' else plan['converter_sha256'],
                 seed_defaults=plan['seed_defaults'],
                 worker_recovery=dict(kind='recovered', record='../cleanup_recovery.json',
                                      original_worker_outcome='post-run-cleanup-error'))
    helper.validate_local_manifest(plan, task, local)
    config = data['runner'].Config(**plan['config'], solver=Path(runtime['executable']), converter=Path(plan['converter']))
    case_objects = [data['launcher'].case_from_record(plan['cases'][i]) for i in task['indices']]
    summary = data['runner']._summary(case_objects, data['rows'], config)
    helper.durable_json(run / 'summary.json', summary)
    helper.durable_json(run / 'manifest.json', local)
    helper.verify_plan(output, plan, task['indices'])
    for name, evidence in record['files'].items():
        path = run / 'manifest.before-recovery.json' if name == 'original_manifest' else Path(evidence['path'])
        if digest(path) != evidence['sha256']: raise ValueError('evidence changed during recovery: ' + name)
    if any(j['job_id'] == record['job_id'] or j.get('comment') == 'z3nia:'+plan['id']+':'+task_id
           for j in queue_snapshot(helper)):
        raise ValueError('original job reappeared during recovery')
    record.update(state='metadata_recovered', recovery_completed_utc=helper.utc())
    helper.durable_json(where / 'cleanup_recovery.json', record)
    done = dict(kind='recovered', returncode=0, job_id=record['job_id'], task_id=task_id, utc=helper.utc(),
                original_worker_outcome='post-run-cleanup-error', recovery_record='cleanup_recovery.json',
                recovery_record_sha256=digest(where / 'cleanup_recovery.json'),
                results_sha256=record['files']['results_csv']['sha256'])
    helper.durable_json(where / 'worker_done.json', done, exclusive=True)
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('check', 'recover'):
        p = sub.add_parser(command); p.add_argument('output', type=Path); p.add_argument('task_id')
        if command == 'recover': p.add_argument('--apply', required=True, action='store_true')
    args = parser.parse_args(argv)
    try:
        value = check(args.output, args.task_id) if args.command == 'check' else recover(args.output, args.task_id)
        print(json.dumps(value, indent=2)); return 0
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        print(f'cleanup recovery refused: {error}', file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
