#!/usr/bin/env python3
"""Independent product-graph source relaxation checker, never an optimizer.

Certificate structure alone does not prove a bound. Only a fresh QF_LIA UNSAT
replay of exactly the reconstructed relaxation does; no incumbent is verified.
"""
from __future__ import annotations

from collections import Counter
import argparse
import builtins
import ctypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time

sys.dont_write_bytecode = True
_path = Path(__file__).with_name('verify_objective_slice_certificate.py')
_spec = importlib.util.spec_from_file_location('_product_graph_independent_base', _path)
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
VerificationError = base.VerificationError
require = base.require
MAX_PRODUCTS, MAX_CUTS = 50000, 500000


class Budget:
    def __init__(self, deadline):
        require(type(deadline) in (int, float) and math.isfinite(deadline), 'invalid absolute deadline')
        self.deadline = deadline

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('product-graph verification deadline reached')

    def milliseconds(self):
        self.check()
        result = int(min((self.deadline - time.monotonic()) * 1000, (1 << 32) - 1))
        if result < 1:
            raise TimeoutError('no product-graph replay time remains')
        return result


class NumericIdentity:
    """Exact identity plus ground-Int evaluation, comparison duals, association.

    SMT2 prints a negative numeral as (- n), which reparses to unary minus.
    Only closed integer ADD/SUB/MUL/UMINUS terms are evaluated; expressions
    involving variables and every Boolean branch stay intact. GE(a,b) is
    normalized to LE(b,a), and GT to LT, without changing strictness or bounds.
    """
    def __init__(self, z3, budget):
        self.z3, self.budget = z3, budget
        self.identity, self.memo = base.AstIdentity(z3, budget), {}

    def key(self, expression):
        z3, pending = self.z3, [(expression, False)]
        arithmetic = {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_MUL, z3.Z3_OP_UMINUS}
        while pending:
            self.budget.check()
            current, ready = pending.pop()
            if current.get_id() in self.memo:
                continue
            require(z3.is_app(current), 'identity contains quantified/bound terms')
            if not ready:
                require(len(self.memo) + len(pending) <= base.MAX_AST_NODES * 4, 'numeric identity resource guard exceeded')
                pending.append((current, True))
                pending.extend((child, False) for child in reversed(current.children()))
                continue
            children = [self.memo[child.get_id()][1] for child in current.children()]
            kind = current.decl().kind()
            if children and kind in arithmetic and all(z3.is_int_value(child) for child in children):
                values = [child.as_long() for child in children]
                if kind == z3.Z3_OP_ADD:
                    value = sum(values)
                elif kind == z3.Z3_OP_SUB:
                    value = values[0] - sum(values[1:])
                elif kind == z3.Z3_OP_UMINUS:
                    require(len(values) == 1, 'malformed integer negation')
                    value = -values[0]
                else:
                    value = math.prod(values)
                require(abs(value).bit_length() <= base.MAX_BITS, 'numeric identity bit guard exceeded')
                rebuilt = z3.IntVal(base.decimal_text(value))
            elif kind == z3.Z3_OP_GE and len(children) == 2:
                rebuilt = children[1].__le__(children[0])
            elif kind == z3.Z3_OP_GT and len(children) == 2:
                rebuilt = children[1].__lt__(children[0])
            else:
                rebuilt = current.decl()(*children) if children else current
            self.memo[current.get_id()] = current, rebuilt
            require(len(self.memo) <= base.MAX_AST_NODES, 'numeric identity node guard exceeded')
        return self.identity.key(self.memo[expression.get_id()][1])


