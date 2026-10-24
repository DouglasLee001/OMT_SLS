#!/usr/bin/env python3
"""Rerun original and new OMT(NIA) inputs, retaining final infinity reports."""
import argparse
import math
import os
from pathlib import Path
import sys

import run as runner
import run_new


ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_ROOT = Path('/data/shared/lbh/OMT/benchmarks')
NEW_ROOT = ROOT / 'new_benchmark'
DEFAULT_OUTPUT = Path('/data/shared/lbh/OMT/unbounded')


def collect_suites(solver, selection, output_root, jobs, timeout):
    suites = []
    if selection in ('all', 'original'):
        config = runner.Config(
            solver, ORIGINAL_ROOT, [ORIGINAL_ROOT / 'nia_benchmarks_subset.txt'],
            output_root / 'original', jobs, timeout)
        inputs = runner.load_benchmarks(config)
        if any(not name.startswith('QF_NIA/') for name in inputs):
            raise ValueError('Original input list must contain only QF_NIA/ instances')
        suites.append(('original', config, inputs))
    if selection in ('all', 'new'):
        inputs, tags = run_new.collect_inputs(NEW_ROOT, run_new.FAMILIES)
        config = runner.Config(solver, NEW_ROOT, [], output_root / 'new', jobs,
                               timeout, case_tags=tags)
        suites.append(('new', config, inputs))
    return suites


def check_solver(solver):
    executable = ROOT / 'competitor/bin' / solver
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f'Solver executable missing: {executable}')
    if not runner.WORKER.is_file():
        raise ValueError(f'Worker missing: {runner.WORKER}')
    if solver == 'z3' and not (
            ROOT / 'competitor/z3-5.1.0/install/lib/python3.12/site-packages/z3').is_dir():
        raise ValueError('Locally built Z3 Python binding is missing')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('solver', choices=('z3', 'optimathsat'))
    parser.add_argument('--suite', choices=('all', 'original', 'new'), default='all')
    parser.add_argument('--jobs', type=int, default=180)
    parser.add_argument('--timeout', type=float, default=1200,
                        help='Positive wall-clock seconds per instance (default: 1200)')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--dry-run', action='store_true',
                        help='Validate selections without workers or output directories')
    args = parser.parse_args(argv)
    try:
        if args.jobs < 1:
            raise ValueError('--jobs must be positive')
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('--timeout must be finite and positive')
        suites = collect_suites(args.solver, args.suite, args.output_root.resolve(),
                                args.jobs, args.timeout)
        print(f'Solver: {args.solver}; total inputs: {sum(len(s[2]) for s in suites)}; '
              f'parallel jobs: {args.jobs}; timeout: {args.timeout:g}s', flush=True)
        print('No added per-worker memory limit. Final infinity reports are retained.', flush=True)
        for name, config, inputs in suites:
            print(f'{name}: {len(inputs)} inputs; result parent: '
                  f'{config.result_root / args.solver}', flush=True)
        if args.dry_run:
            print('Dry run: no workers started and no result directory created.', flush=True)
            return 0
        check_solver(args.solver)
        exit_code = 0
        for name, config, inputs in suites:
            directory = runner.create_run_directory(config)
            print(f'Starting {name}; output: {directory}', flush=True)
            code = runner.run_batch(config, inputs, directory, preserve_optimality=True)
            if code == 130:
                return code
            exit_code = max(exit_code, code)
        return exit_code
    except (ValueError, OSError, KeyError) as exc:
        print(f'run_unbounded.sh: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
