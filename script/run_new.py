#!/usr/bin/env python3
"""Run the flat application OMT(NIA) suite with the existing bounded scheduler."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys

import run as runner
from smtlib import objective_sense, parse

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_ROOT = ROOT/'new_benchmark'
FAMILIES = ('portfolio','cyclic','packing','design')


def _checked_name(root, family, source):
    path = Path(source).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix!='.smt2':
        raise ValueError(f'Invalid or escaping benchmark: {source}')
    relative = path.relative_to(root)
    if len(relative.parts)!=3 or relative.parts[:2]!=(family,'instance'):
        raise ValueError(f'Expected a flat {family}/instance/*.smt2 path: {source}')
    return relative.as_posix()


def collect_inputs(root, families, new_only=False):
    root = Path(root).resolve()
    names, tags = set(), {}
    for family in families:
        if family not in FAMILIES:
            raise ValueError('Unknown application: '+family)
        tier_map = {}
        tiers_file = root/family/'src/scaled_tiers.json'
        if tiers_file.is_file():
            for tier in json.loads(tiers_file.read_text()).get('tiers',[]):
                for name in tier.get('instances',[]):
                    if name in tier_map and tier_map[name]!=tier['tier']:
                        raise ValueError('Conflicting scale tiers: '+name)
                    tier_map[name] = tier['tier']
        if new_only:
            listing = root/family/'src/scaled_instances.txt'
            if not listing.is_file():
                raise ValueError('New-batch input list missing: '+str(listing))
            sources = [root/line.strip() for line in listing.read_text().splitlines()
                       if line.strip() and not line.lstrip().startswith('#')]
        else:
            sources = sorted((root/family/'instance').glob('*.smt2'))
        for source in sources:
            name = _checked_name(root,family,source)
            if name in names:
                continue
            text = (root/name).read_text()
            forms = parse(text)
            effective=[]
            for command in forms:
                if not isinstance(command,list) or not command:
                    raise ValueError('Expected SMT commands: '+name)
                if command[0]=='exit':
                    break
                effective.append(command)
            if [x for x in effective if x[0]=='set-logic'] != [['set-logic','QF_NIA']]:
                raise ValueError('Only QF_NIA is accepted: '+name)
            if any(x[0]=='declare-fun' and (len(x)!=4 or x[2]!=[] or x[3] not in ('Int','Bool')) for x in effective):
                raise ValueError('Non-integer arithmetic declaration: '+name)
            sense = objective_sense(text)
            names.add(name)
            tags[name] = {'family':family,'scale_tier':tier_map.get(name,'existing'),
                          'objective_sense':sense,'theory':'NIA'}
    if not names:
        raise ValueError('No application benchmarks selected')
    return sorted(names), tags


def make_config(solver, benchmark_root, tags):
    jobs = int(os.environ.get('JOBS') or '180')
    timeout = float(os.environ.get('TIMEOUT') or '1200')
    if jobs<1:
        raise ValueError('JOBS must be a positive integer')
    if not math.isfinite(timeout) or timeout<0:
        raise ValueError('TIMEOUT must be finite and nonnegative (0 disables timeout)')
    output = Path(os.environ.get('RESULT_ROOT') or ROOT/'result/new_benchmark').resolve()
    return runner.Config(solver,Path(benchmark_root).resolve(),[],output,jobs,timeout,
                         case_tags=tags,memory_mb=runner.load_memory_limit())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('solver',choices=('z3','optimathsat'))
    parser.add_argument('family',nargs='?',choices=('all',*FAMILIES),default='all')
    parser.add_argument('--new-only',action='store_true',help='Only the new 480-instance scaled batch')
    parser.add_argument('--dry-run',action='store_true',help='Check inputs and budgets without running or writing results')
    args=parser.parse_args(argv)
    try:
        families = FAMILIES if args.family=='all' else (args.family,)
        inputs,tags = collect_inputs(BENCHMARK_ROOT,families,args.new_only)
        config=make_config(args.solver,BENCHMARK_ROOT,tags)
        counts=Counter((tag['family'],tag['scale_tier']) for tag in tags.values())
        print(f'Solver: {args.solver}; inputs: {len(inputs)}; parallel jobs: {config.jobs}; timeout: {config.timeout:g}s')
        if config.memory_mb:
            print(f'Worker memory limit: {config.memory_mb} MiB virtual address space')
        print('Groups: '+', '.join(f'{family}/{tier}={n}' for (family,tier),n in sorted(counts.items())))
        print(f'Result parent: {config.result_root/args.solver}')
        if args.dry_run:
            print('Dry run: no workers started and no result directory created.')
            return 0
        executable=ROOT/'competitor/bin'/args.solver
        if not executable.is_file() or not os.access(executable,os.X_OK):
            raise ValueError('Solver executable missing: '+str(executable))
        if args.solver=='z3' and not (ROOT/'competitor/z3-5.1.0/install/lib/python3.12/site-packages/z3').is_dir():
            raise ValueError('Locally built Z3 Python binding is missing')
        if not runner.WORKER.is_file():
            raise ValueError('Worker missing: '+str(runner.WORKER))
        directory=runner.create_run_directory(config)
        listing=directory/'inputs.txt'
        listing.write_text('\n'.join(inputs)+'\n')
        config.lists=[listing]
        (directory/'selection.json').write_text(json.dumps({
            'benchmark_root':str(config.benchmark_root),'families':list(families),
            'new_only':args.new_only,'input_count':len(inputs),
            'scale_tier_note':'Parameter-size groups, not measured difficulty.',
            'groups':[{'family':f,'scale_tier':t,'count':n} for (f,t),n in sorted(counts.items())]
        },indent=2)+'\n')
        print(f'Output: {directory}',flush=True)
        return runner.run_batch(config,inputs,directory)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print(f'run_new.sh: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
