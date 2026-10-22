#!/usr/bin/env python3
"""Optimize exactly the unsolved bounded subsets with native NIA_LS search."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

try:
    from smtlib import objective_sense, parse
except ModuleNotFoundError:
    from script.smtlib import objective_sense, parse

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ROOT = Path('/data/shared/lbh/OMT/benchmarks')
FIELDS = ('filename', 'category', 'suite', 'direction', 'status', 'termination',
          'best_value', 'time_to_first_seconds', 'time_to_best_seconds',
          'wall_seconds', 'model_validated', 'is_optimal', 'z3_best_value',
          'optimathsat_best_value', 'reference_best_value', 'vs_z3',
          'vs_optimathsat', 'vs_best', 'strictly_better_than_best', 'error')


@dataclass(frozen=True)
class InputCase:
    source: Path
    filename: str
    category: str
    suite: str
    direction: str
    z3: int | None = None
    optimathsat: int | None = None


@dataclass(frozen=True)
class Config:
    jobs: int = 180
    timeout: float = 1200.0
    solver: Path = ROOT / 'NIA_LS/src/nia_ls'
    converter: Path = ROOT / 'NIA_LS/converter/omt_to_ls.py'
    use_target: bool = True


def make_parser(environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', nargs='?', choices=('all', 'original', 'new'), default='all')
    parser.add_argument('--jobs', type=int, default=environ.get('JOBS') or 180)
    parser.add_argument('--timeout', type=float, default=environ.get('TIMEOUT') or 1200,
                        help='total seconds per case including conversion; at most 1200')
    parser.add_argument('--output', type=Path, help='new result directory; must not exist')
    parser.add_argument('--input', action='append', type=Path,
                        help='restrict the selected subset to these source files (repeatable)')
    parser.add_argument('--limit', type=int, help='run the first N selected cases')
    parser.add_argument('--no-target', action='store_true',
                        help='do not pass the historical scalar reference as an early-stop target')
    parser.add_argument('--dry-run', action='store_true', help='audit selection without running or writing')
    return parser


def validate_budget(jobs, timeout):
    if not 1 <= jobs <= 180:
        raise ValueError('--jobs/JOBS must be between 1 and 180')
    if not math.isfinite(timeout) or not 0 < timeout <= 1200:
        raise ValueError('--timeout/TIMEOUT must be finite, positive, and at most 1200 seconds')


def parse_integer(value):
    """Read an exact decimal or SMT-LIB unary-negative integer; blanks are absent."""
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError(f'Expected an exact integer string: {value!r}')
    value = value.strip()
    if not value:
        return None

    def integer(form):
        if isinstance(form, str) and re.fullmatch(r'[+-]?\d+', form, flags=re.ASCII):
            return int(form)
        if isinstance(form, list) and len(form) == 2 and form[0] == '-':
            return -integer(form[1])
        raise ValueError(f'Expected a finite integer: {value!r}')

    forms = parse(value)
    if len(forms) != 1:
        raise ValueError(f'Expected one finite integer: {value!r}')
    return integer(forms[0])


def reference_best(z3, optimathsat, direction):
    if direction not in ('min', 'max'):
        raise ValueError(f'Invalid objective direction: {direction!r}')
    values = [v for v in (z3, optimathsat) if v is not None]
    return (min(values) if direction == 'min' else max(values)) if values else None


def comparisons(value, z3, optimathsat, direction):
    best = reference_best(z3, optimathsat, direction)

    def compare(reference):
        if reference is None:
            return 'no_reference'
        if value is None:
            return 'no_incumbent'
        if value == reference:
            return 'tie'
        return 'better' if ((value < reference) == (direction == 'min')) else 'worse'

    result = {'vs_z3': compare(z3), 'vs_optimathsat': compare(optimathsat),
              'vs_best': compare(best), 'reference_best_value': '' if best is None else str(best)}
    result['strictly_better_than_best'] = str(result['vs_best'] == 'better').lower()
    return result


def collect_inputs(suite, root=ROOT, original_root=ORIGINAL_ROOT):
    """Align only listed rows to their original SMT2 sources; reject ambiguity."""
    root, original_root = Path(root).resolve(), Path(original_root).resolve()
    cases, seen = [], set()
    for selected in ('original', 'new'):
        if suite not in ('all', selected):
            continue
        listing = root / f'result/{selected}/{selected}_unsolved_bounded_sol.csv'
        with listing.open(newline='') as handle:
            reader = csv.DictReader(handle)
            required = {'filename', 'z3_best_value', 'optimathsat_best_value'}
            if selected == 'new':
                required.add('category')
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f'Missing reference columns in {listing}')
            for row in reader:
                name = row['filename']
                category = 'original' if selected == 'original' else row['category']
                base = original_root if selected == 'original' else root / 'new_benchmark' / category / 'instance'
                if selected == 'new' and (not category or Path(category).name != category):
                    raise ValueError(f'Invalid generated category in {listing}: {category!r}')
                source = (base / name).resolve()
                if (not name or not source.is_relative_to(base.resolve()) or not source.is_file()
                        or source.suffix != '.smt2' or (selected == 'new' and Path(name).name != name)):
                    raise ValueError(f'Invalid benchmark in {listing}: {name!r}')
                if source in seen:
                    raise ValueError(f'Duplicate benchmark in subset tables: {source}')
                seen.add(source)
                direction = {'minimize': 'min', 'maximize': 'max'}[objective_sense(source.read_text())]
                cases.append(InputCase(source, name, category, selected, direction,
                                       parse_integer(row['z3_best_value']), parse_integer(row['optimathsat_best_value'])))
    if not cases:
        raise ValueError('No benchmarks selected')
    return cases


def _kill_group(process):
    """Terminate children even when the group leader has already exited."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    process.wait()


