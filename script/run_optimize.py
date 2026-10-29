#!/usr/bin/env python3
"""Optimize selected historical benchmark lists with native NIA_LS search."""
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
from zoneinfo import ZoneInfo

try:
    from smtlib import objective_sense, parse
except ModuleNotFoundError:
    from script.smtlib import objective_sense, parse

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ROOT = Path('/data/shared/lbh/OMT/benchmarks')
RESULT_ROOT = Path('/data/shared/lbh/OMT/result')
SEARCH_EXCHANGE_MODES = ('off', 'observe', 'sls-to-z3', 'z3-to-sls', 'both')
FIELDS = ('filename', 'category', 'suite', 'direction', 'status', 'termination',
          'best_value', 'time_to_first_seconds', 'time_to_best_seconds',
          'wall_seconds', 'model_validated', 'is_optimal', 'z3_best_value',
          'optimathsat_best_value', 'reference_best_value', 'vs_z3',
          'vs_optimathsat', 'vs_best', 'strictly_better_than_best', 'error',
          'known_optimum_value', 'known_optimum_source', 'known_optimum_reached',
          'large_target_magnitude', 'large_target_attempts', 'large_target_slices',
          'large_target_seconds', 'large_target_reached', 'large_target_status',
          'prove_unbounded', 'is_unbounded', 'unbounded_threshold', 'unbounded_attempts',
          'unbounded_queries', 'unbounded_seconds', 'unbounded_status', 'time_to_unbounded_seconds',
          'prove_optimal', 'optimality_stall_seconds', 'optimality_timeout_seconds',
          'optimality_search_window_seconds', 'optimality_resume', 'optimality_burst', 'optimality_cutset',
          'optimality_balanced',
          'optimality_attempts', 'optimality_seconds', 'optimality_status',
          'time_to_optimality_seconds', 'optimality_proved_value', 'optimality_schedule',
          'optimality_next_timeout_seconds', 'optimality_sat_models_accepted')

LARGE_TARGET_MAX = 2**127 - 1


def default_output_directory():
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    return RESULT_ROOT / ('NIA_' + stamp)


def validate_large_target(value):
    """The finite-target magnitude must fit a positive signed 128-bit integer."""
    if value is not None and (type(value) is not int or not 0 < value <= LARGE_TARGET_MAX):
        raise ValueError('--large-target must be a positive integer at most 2^127-1')


def parse_large_target(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value, flags=re.ASCII):
        raise argparse.ArgumentTypeError('--large-target requires a positive decimal integer')
    magnitude = int(value)
    try:
        validate_large_target(magnitude)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return magnitude


def parse_unbounded_threshold(value):
    try:
        return parse_large_target(value)
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError(str(exc).replace('--large-target', '--unbounded-threshold')) from exc


def validate_optimality_settings(enabled, stall, timeout, search_window=0, resume=False, burst=False,
                                 cutset=False, balanced=False):
    if type(enabled) is not bool:
        raise ValueError('prove_optimal must be a Boolean')
    for name, value in (('optimality-stall', stall), ('optimality-timeout', timeout)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value <= 0):
            raise ValueError('--' + name + ' must be finite and positive')
    if (not isinstance(search_window, (int, float)) or isinstance(search_window, bool)
            or not math.isfinite(search_window) or search_window < 0):
        raise ValueError('--optimality-search-window must be finite and nonnegative')
    if type(resume) is not bool:
        raise ValueError('optimality_resume must be a Boolean')
    if type(burst) is not bool:
        raise ValueError('optimality_burst must be a Boolean')
    if type(cutset) is not bool:
        raise ValueError('optimality_cutset must be a Boolean')
    if type(balanced) is not bool:
        raise ValueError('optimality_balanced must be a Boolean')
    if balanced and search_window > 0:
        raise ValueError('--optimality-balanced conflicts with a positive --optimality-search-window')
    if not enabled and ((stall, timeout) != (50, 30) or search_window != 0 or resume or burst or cutset or balanced):
        raise ValueError('nondefault --optimality-* settings require --prove-optimal')


def validate_initial_z3_settings(enabled, after, timeout):
    if type(enabled) is not bool:
        raise ValueError('prove_optimal must be a Boolean')
    for name, value, positive in (('initial-z3-after', after, False),
                                  ('initial-z3-timeout', timeout, True)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or (value <= 0 if positive else value < 0)):
            raise ValueError('--' + name + ' must be finite and ' + ('positive' if positive else 'nonnegative'))
    if not enabled and (after, timeout) != (0, 120):
        raise ValueError('nondefault --initial-z3-* settings require --prove-optimal')


def validate_search_exchange(enabled, mode):
    if mode not in SEARCH_EXCHANGE_MODES:
        raise ValueError('invalid --search-exchange mode')
    if mode != 'off' and not enabled:
        raise ValueError('--search-exchange requires --prove-optimal')


