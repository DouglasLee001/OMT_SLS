#!/usr/bin/env python3
"""Verify exact integer strip bounds using only original packing SMT assertions.

At each integer horizontal strip, the active rectangles have disjoint horizontal
intervals, so their total width is at most W.  For any nonnegative integer item
weights z, let D be their exact 0/1 knapsack maximum at capacity W.  Summing over
the H strips gives sum(h_i*z_i) <= H*D.  Interchanging the axes gives the other
allowed bound.  This checker recomputes both sides and covers every source W
under cost < the canonical historical reference.  It invokes no solver or LP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    from smtlib import parse
    from verify_optimize import VerificationError, require
    from verify_cyclic_clique_bound import canonical_context
except ModuleNotFoundError:
    from script.smtlib import parse
    from script.verify_optimize import VerificationError, require
    from script.verify_cyclic_clique_bound import canonical_context

METHOD = 'exact_integer_packing_row_bound_v1'


def _integer(value, label):
    require(type(value) is int, label + ' must be an exact JSON integer')
    return value


def _reference(value):
    require(isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value),
            'reference must be an exact integer string')
    return int(value)


def _freeze(value):
    return tuple(_freeze(x) for x in value) if isinstance(value, list) else value


def knapsack_max(sizes, weights, capacity):
    """Exact 0/1 maximum; descending updates use each item at most once."""
    require(isinstance(sizes, (list, tuple)) and isinstance(weights, (list, tuple))
            and len(sizes) == len(weights), 'invalid knapsack vectors')
    require(_integer(capacity, 'knapsack capacity') >= 0, 'negative knapsack capacity')
    for size in sizes:
        require(_integer(size, 'knapsack size') > 0, 'knapsack sizes must be positive')
    for weight in weights:
        require(_integer(weight, 'knapsack weight') >= 0, 'knapsack weights must be nonnegative')
    capacity = min(capacity, sum(sizes))
    best = [0] * (capacity + 1)
    for size, weight in zip(sizes, weights):
        for remaining in range(capacity, size - 1, -1):
            best[remaining] = max(best[remaining], best[remaining - size] + weight)
    return best[capacity]


def _source_facts(text, objective):
    declarations, facts = {}, []
    minimum = checks = 0
    state = 'formula'
    for command in parse(text):
        require(isinstance(command, list) and command and isinstance(command[0], str),
                'invalid source command')
        kind = command[0]
        if kind == 'exit':
            require(state == 'checked' and len(command) == 1, 'invalid exit ordering')
            state = 'exited'
            continue
        require(state not in ('checked', 'exited'), 'source command after check-sat')
        if kind == 'check-sat':
            require(len(command) == 1 and state == 'objective',
                    'check-sat must follow the unique objective')
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
                require(len(command) == 4 and command[2] == [],
                        'only constant declarations are supported')
                name, sort = command[1], command[3]
            else:
                require(len(command) == 3, 'invalid constant declaration')
                name, sort = command[1], command[2]
            require(isinstance(name, str) and name not in declarations,
                    'duplicate or invalid declaration')
            declarations[name] = sort
        elif kind == 'assert':
            require(len(command) == 2, 'invalid assertion')
            facts.append(command[1])
        else:
            raise VerificationError('unsupported source command: ' + kind)
    require(minimum == checks == 1 and state in ('checked', 'exited'),
            'source requires one objective and one check-sat')
    return declarations, facts


def verify_source(text, certificate):
    """Verify proof premises and all width cases; the wrapper binds source/ref."""
    require(isinstance(certificate, dict) and certificate.get('method') == METHOD,
            'unknown certificate method')
    require(certificate.get('direction') == 'min', 'packing proof requires MIN')
    reference = _reference(certificate.get('reference_best_value'))
    width, height, objective, product = (certificate[key]
                                        for key in ('width', 'height', 'objective', 'product'))
    items = certificate.get('items')
    require(isinstance(items, list) and items, 'missing nonempty rectangle list')
    names = [width, height, objective]
    if product != objective:
        names.append(product)
    widths, heights = [], []
    for item in items:
        require(isinstance(item, dict), 'invalid rectangle')
        names.extend(item[key] for key in ('x', 'y'))
        widths.append(_integer(item['width'], 'rectangle width'))
        heights.append(_integer(item['height'], 'rectangle height'))
    require(all(dimension > 0 for dimension in widths + heights),
            'rectangle dimensions must be positive integers')
    require(all(isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name)
                for name in names), 'proof variable names must be simple SMT identifiers')
    require(len(set(names)) == len(names), 'proof variable roles must be distinct')
    bounds = []
    for key in ('width_bounds', 'height_bounds'):
        value = certificate.get(key)
        require(isinstance(value, list) and len(value) == 2, 'invalid ' + key)
        lo, hi = (_integer(value[i], key) for i in range(2))
        require(0 < lo <= hi, 'container bounds must be finite positive integers')
        bounds.append((lo, hi))
    (wlo, whi), (hlo, hhi) = bounds
    declarations, facts = _source_facts(text, objective)
    require(all(declarations.get(name) == 'Int' for name in names),
            'all packing proof variables must be declared Int')
    positions = {}
    pair_positions = {}
    for index, fact in enumerate(facts):
        positions.setdefault(_freeze(fact), index)
        if isinstance(fact, list) and len(fact) == 5 and fact[0] == 'or':
            pair_positions.setdefault(frozenset(_freeze(term) for term in fact[1:]), index)

    def premise(options, label):
        for expression in options:
            if _freeze(expression) in positions:
                return positions[_freeze(expression)]
        raise VerificationError('missing source premise: ' + label)

    evidence = {'container_bounds': [], 'coordinate_nonnegativity': [],
                'containment': [], 'nonoverlap_pairs': []}
    for name, (lo, hi) in zip((width, height), bounds):
        evidence['container_bounds'].append(premise(
            [['>=', name, str(lo)], ['<=', str(lo), name]], name + ' lower bound'))
        evidence['container_bounds'].append(premise(
            [['<=', name, str(hi)], ['>=', str(hi), name]], name + ' upper bound'))
    for item in items:
        for coordinate, dimension, container in ((item['x'], item['width'], width),
                                                  (item['y'], item['height'], height)):
            evidence['coordinate_nonnegativity'].append(premise(
                [['>=', coordinate, '0'], ['<=', '0', coordinate]], coordinate + ' >= 0'))
            sums = [['+', coordinate, str(dimension)], ['+', str(dimension), coordinate]]
            evidence['containment'].append(premise(
                [['<=', expression, container] for expression in sums]
                + [['>=', container, expression] for expression in sums],
                coordinate + ' containment'))
    for i, left in enumerate(items):
        for right in items[i + 1:]:
            # All four disjuncts must occur in one unconditional source OR.
            separation = frozenset(_freeze(term) for term in [
                ['<=', ['+', left['x'], str(left['width'])], right['x']],
                ['<=', ['+', right['x'], str(right['width'])], left['x']],
                ['<=', ['+', left['y'], str(left['height'])], right['y']],
                ['<=', ['+', right['y'], str(right['height'])], left['y']]])
            require(separation in pair_positions,
                    'missing source premise: complete rectangle nonoverlap')
            evidence['nonoverlap_pairs'].append(pair_positions[separation])
    products = [['*', width, height], ['*', height, width]]
    evidence['product_equation'] = premise(
        [['=', product, expression] for expression in products]
        + [['=', expression, product] for expression in products], 'product = W*H')
    if product != objective:
        evidence['objective_alias'] = premise(
            [['=', objective, product], ['=', product, objective]], 'objective = product')

    cases = certificate.get('width_cases')
    require(isinstance(cases, list) and len(cases) == whi - wlo + 1,
            'width cases do not cover the source interval')
    seen = set()
    verified_cases = []
    counts = {'horizontal_cuts': 0, 'transposed_cuts': 0, 'height_bound_exclusions': 0}
    for row in cases:
        require(isinstance(row, dict), 'invalid width case')
        w = _integer(row.get('width'), 'case width')
        require(wlo <= w <= whi and w not in seen, 'duplicate or outside source width')
        seen.add(w)
        h = min(hhi, (reference - 1) // w)
        require(_integer(row.get('height_allowed'), 'height allowed') == h,
                'height cap differs from the exact strict objective bound')
        if h < hlo:
            require(row.get('axis') == 'height_bound', 'empty source height domain must use source bound')
            counts['height_bound_exclusions'] += 1
            verified_cases.append({'width': w, 'height_allowed': h, 'axis': 'height_bound'})
            continue
        axis = row.get('axis')
        require(axis in ('horizontal', 'transposed'), 'invalid strip axis for nonempty height domain')
        weights = row.get('weights')
        require(isinstance(weights, list) and len(weights) == len(items), 'wrong strip weight vector length')
        require(all(_integer(z, 'strip weight') >= 0 for z in weights), 'negative strip weight')
        sizes, other_sizes, capacity, other_capacity = ((widths, heights, w, h)
            if axis == 'horizontal' else (heights, widths, h, w))
        denominator = knapsack_max(sizes, weights, capacity)
        numerator = sum(size * z for size, z in zip(other_sizes, weights))
        require(_integer(row.get('D'), 'claimed D') == denominator, 'claimed D differs from exact 0/1 maximum')
        require(_integer(row.get('N'), 'claimed N') == numerator, 'claimed N differs from exact weighted dimensions')
        require(numerator > other_capacity * denominator,
                'strip inequality does not exclude the entire allowed box')
        counts['horizontal_cuts' if axis == 'horizontal' else 'transposed_cuts'] += 1
        verified_cases.append({'width': w, 'height_allowed': h, 'axis': axis,
                               'D': denominator, 'N': numerator})
    require(seen == set(range(wlo, whi + 1)), 'source width interval not completely covered')
    return {'items': len(items), 'geometry_pairs': len(evidence['nonoverlap_pairs']),
            'widths_verified': len(seen), 'width_bounds': [wlo, whi], 'height_bounds': [hlo, hhi],
            'source_assertion_evidence': evidence, 'verified_width_cases': verified_cases,
            'solver_calls': False, 'lp_calls': False, **counts}


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
        require(_reference(certificate.get('reference_best_value')) == reference,
                'certificate reference differs from canonical historical minimum')
        raw = Path(source).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        require(certificate.get('source_sha256') == digest, 'original source SHA256 mismatch')
        details = verify_source(raw.decode('utf-8'), certificate)
        result.update(verified=True, source=source, source_sha256=digest, filename=metadata['filename'],
                      direction='min', reference_best_value=str(reference),
                      strict_improvement_proven_impossible=True, **details)
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
    print(json.dumps({'ok': report['ok'], 'verified_unique_source_count': len(sources),
                      'solver_calls': False, 'lp_calls': False}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
