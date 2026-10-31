"""Independent exact checker for source-affine-ray-v1 certificates.

Z3 is supplied by the caller and is used only to parse original source ASTs.
No solver/Optimize check is called. Polynomial arithmetic and tail truth are
implemented here independently of the candidate producer.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import time

SCHEMA = 'source-affine-ray-v1'
FIELDS = {'schema', 'source', 'source_sha256', 'direction', 'objective',
          'integer_variables', 'threshold'}
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)\Z')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


class _Budget:
    def __init__(self, deadline, max_nodes, max_bits):
        _require(type(max_nodes) is int and 0 < max_nodes <= 1000000, 'invalid node budget')
        _require(type(max_bits) is int and 0 < max_bits <= 65536, 'invalid integer-bit budget')
        self.deadline = time.monotonic() + 10 if deadline is None else float(deadline)
        _require(math.isfinite(self.deadline), 'invalid deadline')
        self.max_nodes, self.max_bits, self.work = max_nodes, max_bits, 0
        self.check()

    def check(self, count=1):
        self.work += count
        if time.monotonic() >= self.deadline:
            raise TimeoutError('affine certificate checker deadline reached')
        _require(self.work <= self.max_nodes, 'affine certificate node/work resource limit')

    def integer(self, value):
        self.check()
        _require(type(value) is int and abs(value).bit_length() <= self.max_bits,
                 'affine certificate integer-bit resource limit')
        return value

    def decimal(self, value):
        _require(type(value) is str and DECIMAL.fullmatch(value), 'expected exact decimal integer string')
        _require(len(value.lstrip('-')) <= self.max_bits * 30103 // 100000 + 2,
                 'affine certificate integer-bit resource limit')
        return self.integer(int(value))


def _forms(text, budget):
    """Read SMT-LIB tokens, including comments and quoted metadata/symbols."""
    top, stack, i, size = [], [], 0, len(text)
    while i < size:
        budget.check()
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == ';':
            end = text.find('\n', i)
            i = size if end < 0 else end + 1
            continue
        if char == '(':
            form = []
            (stack[-1] if stack else top).append(form)
            stack.append(form)
            _require(len(stack) <= 256, 'source nesting resource limit')
            i += 1
            continue
        if char == ')':
            _require(bool(stack), 'unmatched source parenthesis')
            stack.pop()
            i += 1
            continue
        start = i
        if char == '|':
            i = text.find('|', i + 1)
            _require(i >= 0, 'unterminated source quoted symbol')
            i += 1
        elif char == '"':
            i += 1
            while i < size:
                if text[i] == '"':
                    i += 1
                    if i < size and text[i] == '"':
                        i += 1
                        continue
                    break
                i += 1
            else:
                raise ValueError('unterminated source string')
        else:
            while i < size and not text[i].isspace() and text[i] not in '();':
                i += 1
        (stack[-1] if stack else top).append(text[start:i])
    _require(not stack, 'unclosed source parenthesis')
    return top


def _symbol(token):
    _require(isinstance(token, str) and token and not token.startswith('"'), 'invalid source symbol')
    name = token[1:-1] if token.startswith('|') and token.endswith('|') else token
    _require(name and '\\' not in name and not DECIMAL.fullmatch(name), 'unsupported source symbol')
    return name


def _prepare_source(text, budget):
    """Validate raw syntax before SDK folding; remove all executable commands."""
    declared, kept = set(), []
    direction, checked = None, False

    def expression(node, depth=0):
        budget.check()
        _require(depth <= 256, 'source expression depth resource limit')
        if isinstance(node, str):
            if node in ('true', 'false'):
                return 'Bool'
            if DECIMAL.fullmatch(node):
                budget.decimal(node)
                return 'Int'
            _require(_symbol(node) in declared, 'unknown or unsupported source expression symbol')
            return 'Int'
        _require(isinstance(node, list) and node and isinstance(node[0], str), 'invalid source expression')
        op, args = node[0], node[1:]
        if not args and _symbol(op) in declared:
            return 'Int'
        _require(op in ('+', '-', '*', '=', '<=', '<', '>=', '>', 'and', 'or', 'not'),
                 'unsupported raw source operator: ' + op)
        sorts = [expression(arg, depth + 1) for arg in args]
        if op in ('+', '-', '*'):
            _require(sorts and all(sort == 'Int' for sort in sorts), 'unsupported arithmetic sort/arity')
            return 'Int'
        if op in ('=', '<=', '<', '>=', '>'):
            _require(len(sorts) >= 2 and all(sort == 'Int' for sort in sorts),
                     'unsupported comparison/equality sort/arity')
            return 'Bool'
        _require(all(sort == 'Bool' for sort in sorts) and (op != 'not' or len(sorts) == 1),
                 'unsupported Boolean sort/arity')
        return 'Bool'

    def declare(token):
        name = _symbol(token)
        _require(name not in declared, 'duplicate source declaration')
        declared.add(name)

    def render(node):
        return node if isinstance(node, str) else '(' + ' '.join(render(item) for item in node) + ')'

    for form in _forms(text, budget):
        budget.check()
        _require(isinstance(form, list) and form and isinstance(form[0], str), 'invalid source command')
        op = form[0]
        if op == 'exit':
            _require(len(form) == 1, 'invalid exit command')
            break
        if op in ('set-info', 'set-option', 'echo') or op.startswith('get-'):
            continue
        _require(not checked, 'source mutation after check-sat')
        if op == 'check-sat':
            _require(len(form) == 1, 'unsupported check command')
            checked = True
            continue
        if op == 'set-logic':
            _require(len(form) == 2, 'invalid source logic declaration')
        elif op == 'declare-const':
            _require(len(form) == 3 and form[2] == 'Int', 'only Int source constants are supported')
            declare(form[1])
        elif op == 'declare-fun':
            _require(len(form) == 4 and form[2] == [] and form[3] == 'Int', 'source UF/Bool/Real is unsupported')
            declare(form[1])
        elif op == 'define-fun':
            _require(len(form) == 5 and form[2] == [] and form[3] == 'Int', 'unsupported source definition')
            _require(expression(form[4]) == 'Int', 'source definition must be Int')
            declare(form[1])
        elif op == 'assert':
            _require(len(form) == 2 and expression(form[1]) == 'Bool', 'invalid source assertion')
        elif op in ('minimize', 'maximize'):
            _require(direction is None and len(form) == 2 and expression(form[1]) == 'Int',
                     'expected one original Int objective')
            direction = 'min' if op == 'minimize' else 'max'
            form = ['minimize', form[1]]
        else:
            raise ValueError('unsupported raw source command: ' + op)
        kept.append(form)
    _require(direction is not None, 'source has no objective')
    return '\n'.join(render(form) for form in kept), direction, declared


class _ExactTail:
    def __init__(self, z3, budget, values):
        self.z3, self.budget, self.values = z3, budget, values
        self.polynomials, self.booleans = {}, {}
        self.threshold, self.comparisons = 1, 0

    def checked(self, coefficients):
        result = list(coefficients)
        while len(result) > 1 and result[-1] == 0:
            result.pop()
        _require(len(result) <= 3, 'affine trajectory polynomial degree exceeds 2')
        for value in result:
            self.budget.integer(value)
        return tuple(result)

    def add(self, left, right, sign=1):
        result = list(left) + [0] * max(0, len(right) - len(left))
        for index, value in enumerate(right):
            self.budget.check()
            result[index] += sign * value
        return self.checked(result)

    def multiply(self, left, right):
        result = [0] * (len(left) + len(right) - 1)
        for i, a in enumerate(left):
            for j, b in enumerate(right):
                self.budget.check()
                result[i + j] += a * b
        return self.checked(result)

    def polynomial(self, node):
        self.budget.check()
        identity = node.get_id()
        if identity in self.polynomials:
            return self.polynomials[identity]
        z, kind = self.z3, node.decl().kind()
        if z.is_int_value(node):
            result = self.checked([node.as_long()])
        elif kind == z.Z3_OP_UNINTERPRETED:
            result = self.checked(self.values[str(node.decl().name())])
        else:
            parts = [self.polynomial(child) for child in node.children()]
            if kind == z.Z3_OP_MUL:
                result = (1,)
                for part in parts:
                    result = self.multiply(result, part)
            elif kind == z.Z3_OP_ADD:
                result = (0,)
                for part in parts:
                    result = self.add(result, part)
            elif kind == z.Z3_OP_UMINUS:
                result = self.checked([-value for value in parts[0]])
            elif kind == z.Z3_OP_SUB:
                result = parts[0]
                for part in parts[1:]:
                    result = self.add(result, part, -1)
            else:
                raise ValueError('unsupported arithmetic AST')
        self.polynomials[identity] = result
        return result

    def stabilize(self, poly):
        self.budget.check()
        if len(poly) > 1:
            lower = sum(abs(value) for value in poly[:-1])
            lead = abs(poly[-1])
            safe = self.budget.integer(1 + (lower + lead - 1) // lead)
            self.threshold = max(self.threshold, safe)

    def truth(self, node):
        self.budget.check()
        identity = node.get_id()
        if identity in self.booleans:
            return self.booleans[identity]
        z, kind = self.z3, node.decl().kind()
        if kind == z.Z3_OP_TRUE:
            result = True
        elif kind == z.Z3_OP_FALSE:
            result = False
        elif kind in (z.Z3_OP_AND, z.Z3_OP_OR, z.Z3_OP_NOT):
            # Evaluate every child, including unselected OR branches: every
            # comparison must contribute its own stabilization threshold.
            parts = [self.truth(child) for child in node.children()]
            result = all(parts) if kind == z.Z3_OP_AND else any(parts) if kind == z.Z3_OP_OR else not parts[0]
        else:
            poly = self.add(self.polynomial(node.arg(0)), self.polynomial(node.arg(1)), -1)
            self.stabilize(poly)
            self.comparisons += 1
            sign = (poly[-1] > 0) - (poly[-1] < 0)
            result = {z.Z3_OP_EQ: sign == 0, z.Z3_OP_LE: sign <= 0,
                      z.Z3_OP_LT: sign < 0, z.Z3_OP_GE: sign >= 0,
                      z.Z3_OP_GT: sign > 0}[kind]
        self.booleans[identity] = result
        return result


def _inspect_ast(z3, roots, declared, budget):
    cache, used = {}, set()

    def visit(node, depth=0):
        budget.check()
        _require(depth <= 256, 'source AST depth resource limit')
        identity = node.get_id()
        if identity in cache:
            return cache[identity]
        _require(not z3.is_quantifier(node) and z3.is_app(node), 'unsupported source AST')
        kind, children = node.decl().kind(), node.children()
        if z3.is_int(node):
            if z3.is_int_value(node):
                budget.integer(node.as_long())
                degree = 0
            elif kind == z3.Z3_OP_UNINTERPRETED and not children:
                name = str(node.decl().name())
                _require(name in declared, 'foreign original AST constant')
                used.add(name)
                degree = 1
            else:
                _require(kind in (z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS, z3.Z3_OP_MUL)
                         and children and all(z3.is_int(child) for child in children), 'unsupported integer source AST')
                degrees = [visit(child, depth + 1) for child in children]
                degree = sum(degrees) if kind == z3.Z3_OP_MUL else max(degrees)
            _require(degree <= 2, 'original source polynomial degree exceeds 2')
        else:
            _require(z3.is_bool(node), 'non-Int/Bool source AST')
            if kind in (z3.Z3_OP_TRUE, z3.Z3_OP_FALSE):
                pass
            elif kind in (z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT):
                _require(all(z3.is_bool(child) for child in children)
                         and (kind != z3.Z3_OP_NOT or len(children) == 1), 'invalid Boolean source AST')
                for child in children:
                    visit(child, depth + 1)
            else:
                _require(kind in (z3.Z3_OP_EQ, z3.Z3_OP_LE, z3.Z3_OP_LT, z3.Z3_OP_GE, z3.Z3_OP_GT)
                         and len(children) == 2 and all(z3.is_int(child) for child in children),
                         'unsupported Boolean/comparison source AST')
                for child in children:
                    visit(child, depth + 1)
            degree = None
        cache[identity] = degree
        return degree

    for root in roots:
        visit(root)
    return sorted(used)


def validate_certificate(z3, certificate, deadline=None, expected_source=None, *, max_nodes=100000, max_bits=8192):
    """Verify a complete original-source integer affine tail; never solve.

    Source-used variables are exactly those present in full hard+objective ASTs.
    The offset at t=0 need not satisfy hard. Success is a diagnostic source
    mathematical conclusion, not an automatic formal A1/product status update.
    """
    budget = _Budget(deadline, max_nodes, max_bits)
    _require(type(certificate) is dict and set(certificate) == FIELDS, 'invalid certificate fields')
    _require(certificate['schema'] == SCHEMA, 'unsupported certificate schema')
    _require(type(certificate['source']) is str and Path(certificate['source']).is_absolute(), 'source must be an absolute path')
    source = Path(certificate['source'])
    _require(source.is_file(), 'source must be a regular file')
    if expected_source is not None:
        _require(source.resolve(strict=True) == Path(expected_source).resolve(strict=True), 'source path identity mismatch')
    _require(type(certificate['source_sha256']) is str and re.fullmatch('[0-9a-f]{64}', certificate['source_sha256']), 'invalid source hash')
    raw = source.read_bytes()
    _require(len(raw) <= 8 * 1024 * 1024, 'source size resource limit')
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == certificate['source_sha256'], 'source hash identity mismatch')
    filtered, direction, declared = _prepare_source(raw.decode('utf-8'), budget)
    _require(certificate['direction'] == direction, 'original objective direction mismatch')
    context = z3.Context()
    parser = z3.Optimize(ctx=context)
    parser.from_string(filtered)
    budget.check()
    objectives = list(parser.objectives())
    _require(len(objectives) == 1, 'expected one original objective AST')
    objective, hard = objectives[0], list(parser.assertions())
    _require(type(certificate['objective']) is str and certificate['objective'] == objective.sexpr(), 'original objective AST mismatch')
    names = _inspect_ast(z3, hard + [objective], declared, budget)
    entries = certificate['integer_variables']
    _require(type(entries) is list and len(entries) == len(names), 'source-used variable count mismatch')
    values = {}
    for name, entry in zip(names, entries):
        budget.check()
        _require(type(entry) is dict and set(entry) == {'name', 'intercept', 'slope'}
                 and type(entry['name']) is str and entry['name'] == name, 'source-used variables must be complete, unique and sorted')
        values[name] = (budget.decimal(entry['intercept']), budget.decimal(entry['slope']))
    threshold = budget.decimal(certificate['threshold'])
    _require(threshold >= 1, 'threshold must be a positive integer')
    exact = _ExactTail(z3, budget, values)
    truths = [exact.truth(form) for form in hard]
    poly = exact.polynomial(objective)
    _require(len(poly) > 1 and (poly[-1] < 0 if direction == 'min' else poly[-1] > 0),
             'objective must have positive degree and diverge in the optimizing direction')
    exact.stabilize(poly)
    _require(threshold >= exact.threshold, 'supplied threshold is below the independently computed safe threshold')
    _require(all(truths), 'original hard formula is eventually false')
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'source changed during verification')
    budget.check()
    return dict(schema='source-affine-ray-verification-v1', certificate_valid=True,
                source_unbounded_verified=True, formal_A1=False, is_optimal=False,
                source=str(source), source_sha256=digest, direction=direction,
                objective=objective.sexpr(), objective_polynomial=list(map(str, poly)),
                computed_threshold=str(exact.threshold), threshold=str(threshold),
                integer_variable_count=len(names), assertion_count=len(hard),
                comparison_count=exact.comparisons, arithmetic_work=budget.work, solver_calls=0)