@dataclass(frozen=True)
class InputCase:
    source: Path
    filename: str
    category: str
    suite: str
    direction: str
    z3: int | None = None
    optimathsat: int | None = None
    known_optimum: int | None = None
    known_optimum_source: str = ''


@dataclass(frozen=True)
class Config:
    jobs: int = 180
    timeout: float = 1200.0
    solver: Path = ROOT / 'NIA_LS/src/nia_ls'
    converter: Path = ROOT / 'NIA_LS/converter/omt_to_ls.py'
    use_target: bool = True
    subset: str = 'unsolved_bounded_sol'
    stop_at_known_optimum: bool = True
    large_target: int | None = None
    selection_suite: str = 'all'
    selection_complete: bool = False
    prove_unbounded: bool = False
    unbounded_threshold: int = 1000000
    prove_optimal: bool = False
    optimality_stall: float = 50.0
    optimality_timeout: float = 30.0
    optimality_search_window: float = 0.0
    optimality_resume: bool = False
    optimality_burst: bool = False
    optimality_cutset: bool = False
    optimality_balanced: bool = False
    initial_z3_after: float = 0.0
    initial_z3_timeout: float = 120.0
    search_exchange: str = 'off'

    def __post_init__(self):
        validate_large_target(self.large_target)
        if type(self.prove_unbounded) is not bool:
            raise ValueError('prove_unbounded must be a Boolean')
        if type(self.unbounded_threshold) is not int or not 0 < self.unbounded_threshold <= LARGE_TARGET_MAX:
            raise ValueError('--unbounded-threshold must be a positive integer at most 2^127-1')
        validate_optimality_settings(self.prove_optimal, self.optimality_stall, self.optimality_timeout,
                                     self.optimality_search_window, self.optimality_resume,
                                     self.optimality_burst, self.optimality_cutset, self.optimality_balanced)
        validate_initial_z3_settings(self.prove_optimal, self.initial_z3_after, self.initial_z3_timeout)
        validate_search_exchange(self.prove_optimal, self.search_exchange)
        if self.prove_optimal:
            object.__setattr__(self, 'use_target', False)
            object.__setattr__(self, 'stop_at_known_optimum', False)