def _lia(z3, roots, budget):
    """Reject every nonlinear, quantified, foreign-theory or UF query node."""
    seen, todo, ground = set(), list(roots), {}
    arithmetic = {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS}
    comparisons = {z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT}
    boolean = {z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES,
               z3.Z3_OP_IFF, z3.Z3_OP_XOR}

    def integer_constant(expression):
        pending = [(expression, False)]
        operations = arithmetic | {z3.Z3_OP_MUL}
        while pending:
            budget.check()
            current, ready = pending.pop()
            if current.get_id() in ground:
                continue
            if z3.is_int_value(current):
                ground[current.get_id()] = True
            elif (not z3.is_app(current) or current.sort().kind() != z3.Z3_INT_SORT
                  or current.decl().kind() not in operations or not current.num_args()):
                ground[current.get_id()] = False
            elif ready:
                ground[current.get_id()] = all(ground[child.get_id()] for child in current.children())
            else:
                pending.append((current, True))
                pending.extend((child, False) for child in current.children())
            require(len(ground) <= base.MAX_AST_NODES, 'constant coefficient guard exceeded')
        return ground[expression.get_id()]
    while todo:
        budget.check()
        node = todo.pop()
        if node.get_id() in seen:
            continue
        seen.add(node.get_id())
        require(len(seen) <= base.MAX_AST_NODES, 'LIA DAG resource guard exceeded')
        require(z3.is_app(node), 'LIA query contains quantified/bound terms')
        sort, kind, children = node.sort().kind(), node.decl().kind(), node.children()
        sorts = [child.sort().kind() for child in children]
        valid = sort in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT)
        if z3.is_int_value(node) or z3.is_true(node) or z3.is_false(node):
            valid = valid and not children
        elif kind == z3.Z3_OP_UNINTERPRETED:
            valid = valid and not children
        elif kind in arithmetic:
            valid = sort == z3.Z3_INT_SORT and bool(children) and all(s == z3.Z3_INT_SORT for s in sorts)
        elif kind == z3.Z3_OP_MUL:
            valid = (sort == z3.Z3_INT_SORT and bool(children) and all(s == z3.Z3_INT_SORT for s in sorts)
                     and sum(not integer_constant(child) for child in children) <= 1)
        elif kind in comparisons:
            valid = sort == z3.Z3_BOOL_SORT and sorts == [z3.Z3_INT_SORT] * 2
        elif kind in (z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT):
            valid = sort == z3.Z3_BOOL_SORT and bool(children) and len(set(sorts)) == 1 and sorts[0] in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT)
        elif kind in boolean:
            valid = sort == z3.Z3_BOOL_SORT and all(s == z3.Z3_BOOL_SORT for s in sorts)
        elif kind == z3.Z3_OP_ITE:
            valid = len(children) == 3 and sorts == [z3.Z3_BOOL_SORT, sort, sort] and sort in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT)
        else:
            valid = False
        require(valid, 'query/factor is not supported QF_LIA: ' + str(node.decl().name()))
        todo.extend(children)


def _name(token):
    return token[1:-1] if token.startswith('|') and token.endswith('|') else token


def _source_namespace(strict, filtered, budget):
    result = set()
    for command in strict._commands(filtered):
        budget.check()
        if command and command[0] in ('declare-const', 'declare-fun', 'define-fun'):
            require(len(command) >= 3 and isinstance(command[1], str), 'malformed source declaration')
            result.add(_name(command[1]))
    return result


def _cut(z3, entry, products, budget):
    budget.check()
    require(isinstance(entry, dict), 'cut must be an object')
    kind, name = entry.get('kind'), entry.get('product')
    require(name in products, 'cut refers to a foreign product')
    p, left, right = products[name]
    allowed = {'kind', 'product'}
    if kind == 'zero':
        forms = [(p == 0) == z3.Or(left == 0, right == 0)]
    elif kind == 'square_nonnegative':
        require(z3.eq(left, right), 'square lemma on unequal factors')
        forms = [p >= 0]
    elif kind == 'integer_square':
        require(z3.eq(left, right), 'integer square lemma on unequal factors')
        a = base.integer(entry.get('a'), 'square anchor')
        allowed.add('a')
        forms = [p >= (2 * a + 1) * left - a * (a + 1)]
    elif kind == 'orthant':
        a, b = base.integer(entry.get('a'), 'orthant anchor a'), base.integer(entry.get('b'), 'orthant anchor b')
        allowed.update(('a', 'b'))
        q = a * right + b * left - a * b
        forms = [z3.Implies(z3.And(left >= a, right >= b), p >= q),
                 z3.Implies(z3.And(left <= a, right <= b), p >= q),
                 z3.Implies(z3.And(left >= a, right <= b), p <= q),
                 z3.Implies(z3.And(left <= a, right >= b), p <= q)]
    elif kind in ('line_left', 'line_right'):
        a = base.integer(entry.get('a'), 'line anchor')
        allowed.add('a')
        forms = ([z3.Or(z3.Not(left == a), p == a * right)] if kind == 'line_left'
                 else [z3.Or(z3.Not(right == a), p == a * left)])
    else:
        raise VerificationError('unsupported cut schema: ' + str(kind))
    require(set(entry) == allowed, 'cut has missing or extra schema fields')
    return forms


