#!/usr/bin/env python3
"""Run NIA_LS until its first validated feasible model, without optimization."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ROOT = Path('/data/shared/lbh/OMT/benchmarks')
FIELDS = ('filename', 'category', 'status', 'initial_value',
          'time_to_first_seconds', 'error')


@dataclass(frozen=True)
class InputCase:
    source: Path
    filename: str
    category: str


@dataclass(frozen=True)
class Config:
    jobs: int = 180
    timeout: float = 1200.0
    solver: Path = ROOT / 'NIA_LS/src/nia_ls'
    converter: Path = ROOT / 'NIA_LS/converter/omt_to_ls.py'


def make_parser(environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', nargs='?', choices=('all', 'original', 'new'), default='all')
    parser.add_argument('--jobs', type=int, default=environ.get('JOBS') or 180)
    parser.add_argument('--timeout', type=float, default=environ.get('TIMEOUT') or 1200,
                        help='total seconds per case, including conversion (default: 1200)')
    parser.add_argument('--output', type=Path, help='new output directory; must not already exist')
    parser.add_argument('--input', action='append', type=Path,
                        help='run only these SMT2 files (repeatable, replaces suite selection)')
    parser.add_argument('--limit', type=int, help='only the first N selected cases, for a smoke run')
    parser.add_argument('--dry-run', action='store_true', help='list selection and budgets without running')
    return parser


def collect_inputs(suite, root=ROOT, original_root=ORIGINAL_ROOT):
    root, original_root = Path(root).resolve(), Path(original_root).resolve()
    cases = []
    seen = set()
    if suite in ('all', 'original'):
        listing = root / 'result/original/original.csv'
        with listing.open(newline='') as handle:
            for row in csv.DictReader(handle):
                name = row.get('filename', '')
                source = (original_root / name).resolve()
                if (not name or not source.is_relative_to(original_root)
                        or not source.is_file() or source.suffix != '.smt2'):
                    raise ValueError(f'Invalid original benchmark in {listing}: {name}')
                if source not in seen:
                    cases.append(InputCase(source, source.relative_to(original_root).as_posix(), 'original'))
                    seen.add(source)
    if suite in ('all', 'new'):
        for source in sorted((root / 'new_benchmark').glob('*/instance/*.smt2')):
            source = source.resolve()
            if not source.is_relative_to(root / 'new_benchmark'):
                raise ValueError(f'Escaping generated benchmark: {source}')
            if source not in seen:
                cases.append(InputCase(source, source.relative_to(root).as_posix(), source.parent.parent.name))
                seen.add(source)
    if not cases:
        raise ValueError('No benchmarks selected')
    return cases


def _explicit_case(path):
    source = path.resolve()
    if not source.is_file() or source.suffix != '.smt2':
        raise ValueError(f'Expected an existing SMT2 input: {path}')
    if source.is_relative_to(ORIGINAL_ROOT):
        return InputCase(source, source.relative_to(ORIGINAL_ROOT).as_posix(), 'original')
    if source.is_relative_to(ROOT / 'new_benchmark'):
        return InputCase(source, source.relative_to(ROOT).as_posix(), source.parent.parent.name)
    return InputCase(source, str(source), 'custom')


def _kill_group(process):
    """Always clean up the process group, including children of an exited parent."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _stage(command, deadline, stop, stdout, stderr):
    if stop.is_set():
        raise InterruptedError('run interrupted')
    if time.monotonic() >= deadline:
        raise TimeoutError('per-case wallclock limit reached')
    with stdout.open('wb') as output, stderr.open('wb') as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, start_new_session=True)
        try:
            while True:
                if stop.is_set():
                    raise InterruptedError('run interrupted')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('per-case wallclock limit reached')
                try:
                    return process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _kill_group(process)


def _tail(path, limit=1200):
    if not path.is_file():
        return ''
    with path.open('rb') as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode('utf-8', errors='replace').strip()


def _native_report(path):
    # Existing local-search diagnostics can precede the final machine-readable line.
    for line in reversed(_tail(path, 65536).splitlines()):
        try:
            report = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(report, dict) and 'status' in report:
            return report
    raise ValueError('NIA_LS did not return a JSON status record')