def make_parser(environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', nargs='?', choices=('all', 'original', 'new'), default='all')
    parser.add_argument('--subset', choices=('unsolved_bounded_sol', 'bounded', 'excluded', 'all'),
                        default='unsolved_bounded_sol',
                        help='all: complete original/new tables; default: unsolved subset; bounded: *_bound.csv; excluded: full minus bounded '
                             '(historical large-absolute-value filter, not an unboundedness proof)')
    parser.add_argument('--jobs', type=int, default=environ.get('JOBS') or 180)
    parser.add_argument('--timeout', type=float, default=environ.get('TIMEOUT') or 1200,
                        help='total seconds per case including conversion; at most 1200')
    parser.add_argument('--output', type=Path,
                        help='new result directory; default: /data/shared/lbh/OMT/result/NIA_<Beijing timestamp>; must not exist')
    parser.add_argument('--input', action='append', type=Path,
                        help='restrict the selected subset to these source files (repeatable)')
    parser.add_argument('--limit', type=int, help='run the first N selected cases')
    parser.add_argument('--no-target', action='store_true',
                        help='do not pass the historical scalar reference as an early-stop target')
    parser.add_argument('--no-known-optimum-stop', action='store_true',
                        help='disable stopping at a validated incumbent equal to an externally proven optimum')
    parser.add_argument('--large-target', type=parse_large_target, metavar='M',
                        help='enable finite large-target repair: min <= -M or max >= M; '
                             'positive decimal integer <= 2^127-1, disabled by default; does not prove unboundedness')
    parser.add_argument('--prove-unbounded', action='store_true',
                        help='attempt one exact affine-ray proof after a threshold-reaching incumbent; disables historical target stopping')
    parser.add_argument('--unbounded-threshold', type=parse_unbounded_threshold, metavar='M',
                        help='positive proof trigger <= 2^127-1 (default 1000000); requires --prove-unbounded')
    parser.add_argument('--prove-optimal', action='store_true',
                        help='attempt an independent strict-improvement UNSAT check; disables historical target and known-optimum stopping')
    parser.add_argument('--search-exchange', choices=SEARCH_EXCHANGE_MODES, default='off',
                        help='bounded search-information exchange mode; non-off requires --prove-optimal')
    parser.add_argument('--optimality-resume', action='store_true',
                        help='pause and resume the same Z3 proof query while the incumbent is unchanged; requires --prove-optimal')
    parser.add_argument('--optimality-burst', action='store_true',
                        help='continue strict-improvement queries after validated SAT models within the same bounded Z3 slice; requires --prove-optimal')
    parser.add_argument('--optimality-cutset', action='store_true',
                        help='try finite nonlinear-variable cutset branching into linear queries during proof search; requires --prove-optimal')
    parser.add_argument('--optimality-balanced', action='store_true',
                        help='enable balanced proof/search scheduling; requires --prove-optimal and a zero --optimality-search-window')
    parser.add_argument('--optimality-stall', type=float, default=50.0,
                        help='positive seconds without improvement before a proof attempt (default 50)')
    parser.add_argument('--optimality-timeout', type=float, default=30.0,
                        help='positive seconds allowed per finite optimality check (default 30)')
    parser.add_argument('--optimality-search-window', type=float, default=0.0,
                        help='nonnegative SLS seconds between proof attempts; 0 retains adaptive scheduling; requires --prove-optimal when positive')
    parser.add_argument('--initial-z3-after', type=float, default=0.0,
                        help='start one source-SMT Z3 feasibility attempt after these seconds without an incumbent; 0 disables it')
    parser.add_argument('--initial-z3-timeout', type=float, default=120.0,
                        help='positive maximum seconds for the optional initial-model Z3 attempt (default 120)')
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


def collect_inputs(suite, root=ROOT, original_root=ORIGINAL_ROOT, subset='unsolved_bounded_sol'):
    """Align only listed rows to their original SMT2 sources; reject ambiguity."""
    if subset not in ('unsolved_bounded_sol', 'bounded', 'excluded', 'all'):
        raise ValueError(f'Invalid benchmark subset: {subset!r}')
    suffix = 'bound' if subset == 'bounded' else subset
    root, original_root = Path(root).resolve(), Path(original_root).resolve()
    cases, seen = [], set()
    for selected in ('original', 'new'):
        if suite not in ('all', selected):
            continue

        def read_listing(listing):
            entries, listed = [], set()
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
                    if selected == 'new' and (not category or category in ('.', '..')
                                              or Path(category).name != category):
                        raise ValueError(f'Invalid generated category in {listing}: {category!r}')
                    base = original_root if selected == 'original' else root / 'new_benchmark' / category / 'instance'
                    source = (base / name).resolve() if name else base.resolve()
                    if (not name or not source.is_relative_to(base.resolve()) or not source.is_file()
                            or source.suffix != '.smt2' or (selected == 'new' and Path(name).name != name)):
                        raise ValueError(f'Invalid benchmark in {listing}: {name!r}')
                    if source in listed:
                        raise ValueError(f'Duplicate benchmark in {listing}: {source}')
                    listed.add(source)
                    entries.append((source, name, category, row))
            return entries

        if subset == 'all':
            entries = read_listing(root / f'result/{selected}/{selected}.csv')
        elif subset == 'excluded':
            entries = read_listing(root / f'result/{selected}/{selected}.csv')
            bounded = read_listing(root / f'result/{selected}/{selected}_bound.csv')
            full_sources = {entry[0] for entry in entries}
            bounded_sources = {entry[0] for entry in bounded}
            if bounded_sources - full_sources:
                raise ValueError(f'Bounded rows must belong to the full {selected} table: '
                                 f'{sorted(map(str, bounded_sources - full_sources))[:3]}')
            entries = [entry for entry in entries if entry[0] not in bounded_sources]
        else:
            entries = read_listing(root / f'result/{selected}/{selected}_{suffix}.csv')
        for source, name, category, row in entries:
            if source in seen:
                raise ValueError(f'Duplicate benchmark in subset tables: {source}')
            seen.add(source)
            direction = {'minimize': 'min', 'maximize': 'max'}[objective_sense(source.read_text())]
            z3 = parse_integer(row['z3_best_value'])
            optimathsat = parse_integer(row['optimathsat_best_value'])
            proven = [(solver, value) for solver, value in (('z3', z3), ('optimathsat', optimathsat))
                      if value is not None and (row.get(f'{solver}_is_optimal') or '').strip().lower() == 'true']
            optimum = proven[0][1] if proven else None
            if (any(value != optimum for _, value in proven)
                    or (optimum is not None and reference_best(z3, optimathsat, direction) != optimum)):
                optimum = None
            provenance = '+'.join(solver for solver, _ in proven) if optimum is not None else ''
            cases.append(InputCase(source, name, category, selected, direction,
                                   z3, optimathsat, optimum, provenance))
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


class _StageStopped(Exception):
    """A live stage was stopped by its validated-progress predicate."""
    returncode: int | None = None


def _stage(command, deadline, stop, stdout, stderr, on_line=None, stop_when=None):
    if stop.is_set():
        raise InterruptedError('run interrupted')
    if time.monotonic() >= deadline:
        raise TimeoutError('per-case wallclock limit reached')
    with stdout.open('wb') as output, stderr.open('wb') as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, start_new_session=True)
        with stdout.open('rb') as reader:
            partial = b''
            early_stop = None

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
                    if stop_when is not None and stop_when() and process.poll() is None:
                        early_stop = _StageStopped()
                        raise early_stop
                    try:
                        code = process.wait(timeout=min(0.05, remaining))
                        drain(final=True)
                        return code
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                _kill_group(process)
                if early_stop is not None:
                    early_stop.returncode = process.returncode
                drain(final=True)


