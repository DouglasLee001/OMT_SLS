#!/usr/bin/env python3
"""Independently replay original-source objective-slice UNSAT certificates.

The authoritative --source is parsed with the official Z3 5.1 SDK. Only DFS
components obtained by splitting top-level ANDs may occur in the subset query,
followed by the unchanged original objective's exact strict-improvement bound.
An independently replayed UNSAT proves that bound for the full original hard
formula. Optimality additionally requires a feasible original-source incumbent.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

sys.dont_write_bytecode = True
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_BITS, MAX_AST_NODES, MAX_COMPONENTS = 65536, 200000, 100000
MAX_TOKENS, MAX_DEPTH = 2000000, 512
TREE = Path(__file__).resolve().parents[1]
CONVERTER_PATH = TREE / 'reference/NIA_LS/converter/omt_to_ls.py'
SDK_ROOT = TREE.parent / 'competitor/z3-5.1.0'
_spec = importlib.util.spec_from_file_location('_objective_slice_source_parser', CONVERTER_PATH)
converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(converter)
# The repository converter's fallback is relative to its own reference tree.
# Set the authoritative SDK before the very first load, including direct CLI use.
converter.Z3_ROOT = SDK_ROOT


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


class Budget:
    def __init__(self, timeout):
        try:
            valid = (not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                     and math.isfinite(timeout) and timeout > 0)
        except OverflowError:
            valid = False
        require(valid, 'timeout must be finite positive seconds')
        self.start = time.monotonic()
        self.deadline = self.start + timeout

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('objective-slice verification exceeded its time budget')

    def milliseconds(self):
        self.check()
        remaining = int(min((self.deadline - time.monotonic()) * 1000, (1 << 32) - 1))
        if remaining < 1:
            raise TimeoutError('objective-slice verification has no solver time remaining')
        return remaining


def integer(value, label):
    require(isinstance(value, str) and len(value) <= MAX_BITS,
            label + ' exceeds the decimal input resource guard')
    require(re.fullmatch(r'[+-]?[0-9]+', value) is not None,
            label + ' must be an exact decimal integer string')
    # Decimal -> int and Decimal formatting avoid Python's decimal digit limit.
    result = int(Decimal(value))
    require(abs(result).bit_length() <= MAX_BITS, label + ' exceeds the arithmetic bit guard')
    return result


def decimal_text(value):
    return format(Decimal(value), 'f')


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON object key: ' + key)
        result[key] = value
    return result


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def resolved_path(path, *, strict=False):
    try:
        return Path(path).resolve(strict=strict)
    except RuntimeError as exc:
        raise VerificationError('invalid input/output path: ' + str(exc)) from exc


def read_frozen(path, label):
    input_path = Path(path).absolute()
    path = resolved_path(input_path, strict=True)
    # O_NONBLOCK prevents a FIFO/device path from blocking before fstat can
    # reject it, including a path replaced between resolve and open.
    with open(path, 'rb', opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)) as handle:
        before = os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode), label + ' must be a regular file')
        require(before.st_size <= MAX_INPUT_BYTES, label + ' exceeds the input size resource guard')
        content = handle.read(MAX_INPUT_BYTES + 1)
        after = os.fstat(handle.fileno())
    require(len(content) <= MAX_INPUT_BYTES, label + ' exceeds the input size resource guard')
    require(_signature(before) == _signature(after), label + ' changed while being read')
    require(resolved_path(input_path, strict=True) == path, label + ' path changed while being read')
    return dict(path=path, input_path=input_path, content=content, sha256=hashlib.sha256(content).hexdigest(),
                signature=_signature(after), label=label)


def check_frozen(snapshot):
    require(resolved_path(snapshot['input_path'], strict=True) == snapshot['path'],
            snapshot['label'] + ' path changed during verification')
    current = read_frozen(snapshot['input_path'], snapshot['label'])
    require(current['sha256'] == snapshot['sha256'] and current['signature'] == snapshot['signature'],
            snapshot['label'] + ' changed during verification')


def guard_text(text, budget, label):
    require(isinstance(text, str) and bool(text), label + ' must be nonempty SMT2 text')
    require(len(text.encode('utf-8')) <= MAX_INPUT_BYTES, label + ' exceeds the input size resource guard')
    depth = 0
    for count, token in enumerate(converter._tokens(text), 1):
        budget.check()
        require(count <= MAX_TOKENS, label + ' exceeds the token resource guard')
        if token == '(':
            depth += 1
            require(depth <= MAX_DEPTH, label + ' exceeds the nesting resource guard')
        elif token == ')':
            depth -= 1
            require(depth >= 0, label + ' has unmatched parentheses')
        elif re.fullmatch(r'[0-9]+', token):
            integer(token, label + ' numeral')
    require(depth == 0, label + ' has unmatched parentheses')


def guard_source_expansion(text, budget):
    """Bound nonrecursive macro expansion before handing text to native Z3.

    A body with B nodes at unit-size parameters expands to at most B*M
    nodes when every argument has size at most M. This deliberately loose
    bound may reject complex valid sources; it never becomes a proof premise.
    """
    macros = {}

    def cost(form):
        budget.check()
        if isinstance(form, str):
            summary = macros.get(form)
            return summary[0] if summary is not None and summary[1] == 0 else 1
        require(isinstance(form, list), 'unsupported source macro syntax')
        children = form[1:] if form and isinstance(form[0], str) else form
        sizes = [cost(child) for child in children]
        work = 1 + sum(sizes)
        summary = macros.get(form[0]) if form and isinstance(form[0], str) else None
        if summary is not None:
            require(len(sizes) == summary[1], 'source macro call has the wrong arity')
            work = summary[0] * max([1, *sizes]) + sum(sizes)
        require(work <= MAX_AST_NODES, 'source exceeds the macro expansion resource guard')
        return work

    total = 0
    for form in converter._commands(text):
        budget.check()
        require(isinstance(form, list) and form and isinstance(form[0], str), 'malformed source command')
        if form[0] == 'exit':
            break
        require(form[0] not in ('define-fun-rec', 'define-funs-rec'),
                'recursive source macros cannot satisfy the macro expansion resource guard')
        if form[0] == 'define-fun':
            require(len(form) == 5 and isinstance(form[1], str) and isinstance(form[2], list),
                    'unsupported source macro definition')
            require(form[1] not in macros, 'duplicate source macro definition')
            work = cost(form[4])
            macros[form[1]] = (work, len(form[2]))
        else:
            work = cost(form)
        total += work
        require(total <= MAX_AST_NODES, 'source exceeds the macro expansion resource guard')


def constants(z3, expressions, budget):
    found, seen, pending = {}, set(), list(expressions)
    while pending:
        budget.check()
        expression = pending.pop()
        if expression.get_id() in seen:
            continue
        seen.add(expression.get_id())
        require(len(seen) <= MAX_AST_NODES, 'source/query AST node guard exceeded')
        require(z3.is_app(expression) and not z3.is_quantifier(expression),
                'source/query contains unsupported quantified or bound expressions')
        require(expression.sort().kind() in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT),
                'source/query contains a non-Int/Bool sort')
        if z3.is_int_value(expression):
            integer(expression.as_string(), 'source/query numeral')
        if expression.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            require(expression.num_args() == 0, 'source/query contains an uninterpreted function')
            name = str(expression.decl().name())
            require(name not in found or z3.eq(found[name], expression),
                    'source/query overloads a name with multiple sorts')
            found[name] = expression
        pending.extend(expression.children())
    return found


def flatten_source(z3, hard, budget):
    result, pending = [], list(reversed(hard))
    visits = 0
    while pending:
        budget.check()
        visits += 1
        require(visits <= MAX_AST_NODES, 'source conjunction traversal resource guard exceeded')
        expression = pending.pop()
        if z3.is_and(expression):
            pending.extend(reversed(expression.children()))
        else:
            result.append(expression)
            require(len(result) <= MAX_COMPONENTS, 'source component resource guard exceeded')
    return result


def parse_expression(z3, text, declarations, budget, *, boolean):
    guard_text(text, budget, 'certificate AST')
    forms = converter._commands(text)
    require(len(forms) == 1, 'certificate AST must contain exactly one expression')
    rendered = converter._render(forms[0])
    assertion = rendered if boolean else '(= ' + rendered + ' 0)'
    parsed = z3.parse_smt2_string('(assert ' + assertion + ')', decls=declarations)
    require(len(parsed) == 1, 'certificate AST must yield exactly one assertion')
    expression = parsed[0] if boolean else parsed[0].arg(0)
    require(expression.sort().kind() == (z3.Z3_BOOL_SORT if boolean else z3.Z3_INT_SORT),
            'certificate AST has the wrong sort')
    return expression


class AstIdentity:
    """Exact AST keys modulo ordered AND/OR/Int-ADD/Int-MUL association only.

    Integer keys share normalized subgraphs. Every raw edge and copied child
    slot is charged before allocation, so shared DAGs cannot expand without
    bound. Retained raw AST references also prevent native AST-ID reuse.
    """
    def __init__(self, z3, budget):
        self.z3, self.budget = z3, budget
        self.memo, self.intern, self.nodes = {}, {}, []
        self.work, self.context = 0, None

    def charge(self, amount):
        self.budget.check()
        require(amount <= MAX_AST_NODES - self.work, 'AST identity work/expansion resource guard exceeded')
        self.work += amount

    def key(self, expression):
        if self.context is None:
            self.context = expression.ctx
        require(expression.ctx == self.context, 'source/query AST identity changed context')
        pending = [(expression, None)]
        while pending:
            self.budget.check()
            current, children = pending.pop()
            raw_id = current.get_id()
            if raw_id in self.memo:
                continue
            if children is None:
                require(self.z3.is_app(current), 'AST identity contains an unsupported bound expression')
                self.charge(1 + current.num_args())
                children = current.children()
                pending.append((current, children))
                pending.extend((child, None) for child in reversed(children))
                continue
            child_ids = tuple(self.memo[child.get_id()][1] for child in children)
            kind, sort = current.decl().kind(), current.sort()
            associative = (len(children) >= 2 and
                ((sort.kind() == self.z3.Z3_BOOL_SORT and kind in (self.z3.Z3_OP_AND, self.z3.Z3_OP_OR))
                 or (sort.kind() == self.z3.Z3_INT_SORT and kind in (self.z3.Z3_OP_ADD, self.z3.Z3_OP_MUL))))
            if associative:
                flat = []
                for child_id in child_ids:
                    node = self.nodes[child_id]
                    additions = node[3] if node[:3] == ('assoc', kind, sort.get_id()) else (child_id,)
                    self.charge(len(additions))
                    flat.extend(additions)
                key = ('assoc', kind, sort.get_id(), tuple(flat))
            else:
                key = ('exact', current.decl().get_id(), sort.get_id(), child_ids)
            normalized_id = self.intern.get(key)
            if normalized_id is None:
                normalized_id = len(self.nodes)
                self.nodes.append(key)
                self.intern[key] = normalized_id
            self.memo[raw_id] = (current, normalized_id)
        self.budget.check()
        return self.memo[expression.get_id()][1]


def query_assertions(z3, text, source_constants, expected, budget, identity):
    guard_text(text, budget, 'subset query')
    needed = constants(z3, expected, budget)
    forms, kept, declared, checks = converter._commands(text), [], set(), 0
    for form in forms:
        budget.check()
        require(isinstance(form, list) and form and isinstance(form[0], str),
                'subset query contains a malformed command')
        require(not checks, 'subset query changes state after check-sat')
        command = form[0]
        if command == 'check-sat':
            require(form == ['check-sat'], 'subset query has malformed check-sat')
            checks += 1
        elif command in ('set-info', 'set-logic'):
            require((command == 'set-info' and len(form) == 3)
                    or (command == 'set-logic' and len(form) == 2),
                    'subset query has malformed metadata')
        elif command in ('declare-fun', 'declare-const'):
            valid = (len(form) == 4 and form[2] == [] if command == 'declare-fun' else len(form) == 3)
            require(valid and isinstance(form[1], str), 'subset query declares an unsupported symbol')
            token = form[1]
            name = token[1:-1] if token.startswith('|') and token.endswith('|') else token
            expression = source_constants.get(name)
            require(expression is not None and form[-1] == str(expression.sort()),
                    'subset query declares a foreign symbol or changes its source sort')
            require(name not in declared, 'subset query duplicates a declaration')
            declared.add(name)
            kept.append(form)
        elif command == 'assert':
            require(len(form) == 2, 'subset query has a malformed assertion')
            kept.append(form)
        else:
            raise VerificationError('subset query contains unsupported command: ' + command)
    require(checks == 1, 'subset query must contain exactly one check-sat')
    require(declared == set(needed), 'subset query omits or adds source declarations')
    actual = list(z3.parse_smt2_string('\n'.join(converter._render(form) for form in kept)))
    used = constants(z3, actual, budget)
    require(all(name in needed and z3.eq(value, needed[name]) for name, value in used.items()),
            'subset query changes source declaration identities')
    # Only ordered AND/OR/Int-ADD/Int-MUL association is normalized. Extra assertions,
    # anchors, coefficient changes and every other operator remain exact.
    require(Counter(identity.key(item) for item in actual) == Counter(identity.key(item) for item in expected),
            'subset query omits, changes, or adds selected source assertions/strict bound')
    return actual


def replay_query(z3, assertions, budget):
    solver = z3.Solver()
    solver.add(*assertions)
    try:
        solver.set(timeout=budget.milliseconds())
        answer = solver.check()
        budget.check()
        reason = solver.reason_unknown() if answer == z3.unknown else ''
        return answer, reason
    except TimeoutError as exc:
        return z3.unknown, str(exc)


def verify_incumbent(z3, snapshot, hard, objective, best, source_constants, budget):
    fixed, ignored, supplied = [], [], set()
    for line in snapshot['content'].decode('utf-8').splitlines():
        budget.check()
        require('\t' in line, 'incumbent model line has no tab separator')
        name, value = line.rsplit('\t', 1)
        require(name and name not in supplied, 'incumbent model contains an empty or duplicate name')
        supplied.add(name)
        expression = source_constants.get(name)
        if expression is None:
            ignored.append(name)
        elif expression.sort().kind() == z3.Z3_BOOL_SORT:
            require(value in ('true', 'false'), 'incumbent source Boolean has the wrong sort/value')
            fixed.append(expression == z3.BoolVal(value == 'true'))
        else:
            fixed.append(expression == z3.IntVal(decimal_text(integer(value, 'incumbent source integer'))))
    solver = z3.Solver()
    solver.add(*hard, objective == z3.IntVal(decimal_text(best)), *fixed)
    solver.set(timeout=budget.milliseconds())
    answer = solver.check()
    budget.check()
    if answer == z3.unknown:
        raise TimeoutError('source incumbent validation is unknown: ' + solver.reason_unknown())
    require(answer == z3.sat, 'incumbent witness cannot attain the claimed best in the full original source')
    model = solver.model()
    for expression in hard:
        budget.check()
        require(z3.is_true(model.eval(expression, model_completion=True)),
                'completed incumbent model violates an original hard assertion')
    value = model.eval(objective, model_completion=True)
    require(z3.is_int_value(value) and integer(value.as_string(), 'source model objective') == best,
            'completed incumbent model has a different original objective')
    values = {}
    for expression in source_constants.values():
        budget.check()
        value = model.eval(expression, model_completion=True)
        require(z3.is_int_value(value) or z3.is_true(value) or z3.is_false(value),
                'source incumbent model is not concrete')
        values[expression.sexpr()] = value.as_string() if z3.is_int_value(value) else str(value).lower()
    budget.check()
    return dict(provided=True, verified=True, result='sat', matched_source_variables=len(fixed),
                ignored_native_variables=sorted(ignored), completed_source_values=values,
                model_sha256=snapshot['sha256'])


def verify_certificate(source, certificate, *, timeout=60, incumbent_model=None, expected_source_sha256=None):
    budget = Budget(timeout)
    source_file, certificate_file = read_frozen(source, 'source'), read_frozen(certificate, 'certificate')
    files = [source_file, certificate_file]
    model_file = read_frozen(incumbent_model, 'incumbent model') if incumbent_model is not None else None
    if model_file is not None:
        files.append(model_file)
    if expected_source_sha256 is not None:
        require(isinstance(expected_source_sha256, str)
                and re.fullmatch('[0-9a-fA-F]{64}', expected_source_sha256) is not None,
                'expected source SHA256 must be 64 hexadecimal digits')
        require(source_file['sha256'] == expected_source_sha256.lower(), 'source SHA256 differs from expected source')
    budget.check()
    payload = json.loads(certificate_file['content'], object_pairs_hook=json_object,
                         parse_constant=lambda token: (_ for _ in ()).throw(VerificationError('nonfinite JSON number: ' + token)))
    require(isinstance(payload, dict) and payload.get('version') == 'omt-objective-slice-v1',
            'unsupported objective-slice certificate version')
    z3 = converter.load_z3()
    try:
        source_text = source_file['content'].decode('utf-8')
        guard_text(source_text, budget, 'source')
        guard_source_expansion(source_text, budget)
        query, direction, _ = converter._query(source_text)
        parsed = z3.Optimize(); parsed.from_string(query)
        budget.check()
        hard, objectives = list(parsed.assertions()), parsed.objectives()
        require(len(objectives) == 1 and objectives[0].sort().kind() == z3.Z3_INT_SORT,
                'source must have exactly one integer objective')
        objective = objectives[0]
        source_constants = constants(z3, [*hard, objective], budget)
        identity = AstIdentity(z3, budget)
        require(payload['direction'] == direction, 'certificate objective direction differs from source')
        actual_objective = parse_expression(z3, payload['original_objective'], source_constants, budget, boolean=False)
        require(identity.key(actual_objective) == identity.key(objective), 'certificate original objective differs from source')
        flat = flatten_source(z3, hard, budget)
        require(type(payload['flattened_source_count']) is int and payload['flattened_source_count'] == len(flat),
                'certificate flattened component count differs from source')
        indices, texts = payload['selected_indices'], payload['selected_assertions']
        require(isinstance(indices, list) and isinstance(texts, list) and len(indices) == len(texts)
                and len(indices) <= MAX_COMPONENTS, 'selected component lists have missing/extra entries or exceed resource guard')
        require(all(type(index) is int and 0 <= index < len(flat) for index in indices),
                'selected component index is outside the original DFS components')
        require(len(set(indices)) == len(indices), 'selected component index is duplicated')
        selected = [flat[index] for index in indices]
        for index, text, expression in zip(indices, texts, selected):
            actual = parse_expression(z3, text, source_constants, budget, boolean=True)
            require(identity.key(actual) == identity.key(expression), 'selected component AST differs from original source at index ' + str(index))
        best = integer(payload['original_best'], 'original_best')
        value = z3.IntVal(decimal_text(best))
        # Python reflects comparisons against IntNumRef (a subclass of
        # ArithRef), reversing the AST operands. Parse the original-order
        # comparison so exact matching agrees with the native C++ query.
        strict_text = '(' + ('<' if direction == 'min' else '>') + ' ' + objective.sexpr() + ' ' + value.sexpr() + ')'
        strict = parse_expression(z3, strict_text, source_constants, budget, boolean=True)
        assertions = query_assertions(z3, payload['query_smt2'], source_constants, [*selected, strict], budget, identity)
        budget.check()
        answer, reason = replay_query(z3, assertions, budget)
        bound_verified = answer == z3.unsat
        incomplete = answer == z3.unknown
        witness = dict(provided=model_file is not None, verified=False)
        if model_file is not None and not incomplete:
            try:
                witness = verify_incumbent(z3, model_file, hard, objective, best, source_constants, budget)
            except TimeoutError as exc:
                incomplete = True
                witness.update(result='unknown', reason=str(exc))
            except (ValueError, z3.Z3Exception) as exc:
                witness.update(result='invalid', reason=str(exc))
        elif model_file is not None:
            witness['reason'] = 'subset replay incomplete; incumbent validation not attempted'
        # Freeze actual inputs, including omitted source constraints, across all
        # solver and model work. These hashes are computed here, never trusted
        # from a native certificate or a placeholder source identity.
        for snapshot in files:
            check_frozen(snapshot)
        if not incomplete:
            try:
                budget.check()
            except TimeoutError as exc:
                incomplete, bound_verified, answer, reason = True, False, z3.unknown, str(exc)
                witness['verified'] = False
        return dict(schema_version=1, certificate_version=payload['version'], certificate_valid=True,
                    source=str(source_file['path']), certificate=str(certificate_file['path']),
                    source_sha256=source_file['sha256'], certificate_sha256=certificate_file['sha256'],
                    source_hash_verified=expected_source_sha256 is not None, source_unchanged=True,
                    source_identity_verified=True, exact_query_verified=True,
                    verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    source_parser_helper_sha256=hashlib.sha256(CONVERTER_PATH.read_bytes()).hexdigest(),
                    z3_version=z3.get_version_string(), z3_sdk_root=str(SDK_ROOT),
                    flattened_source_count=len(flat), selected_components_verified=len(selected),
                    original_direction=direction, original_best=payload['original_best'],
                    subset_replay_result=str(answer), replay_unknown_reason=reason,
                    no_better_bound_verified=bound_verified, optimality_verified=bool(bound_verified and witness['verified']),
                    verification_incomplete=incomplete, incumbent_validation=witness,
                    timeout_seconds=timeout, seconds=time.monotonic() - budget.start)
    except (KeyError, TypeError, IndexError, z3.Z3Exception) as exc:
        raise VerificationError('malformed or unsupported objective-slice certificate: ' + str(exc)) from exc


def atomic_json(path, record):
    path, temporary = Path(path), None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '-', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def output_aliases(path, inputs):
    destination = resolved_path(path)
    return any(destination == resolved_path(source)
               or (path.exists() and source.exists() and os.path.samefile(path, source)) for source in inputs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--certificate', required=True, type=Path)
    parser.add_argument('--source-sha256', help='optional trusted raw-source SHA256 from an external manifest')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--incumbent-model', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    inputs = [path for path in (args.source, args.certificate, args.incumbent_model) if path is not None]
    try:
        require(not output_aliases(args.output, inputs), 'output would overwrite a source, certificate, or incumbent input')
    except (OSError, ValueError) as exc:
        print('verify_objective_slice_certificate: ' + str(exc), file=sys.stderr)
        return 2
    try:
        result = verify_certificate(args.source, args.certificate, timeout=args.timeout,
                                    incumbent_model=args.incumbent_model, expected_source_sha256=args.source_sha256)
        code = (3 if result['verification_incomplete'] else
                0 if result['no_better_bound_verified'] and
                (not result['incumbent_validation']['provided'] or result['incumbent_validation']['verified']) else 2)
    except (OSError, ValueError, TimeoutError, RecursionError) as exc:
        result = dict(certificate_valid=None if isinstance(exc, TimeoutError) else False,
                      no_better_bound_verified=False, optimality_verified=False,
                      verification_incomplete=isinstance(exc, TimeoutError), error=str(exc),
                      source=str(args.source.absolute()), certificate=str(args.certificate.absolute()),
                      timeout_seconds=args.timeout)
        code = 3 if isinstance(exc, TimeoutError) else 2
    try:
        atomic_json(args.output, result)
    except OSError as exc:
        print('verify_objective_slice_certificate: cannot write report: ' + str(exc), file=sys.stderr)
        return 2
    if code:
        print('verify_objective_slice_certificate: ' + result.get('error',
              result.get('replay_unknown_reason') or 'no independently verified bound/incumbent'), file=sys.stderr)
    else:
        print(json.dumps({key: result[key] for key in ('certificate_valid', 'no_better_bound_verified', 'optimality_verified')}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
