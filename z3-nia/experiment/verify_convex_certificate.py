#!/usr/bin/env python3
"""Independently check source-AST convex OA certificates with exact arithmetic.

The original SMT2 is parsed by the matching official Z3 5.1 Python SDK. Only
the converter's text filter and SDK loader are reused: polynomial expansion,
projection checks, matrix identities, derivatives, and query reconstruction
are independent of the native implementation. Certificate queries are checked
against reconstructed assertions before a new QF_LIA solver replays them.

A strict-improvement UNSAT certificate proves a bound. Optimality additionally
requires a feasible source incumbent at the claimed objective. An optional
native model.tsv fixes matching source names/sorts; a source solver completes
remaining variables and every original assertion/objective is then evaluated.
Unknown native auxiliary names never constrain that source solver.

Batch replay is the default. Incremental replay starts a new independent
solver and learns only by checking successive prefixes of already validated
cuts. Both modes share the original global verification deadline.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.dont_write_bytecode = True
TREE = Path(__file__).resolve().parents[1]
CONVERTER_PATH = TREE / 'reference/NIA_LS/converter/omt_to_ls.py'
_spec = importlib.util.spec_from_file_location('_convex_certificate_source_parser', CONVERTER_PATH)
converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(converter)


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


class Budget:
    def __init__(self, timeout):
        require(not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                and math.isfinite(timeout) and timeout > 0, 'timeout must be finite positive seconds')
        self.start = time.monotonic()
        self.deadline = self.start + timeout

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('certificate verification exceeded its time budget')

    def milliseconds(self):
        self.check()
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining < 1:
            raise TimeoutError('certificate verification has no solver time remaining')
        return min(remaining, (1 << 32) - 1)


def integer(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+', value) is not None,
            label + ' must be an exact decimal integer string')
    # Decimal -> int bypasses Python's decimal-string digit limit exactly.
    return int(Decimal(value))


def rational(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+(?:/[0-9]+)?', value) is not None,
            label + ' must be an exact integer or rational string')
    numerator, separator, denominator = value.partition('/')
    divisor = integer(denominator, label) if separator else 1
    require(divisor > 0, label + ' has a zero denominator')
    return Fraction(integer(numerator, label), divisor)


def decimal_text(value):
    return format(Decimal(value), 'f')


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON object key: ' + key)
        result[key] = value
    return result


def constants(z3, expressions, budget):
    """Inventory actual source declarations, preserving names and sorts."""
    found, seen, pending = {}, set(), list(expressions)
    while pending:
        budget.check()
        expression = pending.pop()
        if expression.get_id() in seen:
            continue
        seen.add(expression.get_id())
        require(z3.is_app(expression) and not z3.is_quantifier(expression),
                'source/query contains quantifiers or unsupported bound expressions')
        require(expression.sort().kind() in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT),
                'source/query contains a non-Int/Bool sort')
        if expression.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            require(expression.num_args() == 0, 'source/query contains an uninterpreted function')
            name = str(expression.decl().name())
            require(name not in found or z3.eq(found[name], expression),
                    'source/query overloads one symbol with multiple sorts')
            found[name] = expression
        pending.extend(expression.children())
    return found


def parse_expression(z3, text, declarations, boolean=False):
    require(isinstance(text, str) and bool(text), 'certificate AST must be a nonempty string')
    forms = converter._commands(text)
    require(len(forms) == 1, 'certificate AST contains more than one expression')
    expression_text = converter._render(forms[0])
    assertion = expression_text if boolean else '(= ' + expression_text + ' 0)'
    try:
        parsed = z3.parse_smt2_string('(assert ' + assertion + ')', decls=declarations)
    except z3.Z3Exception as exc:
        raise VerificationError('cannot parse certificate AST: ' + str(exc)) from exc
    require(len(parsed) == 1, 'certificate AST did not produce exactly one assertion')
    expression = parsed[0] if boolean else parsed[0].arg(0)
    require(expression.sort().kind() == (z3.Z3_BOOL_SORT if boolean else z3.Z3_INT_SORT),
            'certificate AST has the wrong sort')
    return expression


def same_ast(z3, text, expected, declarations, label, simplify=False):
    actual = parse_expression(z3, text, declarations, z3.is_bool(expected))
    if simplify:
        actual, expected = z3.simplify(actual), z3.simplify(expected)
    require(z3.eq(actual, expected), label + ' differs from independently parsed source')


def ast_list(z3, texts, expected, declarations, label, simplify=False):
    require(isinstance(texts, list) and len(texts) == len(expected), label + ' has missing or extra assertions')
    for index, (text, expression) in enumerate(zip(texts, expected)):
        same_ast(z3, text, expression, declarations, f'{label}[{index}]', simplify)


def contains(z3, expression, target):
    seen, pending = set(), [expression]
    while pending:
        current = pending.pop()
        if z3.eq(current, target):
            return True
        if current.get_id() not in seen:
            seen.add(current.get_id())
            pending.extend(current.children())
    return False


def polynomial(z3, expression, budget):
    """Expand arithmetic into named monomials; no native polynomial is trusted."""
    cache = {}

    def plus(left, right, sign=1):
        result = dict(left)
        for powers, coefficient in right.items():
            value = result.get(powers, Fraction(0)) + sign * coefficient
            if value:
                result[powers] = value
            else:
                result.pop(powers, None)
        return result

    def times(left, right):
        result = {}
        for powers, coefficient in left.items():
            for other_powers, other_coefficient in right.items():
                budget.check()
                key = tuple(sorted(powers + other_powers))
                require(len(key) <= 2, 'source objective degree exceeds two')
                result = plus(result, {key: coefficient * other_coefficient})
        return result

    def visit(current):
        budget.check()
        key = current.get_id()
        if key in cache:
            return cache[key]
        require(z3.is_app(current) and current.sort().kind() == z3.Z3_INT_SORT,
                'source objective is not an integer polynomial')
        kind, children = current.decl().kind(), current.children()
        if z3.is_int_value(current):
            value = Fraction(integer(current.as_string(), 'source numeral'))
            result = {(): value} if value else {}
        elif kind == z3.Z3_OP_UNINTERPRETED and not children:
            result = {(current.sexpr(),): Fraction(1)}
        elif kind == z3.Z3_OP_ADD:
            result = {}
            for child in children:
                result = plus(result, visit(child))
        elif kind in (z3.Z3_OP_SUB, z3.Z3_OP_UMINUS):
            require(bool(children), 'malformed source subtraction')
            result = visit(children[0])
            if len(children) == 1:
                result = plus({}, result, -1)
            else:
                for child in children[1:]:
                    result = plus(result, visit(child), -1)
        elif kind == z3.Z3_OP_MUL:
            result = {(): Fraction(1)}
            for child in children:
                result = times(result, visit(child))
        elif kind == z3.Z3_OP_POWER and len(children) == 2 and z3.is_int_value(children[1]):
            exponent = integer(children[1].as_string(), 'source exponent')
            require(0 <= exponent <= 2, 'unsupported source objective exponent')
            result, base = {(): Fraction(1)}, visit(children[0])
            for _ in range(exponent):
                result = times(result, base)
        else:
            raise VerificationError('unsupported source objective polynomial operator')
        cache[key] = result
        return result

    return visit(expression)


def differentiate(terms, variable):
    result = {}
    for powers, coefficient in terms.items():
        count = powers.count(variable)
        if count:
            remaining = list(powers)
            remaining.remove(variable)
            result[tuple(remaining)] = coefficient * count
    return result


def evaluate(terms, point):
    value = Fraction(0)
    for powers, coefficient in terms.items():
        term = coefficient
        for variable in powers:
            term *= point[variable]
        value += term
    return value


def vector(values, length, label, convert=rational):
    require(isinstance(values, list) and len(values) == length, label + ' has the wrong dimension')
    return [convert(value, label) for value in values]


def matrix(values, length, label):
    require(isinstance(values, list) and len(values) == length, label + ' has the wrong dimension')
    return [vector(row, length, label) for row in values]


def query_assertions(z3, text, source_constants, budget, label):
    require(isinstance(text, str) and bool(text), label + ' must contain a full SMT2 query')
    forms, kept, checks = converter._commands(text), [], 0
    by_expression = {expression.sexpr(): expression for expression in source_constants.values()}
    for form in forms:
        budget.check()
        require(isinstance(form, list) and bool(form), label + ' has malformed commands')
        command = form[0]
        require(not checks, label + ' changes state after check-sat')
        if command == 'check-sat':
            require(form == ['check-sat'], label + ' has malformed check-sat')
            checks += 1
        elif command in ('set-info', 'set-logic'):
            continue
        elif command in ('declare-fun', 'declare-const'):
            valid = (len(form) == 4 and form[2] == [] if command == 'declare-fun' else len(form) == 3)
            require(valid and isinstance(form[1], str), label + ' declares an unsupported symbol')
            expression = by_expression.get(form[1])
            require(expression is not None and form[-1] == str(expression.sort()),
                    label + ' declares a symbol absent from original source or changes its sort')
            kept.append(form)
        elif command == 'assert':
            require(len(form) == 2, label + ' has a malformed assertion')
            kept.append(form)
        else:
            raise VerificationError(label + ' contains unsupported command: ' + str(command))
    require(checks == 1, label + ' must contain exactly one check-sat')
    try:
        assertions = list(z3.parse_smt2_string('\n'.join(converter._render(form) for form in kept)))
    except z3.Z3Exception as exc:
        raise VerificationError(label + ' cannot be parsed: ' + str(exc)) from exc
    used = constants(z3, assertions, budget)
    require(all(name in source_constants and z3.eq(value, source_constants[name])
                for name, value in used.items()), label + ' introduces foreign source declarations')
    return assertions


def match_query(z3, actual, expected, label):
    # Match assertion multisets after sound local rewriting. Do not accept an
    # extra bound even if it happens to be implied by the source hard formula.
    def assertion_key(expression):
        expression = z3.simplify(expression)
        kind = expression.decl().kind()
        if kind in (z3.Z3_OP_LE, z3.Z3_OP_GE) and all(z3.is_int(child) for child in expression.children()):
            left, right = expression.children()
            if kind == z3.Z3_OP_GE:
                left, right = right, left
            # Build a key, not another overloaded comparison: Python reflects
            # ArithRef <= IntNumRef into GE because IntNumRef is a subclass.
            return ('integer-le', left.sexpr(), right.sexpr())
        return ('ast', expression.sexpr())
    signature = lambda expressions: Counter(assertion_key(expression) for expression in expressions)
    require(signature(actual) == signature(expected), label + ' omits, changes, or adds hard constraints/cuts')


def verify_incumbent(z3, path, hard, objective, best, source_constants, budget):
    model_bytes = Path(path).read_bytes()
    fixed, ignored, supplied = [], [], set()
    for line in model_bytes.decode('utf-8').splitlines():
        require('\t' in line, 'incumbent model line has no tab separator')
        name, value = line.rsplit('\t', 1)
        require(bool(name) and name not in supplied, 'incumbent model contains an empty or duplicate name')
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
    require(answer == z3.sat, 'incumbent witness cannot attain the claimed best in the original source')
    model = solver.model()
    require(all(z3.is_true(model.eval(expression, model_completion=True)) for expression in hard),
            'completed incumbent source model violates an original hard assertion')
    value = model.eval(objective, model_completion=True)
    require(z3.is_int_value(value) and integer(value.as_string(), 'source model objective') == best,
            'completed incumbent source model has a different objective')
    values = {}
    for expression in source_constants.values():
        value = model.eval(expression, model_completion=True)
        require(z3.is_int_value(value) or z3.is_true(value) or z3.is_false(value),
                'source incumbent model is not concrete')
        values[expression.sexpr()] = value.as_string() if z3.is_int_value(value) else str(value).lower()
    return dict(provided=True, verified=True, result='sat', matched_source_variables=len(fixed),
                ignored_native_variables=sorted(ignored), completed_source_values=values,
                model_sha256=hashlib.sha256(model_bytes).hexdigest())


def replay_linear(z3, linear, cuts, budget, *, replay_mode='batch'):
    """Replay only fully validated constraints; never import native solver state."""
    require(replay_mode in ('batch', 'incremental'), 'unsupported linear replay mode')
    require(bool(cuts), 'linear replay requires validated cuts')
    replay = z3.SolverFor('QF_LIA')
    replay.add(*linear)
    evidence = dict(replay_mode=replay_mode, replay_checks=0, replay_prefix_cuts=0,
                    replay_unknown_prefixes=0, replay_last_unknown_reason='', replay_trace=[])
    started, answer = time.monotonic(), z3.unknown
    prefixes = [(len(cuts), cuts)] if replay_mode == 'batch' else ((i + 1, [cut]) for i, cut in enumerate(cuts))
    for prefix, additions in prefixes:
        replay.add(*additions)
        try:
            remaining = budget.milliseconds()
        except TimeoutError as exc:
            answer = z3.unknown
            evidence['replay_last_unknown_reason'] = str(exc)
            break
        # Reserve time for later cuts when a prefix times out. Unused time
        # remains in this same deadline; the last prefix gets all that remains.
        checks_left = len(cuts) - prefix + 1 if replay_mode == 'incremental' else 1
        allowance = max(1, remaining // checks_left)
        replay.set(timeout=allowance)
        answer = replay.check()
        evidence['replay_checks'] += 1
        evidence['replay_prefix_cuts'] = prefix
        expired = False
        try:
            budget.check()
        except TimeoutError as exc:
            answer, expired = z3.unknown, True
            evidence['replay_last_unknown_reason'] = str(exc)
        if answer == z3.unknown:
            evidence['replay_unknown_prefixes'] += 1
            if not expired:
                evidence['replay_last_unknown_reason'] = replay.reason_unknown()
        evidence['replay_trace'].append(dict(prefix_cuts=prefix, timeout_ms=allowance, result=str(answer)))
        if answer == z3.unsat or expired:
            break
    evidence['replay_seconds'] = time.monotonic() - started
    return answer, evidence


def verify_certificate(source, certificate, *, timeout=60, incumbent_model=None, replay_mode='batch'):
    require(replay_mode in ('batch', 'incremental'), 'unsupported linear replay mode')
    budget = Budget(timeout)
    source, certificate = Path(source).resolve(), Path(certificate).resolve()
    source_bytes, certificate_bytes = source.read_bytes(), certificate.read_bytes()
    payload = json.loads(certificate_bytes, object_pairs_hook=json_object,
                         parse_constant=lambda token: (_ for _ in ()).throw(VerificationError('nonfinite JSON number: ' + token)))
    require(isinstance(payload, dict) and payload.get('version') == 'omt-convex-exchange-v1',
            'unsupported convex certificate version')
    z3 = converter.load_z3()
    try:
        query, direction, _ = converter._query(source_bytes.decode('utf-8'))
        parsed = z3.Optimize(); parsed.from_string(query)
        hard, objectives = list(parsed.assertions()), parsed.objectives()
        require(len(objectives) == 1 and objectives[0].sort().kind() == z3.Z3_INT_SORT,
                'source must have exactly one integer objective')
        objective, minimize = objectives[0], direction == 'min'
        source_constants = constants(z3, [*hard, objective], budget)
        declarations = {name: expression for name, expression in source_constants.items()}
        identity = payload['source_identity']
        require(type(identity['minimize']) is bool and identity['minimize'] == minimize,
                'certificate original objective direction differs from source')
        ast_list(z3, identity['hard'], hard, declarations, 'source_identity.hard')
        same_ast(z3, identity['objective'], objective, declarations, 'source_identity.objective')

        definitions = []
        if objective.decl().kind() == z3.Z3_OP_UNINTERPRETED and objective.num_args() == 0:
            for index, expression in enumerate(hard):
                if z3.is_eq(expression) and expression.num_args() == 2:
                    if z3.eq(expression.arg(0), objective):
                        definitions.append((index, expression.arg(1)))
                    elif z3.eq(expression.arg(1), objective):
                        definitions.append((index, expression.arg(0)))
        require(len(definitions) <= 1, 'source objective has multiple direct definitions')
        projection, projected_objective, raw = payload['projection'], objective, list(hard)
        if definitions:
            index, rhs = definitions[0]
            require(not contains(z3, rhs, objective), 'source objective definition is self-referential')
            require(isinstance(projection, dict) and type(projection['definition_index']) is int
                    and projection['definition_index'] == index, 'certificate projection removes a different source assertion')
            same_ast(z3, projection['variable'], objective, declarations, 'projection.variable')
            same_ast(z3, projection['rhs'], rhs, declarations, 'projection.rhs')
            raw = [z3.substitute(expression, (objective, rhs)) for i, expression in enumerate(hard) if i != index]
            projected_objective = rhs
        else:
            require(projection is None, 'certificate invents an objective projection absent from source')
        ast_list(z3, payload['projected_hard_raw'], raw, declarations, 'projected_hard_raw')
        linear = [z3.simplify(expression) for expression in raw]
        ast_list(z3, payload['linear_hard'], linear, declarations, 'linear_hard', simplify=True)
        goal = z3.Goal(); goal.add(*linear)
        require(z3.Probe('is-qflia')(goal) != 0, 'projected original hard formula is not QF_LIA')
        normalized = projected_objective if minimize else -projected_objective
        same_ast(z3, payload['normalized_objective'], normalized, declarations, 'normalized_objective')
        terms = polynomial(z3, normalized, budget)
        objective_constants = constants(z3, [normalized], budget)
        variable_texts = payload['variables']
        require(isinstance(variable_texts, list) and all(isinstance(text, str) for text in variable_texts),
                'certificate variables must be source AST strings')
        variables = [parse_expression(z3, text, declarations) for text in variable_texts]
        expected_names = {value.sexpr() for value in objective_constants.values()}
        require(len(set(variable_texts)) == len(variable_texts)
                and {value.sexpr() for value in variables} == expected_names
                and len(variables) == len(expected_names), 'certificate objective variable inventory differs from source')
        names, count = [variable.sexpr() for variable in variables], len(variables)
        derivatives = [differentiate(terms, name) for name in names]
        zero = dict.fromkeys(names, 0)
        constant = evaluate(terms, zero)
        gradient = [evaluate(derivative, zero) for derivative in derivatives]
        hessian = [[evaluate(differentiate(derivative, name), zero) for name in names]
                   for derivative in derivatives]
        g = payload['g']
        require(rational(g['constant'], 'g.constant') == constant, 'g.constant differs from original source polynomial')
        require(vector(g['linear'], count, 'g.linear') == gradient, 'g.linear differs from original source polynomial')
        require(matrix(g['hessian'], count, 'g.hessian') == hessian, 'g.hessian differs from original source polynomial')
        require(matrix(g['quadratic'], count, 'g.quadratic') == [[value / 2 for value in row] for row in hessian],
                'g.quadratic differs from original source polynomial')
        lower = matrix(payload['ldl']['lower'], count, 'ldl.lower')
        diagonal = vector(payload['ldl']['diagonal'], count, 'ldl.diagonal')
        require(all(value >= 0 for value in diagonal), 'LDL diagonal is negative; source PSD is not certified')
        for i in range(count):
            require(lower[i][i] == 1 and all(lower[i][j] == 0 for j in range(i + 1, count)),
                    'LDL factor is not unit lower triangular')
            for j in range(count):
                budget.check()
                require(sum(lower[i][k] * diagonal[k] * lower[j][k] for k in range(count)) == hessian[i][j],
                        'LDL product differs from independently reconstructed source Hessian')

        incumbent = payload['incumbent']
        best = integer(incumbent['original_best'], 'incumbent.original_best')
        normalized_best = best if minimize else -best
        bound = normalized_best - 1
        require(integer(incumbent['normalized_best'], 'incumbent.normalized_best') == normalized_best
                and integer(incumbent['strict_bound'], 'incumbent.strict_bound') == bound,
                'certificate incumbent sign or integer strict-improvement bound is inconsistent')
        cuts, expected_cuts = payload['cuts'], []
        require(isinstance(cuts, list) and bool(cuts), 'certificate has no tangent cuts')
        for cut in cuts:
            budget.check()
            coordinates = vector(cut['point'], count, 'cut.point', integer)
            point = dict(zip(names, coordinates))
            coefficients = [evaluate(derivative, point) for derivative in derivatives]
            intercept = evaluate(terms, point) - sum(coefficient * coordinate
                                                    for coefficient, coordinate in zip(coefficients, coordinates))
            require(vector(cut['coefficients'], count, 'cut.coefficients', integer) == coefficients
                    and integer(cut['constant'], 'cut.constant') == intercept,
                    'cut gradient/intercept is not the tangent of the original source polynomial at its integer point')
            require(intercept.denominator == 1 and all(value.denominator == 1 for value in coefficients),
                    'source integer polynomial produced a nonintegral tangent')
            expression = z3.IntVal(decimal_text(intercept.numerator))
            for variable, coefficient in zip(variables, coefficients):
                if coefficient:
                    expression = expression + z3.IntVal(decimal_text(coefficient.numerator)) * variable
            expected_cuts.append(expression <= z3.IntVal(decimal_text(bound)))
        require(type(payload['queries']) is int and payload['queries'] == len(cuts),
                'certificate query/cut counts are inconsistent')
        actual_linear = query_assertions(z3, payload['linear_query_smt2'], source_constants, budget, 'linear query')
        match_query(z3, actual_linear, [*linear, *expected_cuts], 'linear query')
        actual_original = query_assertions(z3, payload['source_query_smt2'], source_constants, budget, 'source query')
        strict = objective < z3.IntVal(decimal_text(best)) if minimize else objective > z3.IntVal(decimal_text(best))
        match_query(z3, actual_original, [*hard, strict], 'source query')
        # Every cut, both full query identities, and source/PSD evidence have
        # already passed before even an UNSAT prefix may establish a bound.
        answer, replay_evidence = replay_linear(z3, linear, expected_cuts, budget, replay_mode=replay_mode)
        incomplete = answer == z3.unknown
        witness = dict(provided=incumbent_model is not None, verified=False)
        if incumbent_model is not None and incomplete:
            witness['reason'] = 'linear replay incomplete; source incumbent validation not attempted'
        if incumbent_model is not None and not incomplete:
            witness = verify_incumbent(z3, incumbent_model, hard, objective, best, source_constants, budget)
        bound_verified = answer == z3.unsat
        if not incomplete:
            budget.check()
        return dict(schema_version=1, certificate_valid=True, source=str(source), certificate=str(certificate),
                    source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                    certificate_sha256=hashlib.sha256(certificate_bytes).hexdigest(),
                    verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    source_parser_helper_sha256=hashlib.sha256(CONVERTER_PATH.read_bytes()).hexdigest(),
                    z3_version=z3.get_version_string(), source_manifest_hash_verified=False,
                    source_identity_verified=True, projection_verified=True, psd_verified=True,
                    tangent_cuts_verified=len(cuts), exact_query_verified=True,
                    original_direction=direction, original_best=incumbent['original_best'],
                    linear_replay_result=str(answer), no_better_bound_verified=bound_verified,
                    verification_incomplete=incomplete, timeout_seconds=timeout, **replay_evidence,
                    incumbent_validation=witness,
                    optimality_verified=bool(bound_verified and witness['verified']),
                    seconds=time.monotonic() - budget.start)
    except (KeyError, TypeError, IndexError, z3.Z3Exception) as exc:
        raise VerificationError('malformed or unsupported convex certificate: ' + str(exc)) from exc


def atomic_json(path, record):
    path = Path(path)
    temporary = None
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--certificate', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--replay-mode', choices=('batch', 'incremental'), default='batch')
    parser.add_argument('--incumbent-model', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    protected = {path.resolve() for path in (args.source, args.certificate, args.incumbent_model) if path is not None}
    if args.output.resolve() in protected:
        print('verify_convex_certificate: output would overwrite a source, certificate, or incumbent input', file=sys.stderr)
        return 2
    try:
        result = verify_certificate(args.source, args.certificate, timeout=args.timeout,
                                    incumbent_model=args.incumbent_model, replay_mode=args.replay_mode)
        code = 3 if result['verification_incomplete'] else 0
        if code == 3:
            print('verify_convex_certificate: independent replay is incomplete: ' + result['replay_last_unknown_reason'],
                  file=sys.stderr)
    except (OSError, ValueError, TimeoutError, RecursionError) as exc:
        result = dict(certificate_valid=None if isinstance(exc, TimeoutError) else False,
                      no_better_bound_verified=False, optimality_verified=False,
                      error=str(exc), verification_incomplete=isinstance(exc, TimeoutError),
                      source=str(args.source.resolve()), certificate=str(args.certificate.resolve()),
                      replay_mode=args.replay_mode, timeout_seconds=args.timeout)
        print('verify_convex_certificate: ' + str(exc), file=sys.stderr)
        code = 3 if isinstance(exc, TimeoutError) else 2
    try:
        atomic_json(args.output, result)
    except OSError as exc:
        print('verify_convex_certificate: cannot write verification report: ' + str(exc), file=sys.stderr)
        return 2
    if code == 0:
        print(json.dumps({key: result[key] for key in ('certificate_valid', 'linear_replay_result',
                                                      'no_better_bound_verified', 'optimality_verified')}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