def _tail(path, limit=1600):
    if not path.is_file():
        return ''
    with path.open('rb') as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode('utf-8', errors='replace').strip()


def _large_target_metadata(report, config, best, direction):
    """Copy complete native accounting; never infer an unobserved probe failure."""
    fields = ('large_target_magnitude', 'large_target_attempts', 'large_target_slices',
              'large_target_seconds', 'large_target_reached', 'large_target_status')
    if not any(field in report for field in fields):
        return {}
    if any(field not in report for field in fields):
        raise ValueError('NIA_LS final record has incomplete large_target metadata')
    magnitude = report['large_target_magnitude']
    if magnitude is not None:
        if isinstance(magnitude, str) and re.fullmatch(r'[0-9]+', magnitude, flags=re.ASCII):
            magnitude = int(magnitude)
        validate_large_target(magnitude)
    if magnitude != config.large_target:
        raise ValueError('NIA_LS large_target_magnitude disagrees with configuration')
    for field in ('large_target_attempts', 'large_target_slices'):
        if type(report[field]) is not int or report[field] < 0:
            raise ValueError(f'NIA_LS final record has invalid {field}')
    seconds = report['large_target_seconds']
    if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
            or not math.isfinite(seconds) or seconds < 0):
        raise ValueError('NIA_LS final record has invalid large_target_seconds')
    reached = (magnitude is not None and best is not None
               and (best <= -magnitude if direction == 'min' else best >= magnitude))
    if type(report['large_target_reached']) is not bool or report['large_target_reached'] != reached:
        raise ValueError('NIA_LS large_target_reached disagrees with validated incumbent')
    status = report['large_target_status']
    if status not in ('disabled', 'waiting', 'searching', 'reached', 'exhausted', 'unsupported', 'budget'):
        raise ValueError('NIA_LS final record has invalid large_target_status')
    if (status == 'reached') != reached or (status == 'disabled') != (magnitude is None):
        raise ValueError('NIA_LS large_target_status disagrees with configuration or validated incumbent')
    if magnitude is None and (report['large_target_attempts'] or report['large_target_slices'] or seconds):
        raise ValueError('NIA_LS disabled large_target has nonzero accounting')
    return {'large_target_magnitude': '' if magnitude is None else str(magnitude),
            'large_target_attempts': str(report['large_target_attempts']),
            'large_target_slices': str(report['large_target_slices']),
            'large_target_seconds': str(seconds), 'large_target_reached': str(reached).lower(),
            'large_target_status': report['large_target_status']}


def _unbounded_defaults(config):
    return {'prove_unbounded': str(config.prove_unbounded).lower(), 'is_unbounded': 'false',
            'unbounded_threshold': str(config.unbounded_threshold) if config.prove_unbounded else '',
            'unbounded_attempts': '' if config.prove_unbounded else '0',
            'unbounded_queries': '' if config.prove_unbounded else '0',
            'unbounded_seconds': '' if config.prove_unbounded else '0',
            'unbounded_status': '' if config.prove_unbounded else 'disabled',
            'time_to_unbounded_seconds': ''}


def _optimality_defaults(config):
    return {'prove_optimal': str(config.prove_optimal).lower(),
            'optimality_stall_seconds': str(config.optimality_stall),
            'optimality_timeout_seconds': str(config.optimality_timeout),
            'optimality_search_window_seconds': str(config.optimality_search_window),
            'optimality_resume': str(config.optimality_resume).lower(),
            'optimality_burst': str(config.optimality_burst).lower(),
            'optimality_cutset': str(config.optimality_cutset).lower(),
            'optimality_balanced': str(config.optimality_balanced).lower(),
            'optimality_attempts': '' if config.prove_optimal else '0',
            'optimality_seconds': '' if config.prove_optimal else '0',
            'optimality_status': '' if config.prove_optimal else 'disabled',
            'time_to_optimality_seconds': '', 'optimality_proved_value': '',
            'optimality_schedule': '', 'optimality_next_timeout_seconds': '',
            'optimality_sat_models_accepted': ''}