def run_case(case, config, stop):
    result = dict(zip(FIELDS, (case.filename, case.category, 'error', '', '', '')))
    start = time.monotonic()
    deadline = start + config.timeout
    stage = 'conversion'
    with tempfile.TemporaryDirectory(prefix='nia-first-case-') as temporary:
        directory = Path(temporary)
        converted, model = directory / 'input.nia', directory / 'model.tsv'
        output, errors = directory / 'stdout', directory / 'stderr'
        try:
            command = [sys.executable, str(config.converter), str(case.source), '-o', str(converted)]
            # Keep the original frontend/search path for every other source.
            if case.category == 'original' and case.filename.startswith('QF_NIA/leipzig/'):
                command.append('--keep-definitions')
            code = _stage(command, deadline, stop, output, errors)
            if code:
                raise ValueError(f'converter exit {code}: {_tail(errors) or _tail(output)}')
            if not converted.is_file():
                raise ValueError('converter did not write an intermediate input')
            stage = 'initial solution'
            remaining = deadline - time.monotonic()
            native_started = time.monotonic()
            code = _stage([str(config.solver), str(converted), '--first-solution',
                           f'--timeout={max(remaining, 0.000001):.9f}', f'--model-output={model}'],
                          deadline, stop, output, errors)
            native_observed = time.monotonic() - native_started
            if code:
                detail = ''
                try:
                    failure = _native_report(output)
                    detail = str(failure.get('error') or failure.get('detail') or failure.get('message') or '')
                except ValueError:
                    pass
                raise ValueError(f'NIA_LS exit {code}: {detail or _tail(errors) or _tail(output)}')
            report = _native_report(output)
            status = report['status']
            if status not in ('sat', 'unknown', 'unsat', 'error'):
                raise ValueError(f'Unexpected NIA_LS status: {status!r}')
            if status == 'sat':
                value = report.get('objective_value')
                if (report.get('model_validated') is not True or not isinstance(value, str)
                        or not value or not value.lstrip('-').isdigit()):
                    raise ValueError('NIA_LS SAT record has no validated model and exact objective value')
                elapsed = report.get('elapsed_seconds')
                if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                        or not math.isfinite(elapsed) or elapsed < 0
                        or elapsed > native_observed + 0.01):
                    raise ValueError('NIA_LS SAT record has an invalid elapsed_seconds time')
                # Native timing ends after exact validation/model output. The
                # parent may notice exit later, especially with 180 active jobs.
                # Include conversion/startup, but exclude that polling tail.
                result.update(status='sat', initial_value=value,
                              time_to_first_seconds=f'{native_started - start + elapsed:.6f}')
            else:
                result['status'] = status
                if status == 'error':
                    result['error'] = str(report.get('error') or report.get('message') or _tail(errors) or 'NIA_LS error')[:1600]
        except TimeoutError:
            result.update(status='timeout', error=f'{stage}: {config.timeout:g}s total wallclock limit')
        except InterruptedError:
            result.update(status='interrupted', error=f'{stage}: run interrupted')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status='error', error=f'{stage}: {exc}'[:1600])
    return result


def run_batch(cases, config, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda _signum, _frame: stop.set())
    counts = Counter()
    completed = 0
    try:
        # One immutable executable per run prevents a rebuild changing later cases.
        with tempfile.TemporaryDirectory(prefix='nia-first-run-') as temporary:
            snapshot = Path(temporary) / 'nia_ls'
            shutil.copy2(config.solver, snapshot)
            actual = replace(config, solver=snapshot)
            with (output / 'results.csv').open('x', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                handle.flush()
                with ThreadPoolExecutor(max_workers=config.jobs) as pool:
                    pending = {}
                    inputs = iter(cases)

                    def submit_next():
                        if stop.is_set():
                            return
                        case = next(inputs, None)
                        if case is not None:
                            pending[pool.submit(run_case, case, actual, stop)] = case

                    for _ in range(min(config.jobs, len(cases))):
                        submit_next()
                    try:
                        while pending:
                            ready, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                            for future in ready:
                                case = pending.pop(future)
                                try:
                                    row = future.result()
                                except Exception as exc:
                                    row = dict(zip(FIELDS, (case.filename, case.category, 'error', '', '', str(exc)[:1600])))
                                writer.writerow(row)
                                handle.flush()
                                completed += 1
                                counts[row['status']] += 1
                                print(f'[{completed}/{len(cases)}] {row["status"]}: {case.filename}', flush=True)
                                submit_next()
                    finally:
                        stop.set()
        print('Counts: ' + ', '.join(f'{status}={count}' for status, count in sorted(counts.items())), flush=True)
        return 130 if completed < len(cases) or counts['interrupted'] else 0
    finally:
        stop.set()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.jobs < 1:
            raise ValueError('--jobs/JOBS must be positive')
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('--timeout/TIMEOUT must be finite and positive')
        if args.limit is not None and args.limit < 1:
            raise ValueError('--limit must be positive')
        cases = ([_explicit_case(path) for path in args.input] if args.input else collect_inputs(args.suite))
        # Explicit repeated paths do not accidentally run the same case twice.
        cases = list({case.source: case for case in cases}.values())
        if args.limit:
            cases = cases[:args.limit]
        print(f'NIA_LS first feasible model; cases={len(cases)}; parallel={args.jobs}; total timeout={args.timeout:g}s', flush=True)
        print('Groups: ' + ', '.join(f'{name}={count}' for name, count in sorted(Counter(c.category for c in cases).items())), flush=True)
        if args.dry_run:
            print('Dry run: no build, conversion, search, or result writes.')
            return 0
        config = Config(jobs=args.jobs, timeout=args.timeout)
        if not config.solver.is_file() or not os.access(config.solver, os.X_OK):
            raise ValueError(f'NIA_LS executable missing: {config.solver}; use script/run_initial.sh to build first')
        if not config.converter.is_file():
            raise ValueError(f'Converter missing: {config.converter}')
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S-%f')
        output = args.output or ROOT / 'result/initial' / stamp
        print(f'Results: {output.resolve() / "results.csv"}', flush=True)
        return run_batch(cases, config, output)
    except (OSError, ValueError) as exc:
        print(f'run_initial.sh: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
