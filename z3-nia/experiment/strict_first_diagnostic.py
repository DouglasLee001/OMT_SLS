"""Independent fresh-strict legacy-first diagnostic, never an optimization run.

The coordinator imports no Z3. Native exact evaluation and optional source
completion run in a separately killable process, within the same case deadline.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 64 * 1024 * 1024
_modules, _digests = {}, {}
_module_lock, _digest_lock = threading.RLock(), threading.Lock()
if hasattr(sys, 'set_int_max_str_digits'):
    sys.set_int_max_str_digits(0)


@dataclass(frozen=True)
class Config:
    solver: Path
    initial_helper: Path
    verify_helper: Path
    sdk_python: Path
    timeout: float = 1200
    source_check_seconds: float = 5
    seed: int = 0
    strict_helper: Path | None = None
    sdk_library: Path | None = None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _file(path, expected=None, *, content=False):
    """Hash a stable regular input; never open a FIFO or truncate an input."""
    path = Path(path)
    resolved = path.resolve(strict=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    data, digest = [], hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode), f'input is not a regular file: {path}')
        _require(not content or before.st_size <= MAX_INPUT_BYTES, 'diagnostic input too large')
        while block := stream.read(1024 * 1024):
            digest.update(block)
            if content:
                data.append(block)
        _require(_signature(before) == _signature(os.fstat(stream.fileno())), f'input changed: {path}')
    _require(path.resolve(strict=True) == resolved and _signature(path.stat()) == _signature(before),
             f'input identity changed: {path}')
    value = digest.hexdigest()
    _require(expected is None or value == expected, f'SHA256 mismatch: {path}')
    return b''.join(data) if content else value


def _digest(path):
    path = Path(path)
    key = str(path.resolve(strict=True)), _signature(path.stat())
    with _digest_lock:
        if key not in _digests:
            _digests[key] = _file(path)
        return _digests[key]


def _helper(path):
    key = str(Path(path).resolve()), _digest(path)
    with _module_lock:
        if key not in _modules:
            name = '_strict_diagnostic_helper_' + str(len(_modules))
            spec = importlib.util.spec_from_file_location(name, key[0])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            _modules[key] = module
        return _modules[key]


def _exact(value):
    _require(isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value),
             'M/value must be an exact finite decimal integer')
    return int(value)


def build_strict_omt(source, M, direction, helper_path=None):
    """Reuse the existing lexical builder, retaining the original objective."""
    _exact(M)
    helper = _helper(helper_path or ROOT / 'script/run_strict_bound.py')
    query, objective = helper.build_query(source, M, direction)
    commands = helper._commands(query)
    _require(commands[-2:] == [['check-sat'], ['exit']], 'unexpected strict builder terminator')
    command = 'minimize' if direction == 'min' else 'maximize'
    text = '\n'.join(helper._render(form) for form in commands[:-2])
    return text + f'\n({command} {objective})\n(check-sat)\n', objective


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def _library(config):
    sdk = Path(config.sdk_python).resolve(strict=True)
    _require((sdk / 'z3/z3.py').is_file(), 'sdk_python must contain the frozen z3 package')
    if config.sdk_library is not None:
        library = Path(config.sdk_library).resolve(strict=True)
    else:
        candidates = [sdk / 'z3/lib/libz3.so', sdk.parent / 'libz3.so']
        library = next((path.resolve() for path in candidates if path.is_file()), None)
        _require(library is not None, 'frozen SDK libz3.so missing')
    preferred = library.parent / 'libz3.so.5.1'
    _require(not preferred.exists() or preferred.resolve() == library,
             'ambiguous frozen SDK library')
    return sdk, library


def run_case(record, config, case_dir, stop):
    """Run only this supplied record; case_dir must be a new independent directory."""
    started, prep, owned = time.monotonic(), 0.0, False
    result = {key: record.get(key) for key in ('index', 'filename', 'category', 'direction', 'M')}
    result.update(native_outcome='pending', native_value=None, native_time_seconds=0.0,
                  native_verified=False, strict_improvement=False,
                  source_verification_status='pending', source_verification_reason='not checked',
                  source_model_verified=False, source_bound_unsat_verified=False,
                  diagnostic_only=True, is_optimal=False, hashes={}, command=[],
                  time_to_first_seconds=None)
    case_dir = Path(case_dir)
    try:
        case_dir.mkdir(parents=False, exist_ok=False)
        owned = True
        case_dir = case_dir.resolve()
        prep = float(record.get('query_preparation_seconds', 0))
        _require(math.isfinite(prep) and prep >= 0, 'invalid query_preparation_seconds')
        timeout, check_seconds = float(config.timeout), float(config.source_check_seconds)
        _require(math.isfinite(timeout) and timeout > 0, 'invalid timeout')
        _require(math.isfinite(check_seconds) and 0 <= check_seconds <= 5, 'invalid source_check_seconds')
        _require(type(config.seed) is int and 0 <= config.seed < 2 ** 32, 'invalid seed')
        deadline = started + max(0.0, timeout - prep)

        def remaining():
            if stop.is_set():
                raise InterruptedError('run interrupted')
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('per-case total wallclock limit reached')
            return value

        remaining()
        _exact(record['M'])
        _require(record['direction'] in ('min', 'max'), 'invalid direction')
        for key in ('source', 'query'):
            path = Path(record[key])
            _require(path.is_absolute(), f'{key} must be absolute')
            _require(not path.resolve().is_relative_to(case_dir), 'input aliases output directory')
            result['hashes'][key] = _file(path, record[key + '_sha256'])
        _require(Path(record['source']).resolve() != Path(record['query']).resolve(), 'source/query alias')
        strict = Path(config.strict_helper or ROOT / 'script/run_strict_bound.py').resolve()
        sdk, library = _library(config)
        paths = dict(solver=Path(config.solver).resolve(strict=True),
                     initial_helper=Path(config.initial_helper).resolve(strict=True),
                     verify_helper=Path(config.verify_helper).resolve(strict=True),
                     strict_helper=strict, sdk_library=library,
                     sdk_core=sdk / 'z3/z3core.py', worker=Path(__file__).resolve())
        for key, path in paths.items():
            result['hashes'][key] = _digest(path)
        _require(os.access(paths['solver'], os.X_OK), 'solver is not executable')
        helper = _helper(paths['initial_helper'])
        native, model = case_dir / 'input.nia', case_dir / 'model.tsv'
        command = [str(paths['solver']), '--nia-ls', str(record['query']), '--first-solution',
                   '--initialize=off', '--timeout=' + str(remaining()), '--seed=' + str(config.seed),
                   '--export-native=' + str(native), '--model-output=' + str(model)]
        result['command'] = command
        native_started = time.monotonic()
        try:
            result['returncode'] = helper._stage(command, deadline, stop,
                                                case_dir / 'native.stdout', case_dir / 'native.stderr')
        finally:
            result['native_time_seconds'] = time.monotonic() - native_started
        remaining()
        report = helper._native_report(case_dir / 'native.stdout')
        result['native_report'] = report
        _require(result['returncode'] == 0, 'native command failed')
        outcome = report.get('status')
        _require(outcome in ('sat', 'unsat', 'unknown'), 'unsupported native outcome')
        result['native_outcome'] = outcome
        if outcome == 'sat':
            _exact(report.get('objective_value'))
            result['native_value'] = report['objective_value']
            result['time_to_first_seconds'] = prep + time.monotonic() - started
        if outcome in ('sat', 'unsat') and check_seconds > 0:
            child_deadline = min(deadline, time.monotonic() + check_seconds)
            payload = dict(record=dict(record), native=str(native), model=str(model),
                           native_outcome=outcome, native_value=result['native_value'],
                           deadline=child_deadline, sdk_python=str(sdk),
                           paths={key: str(path) for key, path in paths.items()}, hashes=result['hashes'])
            payload_path, checkpoint = case_dir / 'source-check-input.json', case_dir / 'source-check.json'
            _write(payload_path, payload)
            try:
                helper._stage([sys.executable, str(paths['worker']), '--source-check',
                               str(payload_path), str(checkpoint)], child_deadline, stop,
                              case_dir / 'source-check.stdout', case_dir / 'source-check.stderr')
            except (TimeoutError, InterruptedError) as error:
                result['source_verification_reason'] = str(error)
            if checkpoint.is_file():
                checked = json.loads(checkpoint.read_text())
                for key in ('native_verified', 'strict_improvement', 'source_verification_status',
                            'source_verification_reason', 'source_model_verified', 'source_bound_unsat_verified'):
                    result[key] = checked[key]
                result['source_check'] = checked
            if time.monotonic() >= child_deadline or stop.is_set():
                result.update(source_verification_status='pending', source_model_verified=False,
                              source_bound_unsat_verified=False,
                              source_verification_reason='source-check deadline/interruption after stage')
        else:
            result['source_verification_reason'] = 'no source-check allowance or native candidate'
        for key in ('source', 'query'):
            _file(record[key], record[key + '_sha256'])
        if time.monotonic() >= deadline or stop.is_set():
            result.update(source_verification_status='pending', source_model_verified=False,
                          source_bound_unsat_verified=False, source_verification_reason='total deadline/interruption after stage')
    except InterruptedError as error:
        result.update(native_outcome='interrupted', source_verification_reason=str(error))
    except TimeoutError as error:
        result.update(native_outcome='timeout', source_verification_reason=str(error))
    except Exception as error:
        result.update(native_outcome='error', native_verified=False, strict_improvement=False,
                      source_verification_status='pending', source_model_verified=False,
                      source_bound_unsat_verified=False, source_verification_reason=f'{type(error).__name__}: {error}')
    finally:
        prep = prep if math.isfinite(prep) and prep >= 0 else 0.0
        result['query_preparation_seconds'] = prep
        result['elapsed_seconds'] = prep + time.monotonic() - started
        if owned:
            _write(case_dir / 'result.json', result)
    return result


def _source_check(payload_path, output_path):
    """Bounded child: native exact CNF first, then independent original source."""
    payload = json.loads(Path(payload_path).read_text())
    record, paths = payload['record'], payload['paths']
    checked = dict(native_verified=False, strict_improvement=False,
                   source_verification_status='pending', source_verification_reason='check started',
                   source_model_verified=False, source_bound_unsat_verified=False)

    def remaining():
        value = payload['deadline'] - time.monotonic()
        if value <= 0:
            raise TimeoutError('source-check wallclock limit reached')
        return value

    def checkpoint():
        _write(output_path, checked)

    checkpoint()
    try:
        remaining()
        for key in ('worker', 'strict_helper', 'verify_helper', 'sdk_library', 'sdk_core'):
            _file(paths[key], payload['hashes'][key])
        source = _file(record['source'], record['source_sha256'], content=True).decode('utf-8')
        query = _file(record['query'], record['query_sha256'], content=True).decode('utf-8')
        rebuilt, objective_text = build_strict_omt(source, record['M'], record['direction'],
                                                  helper_path=paths['strict_helper'])
        _require(query == rebuilt and record['objective'] == objective_text,
                 'derived query/original objective does not match the original source and bound')
        strict, verify = _helper(paths['strict_helper']), _helper(paths['verify_helper'])
        native_bytes = _file(payload['native'], content=True)
        native_text = native_bytes.decode('utf-8')
        native = verify.parse_native(native_text)
        headers = [line.split() for line in native_text.splitlines()
                   if line.startswith('ORIGINAL_OBJECTIVE ')]
        _require(len(headers) == 1 and len(headers[0]) == 2, 'missing original-objective header')
        _require(verify.decode_hex(headers[0][1], 'original objective') == objective_text,
                 'native original-objective header mismatch')
        _require(native['direction'] == record['direction'] and native['source'] is not None
                 and Path(native['source']).resolve() == Path(record['query']).resolve(),
                 'native source/direction mismatch')
        checked['native_sha256'] = hashlib.sha256(native_bytes).hexdigest()
        supplied = {}
        if payload['native_outcome'] == 'sat':
            model_bytes = _file(payload['model'], content=True)
            model_text = model_bytes.decode('utf-8')
            value = verify.evaluate_model(native, model_text)
            _require(value == _exact(payload['native_value']), 'reported/native objective mismatch')
            best = _exact(record['M'])
            _require(value < best if record['direction'] == 'min' else value > best,
                     'native model does not strictly improve M')
            supplied = dict(line.rsplit('\t', 1) for line in model_text.splitlines())
            checked.update(native_verified=True, strict_improvement=True,
                           native_value=str(value), model_sha256=hashlib.sha256(model_bytes).hexdigest())
            remaining()
            checkpoint()  # Preserve exact native acceptance even if SDK completion times out.
        remaining()

        # Load only the explicitly frozen SDK, inside this process. No converter
        # or independent NIA_LS executable is invoked here.
        import builtins
        import ctypes
        library = Path(paths['sdk_library']).resolve(strict=True)
        ctypes.CDLL(str(library))
        builtins.Z3_LIB_DIRS = [str(library.parent)]
        for name in ('Z3_LIBRARY_PATH', 'PYTHONPATH', 'PATH'):
            os.environ.pop(name, None)
        sdk = Path(payload['sdk_python']).resolve(strict=True)
        sys.path.insert(0, str(sdk))
        import z3
        _require(Path(z3.__file__).resolve().is_relative_to(sdk), 'SDK Python escaped frozen path')
        _require(z3.get_version()[:3] == (5, 1, 0), 'expected frozen embedded SDK 5.1.0')
        filtered, direction, parsed_objective = strict._query(source)
        _require(direction == record['direction'] and parsed_objective == objective_text,
                 'original objective/direction mismatch')
        original = z3.Optimize()
        original.from_string(filtered)  # Parse only; never invoke Optimize.check.
        objectives, hard = original.objectives(), list(original.assertions())
        _require(len(objectives) == 1 and objectives[0].sort().kind() == z3.Z3_INT_SORT,
                 'expected one original integer objective')
        objective = objectives[0]
        best = z3.IntVal(record['M'])
        bound = objective < best if direction == 'min' else objective > best
        solver = z3.Solver()
        solver.add(*hard, bound)
        constants, seen, todo = {}, set(), hard + [objective]
        while todo:
            remaining()
            expression = todo.pop()
            identity = expression.get_id()
            if identity in seen:
                continue
            seen.add(identity)
            _require(len(seen) <= 200000, 'source DAG node limit')
            _require(z3.is_app(expression), 'quantified/bound source expression unsupported')
            declaration = expression.decl()
            if declaration.kind() == z3.Z3_OP_UNINTERPRETED:
                _require(expression.num_args() == 0, 'source uninterpreted function unsupported')
                kind = expression.sort().kind()
                _require(kind in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT), 'source sort unsupported')
                constants[(str(declaration.name()), kind)] = expression
            todo.extend(expression.children())
        if payload['native_outcome'] == 'sat':
            solver.add(objective == z3.IntVal(payload['native_value']))
            fixed = []
            for kind, name in native['variables'].values():
                sort = z3.Z3_INT_SORT if kind == 'I' else z3.Z3_BOOL_SORT
                source_constant = constants.get((name, sort))
                if source_constant is not None:
                    value = z3.IntVal(supplied[name]) if kind == 'I' else z3.BoolVal(supplied[name] == 'true')
                    solver.add(source_constant == value)
                    fixed.append(dict(name=name, sort=kind, value=supplied[name]))
            checked['fixed_source_values'] = fixed
        solver.set(timeout=max(1, int(1000 * remaining())))
        answer = solver.check()
        remaining()
        if answer == z3.unknown:
            checked.update(source_verification_status='unknown', source_verification_reason=solver.reason_unknown())
        elif answer == z3.unsat:
            if payload['native_outcome'] == 'unsat':
                checked.update(source_verification_status='unsat_verified', source_bound_unsat_verified=True,
                               source_verification_reason='independent original F plus strict bound is UNSAT')
            else:
                checked.update(source_verification_status='rejected',
                               source_verification_reason='native proposal has no original-source completion; not a bound proof')
        else:
            model = solver.model()
            _require(all(z3.is_true(model.eval(form, model_completion=True)) for form in hard + [bound]),
                     'completed model does not satisfy all original hard assertions and strict bound')
            actual = model.eval(objective, model_completion=True)
            _require(z3.is_int_value(actual), 'nonconcrete original objective')
            if payload['native_outcome'] == 'sat':
                _require(actual.as_long() == _exact(payload['native_value']), 'source/native value mismatch')
                checked.update(source_verification_status='sat_verified', source_model_verified=True,
                               source_verification_reason='complete original source and strict bound verified')
            else:
                checked.update(source_verification_status='native_unsat_not_confirmed',
                               source_verification_reason='independent original strict query is SAT')
            checked['source_value'] = str(actual.as_long())
            checked['source_model'] = [dict(name=name, sort='I' if kind == z3.Z3_INT_SORT else 'B',
                                           value=str(model.eval(expr, model_completion=True)))
                                       for (name, kind), expr in sorted(constants.items())]
        for key in ('source', 'query'):
            _file(record[key], record[key + '_sha256'])
        remaining()
    except TimeoutError as error:
        checked.update(source_verification_status='pending', source_verification_reason=str(error),
                       source_model_verified=False, source_bound_unsat_verified=False)
    except Exception as error:
        checked.update(source_verification_status='error',
                       source_verification_reason=f'{type(error).__name__}: {error}',
                       source_model_verified=False, source_bound_unsat_verified=False)
    checkpoint()


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--source-check':
        raise SystemExit('only the private --source-check PAYLOAD OUTPUT entry is supported')
    _source_check(sys.argv[2], sys.argv[3])