def _optimality_metadata(report, config, best, direction, offset, result, best_time):
    try:
        import verify_optimize as verifier
    except ModuleNotFoundError:
        from script import verify_optimize as verifier
    if not any(field in report for field in (*verifier.OPTIMALITY_FIELDS, 'optimality_burst',
                                            'optimality_cutset', 'optimality_balanced')):
        if config.prove_optimal or report.get('is_optimal') is True:
            raise ValueError('NIA_LS final lacks optimality metadata')
        return {}
    claimed = verifier.validate_optimality_metadata(
        report, config.prove_optimal, config.optimality_stall, config.optimality_timeout,
        best, direction, max(0, config.timeout - offset),
        None if best_time is None else best_time - offset,
        search_window=config.optimality_search_window, resume=config.optimality_resume,
        burst=config.optimality_burst, cutset=config.optimality_cutset, balanced=config.optimality_balanced)
    metadata = {field: str(report[field]) for field in
                ('optimality_attempts', 'optimality_seconds', 'optimality_status')}
    metadata.update({field: str(report[field]) for field in verifier.OPTIMALITY_ADAPTIVE_FIELDS
                     if field in report})
    if 'optimality_search_window_seconds' in report:
        metadata['optimality_search_window_seconds'] = str(report['optimality_search_window_seconds'])
    if claimed and (result['status'] != 'sat'
                    or result['termination'] in ('wall_timeout', 'interrupted', 'error', 'worker_error')):
        return {}
    if claimed and (report['elapsed_seconds'] + offset <= config.timeout
                    and result['status'] == 'sat'
                    and result['termination'] not in ('wall_timeout', 'interrupted', 'error', 'worker_error')):
        metadata.update(is_optimal='true', termination='optimality_proved',
                        optimality_proved_value=report['optimality_proved_value'],
                        time_to_optimality_seconds=f'{report["time_to_optimality_seconds"] + offset:.6f}')
    return metadata


def _unbounded_metadata(report, config, best, direction, offset, result, native_text, model_text, best_time):
    try:
        import verify_optimize as verifier
    except ModuleNotFoundError:
        from script import verify_optimize as verifier
    if not any(field in report for field in verifier.UNBOUNDED_NATIVE_FIELDS):
        if config.prove_unbounded or report.get('termination') == 'unbounded_proved':
            raise ValueError('NIA_LS proof-enabled final lacks unbounded metadata')
        return {}
    claimed = verifier.validate_unbounded_metadata(report, config.prove_unbounded, config.unbounded_threshold,
                                                  best, direction, max(0, config.timeout - offset),
                                                  None if best_time is None else best_time - offset)
    metadata = {field: str(report[field]) for field in
                ('unbounded_attempts', 'unbounded_queries', 'unbounded_seconds', 'unbounded_status')}
    if claimed:
        if native_text is None or model_text is None:
            raise ValueError('unbounded proof lacks retained native input/model')
        actual = verifier.verify_unbounded_certificate(verifier.parse_native(native_text),
                                                       report['unbounded_certificate'], model_text)
        if actual != best:
            raise ValueError('unbounded proof base differs from validated retained objective')
        if (report['elapsed_seconds'] + offset <= config.timeout
                and result['termination'] not in ('wall_timeout', 'interrupted', 'error', 'worker_error')):
            # An audited mathematical proof takes precedence over an external
            # stop requested while this same final record was being flushed.
            metadata.update(is_unbounded='true', termination='unbounded_proved',
                            time_to_unbounded_seconds=f'{report["time_to_unbounded_seconds"] + offset:.6f}')
    return metadata