def _stage(command, deadline, stop, stdout, stderr, on_line=None):
    if stop.is_set():
        raise InterruptedError('run interrupted')
    if time.monotonic() >= deadline:
        raise TimeoutError('per-case wallclock limit reached')
    with stdout.open('wb') as output, stderr.open('wb') as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, start_new_session=True)
        with stdout.open('rb') as reader:
            partial = b''

            def drain(final=False):
                nonlocal partial
                partial += reader.read()
                lines = partial.split(b'\n')
                partial = lines.pop()
                if final and partial:
                    lines.append(partial)
                    partial = b''
                if on_line:
                    for line in lines:
                        on_line(line.decode('utf-8', errors='replace'))

            try:
                while True:
                    drain()
                    if stop.is_set():
                        raise InterruptedError('run interrupted')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('per-case wallclock limit reached')
                    try:
                        code = process.wait(timeout=min(0.05, remaining))
                        drain(final=True)
                        return code
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                _kill_group(process)
                drain(final=True)


def _tail(path, limit=1600):
    if not path.is_file():
        return ''
    with path.open('rb') as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode('utf-8', errors='replace').strip()


def run_case(case, config, stop, artifacts=None):
    result = {field: '' for field in FIELDS}
    result.update(filename=case.filename, category=case.category, suite=case.suite,
                  direction=case.direction, status='error', model_validated='false', is_optimal='false',
                  z3_best_value='' if case.z3 is None else str(case.z3),
                  optimathsat_best_value='' if case.optimathsat is None else str(case.optimathsat))
    started = time.monotonic()
    deadline = started + config.timeout
    stage = 'conversion'
    best, first_time, best_time = None, None, None
    final_report = None
    with tempfile.TemporaryDirectory(prefix='nia-opt-case-') as temporary:
        directory = Path(temporary)
        logs = Path(artifacts) if artifacts is not None else directory
        logs.mkdir(parents=True, exist_ok=True)
        converted, model = logs / 'input.nia', logs / 'model.tsv'
        output, errors = logs / 'conversion.stdout', logs / 'conversion.stderr'
        with (logs / 'progress.jsonl').open('w') as progress:
            try:
                command = [sys.executable, str(config.converter), str(case.source), '-o', str(converted)]
                if case.suite == 'original' and case.filename.startswith('QF_NIA/leipzig/'):
                    command.append('--keep-definitions')
                code = _stage(command, deadline, stop, output, errors)
                if code:
                    raise ValueError(f'converter exit {code}: {_tail(errors) or _tail(output)}')
                if not converted.is_file():
                    raise ValueError('converter did not write an intermediate input')
                stage = 'optimization'
                native_started = time.monotonic()
                output, errors = logs / 'native.stdout', logs / 'native.stderr'

                def consume(line):
                    nonlocal best, first_time, best_time, final_report
                    try:
                        report = json.loads(line)
                    except json.JSONDecodeError:
                        return
                    if not isinstance(report, dict) or 'status' not in report:
                        return
                    if report.get('event') != 'incumbent':
                        final_report = report
                    if report['status'] != 'sat':
                        return
                    value = report.get('objective_value')
                    elapsed = report.get('elapsed_seconds')
                    if (report.get('model_validated') is not True or not isinstance(value, str)
                            or not re.fullmatch(r'-?\d+', value, flags=re.ASCII)):
                        raise ValueError('NIA_LS SAT record lacks a validated model and exact decimal objective')
                    if report.get('direction') != case.direction:
                        raise ValueError('NIA_LS objective direction disagrees with source SMT2')
                    if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                            or not math.isfinite(elapsed) or elapsed < 0
                            or elapsed > time.monotonic() - native_started + 0.05):
                        raise ValueError('NIA_LS SAT record has invalid elapsed_seconds')
                    value = int(value)
                    incumbent_elapsed = elapsed
                    if report.get('event') != 'incumbent' and report.get('time_to_best_seconds') is not None:
                        incumbent_elapsed = report['time_to_best_seconds']
                        if (not isinstance(incumbent_elapsed, (int, float)) or isinstance(incumbent_elapsed, bool)
                                or not math.isfinite(incumbent_elapsed) or not 0 <= incumbent_elapsed <= elapsed):
                            raise ValueError('NIA_LS final record has invalid time_to_best_seconds')
                    elapsed_total = native_started - started + incumbent_elapsed
                    if elapsed_total > config.timeout:
                        return  # A model validated after the total budget cannot count.
                    if first_time is None:
                        first_time = elapsed_total
                    if best is None or ((value < best) if case.direction == 'min' else (value > best)):
                        best, best_time = value, elapsed_total
                        progress.write(json.dumps({'objective_value': str(value), 'elapsed_seconds': incumbent_elapsed,
                                                   'time_seconds': elapsed_total, 'model_validated': True}) + '\n')
                        progress.flush()

                command = [str(config.solver), str(converted), '--optimize',
                           f'--timeout={max(deadline - native_started, 0.000001):.9f}', f'--model-output={model}']
                target = reference_best(case.z3, case.optimathsat, case.direction)
                if config.use_target and target is not None:
                    command.append(f'--target={target}')
                code = _stage(command, deadline, stop, output, errors, consume)
                if code:
                    detail = str((final_report or {}).get('error') or (final_report or {}).get('message') or '')
                    raise ValueError(f'NIA_LS exit {code}: {detail or _tail(errors) or _tail(output)}')
                if final_report is None:
                    raise ValueError('NIA_LS did not return a final JSON status record')
                status = final_report['status']
                if status not in ('sat', 'unknown', 'unsat', 'error'):
                    raise ValueError(f'Unexpected NIA_LS final status: {status!r}')
                if status == 'sat' and best is None:
                    raise ValueError('NIA_LS SAT result has no validated incumbent within budget')
                if status == 'unsat' and best is not None:
                    raise ValueError('NIA_LS UNSAT result contradicts a validated incumbent')
                result.update(status='sat' if best is not None and status != 'error' else status,
                              termination=str(final_report.get('termination', status)))
                if status == 'error':
                    result['error'] = str(final_report.get('error') or final_report.get('message') or 'NIA_LS error')[:1600]
            except TimeoutError:
                result.update(status='sat' if best is not None else 'timeout', termination='wall_timeout',
                              error=f'{stage}: {config.timeout:g}s total wallclock limit')
            except InterruptedError:
                result.update(status='interrupted', termination='interrupted', error=f'{stage}: run interrupted')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result.update(status='error', termination='error', error=f'{stage}: {exc}'[:1600])
    if best is not None:
        result.update(best_value=str(best), model_validated='true',
                      time_to_first_seconds=f'{first_time:.6f}', time_to_best_seconds=f'{best_time:.6f}')
    result.update(wall_seconds=f'{time.monotonic() - started:.6f}')
    result.update(comparisons(best, case.z3, case.optimathsat, case.direction))
    return result