def validate_certificate(z3, cert, source_text, strict_module, deadline):
    """Return exact validated LIA assertions; never check or claim a proof."""
    budget = Budget(deadline); budget.check()
    try:
        require(isinstance(cert, dict) and cert.get('version') == 'product-graph-lia-v1', 'unsupported certificate version')
        require(isinstance(source_text, str), 'authoritative source must be text')
        source_hash = hashlib.sha256(source_text.encode('utf-8')).hexdigest()
        require(cert.get('source_sha256') == source_hash, 'certificate source SHA256 differs from authoritative source')
        base.guard_text(source_text, budget, 'source')
        base.guard_source_expansion(source_text, budget)
        filtered, direction, _ = strict_module._query(source_text)
        require(cert.get('direction') == direction, 'certificate direction differs from source')
        best = base.integer(cert.get('M'), 'own incumbent M')
        original = z3.Optimize(); original.from_string(filtered)
        budget.check()
        hard, objectives = list(original.assertions()), original.objectives()
        require(len(objectives) == 1 and objectives[0].sort().kind() == z3.Z3_INT_SORT, 'expected one integer source objective')
        objective = objectives[0]
        declarations = base.constants(z3, hard + [objective], budget)
        source_names = _source_namespace(strict_module, filtered, budget)
        identity = NumericIdentity(z3, budget)
        stated_obj = base.parse_expression(z3, cert['original_objective'], declarations, budget, boolean=False)
        require(identity.key(stated_obj) == identity.key(objective), 'certificate objective differs from source')
        value = z3.IntVal(base.decimal_text(best + (-1 if direction == 'min' else 1)))
        # Explicit method avoids the IntNumRef subclass reflected comparison.
        bound = objective.__le__(value) if direction == 'min' else objective.__ge__(value)
        expected_source = hard + [bound]
        records, lifted_texts, cuts = cert['products'], cert['lifted_assertions'], cert['cuts']
        require(isinstance(records, list) and len(records) <= MAX_PRODUCTS, 'invalid product graph size')
        require(isinstance(lifted_texts, list) and len(lifted_texts) == len(expected_source), 'missing or extra lifted source assertion')
        require(isinstance(cuts, list) and len(cuts) <= MAX_CUTS, 'invalid cut list size')
        products, expansions = {}, []
        for record in records:
            budget.check()
            require(isinstance(record, dict) and set(record) == {'name', 'left', 'right'}, 'malformed product record')
            name = record['name']
            require(isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_!.]*', name) is not None, 'invalid fresh product name')
            require(name not in source_names and name not in declarations, 'product name is not fresh')
            left = base.parse_expression(z3, record['left'], declarations, budget, boolean=False)
            right = base.parse_expression(z3, record['right'], declarations, budget, boolean=False)
            _lia(z3, [left, right], budget)
            p = z3.Int(name)
            expanded_left = z3.substitute(left, *expansions) if expansions else left
            expanded_right = z3.substitute(right, *expansions) if expansions else right
            expansion = expanded_left * expanded_right
            # Charge expanded DAG identity before retaining it to guard a
            # potentially exponential producer-supplied definition tree.
            identity.key(expansion)
            expansions.append((p, expansion))
            declarations[name] = p
            products[name] = p, left, right
        lifted = [base.parse_expression(z3, text, declarations, budget, boolean=True) for text in lifted_texts]
        _lia(z3, lifted, budget)
        for actual, source in zip(lifted, expected_source):
            expanded = z3.substitute(actual, *expansions) if expansions else actual
            require(identity.key(expanded) == identity.key(source), 'lifted graph does not expand to complete original source query')
        lemmas = []
        for entry in cuts:
            budget.check()
            lemmas.extend(_cut(z3, entry, products, budget))
            require(len(lemmas) <= MAX_CUTS * 4, 'expanded cut resource guard exceeded')
        expected = lifted + lemmas
        _lia(z3, expected, budget)
        query = cert['query_smt2']
        require(isinstance(query, str) and cert.get('query_sha256') == hashlib.sha256(query.encode()).hexdigest(), 'query SHA256 mismatch')
        actual = base.query_assertions(z3, query, declarations, expected, budget, identity)
        _lia(z3, actual, budget)
        budget.check()
        report = dict(certificate_valid=True, source_sha256=source_hash, direction=direction, M=cert['M'],
                      products=len(products), lifted_assertions=len(lifted), cuts=len(cuts), lemma_assertions=len(lemmas),
                      exact_query_verified=True, complete_source_expansion_verified=True,
                      bound_verified=False, is_optimal=False, diagnostic_only=True)
        return dict(validated_assertions=actual, structural_result=report)
    except (KeyError, TypeError, IndexError, z3.Z3Exception) as exc:
        raise VerificationError('malformed/unsupported product-graph certificate: ' + str(exc)) from exc