def run_case(case, config, stop, artifacts=None):
    result = {field: '' for field in FIELDS}
    result.update(filename=case.filename, category=case.category, suite=case.suite,
                  direction=case.direction, status='error', model_validated='false', is_optimal='false',
                  z3_best_value='' if case.z3 is None else str(case.z3),
                  optimathsat_best_value='' if case.optimathsat is None else str(case.optimathsat),
                  known_optimum_value='' if case.known_optimum is None else str(case.known_optimum),
                  known_optimum_source=case.known_optimum_source, known_optimum_reached='false',
                  large_target_magnitude='' if config.large_target is None else str(config.large_target))
    result.update(_unbounded_defaults(config))
    result.update(_optimality_defaults(config))
    started = time.monotonic()
    deadline = started + config.timeout
    stage = 'conversion'
    best, first_time, best_time = None, None, None
    best_model_inode, retained_model_matches = None, True
    final_report = None
    native_started, retained_native, retained_model = None, None, None
    matched_known_optimum = False
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
                    nonlocal best, first_time, best_time, final_report, matched_known_optimum, best_model_inode
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
                    model_inode = report.get('model_inode')
                    if model_inode is not None and (type(model_inode) is not int or model_inode <= 0):
                        raise ValueError('NIA_LS SAT record has invalid model_inode')
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
                        best, best_time, best_model_inode = value, elapsed_total, model_inode
                        progress.write(json.dumps({'objective_value': str(value), 'elapsed_seconds': incumbent_elapsed,
                                                   'time_seconds': elapsed_total, 'model_validated': True}) + '\n')
                        progress.flush()
                    if best == case.known_optimum:
                        matched_known_optimum = True

                command = [str(config.solver), str(converted), '--optimize',
                           f'--timeout={max(deadline - native_started, 0.000001):.9f}', f'--model-output={model}']
                target = reference_best(case.z3, case.optimathsat, case.direction)
                if config.use_target and not config.prove_unbounded and target is not None:
                    command.append(f'--target={target}')
                if config.large_target is not None:
                    command.append(f'--large-target={config.large_target}')
                if config.prove_unbounded:
                    command.extend(['--prove-unbounded', f'--unbounded-threshold={config.unbounded_threshold}'])
                if config.prove_optimal:
                    command.extend([f'--optimality-stall={config.optimality_stall}',
                                    f'--optimality-timeout={config.optimality_timeout}',
                                    f'--optimality-search-window={config.optimality_search_window}',
                                    f'--initial-z3-after={config.initial_z3_after}',
                                    f'--initial-z3-timeout={config.initial_z3_timeout}'])
                if config.optimality_resume:
                    command.append('--optimality-resume')
                if config.optimality_burst:
                    command.append('--optimality-burst')
                if config.optimality_cutset:
                    command.append('--optimality-cutset')
                if config.optimality_balanced:
                    command.append('--optimality-balanced')
                if config.prove_optimal:
                    command.extend(['--prove-optimal',
                                    f'--optimality-query-output={logs / "optimality.smt2"}'])
                if config.search_exchange != 'off':
                    command.append(f'--search-exchange={config.search_exchange}')
                known_stop = False
                try:
                    code = _stage(command, deadline, stop, output, errors, consume,
                                  stop_when=lambda: (config.stop_at_known_optimum and matched_known_optimum
                                                     and final_report is None))
                except _StageStopped as stopped:
                    # _stage has killed the group and drained all remaining records.
                    # A later model can invalidate the historical claim; retain it honestly.
                    if best != case.known_optimum:
                        raise ValueError(f'validated incumbent {best} disagrees with known optimum {case.known_optimum}')
                    known_stop = True
                    code = stopped.returncode
                    if code in (0, -signal.SIGTERM, -signal.SIGKILL):
                        code = 0
                if code:
                    detail = str((final_report or {}).get('error') or (final_report or {}).get('message') or '')
                    raise ValueError(f'NIA_LS exit {code}: {detail or _tail(errors) or _tail(output)}')
                if known_stop and final_report and (final_report.get('termination') == 'error' or final_report.get('error')):
                    detail = final_report.get('error') or final_report.get('message') or 'NIA_LS error'
                    raise ValueError(f'NIA_LS error during known-optimum stop: {detail}')
                if final_report is None and not known_stop:
                    raise ValueError('NIA_LS did not return a final JSON status record')
                status = final_report['status'] if final_report is not None else 'sat'
                if status not in ('sat', 'unknown', 'unsat', 'error'):
                    raise ValueError(f'Unexpected NIA_LS final status: {status!r}')
                if status == 'sat' and best is None:
                    raise ValueError('NIA_LS SAT result has no validated incumbent within budget')
                if status == 'unsat' and best is not None:
                    raise ValueError('NIA_LS UNSAT result contradicts a validated incumbent')
                result.update(status='sat' if best is not None and status != 'error' else status,
                              termination=('known_optimum_reached' if known_stop and status != 'error'
                                           else str((final_report or {}).get('termination', status))))
                if status == 'error':
                    result['error'] = str(final_report.get('error') or final_report.get('message') or 'NIA_LS error')[:1600]
            except TimeoutError:
                result.update(status='sat' if best is not None else 'timeout', termination='wall_timeout',
                              error=f'{stage}: {config.timeout:g}s total wallclock limit')
            except InterruptedError:
                result.update(status='interrupted', termination='interrupted', error=f'{stage}: run interrupted')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result.update(status='error', termination='error', error=f'{stage}: {exc}'[:1600])
        if best_model_inode is not None:
            try:
                # _stage has reaped the complete process group. Native model
                # publication precedes its event, so a deadline may leave a
                # newer, unpublished model in the canonical path.
                for candidate in (model, Path(str(model) + '.prev')):
                    if candidate.is_file() and candidate.stat().st_ino == best_model_inode:
                        if candidate != model:
                            os.replace(candidate, model)
                        break
                else:
                    raise ValueError('last published model is missing from retained generations')
            except (OSError, ValueError) as exc:
                retained_model_matches = False
                result.update(status='error', termination='error', error=f'optimization: {exc}'[:1600])
        if final_report is not None and final_report.get('is_unbounded') is True:
            retained_native = converted.read_text(encoding='utf-8') if converted.is_file() else None
            retained_model = model.read_text(encoding='utf-8') if model.is_file() else None
    # _stage drains stdout while cleaning up on every exit path. A final record
    # observed there still contains accounting, even if process exit hit a limit.
    if final_report is not None:
        try:
            result.update(_large_target_metadata(final_report, config, best, case.direction))
        except (ValueError, TypeError, OverflowError) as exc:
            detail = f'optimization: large_target metadata: {exc}'
            if result['termination'] not in ('wall_timeout', 'interrupted', 'error', 'worker_error'):
                result.update(status='error', termination='error')
            result['error'] = (result['error'] + '; ' + detail if result['error'] else detail)[:1600]
        try:
            result.update(_unbounded_metadata(final_report, config, best, case.direction,
                                              native_started - started, result, retained_native, retained_model, best_time))
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            detail = f'optimization: unbounded metadata: {exc}'
            if result['termination'] not in ('wall_timeout', 'interrupted', 'error', 'worker_error'):
                result.update(status='error', termination='error')
            result['error'] = (result['error'] + '; ' + detail if result['error'] else detail)[:1600]
        try:
            result.update(_optimality_metadata(final_report, config, best, case.direction,
                                               native_started - started, result, best_time))
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            detail = f'optimization: optimality metadata: {exc}'
            if result['termination'] not in ('wall_timeout', 'interrupted', 'error', 'worker_error'):
                result.update(status='error', termination='error')
            result['error'] = (result['error'] + '; ' + detail if result['error'] else detail)[:1600]
    if best is not None:
        result.update(best_value=str(best), model_validated=str(retained_model_matches).lower(),
                      time_to_first_seconds=f'{first_time:.6f}', time_to_best_seconds=f'{best_time:.6f}')
        # This flag records equality with a historical finite value only. It
        # does not override a verified unbounded certificate or prove optimality.
        result['known_optimum_reached'] = str(result['status'] == 'sat' and best == case.known_optimum).lower()
        if config.large_target is not None and (best <= -config.large_target if case.direction == 'min'
                                                else best >= config.large_target):
            result['large_target_reached'] = 'true'
    result.update(wall_seconds=f'{time.monotonic() - started:.6f}')
    result.update(comparisons(best, case.z3, case.optimathsat, case.direction))
    return result