def _summary(cases, rows, config):
    wins = sum(row['strictly_better_than_best'] == 'true' for row in rows)
    return {'selected': len(cases), 'completed': len(rows), 'parallel': config.jobs,
            'total_timeout_seconds': config.timeout, 'scalar_target_enabled': config.use_target,
            'strictly_better_than_best': wins, 'strict_win_rate': wins / len(cases),
            'over_80_percent': wins / len(cases) > 0.8,
            'statuses': dict(Counter(row['status'] for row in rows)),
            'comparisons': {key: dict(Counter(row[key] for row in rows))
                            for key in ('vs_z3', 'vs_optimathsat', 'vs_best')}}


def run_batch(cases, config, output):
    validate_budget(config.jobs, config.timeout)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'cases').mkdir()
    stop, previous, rows = threading.Event(), {}, []
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda _sig, _frame: stop.set())
    manifest = {'solver': str(config.solver.resolve()), 'converter': str(config.converter.resolve()),
                'jobs': config.jobs, 'timeout': config.timeout, 'scalar_target_enabled': config.use_target,
                'started_utc': datetime.now(timezone.utc).isoformat(),
                'cases': [{'filename': c.filename, 'category': c.category, 'suite': c.suite,
                           'source': str(c.source), 'direction': c.direction,
                           'z3': None if c.z3 is None else str(c.z3),
                           'optimathsat': None if c.optimathsat is None else str(c.optimathsat)} for c in cases]}
    try:
        with tempfile.TemporaryDirectory(prefix='nia-opt-run-') as temporary:
            snapshot = Path(temporary) / 'nia_ls'
            shutil.copy2(config.solver, snapshot)
            manifest['solver_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            manifest['converter_sha256'] = hashlib.sha256(config.converter.read_bytes()).hexdigest()
            (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            actual = replace(config, solver=snapshot)
            with (output / 'results.csv').open('x', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader(); handle.flush()
                with ThreadPoolExecutor(max_workers=config.jobs) as pool:
                    pending, inputs = {}, iter(enumerate(cases))

                    def submit_next():
                        if stop.is_set():
                            return
                        item = next(inputs, None)
                        if item is not None:
                            index, case = item
                            artifact = output / 'cases' / f'{index:04d}-{hashlib.sha256(str(case.source).encode()).hexdigest()[:12]}'
                            pending[pool.submit(run_case, case, actual, stop, artifact)] = case

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
                                    row = {field: '' for field in FIELDS}
                                    row.update(filename=case.filename, category=case.category, suite=case.suite,
                                               direction=case.direction, status='error', termination='worker_error',
                                               model_validated='false', is_optimal='false', error=str(exc)[:1600])
                                    row.update(comparisons(None, case.z3, case.optimathsat, case.direction))
                                rows.append(row); writer.writerow(row); handle.flush()
                                print(f'[{len(rows)}/{len(cases)}] {row["status"]} {row["best_value"]} {row["vs_best"]}: {case.filename}', flush=True)
                                submit_next()
                    finally:
                        stop.set()
        summary = _summary(cases, rows, config)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(f'Strictly better than per-case best reference: {summary["strictly_better_than_best"]}/{len(cases)} ({summary["strict_win_rate"]:.2%})', flush=True)
        return 130 if len(rows) < len(cases) or any(row['status'] == 'interrupted' for row in rows) else 0
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        validate_budget(args.jobs, args.timeout)
        if args.limit is not None and args.limit < 1:
            raise ValueError('--limit must be positive')
        cases = collect_inputs(args.suite)
        if args.input:
            requested = {path.resolve() for path in args.input}
            selected = {case.source for case in cases}
            if requested - selected:
                raise ValueError('--input may only select files listed in the chosen unsolved_bounded_sol subset')
            cases = [case for case in cases if case.source in requested]
        if args.limit:
            cases = cases[:args.limit]
        print(f'NIA_LS native optimization; cases={len(cases)}; parallel={args.jobs}; total timeout={args.timeout:g}s', flush=True)
        print('Groups: ' + ', '.join(f'{name}={count}' for name, count in sorted(Counter(c.category for c in cases).items())), flush=True)
        print('Directions: ' + ', '.join(f'{name}={count}' for name, count in sorted(Counter(c.direction for c in cases).items())), flush=True)
        if args.dry_run:
            print('Dry run: no build, conversion, search, or result writes.')
            return 0
        config = Config(jobs=args.jobs, timeout=args.timeout, use_target=not args.no_target)
        if not config.solver.is_file() or not os.access(config.solver, os.X_OK):
            raise ValueError(f'NIA_LS executable missing: {config.solver}; use script/run_optimize.sh to build')
        if not config.converter.is_file():
            raise ValueError(f'Converter missing: {config.converter}')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        output = args.output or ROOT / 'result/optimize' / stamp
        print(f'Results: {output.resolve() / "results.csv"}', flush=True)
        return run_batch(cases, config, output)
    except (OSError, ValueError) as exc:
        print(f'run_optimize.sh: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
