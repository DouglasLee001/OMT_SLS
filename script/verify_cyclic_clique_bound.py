#!/usr/bin/env python3
"""Check integer cyclic-schedule clique lower bounds directly from source SMT.

The proof uses only matched, unconditional source assertions.  If n integer
phases are pairwise distinct and lie in [0,T), then T >= n.  Their lifted
integer times s-k*T are also distinct: equal lifted times would make a
nonzero phase difference a multiple of T although its absolute value is < T.
The source envelopes therefore imply C=hi-lo+1 >= n.  For U >= 0, the source
objective U*T+C is at least (U+1)*n.  No solver, model, LP, or frontend is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

try:
    from smtlib import parse
    from verify_optimize import ROOT, ORIGINAL_ROOT, VerificationError, historical_integer, require
except ModuleNotFoundError:
    from script.smtlib import parse
    from script.verify_optimize import ROOT, ORIGINAL_ROOT, VerificationError, historical_integer, require

METHOD = 'exact_integer_cyclic_clique_bound_v1'


def _integer(value, label):
    require(isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value),
            label + ' must be an exact integer string')
    return int(value)


def _freeze(value):
    return tuple(_freeze(x) for x in value) if isinstance(value, list) else value


def canonical_context():
    """Use the two fixed historical subset CSVs, never a supplied case list."""
    references = {}
    for suite, relative in [('original', 'result/original/original_unsolved_bounded_sol.csv'),
                            ('new', 'result/new/new_unsolved_bounded_sol.csv')]:
        with (ROOT / relative).open(newline='') as handle:
            for row in csv.DictReader(handle):
                if suite == 'original':
                    source = (ORIGINAL_ROOT / row['filename']).resolve()
                else:
                    source = (ROOT / 'new_benchmark' / row['category'] / 'instance' / row['filename']).resolve()
                key = str(source)
                require(key not in references, 'duplicate canonical source')
                values = [historical_integer(row[column]) for column in ('z3_best_value', 'optimathsat_best_value')]
                references[key] = {'filename': row['filename'], 'references': values}
    require(len(references) == 473, 'canonical subset must contain exactly 473 unique sources')
    return references


def verify_source(text, certificate):
    """Check the exact integer proof premises; canonical identity is checked outside."""
    require(isinstance(certificate, dict) and certificate.get('method') == METHOD, 'unknown certificate method')
    names = [certificate[key] for key in ('period', 'lower', 'upper', 'span', 'objective')]
    tasks = certificate.get('tasks')
    require(isinstance(tasks, list) and tasks, 'missing nonempty task clique')
    for task in tasks:
        require(isinstance(task, dict), 'invalid task triple')
        names.extend(task[key] for key in ('phase', 'cycle', 'time'))
    require(all(isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) for name in names),
            'proof variable names must be simple SMT identifiers')
    require(len(set(names)) == len(names), 'proof variable roles must be distinct')
    weight = _integer(certificate.get('period_weight'), 'period weight')
    require(weight >= 0, 'period weight must be nonnegative')
    claimed = _integer(certificate.get('claimed_lower_bound'), 'claimed lower bound')
    period, lower, upper, span, objective = (certificate[k] for k in ('period', 'lower', 'upper', 'span', 'objective'))
    declarations, facts = {}, []
    minimum = checks = 0
    state = 'formula'
    for command in parse(text):
        require(isinstance(command, list) and command and isinstance(command[0], str), 'invalid source command')
        kind = command[0]
        if kind == 'exit':
            require(state == 'checked' and len(command) == 1, 'invalid exit ordering')
            state = 'exited'
            continue
        require(state not in ('checked', 'exited'), 'source command after check-sat')
        if kind == 'check-sat':
            require(len(command) == 1 and state == 'objective', 'check-sat must follow the unique objective')
            checks += 1
            state = 'checked'
            continue
        if kind == 'minimize':
            require(state == 'formula' and len(command) == 2 and command[1] == objective,
                    'source must have one matching minimize objective')
            minimum += 1
            state = 'objective'
            continue
        require(state == 'formula', 'formula command after optimization objective')
        if kind in ('set-info', 'set-option'):
            continue
        if kind == 'set-logic':
            require(command == ['set-logic', 'QF_NIA'], 'source must declare QF_NIA')
            continue
        if kind in ('declare-fun', 'declare-const'):
            if kind == 'declare-fun':
                require(len(command) == 4 and command[2] == [], 'only constant declarations are supported')
                name, sort = command[1], command[3]
            else:
                require(len(command) == 3, 'invalid constant declaration')
                name, sort = command[1], command[2]
            require(isinstance(name, str) and name not in declarations, 'duplicate or invalid declaration')
            declarations[name] = sort
        elif kind == 'assert':
            require(len(command) == 2, 'invalid assertion')
            facts.append(command[1])
        else:
            raise VerificationError('unsupported source command: ' + kind)
    require(minimum == checks == 1 and state in ('checked', 'exited'), 'source requires one objective and one check-sat')
    require(all(declarations.get(name) == 'Int' for name in names), 'all proof variables must be declared Int')
    positions = {}
    for index, fact in enumerate(facts):
        positions.setdefault(_freeze(fact), index)

    def premise(options, label):
        for expression in options:
            if _freeze(expression) in positions:
                return positions[_freeze(expression)]
        raise VerificationError('missing source premise: ' + label)

    evidence = {'phase_lower_bounds': [], 'phase_strict_upper_bounds': [], 'nonoverlap_pairs': [],
                'lift_equations': [], 'lower_envelopes': [], 'upper_envelopes': []}
    for task in tasks:
        phase, cycle, time = (task[k] for k in ('phase', 'cycle', 'time'))
        evidence['phase_lower_bounds'].append(premise([['>=', phase, '0'], ['<=', '0', phase]], phase + ' >= 0'))
        evidence['phase_strict_upper_bounds'].append(premise(
            [['<=', ['+', phase, '1'], period], ['<=', ['+', '1', phase], period], ['<', phase, period]], phase + ' < period'))
        lift = ['-', phase, ['*', cycle, period]]
        evidence['lift_equations'].append(premise([['=', time, lift], ['=', lift, time]], time + ' = phase-cycle*period'))
        evidence['lower_envelopes'].append(premise([['<=', lower, time], ['>=', time, lower]], lower + ' <= ' + time))
        evidence['upper_envelopes'].append(premise([['>=', upper, time], ['<=', time, upper]], time + ' <= ' + upper))
    for i, left in enumerate(tasks):
        for right in tasks[i + 1:]:
            a, b = left['phase'], right['phase']
            first, second = ['<=', ['+', a, '1'], b], ['<=', ['+', b, '1'], a]
            evidence['nonoverlap_pairs'].append(premise([['or', first, second], ['or', second, first]], a + ' != ' + b))
    span_expression = ['+', ['-', upper, lower], '1']
    evidence['span_equation'] = premise([['=', span, span_expression], ['=', span_expression, span]], 'span = upper-lower+1')
    objective_expression = ['+', ['*', str(weight), period], span]
    evidence['objective_equation'] = premise([['=', objective, objective_expression], ['=', objective_expression, objective]], 'objective = U*period+span')
    count = len(tasks)
    bound = (weight + 1) * count
    require(claimed == bound, 'claimed lower bound differs from exact clique bound')
    return {'tasks': count, 'distinct_phase_pairs': count * (count - 1) // 2,
            'period_lower_bound': str(count), 'span_lower_bound': str(count),
            'period_weight': str(weight), 'lower_bound': str(bound), 'source_assertion_evidence': evidence}


def verify_certificate(path):
    result = {'certificate': str(path), 'verified': False, 'solver_calls': False, 'lp_calls': False}
    try:
        certificate = json.loads(Path(path).read_text())
        require(isinstance(certificate, dict), 'certificate must be an object')
        source = str(Path(certificate['source']).resolve())
        references = canonical_context()
        require(source in references, 'source is not in the canonical 473-instance subset')
        metadata = references[source]
        require(certificate.get('filename') == metadata['filename'], 'certificate filename differs from canonical source')
        require(certificate.get('direction') == 'min', 'certificate direction must be canonical MIN')
        available = [value for value in metadata['references'] if value is not None]
        require(available, 'canonical source has no historical reference')
        reference = min(available)
        require(_integer(certificate.get('reference_best_value'), 'reference') == reference,
                'certificate reference differs from canonical historical minimum')
        raw = Path(source).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        require(certificate.get('source_sha256') == digest, 'original source SHA256 mismatch')
        details = verify_source(raw.decode('utf-8'), certificate)
        require(int(details['lower_bound']) >= reference, 'proved lower bound does not exclude strict reference improvement')
        result.update(verified=True, source=source, source_sha256=digest, filename=metadata['filename'], direction='min',
                      reference_best_value=str(reference), strict_improvement_proven_impossible=True, **details)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        result['error'] = str(exc)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('certificates', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    results = [verify_certificate(path) for path in args.certificates]
    sources = sorted({result['source'] for result in results if result['verified']})
    report = {'ok': all(result['verified'] for result in results), 'solver_calls': False, 'lp_calls': False,
              'certificates': results, 'verified_unique_sources': sources, 'verified_unique_source_count': len(sources)}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'ok': report['ok'], 'verified_unique_source_count': len(sources), 'solver_calls': False, 'lp_calls': False}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
