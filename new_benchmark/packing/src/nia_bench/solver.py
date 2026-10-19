"""Run the requested Z3 executable and independently check application decisions."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
import time

from .core import audit_smt, exact_int, json_text, load_family, num, parse_values


def status_of(stdout, returncode=0):
    lines = [line.strip() for line in stdout.splitlines()]
    states = [line for line in lines if line in ('sat', 'unsat', 'unknown')]
    if any(line == 'unsupported' or line.startswith('unsupported ') for line in lines):
        return 'error'
    errors = [line for line in lines if line.startswith('(error')]
    cancellation = lambda line: re.search(r': (?:push )?canceled"\)?$',line) is not None
    if errors and any(cancellation(line) for line in errors) and all(cancellation(line) or 'model is not available' in line for line in errors):
        return 'timeout'
    no_model_after_nonsat = states in (['unknown'],['unsat']) and errors and all('model is not available' in line for line in errors)
    if errors and not no_model_after_nonsat:
        return 'error'
    if len(states) == 1:
        state = states[0]
        return 'error' if returncode and state != 'unknown' and not no_model_after_nonsat else state
    if len(states) > 1:
        return 'error'
    if 'timeout' in stdout.lower():
        return 'timeout'
    return 'error'


def query(text, executable, seconds, directory, tag):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Solver timeout must be positive and finite')
    directory.mkdir(parents=True, exist_ok=True)
    source = directory/(tag+'.smt2')
    source.write_text(f'(set-option :timeout {max(1,math.ceil(seconds*1000))})\n'
                      '(set-option :produce-models true)\n'+text)
    command = [str(Path(executable).resolve()), '-smt2', str(source.resolve())]
    start = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=seconds+.75)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        state = status_of(stdout+'\n'+stderr, code)
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
        stdout, stderr, code = decoded(exc.stdout), decoded(exc.stderr), None
        state = 'timeout'
    elapsed = time.monotonic()-start
    (directory/(tag+'.stdout.txt')).write_text(stdout)
    (directory/(tag+'.stderr.txt')).write_text(stderr)
    return {'status': state, 'elapsed_seconds': elapsed, 'timeout_seconds': seconds,
            'returncode': code, 'command': command, 'stdout': stdout,
            'stderr': stderr}


def checked_reply(module, data, result):
    values = parse_values(result['stdout'])
    decision = module.decode(data, values)
    checked = module.check(data, decision)
    if exact_int(checked['cost']) != values['cost']:
        raise ValueError('SMT objective disagrees with independent application objective')
    return {'decision': decision, 'cost': str(checked['cost']), 'application_check': 'passed'}


def concise(result):
    return {k:v for k,v in result.items() if k not in ('stdout','stderr')}


def probe_case(path, executable, seconds=3, module=None, verify_candidate=False):
    path = Path(path)
    metadata = json.loads((path/'metadata.json').read_text())
    data = json.loads((path/'data.json').read_text())
    witness = json.loads((path/'witness.json').read_text())
    base = (path/'base.smt2').read_text()
    module = module or load_family(metadata['family'])
    model = module.build(data)
    if model.base() != base:
        raise ValueError('Stored base differs from regenerated application model: '+str(path))
    audit = audit_smt(base)
    expected_opt = base+f'({metadata["objective"]["sense"]} cost)\n(check-sat)\n'
    if (path/'opt.smt2').read_text() != expected_opt:
        raise ValueError('Optimization file differs from base or has extra commands')
    validation = json.loads((path/'validation.json').read_text())
    directory = path/'z3'
    report = {'theory_audit': {'status':'passed', **audit},
              'pinned_witness': {'status':'not_available'},
              'optimality': {'status':'not_checked'},
              'strict_improvement': {'status':'not_checked'}}
    usable = None
    if 'decision' in witness:
        checked = module.check(data, witness['decision'])
        if exact_int(checked['cost']) != exact_int(witness['cost']):
            raise ValueError('Stored witness has an incorrect objective')
        values = module.assignment(data, witness['decision'])
        if not set(values).issubset(model.variables):
            raise ValueError('Witness maps undeclared SMT variables')
        pins = ''.join(f'(assert (= {name} {num(value)}))\n' for name,value in sorted(values.items()))
        pins += f'(assert (= cost {num(witness["cost"])}))\n'
        pinned = query(base+pins+'(check-sat)\n', executable, seconds, directory, 'witness')
        report['pinned_witness'] = concise(pinned)
        report['application_witness'] = {'status':'passed', 'cost':str(checked['cost'])}
        if pinned['status'] == 'sat':
            usable = {'decision': witness['decision'], 'cost':str(checked['cost'])}
    names = ' '.join(model.variables)
    optimized = query(expected_opt+f'(get-value ({names}))\n', executable, seconds, directory, 'optimization')
    report['optimization'] = concise(optimized)
    if optimized['status'] in ('sat','unknown','timeout'):
        try:
            candidate = checked_reply(module, data, optimized)
            report['optimization'].update(candidate)
            usable = candidate
            (directory/'optimization_solution.json').write_text(json_text(candidate))
        except (ValueError, KeyError, TypeError) as exc:
            if optimized['status']=='sat':
                report['optimization'].update(application_check='failed', error=str(exc))
    elif optimized['status']=='unsat':
        report['optimality'] = {'status':'unsat_solver_checked', 'method':'Z3 reports base constraints UNSAT'}
    if usable:
        report['optimality'] = {'status':'feasible_only', 'cost':usable['cost']}
    expected = metadata.get('expected_optimum')
    if expected is None and verify_candidate and usable:
        expected = usable['cost']
    if expected is not None:
        expected = str(expected)
        feasible_expected = usable if usable and usable['cost']==expected else None
        if feasible_expected is None and 'decision' in witness and witness['cost']==expected and report['pinned_witness']['status']=='sat':
            feasible_expected = witness
        if feasible_expected is None:
            fixed = query(base+f'(assert (= cost {num(expected)}))\n(check-sat)\n(get-value ({names}))\n',
                          executable, seconds, directory, 'expected_value')
            report['expected_value'] = concise(fixed)
            if fixed['status'] == 'sat':
                try:
                    feasible_expected = checked_reply(module, data, fixed)
                    report['expected_value'].update(feasible_expected)
                except (ValueError,KeyError,TypeError) as exc:
                    report['expected_value'].update(application_check='failed',error=str(exc))
        comparison = '<' if metadata['objective']['sense']=='minimize' else '>'
        improvement = query(base+f'(assert ({comparison} cost {num(expected)}))\n(check-sat)\n',
                            executable, seconds, directory, 'strict_improvement')
        report['strict_improvement'] = concise(improvement)
        if feasible_expected is not None and improvement['status'] == 'unsat':
            report['optimality'] = {'status':'optimal_solver_checked', 'cost':expected,
                                    'method':'independently checked feasible decision plus strict improvement UNSAT'}
        elif improvement['status'] == 'sat':
            report['optimality'] = {'status':'expected_value_refuted', 'claimed_cost':expected}
    validation.update(report)
    validation['z3'] = {'status':'tested', 'executable':str(Path(executable).resolve()),
                        'timeout_seconds':seconds, 'optimization_status':optimized['status']}
    (path/'validation.json').write_text(json_text(validation))
    return report
