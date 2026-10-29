#!/usr/bin/env python3
"""Repeated baseline/candidate native-IR diagnostic; never submits Slurm jobs.

Uses the frozen focus_profiles implementation. Default: the two observed losses,
two identical-executable repeats per backend, eight native cases in one 80-CPU
allocation. CPU binding separates diagnostic children; it does not assert that
other jobs cannot use those cores when Slurm's TaskPlugin is null.
"""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def import_focus(path):
    spec = importlib.util.spec_from_file_location('repeat_frozen_focus', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def repeat_plan(parent, indices, repeats):
    if not indices or len(set(indices)) != len(indices) or not 2 <= repeats <= 10:
        raise ValueError('distinct case indices and 2..10 repeats are required')
    lookup = dict(zip(parent['global_indices'], parent['cases']))
    if any(i not in lookup for i in indices): raise ValueError('case is outside frozen focus inputs')
    if len(indices) * repeats * 2 > 80: raise ValueError('diagnostic concurrency exceeds allocation')
    plan = copy.deepcopy(parent)
    plan['global_indices'] = list(indices); plan['cases'] = [lookup[i] for i in indices]
    sources = {c['source'] for c in plan['cases']}
    for key in ('inputs', 'benchmark_hashes'): plan[key] = {k: v for k, v in parent[key].items() if k in sources}
    plan['variants'] = {name if r == 1 else f'{name}_repeat{r}': copy.deepcopy(parent['variants'][name])
                        for name in ('baseline', 'candidate17') for r in range(1, repeats+1)}
    plan['config']['jobs'] = len(indices)
    plan['jobs_per_variant'] = len(indices)
    plan['maximum_case_concurrency'] = len(indices) * repeats * 2
    plan['evidence_role'] = 'same-node pinned repeated-baseline native-IR diagnostic; not end-to-end full'
    return plan


def distinct_cpus(allowed, count, root=Path('/sys/devices/system/cpu')):
    allowed = sorted(allowed)
    if len(allowed) < count: raise ValueError('too few allowed CPUs')
    chosen, seen = [], set()
    try:
        for cpu in allowed:
            path = Path(root) / f'cpu{cpu}' / 'topology'
            core = (int((path/'physical_package_id').read_text()), int((path/'core_id').read_text()))
            if core not in seen: chosen.append(cpu); seen.add(core)
            if len(chosen) == count: return chosen, True
    except (OSError, ValueError): pass
    return allowed[:count], False


def allocation_cpus(text, host):
    rows = re.findall(r'Nodes=(\S+)\s+CPU_IDs=(\S+)', text)
    if len(rows) != 1 or rows[0][0] != host: raise ValueError('expected CPU_IDs for the exact allocated node')
    cpus = set()
    for token in rows[0][1].split(','):
        if not re.fullmatch(r'\d+(?:-\d+)?', token): raise ValueError('invalid allocation CPU_IDs')
        bounds = list(map(int, token.split('-')))
        cpus.update(range(bounds[0], bounds[-1]+1))
    if len(cpus) != 80: raise ValueError('expected exactly 80 allocated CPU_IDs')
    return cpus


def pinned_stage(original, native_to_cpu):
    def stage(command, deadline, stop, stdout, stderr, *args, **kwargs):
        native = str(command[command.index('--optimize') - 1])
        cpu = native_to_cpu[native]; previous = os.sched_getaffinity(0)
        if cpu not in previous: raise ValueError('selected CPU is outside worker allowed affinity')
        receipt = Path(stderr).parent / 'native-affinity.json'
        os.sched_setaffinity(0, {cpu})
        try:
            actual = sorted(os.sched_getaffinity(0))
            if actual != [cpu]:
                raise ValueError('native affinity mask was not applied')
            receipt.write_text(json.dumps(dict(cpu=cpu, before=sorted(previous), applied=actual,
                                              scope='calling thread and inherited native descendants')) + '\n')
            return original(command, deadline, stop, stdout, stderr, *args, **kwargs)
        finally:
            os.sched_setaffinity(0, previous)
    return stage


def prepare(parent, candidate, output, indices, repeats):
    focus = import_focus(Path(__file__).with_name('focus_profiles.py'))
    output = focus.prepare(parent, candidate, output)
    plan = repeat_plan(focus.load_plan(output), indices, repeats)
    helper = focus.helpers(plan)
    launcher = helper.freeze_file(Path(__file__), output, 'source/repeat_profiles.py', plan['files'])
    plan['focus_launcher'] = plan['launcher']; plan['launcher'] = launcher
    helper.durable_json(output/'source/config.json', plan['config'])
    plan['files']['source/config.json'] = focus.digest(output/'source/config.json')
    (output/'submit.sbatch').write_text(focus.sbatch_text(plan['python'], launcher, output))
    # freeze_file refuses an existing destination; update this new run's copy.
    (output/'source/submit.sbatch').write_text((output/'submit.sbatch').read_text())
    plan['files']['source/submit.sbatch'] = focus.digest(output/'source/submit.sbatch')
    helper.durable_json(output/'manifest.json', plan)
    (output/'manifest.sha256').write_text(focus.digest(output/'manifest.json')+'\n')
    focus.verify_plan(output, plan)
    return output


def configure(output):
    output = Path(output).resolve()
    focus = import_focus(output/'source/focus_profiles.py')
    plan = focus.load_plan(output); focus.VARIANTS = tuple(plan['variants'])
    return focus, plan


def affinity_plan(output, focus, plan):
    host = focus.socket.gethostname().split('.')[0]
    focus.validate_allocation(os.environ, host)
    if (output/'execute_started.json').exists(): raise ValueError('diagnostic already started')
    allowed = os.sched_getaffinity(0)
    control = subprocess.run(['scontrol', 'show', 'job', '-d', os.environ['SLURM_JOB_ID']],
                             check=True, text=True, capture_output=True, timeout=15)
    allocated = allocation_cpus(control.stdout, host)
    if not allocated <= allowed: raise ValueError('allocated CPU_IDs are outside current allowed affinity')
    cpus, physical = distinct_cpus(allocated, plan['maximum_case_concurrency'])
    assignments = {}
    for variant in plan['variants']:
        assignments[variant] = {case['source']: cpus.pop(0) for case in plan['cases']}
    value = dict(allowed_cpus=sorted(allowed), allocated_cpu_ids=sorted(allocated),
                 allocation_cpus=80, scontrol_job=control.stdout,
                 distinct_physical_cores=physical, assignments=assignments,
                 physical_scope='distinct diagnostic native processes only',
                 other_job_exclusivity_verified=False,
                 warning='CPU selection uses Slurm CPU_IDs intersected with allowed affinity; no cross-job exclusivity claim')
    focus.helpers(plan).durable_json(output/'affinity.json', value, exclusive=True)


def worker(output, variant, focus, plan):
    assignments = focus.read_json(output/'affinity.json')['assignments'][variant]
    base_adapter = focus.stage_adapter
    def adapter(original, name, executable, converter, inputs):
        native_to_cpu = {}
        backend = 'baseline' if name.startswith('baseline') else 'candidate17'
        staged = base_adapter(pinned_stage(original, native_to_cpu), backend, executable, converter, inputs)
        def stage(command, *args, **kwargs):
            if len(command) >= 5 and str(command[1]) == str(converter):
                native_to_cpu[str(command[4])] = assignments[str(command[2])]
            return staged(command, *args, **kwargs)
        return stage
    focus.stage_adapter = adapter
    return focus.worker(output, variant)


def report(output, focus, plan):
    result = focus.report(output)
    if not result.get('complete'): return result
    lines = ['# Repeated-baseline native-IR diagnostic', '',
             'Identical executable/IR/seed repeats; each native case pinned separately. No cross-job exclusivity claim.', '',
             '| Variant | A1+A2 | B | C | W | T | L vs first baseline |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for variant in plan['variants']:
        states = result['states'][variant]; pair = result['versus_baseline'][variant]
        values = [variant, states.get('A1', 0)+states.get('A2', 0), states.get('B', 0), states.get('C', 0),
                  pair['wins'], pair['ties'], pair['losses']]
        lines.append('| '+' | '.join(map(str, values))+' |')
    lines += ['', '| Case | '+' | '.join(plan['variants'])+' |', '| --- | '+' | '.join(['---:']*len(plan['variants']))+' |']
    helper = focus.helpers(plan)
    rows = {v: focus.variant_rows(output, plan, v, helper) for v in plan['variants']}
    for index, case in zip(plan['global_indices'], plan['cases']):
        lines.append('| '+case['filename']+' | '+' | '.join(rows[v][index]['best_value'] or 'NA' for v in plan['variants'])+' |')
    (output/'summary.md').write_text('\n'.join(lines)+'\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--parent-run', type=Path, required=True)
    prep.add_argument('--candidate', type=Path, required=True); prep.add_argument('--output', type=Path)
    prep.add_argument('--case-indices', type=int, nargs='+', default=[5746, 6100])
    prep.add_argument('--repeats', type=int, default=2)
    for name in ('_execute', '_worker', 'report'):
        cmd = sub.add_parser(name); cmd.add_argument('output', type=Path)
        if name == '_worker': cmd.add_argument('variant')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            print(prepare(args.parent_run, args.candidate, args.output, args.case_indices, args.repeats)); return 0
        output = args.output.resolve(); focus, plan = configure(output)
        if args.command == '_worker': return worker(output, args.variant, focus, plan)
        if args.command == '_execute':
            affinity_plan(output, focus, plan)
            code = focus.execute(output)
            if code: return code
        print(json.dumps(report(output, focus, plan), indent=2)); return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'repeat profiles: {error}', file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
