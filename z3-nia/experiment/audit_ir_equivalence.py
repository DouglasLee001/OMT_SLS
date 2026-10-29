#!/usr/bin/env python3
"""Exact structural IR equivalence certificates; never invokes Z3 or a solver.

First compare named polynomials and CNF after reindexing. If needed, verify and
eliminate complete acyclic Tseitin definitions of fresh Boolean auxiliaries.
NNF/AC normalization and inverse distributivity then give a shared exact DAG.
Every current IR pair is read and checked directly. Byte-identical labels
require exact byte equality; SHA256 values bind evidence. Structural equality
is checked on exact structures, not on hashes. This compares the two IRs, not either IR against its original SMT2.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_ir(path, *, contents=None):
    # Parse the same immutable bytes whose hash was checked by the caller.
    lines = (Path(path).read_text() if contents is None else contents.decode()).splitlines()
    position = 0

    def take():
        nonlocal position
        require(position < len(lines), 'truncated native IR')
        value = lines[position].split()
        position += 1
        return value

    require(take() == ['NIA_LS_OMT', '1'], 'unsupported native IR header')
    metadata = {}
    for key in ('STATUS', 'SOURCE', 'ORIGINAL_OBJECTIVE', 'DIRECTION'):
        row = take()
        require(len(row) == 2 and row[0] == key, 'invalid metadata: ' + key)
        metadata[key] = row[1]
    row = take()
    require(len(row) == 2 and row[0] == 'VARIABLES', 'missing VARIABLES')
    variables = {}
    for expected in range(int(row[1])):
        row = take()
        require(len(row) == 3 and int(row[0]) == expected and row[1] in ('I', 'B'), 'invalid variable')
        variables[expected] = (row[1], bytes.fromhex(row[2]).decode('utf-8'))
    require(len({name for kind, name in variables.values()}) == len(variables), 'duplicate variable name')

    def polynomial(header):
        require(len(header) == 2, 'invalid polynomial header')
        constant, count = map(int, header)
        require(count >= 0, 'negative term count')
        terms = {}
        for _ in range(count):
            row = list(map(int, take()))
            require(len(row) >= 2 and len(row) == 2 + row[1] * 2 and row[1] > 0, 'invalid monomial')
            factors = []
            for at in range(2, len(row), 2):
                require(row[at] in variables and variables[row[at]][0] == 'I' and row[at+1] > 0,
                        'invalid integer variable/exponent')
                factors.append((variables[row[at]], row[at+1]))
            require(len({v for v, power in factors}) == len(factors), 'duplicate monomial variable')
            monomial = tuple(sorted(factors))
            require(monomial not in terms and row[0] != 0, 'noncanonical duplicate/zero term')
            terms[monomial] = row[0]
        # Built-in Python integers are exact, matching the native GMP keys.
        return constant, tuple(sorted(terms.items()))

    row = take()
    require(len(row) == 2 and row[0] == 'LITERALS', 'missing LITERALS')
    literals = {}
    for expected in range(1, int(row[1]) + 1):
        row = take()
        require(len(row) >= 3 and int(row[0]) == expected, 'invalid literal index')
        kind = row[1]
        if kind == 'B':
            require(len(row) == 3 and variables[int(row[2])][0] == 'B', 'invalid Boolean literal')
            literals[expected] = ('B', variables[int(row[2])][1])
        else:
            require(kind in ('E', 'L'), 'unsupported arithmetic literal')
            literals[expected] = ('A', (kind, polynomial(row[2:])))
    row = take()
    require(len(row) == 2 and row[0] == 'CLAUSES', 'missing CLAUSES')
    clauses = []
    for _ in range(int(row[1])):
        row = list(map(int, take()))
        require(row and len(row) == row[0] + 1, 'invalid clause length')
        require(all(x and abs(x) in literals for x in row[1:]), 'invalid clause literal')
        # OR is idempotent, so duplicate literals may be removed exactly.
        clauses.append(frozenset((1 if x > 0 else -1, literals[abs(x)]) for x in row[1:]))
    row = take()
    require(row and row[0] == 'OBJECTIVE', 'missing OBJECTIVE')
    objective = polynomial(row[1:])
    require(take() == ['END'] and position == len(lines), 'invalid native IR trailer')
    return dict(metadata=metadata, variables=variables, literals=literals,
                clauses=clauses, objective=objective)


class BooleanDAG:
    """Hash-cons exact immutable structures; IDs are not probabilistic hashes."""
    def __init__(self):
        self.nodes = [('const', False), ('const', True)]
        self.intern = {value: at for at, value in enumerate(self.nodes)}
        self.hashes = {}

    def atom(self, value, sign):
        return self._intern(('atom', (value, sign)))

    def _intern(self, key):
        if key not in self.intern:
            self.intern[key] = len(self.nodes)
            self.nodes.append(key)
        return self.intern[key]

    def node(self, tag, children):
        require(tag in ('and', 'or'), 'unsupported Boolean connective')
        flat = set()
        for child in set(children):
            if child == (0 if tag == 'and' else 1):
                return child
            if child == (1 if tag == 'and' else 0):
                continue
            if self.nodes[child][0] == tag:
                flat.update(self.nodes[child][1])
            else:
                flat.add(child)
        if not flat:
            return 1 if tag == 'and' else 0
        if len(flat) == 1:
            return next(iter(flat))
        return self._intern((tag, tuple(sorted(flat))))

    def sha256(self, root):
        if root not in self.hashes:
            tag, value = self.nodes[root]
            if tag in ('and', 'or'):
                payload = tag + ':' + ','.join(sorted(self.sha256(c) for c in value))
            else:
                payload = repr((tag, value))
            self.hashes[root] = hashlib.sha256(payload.encode()).hexdigest()
        return self.hashes[root]


def project_boolean_definitions(clauses, booleans, dag):
    """Return an exact formula for exists booleans: CNF, or refuse to certify."""
    negate = lambda literal: (-literal[0], literal[1])
    binaries = {clause for clause in clauses if len(clause) == 2}
    definitions, used = {}, set()
    for clause in clauses:
        if len(clause) < 3:
            continue
        for literal in sorted(clause, key=repr):
            sign, atom = literal
            if atom[0] != 'B':
                continue
            children = tuple(sorted(clause - {literal}, key=repr))
            required = {frozenset((negate(literal), negate(child))) for child in children}
            if required <= binaries:
                name = atom[1]
                candidate = (sign, children, clause, required)
                require(name not in definitions or definitions[name][:2] == candidate[:2],
                        'ambiguous Boolean definition: ' + name)
                definitions[name] = candidate
    require(set(definitions) == set(booleans), 'missing complete Boolean definitions')
    for sign, children, clause, required in definitions.values():
        used.add(clause)
        used.update(required)
    memo, stack = {}, set()

    def expand(literal):
        sign, atom = literal
        if atom[0] == 'A':
            return dag.atom(atom[1], sign)
        name = atom[1]
        key = name, sign
        if key in memo:
            return memo[key]
        require(name not in stack, 'cyclic Boolean definitions')
        require(name in definitions, 'undefined Boolean auxiliary')
        stack.add(name)
        definition_sign, children, _, _ = definitions[name]
        # (s*b OR c1 OR ... OR cn) AND every (-s*b OR -ci)
        # is the full iff: (-s*b) <-> OR(c1,...,cn).
        positive_or = sign == -definition_sign
        values = [expand(c if positive_or else negate(c)) for c in children]
        result = dag.node('or' if positive_or else 'and', values)
        stack.remove(name)
        memo[key] = result
        return result

    # Check all definitions, even auxiliaries absent from residual clauses.
    # An unused cyclic/contradictory definition must never be silently dropped.
    for name in sorted(booleans):
        expand((1, ('B', name)))
    require({name for name, sign in memo} == set(booleans), 'unchecked Boolean definition')
    residual = [clause for clause in clauses if clause not in used]
    root = dag.node('and', [dag.node('or', [expand(x) for x in sorted(c, key=repr)]) for c in residual])
    factor_trace = []
    while dag.nodes[root][0] == 'and':
        children = set(dag.nodes[root][1])
        disjunctions = [c for c in children if dag.nodes[c][0] == 'or']
        best = None
        for at, left in enumerate(disjunctions):
            left_set = set(dag.nodes[left][1])
            for right in disjunctions[at+1:]:
                right_set = set(dag.nodes[right][1])
                shared = left_set & right_set
                if shared and (best is None or len(shared) > best[0]):
                    best = len(shared), left, right, shared, left_set-shared, right_set-shared
        if best is None:
            break
        _, left, right, shared, a, b = best
        # Valid for arbitrary Boolean subformulas, with empty OR = false:
        # (X OR A) AND (X OR B) == X OR (A AND B).
        replacement = dag.node('or', list(shared) + [dag.node('and', [dag.node('or', a), dag.node('or', b)])])
        factor_trace.append(dict(identity='(X or A) and (X or B) = X or (A and B)',
                                 left_sha256=dag.sha256(left), right_sha256=dag.sha256(right),
                                 replacement_sha256=dag.sha256(replacement),
                                 shared_count=len(shared), left_remainder_count=len(a), right_remainder_count=len(b)))
        children.difference_update((left, right))
        children.add(replacement)
        root = dag.node('and', children)
    return dict(root=root, formula_sha256=dag.sha256(root), boolean_definitions=len(definitions),
                all_definitions_checked_acyclic=True, definition_clause_set_count=len(used),
                residual_clause_count=len(residual), factor_steps=len(factor_trace), factor_trace=factor_trace)


def compare_irs(left, right, source):
    require(left['metadata'] == right['metadata'], 'different IR metadata')
    require(left['objective'] == right['objective'], 'different exact objective polynomials')
    require(Counter(v for v in left['variables'].values() if v[0] == 'I') ==
            Counter(v for v in right['variables'].values() if v[0] == 'I'), 'different integer variables')
    if (Counter(left['variables'].values()) == Counter(right['variables'].values()) and
            Counter(left['literals'].values()) == Counter(right['literals'].values()) and
            Counter(left['clauses']) == Counter(right['clauses'])):
        return dict(equivalent=True, method='exact_named_polynomials_and_cnf',
                    objective_exact_equal=True, identities=['variable/literal reindexing', 'AND/OR commutativity and idempotence'])
    # Conservative freshness check: any occurrence, even in a comment, refuses
    # this fallback. These benchmark sources declare integer variables only.
    require('Bool' not in source, 'cannot certify auxiliaries fresh in a source containing Bool')
    dag, projections = BooleanDAG(), {}
    for variant, ir in [('baseline', left), ('embedded', right)]:
        names = {name for kind, name in ir['variables'].values() if kind == 'B'}
        require(all(name.startswith('k!') and name[2:].isdigit() for name in names), 'unrecognized fresh Boolean name')
        projections[variant] = project_boolean_definitions(ir['clauses'], names, dag)
    same = projections['baseline']['root'] == projections['embedded']['root']
    require(same, 'normalized Boolean projections differ; equivalence unproved')
    for value in projections.values():
        del value['root']
    return dict(equivalent=True, method='exact_tseitin_boolean_projection', objective_exact_equal=True,
                original_source_contains_Bool=False, projected_structures_exact_equal=same,
                identities=['complete acyclic Boolean iff substitution under existential projection',
                            'De Morgan and double negation (NNF)', 'AND/OR associativity, commutativity and idempotence',
                            '(X or A) and (X or B) = X or (A and B)'], projections=projections)


def audit(run):
    run = Path(run).resolve()
    require(digest(run/'manifest.json') == (run/'manifest.sha256').read_text().strip(), 'frozen manifest changed')
    plan = json.loads((run/'manifest.json').read_text())
    import csv
    with (run/'verification/full-native-ir.csv').open(newline='') as handle:
        byte_rows = list(csv.DictReader(handle))
    require(len(byte_rows) == len(plan['cases']) == 6310, 'full byte audit is incomplete')
    require({int(r['index']) for r in byte_rows} == set(range(6310)), 'foreign/duplicate byte audit index')
    # Build once, then read every retained pair. A prior CSV label is not proof.
    paths_by_index = [{} for _ in plan['cases']]
    for task in plan['tasks']:
        if task['phase'] != 'full':
            continue
        variant = task['variant']
        require(variant in ('baseline', 'embedded'), 'unknown full task variant')
        for local, index in enumerate(task['indices']):
            require(type(index) is int and 0 <= index < len(plan['cases']), 'foreign full task index')
            case = plan['cases'][index]
            name = f'{local:04d}-' + hashlib.sha256(case['source'].encode()).hexdigest()[:12]
            require(variant not in paths_by_index[index], 'duplicate full task assignment')
            paths_by_index[index][variant] = run/'tasks'/task['id']/'run/cases'/name/'input.nia'
    require(all(set(paths) == {'baseline', 'embedded'} for paths in paths_by_index), 'incomplete full IR assignment')
    certificates = []
    identical_count = verified_pairs = 0
    for row in byte_rows:
        index = int(row['index'])
        case = plan['cases'][index]
        certificate = dict(index=index, filename=case['filename'], byte_encoding=row['encoding'])
        try:
            require(row['encoding'] in ('identical', 'different'), 'native encoding not available')
            contents, ir_hashes = {}, {}
            for variant, path in paths_by_index[index].items():
                data = path.read_bytes()
                require(data.startswith(b'NIA_LS_OMT 1\n') and data.endswith(b'\nEND\n'),
                        'incomplete native IR: ' + variant)
                actual_hash = hashlib.sha256(data).hexdigest()
                require(actual_hash == row[variant+'_ir_sha256'], 'native IR changed since byte audit: ' + variant)
                contents[variant], ir_hashes[variant] = data, actual_hash
            verified_pairs += 1
            if row['encoding'] == 'identical':
                require(contents['baseline'] == contents['embedded'],
                        'identical byte-audit label disagrees with current bytes')
                identical_count += 1
                continue
            source = Path(case['source'])
            source_contents = source.read_bytes()
            source_hash = hashlib.sha256(source_contents).hexdigest()
            require(source_hash == plan['benchmark_hashes'][str(source)], 'benchmark source changed')
            certificate.update(compare_irs(
                read_ir(paths_by_index[index]['baseline'], contents=contents['baseline']),
                read_ir(paths_by_index[index]['embedded'], contents=contents['embedded']),
                source_contents.decode()))
            certificate.update(ir_sha256=ir_hashes, source_sha256=source_hash)
        except (ValueError, KeyError, IndexError, OSError) as error:
            certificate.update(equivalent=False, error=str(error))
        certificates.append(certificate)
    counts = Counter(r['method'] if r.get('equivalent') else 'unproved' for r in certificates)
    counts['byte_identical'] = identical_count
    equivalent_count = identical_count + sum(bool(c.get('equivalent')) for c in certificates)
    return dict(scope='full 6310 retained IR encodings', expected=6310,
                all_equivalent=equivalent_count == 6310, counts=dict(counts),
                current_ir_pairs_read_and_hashed=verified_pairs,
                byte_identity_check='both current IR files read directly; complete header/trailer and recorded hashes checked; exact bytes compared',
                manifest_sha256=digest(run/'manifest.json'), byte_audit_sha256=digest(run/'verification/full-native-ir.csv'),
                audit_script_sha256=digest(Path(__file__)), certificates=certificates,
                limitation='IR-to-IR equivalence only; no solver invocation or independent replay of UNSAT claims.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = audit(args.run)
    if args.output:
        with args.output.open('x') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps({key: report[key] for key in ('expected', 'all_equivalent', 'counts', 'audit_script_sha256')}, indent=2))
    return 0 if report['all_equivalent'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