def _summary(cases, rows, config):
    wins = sum(row['strictly_better_than_best'] == 'true' for row in rows)
    return {'selected': len(cases), 'completed': len(rows), 'parallel': config.jobs,
            'subset': config.subset,
            'total_timeout_seconds': config.timeout, 'scalar_target_enabled': config.use_target and not config.prove_unbounded,
            'known_optimum_stop_enabled': config.stop_at_known_optimum,
            'prove_optimal': config.prove_optimal,
            'optimality_stall_seconds': config.optimality_stall,
            'optimality_timeout_seconds': config.optimality_timeout,
            'optimality_search_window_seconds': config.optimality_search_window,
            'optimality_resume': config.optimality_resume,
            'optimality_burst': config.optimality_burst,
            'optimality_cutset': config.optimality_cutset,
            'optimality_balanced': config.optimality_balanced,
            'search_exchange': config.search_exchange,
            'initial_z3_after_seconds': config.initial_z3_after,
            'initial_z3_timeout_seconds': config.initial_z3_timeout,
            'is_optimal': sum(row.get('is_optimal') == 'true' for row in rows),
            'large_target_magnitude': None if config.large_target is None else str(config.large_target),
            'large_target_reached': sum(row.get('large_target_reached') == 'true' for row in rows),
            'prove_unbounded': config.prove_unbounded,
            'unbounded_threshold': str(config.unbounded_threshold) if config.prove_unbounded else None,
            'is_unbounded': sum(row.get('is_unbounded') == 'true' for row in rows),
            'known_optimum_reached': sum(row.get('known_optimum_reached') == 'true' for row in rows),
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
                'subset': config.subset,
                'selection_suite': config.selection_suite, 'selection_complete': config.selection_complete,
                'jobs': config.jobs, 'timeout': config.timeout, 'scalar_target_enabled': config.use_target and not config.prove_unbounded,
                'known_optimum_stop_enabled': config.stop_at_known_optimum,
                'prove_optimal': config.prove_optimal,
                'optimality_stall_seconds': config.optimality_stall,
                'optimality_timeout_seconds': config.optimality_timeout,
                'optimality_search_window_seconds': config.optimality_search_window,
                'optimality_resume': config.optimality_resume,
                'optimality_burst': config.optimality_burst,
                'optimality_cutset': config.optimality_cutset,
                'optimality_balanced': config.optimality_balanced,
                'search_exchange': config.search_exchange,
                'initial_z3_after_seconds': config.initial_z3_after,
                'initial_z3_timeout_seconds': config.initial_z3_timeout,
                'large_target_magnitude': None if config.large_target is None else str(config.large_target),
                'prove_unbounded': config.prove_unbounded,
                'unbounded_threshold': str(config.unbounded_threshold) if config.prove_unbounded else None,
                'started_utc': datetime.now(timezone.utc).isoformat(),
                'cases': [{'filename': c.filename, 'category': c.category, 'suite': c.suite,
                           'source': str(c.source), 'direction': c.direction,
                           'z3': None if c.z3 is None else str(c.z3),
                           'optimathsat': None if c.optimathsat is None else str(c.optimathsat),
                           'known_optimum_value': None if c.known_optimum is None else str(c.known_optimum),
                           'known_optimum_source': c.known_optimum_source} for c in cases]}
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
                                               model_validated='false', is_optimal='false', error=str(exc)[:1600],
                                               known_optimum_value='' if case.known_optimum is None else str(case.known_optimum),
                                               known_optimum_source=case.known_optimum_source, known_optimum_reached='false',
                                               large_target_magnitude='' if config.large_target is None else str(config.large_target))
                                    row.update(_unbounded_defaults(config))
                                    row.update(_optimality_defaults(config))
                                    row.update(comparisons(None, case.z3, case.optimathsat, case.direction))
                                rows.append(row); writer.writerow(row); handle.flush()
                                if config.prove_unbounded and row.get('is_unbounded') == 'true':
                                    print(f'[{len(rows)}/{len(cases)}] unbounded_proved {row["best_value"]}: {case.filename}', flush=True)
                                else:
                                    print(f'[{len(rows)}/{len(cases)}] {row["status"]} {row["best_value"]} {row["vs_best"]}: {case.filename}', flush=True)
                                submit_next()
                    finally:
                        stop.set()
        summary = _summary(cases, rows, config)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        if config.prove_unbounded:
            print(f'Proved unbounded: {summary["is_unbounded"]}/{len(cases)}', flush=True)
        else:
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
        validate_optimality_settings(args.prove_optimal, args.optimality_stall, args.optimality_timeout,
                                     args.optimality_search_window, args.optimality_resume,
                                     args.optimality_burst, args.optimality_cutset, args.optimality_balanced)
        validate_initial_z3_settings(args.prove_optimal, args.initial_z3_after, args.initial_z3_timeout)
        validate_search_exchange(args.prove_optimal, args.search_exchange)
        if args.limit is not None and args.limit < 1:
            raise ValueError('--limit must be positive')
        if args.unbounded_threshold is not None and not args.prove_unbounded:
            raise ValueError('--unbounded-threshold requires --prove-unbounded')
        cases = collect_inputs(args.suite, subset=args.subset)
        if args.input:
            requested = {path.resolve() for path in args.input}
            selected = {case.source for case in cases}
            if requested - selected:
                raise ValueError(f'--input may only select files listed in the chosen {args.subset} subset')
            cases = [case for case in cases if case.source in requested]
        if args.limit:
            cases = cases[:args.limit]
        print(f'NIA_LS native optimization; subset={args.subset}; cases={len(cases)}; parallel={args.jobs}; total timeout={args.timeout:g}s', flush=True)
        print('Groups: ' + ', '.join(f'{name}={count}' for name, count in sorted(Counter(c.category for c in cases).items())), flush=True)
        print('Directions: ' + ', '.join(f'{name}={count}' for name, count in sorted(Counter(c.direction for c in cases).items())), flush=True)
        if args.dry_run:
            print('Dry run: no build, conversion, search, or result writes.')
            return 0
        config = Config(jobs=args.jobs, timeout=args.timeout, use_target=not args.no_target and not args.prove_unbounded,
                        subset=args.subset, stop_at_known_optimum=not args.no_known_optimum_stop,
                        large_target=args.large_target, selection_suite=args.suite,
                        selection_complete=not args.input and args.limit is None,
                        prove_unbounded=args.prove_unbounded,
                        prove_optimal=args.prove_optimal, optimality_stall=args.optimality_stall,
                        optimality_timeout=args.optimality_timeout,
                        optimality_search_window=args.optimality_search_window,
                        optimality_resume=args.optimality_resume,
                        optimality_burst=args.optimality_burst,
                        optimality_cutset=args.optimality_cutset,
                        optimality_balanced=args.optimality_balanced, search_exchange=args.search_exchange,
                        initial_z3_after=args.initial_z3_after, initial_z3_timeout=args.initial_z3_timeout,
                        unbounded_threshold=args.unbounded_threshold if args.unbounded_threshold is not None else 1000000)
        if not config.solver.is_file() or not os.access(config.solver, os.X_OK):
            raise ValueError(f'NIA_LS executable missing: {config.solver}; use script/run_optimize.sh to build')
        if not config.converter.is_file():
            raise ValueError(f'Converter missing: {config.converter}')
        output = (args.output or default_output_directory()).resolve()
        print(f'Results: {output / "results.csv"}', flush=True)
        code = run_batch(cases, config, output)
        if code != 0:
            return code
        report = subprocess.run([sys.executable, str(ROOT / 'script/report_results.py'),
                                 str(output), '--output', str(output)], check=False)
        if report.returncode != 0:
            print(f'Optimization completed; report generation failed. Raw results remain in {output}.',
                  file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError) as exc:
        print(f'run_optimize.sh: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
