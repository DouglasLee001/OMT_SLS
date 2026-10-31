"""Independent Int-strict / Int-closed / Real-closed bound diagnostics.

Each arm is a separate killable process. Its complete setup and check share
the arm wall limit and the case deadline. The coordinator never imports Z3.
Real SAT is not an integer candidate; no outcome certifies an incumbent.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

ARMS = ['int_strict', 'int_closed', 'real_closed']
MAX_AST_NODES = 500000
_first_modules, _first_lock = {}, threading.Lock()


@dataclass(frozen=True)
class Config:
    sdk_python: Path
    initial_helper: Path
    strict_helper: Path
    timeout: float = 1200
    arm_seconds: float = 30
    sdk_library: Path | None = None
    first_helper: Path | None = None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _first(path):
    path = Path(path).resolve(strict=True)
    key = str(path), path.stat().st_mtime_ns
    with _first_lock:
        if key not in _first_modules:
            name = '_closed_bound_first_' + str(len(_first_modules))
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            _first_modules[key] = module
        return _first_modules[key]


def arm_order(index, arms=None):
    _require(type(index) is int and index >= 0, 'index must be a nonnegative integer')
    names = ARMS if arms is None else list(arms)
    _require(names and len(set(names)) == len(names)
             and all(isinstance(name, str) and re.fullmatch('[a-z][a-z0-9_]*', name) for name in names),
             'invalid internal diagnostic arm names')
    offset = index % len(names)
    return names[offset:] + names[:offset]


def _empty_arm(arm):
    return dict(arm=arm, status='pending', answer=None, reason='not started',
                query_path=None, query_sha256=None, check_started=False,
                setup_seconds=0.0, check_seconds=0.0, elapsed_seconds=0.0,
                integer_sat_verified=False, real_sat_verified=False,
                integer_bound_unsat_verified=False)


def _pending(arm, status, reason):
    arm.update(status=status, answer=None, reason=reason, integer_sat_verified=False,
               real_sat_verified=False, integer_bound_unsat_verified=False,
               meaning='unchecked diagnostic arm')
    arm.pop('objective_value', None)


def run_case(record, config, case_dir, stop, *, arms=None, worker_path=None, run_metadata=None, max_arm_seconds=30):
    """Consume one own-run record; write only into a newly created case_dir."""
    started, owned, first = time.monotonic(), False, None
    result = {key: record.get(key) for key in ('index', 'filename', 'category', 'direction', 'M')}
    result.update(version='closed-bound-diagnostic-v1', status='pending', arms=[], order=[],
                  diagnostic_only=True, is_optimal=False, hashes={}, reason='',
                  tactic='qfnra-nlsat', factor=False)
    if run_metadata is not None:
        result['version'] = run_metadata['version']
        result['tactic'] = run_metadata['tactic']
        result.pop('factor')
        result['runner_policy'] = run_metadata
    case_dir = Path(case_dir)
    try:
        case_dir.mkdir(parents=False, exist_ok=False)
        owned, case_dir = True, case_dir.resolve()
        timeout, allowance, cap = float(config.timeout), float(config.arm_seconds), float(max_arm_seconds)
        _require(math.isfinite(timeout) and 0 < timeout <= 1200, 'timeout must be in (0,1200]')
        _require(math.isfinite(cap) and 0 < cap <= 1200, 'max_arm_seconds must be in (0,1200]')
        _require(cap <= 30 or cap <= timeout, 'long arm cap exceeds the total case timeout')
        _require(math.isfinite(allowance) and 0 < allowance <= cap, f'arm_seconds must be in (0,{cap:g}]')
        deadline = started + timeout

        def remaining():
            if stop.is_set():
                raise InterruptedError('case interrupted')
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('case total wallclock limit reached')
            return value

        remaining()
        result['order'] = arm_order(record['index'], arms)
        _require(cap <= 30 or len(result['order']) == 1, 'long arm cap requires exactly one arm')
        _require(record['direction'] in ('min', 'max'), 'invalid objective direction')
        first_path = Path(config.first_helper or Path(__file__).with_name('strict_first_diagnostic.py')).resolve()
        first = _first(first_path)
        first._exact(record['M'])
        source = Path(record['source'])
        _require(source.is_absolute() and not source.resolve().is_relative_to(case_dir), 'source/output alias')
        result['hashes']['source'] = first._file(source, record['source_sha256'])
        sdk, library = first._library(config)
        paths = dict(first_helper=first_path, initial_helper=Path(config.initial_helper).resolve(strict=True),
                     strict_helper=Path(config.strict_helper).resolve(strict=True), sdk_library=library,
                     sdk_core=sdk / 'z3/z3core.py', worker=Path(worker_path or __file__).resolve())
        if worker_path is not None:
            paths['runner'] = Path(__file__).resolve()
        for key, path in paths.items():
            result['hashes'][key] = first._digest(path)
        helper = first._helper(paths['initial_helper'])
        for arm_name in result['order']:
            remaining()
            arm = _empty_arm(arm_name)
            result['arms'].append(arm)
            arm_started = time.monotonic()
            arm_deadline = min(deadline, arm_started + allowance)
            payload = dict(record=dict(record), arm=arm_name, deadline=arm_deadline,
                           sdk_python=str(sdk), paths={key: str(path) for key, path in paths.items()},
                           hashes=result['hashes'])
            payload_path, receipt = case_dir / (arm_name + '.input.json'), case_dir / (arm_name + '.json')
            first._write(payload_path, payload)
            stage_status, stage_reason = None, ''
            try:
                code = helper._stage([sys.executable, str(paths['worker']), '--arm',
                                      str(payload_path), str(receipt)], arm_deadline, stop,
                                     case_dir / (arm_name + '.stdout'), case_dir / (arm_name + '.stderr'))
                if code != 0:
                    stage_status, stage_reason = 'error', f'arm child exit code {code}'
            except TimeoutError as error:
                stage_status, stage_reason = 'timeout', str(error)
            except InterruptedError as error:
                stage_status, stage_reason = 'interrupted', str(error)
            if receipt.is_file():
                checked = json.loads(first._file(receipt, content=True))
                _require(checked['arm'] == arm_name, 'arm receipt identity mismatch')
                arm.update(checked)
            elif stage_status is None:
                stage_status, stage_reason = 'error', 'arm child produced no receipt'
            if stage_status is not None:
                _pending(arm, stage_status, stage_reason)
            if time.monotonic() >= arm_deadline:
                _pending(arm, 'timeout', 'arm wallclock deadline reached including setup/cleanup')
            if stop.is_set():
                _pending(arm, 'interrupted', 'case interrupted')
            if arm['query_path'] is not None:
                _require(Path(arm['query_path']).resolve() == case_dir / (arm_name + '.smt2'),
                         'query receipt path mismatch')
                first._file(arm['query_path'], arm['query_sha256'])
            if time.monotonic() >= arm_deadline:
                _pending(arm, 'timeout', 'arm wallclock deadline reached after query identity check')
            arm['elapsed_seconds'] = time.monotonic() - arm_started
        first._file(source, record['source_sha256'])
        remaining()
        result['status'] = 'complete'
    except InterruptedError as error:
        result.update(status='interrupted', reason=str(error))
    except TimeoutError as error:
        result.update(status='timeout', reason=str(error))
    except Exception as error:
        result.update(status='error', reason=f'{type(error).__name__}: {error}')
        for arm in result['arms']:
            _pending(arm, 'error', result['reason'])
    finally:
        result['elapsed_seconds'] = time.monotonic() - started
        if owned:
            # Inputs cannot alias this independently new directory.
            data = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
            (case_dir / 'result.json').write_text(data)
    return result


class Unsupported(ValueError):
    pass


def _supported_dag(z3, roots, remaining):
    """Validate every used source node, preserving DAG sharing and all branches."""
    order, seen, todo = [], set(), [(root, False) for root in roots]
    arithmetic = {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS, z3.Z3_OP_MUL}
    comparisons = {z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT}
    boolean = {z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES,
               z3.Z3_OP_IFF, z3.Z3_OP_XOR}
    sorts = {z3.Z3_INT_SORT, z3.Z3_BOOL_SORT}
    while todo:
        remaining()
        expression, ready = todo.pop()
        identity = expression.get_id()
        if ready:
            order.append(expression)
            continue
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > MAX_AST_NODES:
            raise Unsupported('source DAG resource guard exceeded')
        if not z3.is_app(expression):
            raise Unsupported('quantified or bound source expression')
        kind, sort = expression.decl().kind(), expression.sort().kind()
        children = expression.children()
        child_sorts = [child.sort().kind() for child in children]
        if kind == z3.Z3_OP_POWER:
            raise Unsupported('OP_POWER is unsupported, including zero/positive/negative exponents')
        valid = sort in sorts
        if kind == z3.Z3_OP_UNINTERPRETED:
            valid = valid and not children
        elif z3.is_int_value(expression) or z3.is_true(expression) or z3.is_false(expression):
            valid = valid and not children
        elif kind in arithmetic:
            valid = sort == z3.Z3_INT_SORT and bool(children) and all(k == z3.Z3_INT_SORT for k in child_sorts)
        elif kind in comparisons:
            valid = sort == z3.Z3_BOOL_SORT and len(children) == 2 and child_sorts == [z3.Z3_INT_SORT] * 2
        elif kind in (z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT):
            valid = sort == z3.Z3_BOOL_SORT and bool(children) and child_sorts[0] in sorts and len(set(child_sorts)) == 1
        elif kind in boolean:
            valid = sort == z3.Z3_BOOL_SORT and all(k == z3.Z3_BOOL_SORT for k in child_sorts)
        elif kind == z3.Z3_OP_ITE:
            valid = (len(children) == 3 and child_sorts[0] == z3.Z3_BOOL_SORT
                     and child_sorts[1] == child_sorts[2] == sort and sort in sorts)
        else:
            valid = False
        if not valid:
            raise Unsupported('unsupported source operator/sort: ' + str(expression.decl().name())[:120]
                              + ' / ' + str(expression.sort()))
        todo.append((expression, True))
        for child in reversed(children):
            remaining()
            if child.get_id() not in seen:
                todo.append((child, False))
    return order


def _real_rebuild(z3, order, remaining):
    """Rebuild arithmetic over fresh Reals; never use Int2Real or text replacement."""
    rebuilt, variables = {}, {}
    for expression in order:
        remaining()
        identity, kind = expression.get_id(), expression.decl().kind()
        children = [rebuilt[child.get_id()] for child in expression.children()]
        if z3.is_int_value(expression):
            value = z3.RealVal(str(expression.as_long()))
        elif kind == z3.Z3_OP_UNINTERPRETED:
            if expression.sort().kind() == z3.Z3_INT_SORT:
                declaration = expression.decl().get_id()
                if declaration not in variables:
                    variables[declaration] = (expression, z3.FreshReal('closed_bound_real'))
                value = variables[declaration][1]
            else:
                value = expression
        elif kind == z3.Z3_OP_ADD:
            value = z3.Sum(children)
        elif kind in (z3.Z3_OP_SUB, z3.Z3_OP_UMINUS):
            value = -children[0] if len(children) == 1 else children[0]
            for child in children[1:]:
                value = value - child
        elif kind == z3.Z3_OP_MUL:
            value = children[0]
            for child in children[1:]:
                remaining()
                value = value * child
        elif kind == z3.Z3_OP_ITE:
            value = z3.If(*children)
        elif kind == z3.Z3_OP_EQ:
            value = children[0] == children[1]
        elif kind == z3.Z3_OP_DISTINCT:
            value = z3.Distinct(*children)
        elif kind == z3.Z3_OP_LE:
            value = children[0] <= children[1]
        elif kind == z3.Z3_OP_GE:
            value = children[0] >= children[1]
        elif kind == z3.Z3_OP_LT:
            value = children[0] < children[1]
        elif kind == z3.Z3_OP_GT:
            value = children[0] > children[1]
        else:
            # Validated Boolean operators keep exactly their source signature.
            value = expression.decl()(*children)
        _require(value.sort().kind() in (z3.Z3_REAL_SORT, z3.Z3_BOOL_SORT), 'integer leaked into Real rebuild')
        rebuilt[identity] = value
    return rebuilt, variables


def _mapped_libraries(library):
    """Audit the libraries actually mapped by this Linux-only Python child."""
    mapped = set()
    for line in Path('/proc/self/maps').read_text(errors='surrogateescape').splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[5])
        if Path(path.removesuffix(' (deleted)')).name.startswith('libz3.so'):
            _require(not path.endswith(' (deleted)') and os.path.samefile(path, library),
                     'mapped libz3 differs from the explicit frozen library: ' + path)
            mapped.add(path)
    _require(bool(mapped), 'no mapped frozen libz3 was found in /proc/self/maps')
    return sorted(mapped)


def _run_arm(payload_path, receipt_path, *, solver_factory=None):
    payload = json.loads(Path(payload_path).read_text())
    started, arm = time.monotonic(), _empty_arm(payload['arm'])
    first = _first(payload['paths']['first_helper'])

    def remaining():
        value = payload['deadline'] - time.monotonic()
        if value <= 0:
            raise TimeoutError('arm wallclock limit reached')
        return value

    def checkpoint():
        arm['elapsed_seconds'] = time.monotonic() - started
        first._write(receipt_path, arm)

    checkpoint()
    try:
        paths, record = payload['paths'], payload['record']
        for key, path in paths.items():
            remaining()
            first._file(path, payload['hashes'][key])
        source = first._file(record['source'], record['source_sha256'], content=True).decode('utf-8')
        strict = first._helper(paths['strict_helper'])
        filtered, direction, objective_text = strict._query(source)
        _require(direction == record['direction'], 'source direction differs from record')
        best = first._exact(record['M'])
        import builtins
        import ctypes
        library, sdk = Path(paths['sdk_library']).resolve(), Path(payload['sdk_python']).resolve()
        ctypes.CDLL(str(library))
        builtins.Z3_LIB_DIRS = [str(library.parent)]
        for name in ('Z3_LIBRARY_PATH', 'PYTHONPATH', 'PATH'):
            os.environ.pop(name, None)
        sys.path.insert(0, str(sdk))
        import z3
        _require(Path(z3.__file__).resolve().is_relative_to(sdk), 'SDK Python escaped frozen path')
        _require(z3.get_version()[:3] == (5, 1, 0), 'expected frozen SDK 5.1.0')
        arm['mapped_sdk_libraries'] = _mapped_libraries(library)
        original = z3.Optimize()
        original.from_string(filtered)  # Lexical helper normalized only parser sense to min.
        objectives, hard = original.objectives(), list(original.assertions())
        if len(objectives) != 1 or objectives[0].sort().kind() != z3.Z3_INT_SORT:
            raise Unsupported('expected one source integer objective')
        objective = objectives[0]
        order = _supported_dag(z3, hard + [objective], remaining)
        arm.update(original_objective=objective_text, source_dag_nodes=len(order),
                   original_hard_count=len(hard))
        if solver_factory is None:
            _require(arm['arm'] in ARMS, 'unknown default diagnostic arm')
            arm.update(tactic='qfnra-nlsat', factor=False)
        real = arm['arm'] == 'real_closed'
        if real:
            rebuilt, variables = _real_rebuild(z3, order, remaining)
            hard = [rebuilt[form.get_id()] for form in hard]
            objective = rebuilt[objective.get_id()]
            arm['real_constant_map'] = [dict(source_name=str(source.decl().name()),
                                            real_name=str(value.decl().name()))
                                        for source, value in variables.values()]
        literal = best if arm['arm'] == 'int_strict' else best + (-1 if direction == 'min' else 1)
        number = z3.RealVal(str(literal)) if real else z3.IntVal(str(literal))
        if arm['arm'] == 'int_strict':
            bound = objective < number if direction == 'min' else objective > number
        else:
            bound = objective <= number if direction == 'min' else objective >= number
        assertions = hard + [bound]
        if solver_factory is None:
            solver = z3.With(z3.Tactic('qfnra-nlsat'), factor=False).solver()
            query_header = '; qfnra-nlsat factor=false; independent bound diagnostic\n'
        else:
            solver, policy = solver_factory(z3, arm['arm'])
            arm.update(tactic=policy['tactic'], policy=policy)
            query_header = '; ' + policy['tactic'] + '; independent bound diagnostic\n'
        solver.add(*assertions)
        query_path = Path(receipt_path).with_suffix('.smt2')
        query = query_header + solver.to_smt2()
        query_path.write_text(query)
        arm.update(query_path=str(query_path.resolve()), query_sha256=first._file(query_path),
                   query_assertion_count=len(assertions), closed_bound_literal=str(literal))
        remaining()
        solver.set(timeout=max(1, int(1000 * remaining())))
        arm.update(status='running', setup_seconds=time.monotonic() - started, check_started=True)
        checkpoint()
        check_started = time.monotonic()
        try:
            answer = solver.check()
        finally:
            arm['check_seconds'] = time.monotonic() - check_started
        remaining()
        arm.update(status='complete', answer=str(answer), reason='')
        if answer == z3.unsat:
            arm['integer_bound_unsat_verified'] = True
            arm['meaning'] = 'no_better_integer_bound_only; incumbent optimality not verified'
        elif answer == z3.sat:
            model = solver.model()
            _require(all(z3.is_true(model.eval(form, model_completion=True)) for form in assertions),
                     'model does not satisfy the complete queried hard formula and bound')
            if real:
                arm.update(real_sat_verified=True, meaning='real_relaxation_sat_only; no integer candidate')
            else:
                constants = [node for node in order if node.decl().kind() == z3.Z3_OP_UNINTERPRETED]
                _require(all(z3.is_int_value(model.eval(node, model_completion=True))
                             for node in constants if node.sort().kind() == z3.Z3_INT_SORT),
                         'integer SAT model contains a noninteger source value')
                arm.update(integer_sat_verified=True, meaning='integer_candidate_for_this_diagnostic_only')
            arm['objective_value'] = model.eval(objective, model_completion=True).sexpr()
        else:
            arm['reason'] = solver.reason_unknown()
        first._file(record['source'], record['source_sha256'])
        remaining()
    except Unsupported as error:
        _pending(arm, 'unsupported', str(error))
    except TimeoutError as error:
        _pending(arm, 'timeout', str(error))
    except Exception as error:
        _pending(arm, 'error', f'{type(error).__name__}: {error}')
    checkpoint()


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--arm':
        raise SystemExit('only private --arm PAYLOAD RECEIPT is supported')
    _run_arm(sys.argv[2], sys.argv[3])