def replay_certificate(z3, cert, source_text, strict_module, deadline):
    checked = validate_certificate(z3, cert, source_text, strict_module, deadline)
    budget = Budget(deadline)
    solver = z3.SolverFor('QF_LIA'); solver.add(*checked['validated_assertions'])
    solver.set(timeout=budget.milliseconds())
    answer = solver.check(); budget.check()
    return dict(checked['structural_result'], replay_result=str(answer), bound_verified=answer == z3.unsat,
                replay_reason=solver.reason_unknown() if answer == z3.unknown else '', is_optimal=False)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--certificate', type=Path, required=True)
    parser.add_argument('--sdk-python', type=Path, required=True)
    parser.add_argument('--sdk-library', type=Path, required=True)
    parser.add_argument('--strict-helper', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    require(math.isfinite(options.timeout) and 0 < options.timeout <= 1200, 'timeout must be in (0,1200]')
    deadline = time.monotonic() + options.timeout
    source, cert = base.read_frozen(options.source, 'source'), base.read_frozen(options.certificate, 'certificate')
    library = base.read_frozen(options.sdk_library, 'SDK library')
    strict_file = base.read_frozen(options.strict_helper, 'strict helper')
    if options.output:
        require(not base.output_aliases(options.output, [item['path'] for item in (source, cert, library, strict_file)]),
                'output aliases an input')
    sdk = options.sdk_python.resolve(strict=True)
    ctypes.CDLL(str(library['path'])); builtins.Z3_LIB_DIRS = [str(library['path'].parent)]
    for name in ('Z3_LIBRARY_PATH', 'PATH', 'PYTHONPATH'): os.environ.pop(name, None)
    sys.path.insert(0, str(sdk))
    import z3
    require(Path(z3.__file__).resolve().is_relative_to(sdk), 'SDK Python escaped explicit path')
    require(z3.get_version()[:3] == (5, 1, 0), 'expected explicit Z3 5.1.0 SDK')
    # Reuse the established mapped-library identity check, not its solving logic.
    runner = _load(Path(__file__).with_name('closed_bound_diagnostic.py'), '_pg_check_library_runner')
    mapped = runner._mapped_libraries(library['path'])
    strict = _load(strict_file['path'], '_pg_check_strict')
    payload = json.loads(cert['content'], object_pairs_hook=base.json_object,
                         parse_constant=lambda x: (_ for _ in ()).throw(VerificationError('nonfinite JSON value: ' + x)))
    report = replay_certificate(z3, payload, source['content'].decode('utf-8'), strict, deadline)
    for snapshot in (source, cert, library, strict_file): base.check_frozen(snapshot)
    Budget(deadline).check()
    report.update(source=str(source['path']), certificate=str(cert['path']), certificate_sha256=cert['sha256'],
                  sdk_library_sha256=library['sha256'], mapped_sdk_libraries=mapped,
                  verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if options.output:
        base.atomic_json(options.output, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
