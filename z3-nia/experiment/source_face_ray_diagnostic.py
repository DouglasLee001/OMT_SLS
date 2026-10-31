"""SLS-hinted source affine faces, QF_LIA tail synthesis, candidates only.

Import never loads Z3. The caller supplies the frozen SDK in an externally
bounded child. Fixed values are untrusted search restrictions, not source-model
recovery. Only the independent unchanged source-ray checker can verify a ray.
Face UNSAT/finite results never prove original boundedness or optimality.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OLD = Path(__file__).with_name('affine_ray_diagnostic.py')
CHECKER = Path(__file__).with_name('verify_affine_ray_certificate.py')
OLD_SHA = 'b0de4115da05bc5a5229690744bf681baf2fe56e0e2708652944b6f437dd0ef5'
CHECKER_SHA = 'ee17b2f71c799ca3b949c7783310e8b453f014cc1bbf58650067287982bbae08'
LEXER_SHA = '8129201d54b896b5474bcce036807f8083d4535db4cc2707d59f6cea0350a141'
LIBRARY_SHA = '2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
MAX_BYTES = 8 * 1024 * 1024
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)\Z')


class Unsupported(ValueError):
    pass


class Budget:
    def __init__(self, seconds, max_nodes=50000, max_bits=8192):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 1200:
            raise ValueError('seconds must be finite in (0,1200]')
        if type(max_nodes) is not int or not 1 <= max_nodes <= 50000:
            raise ValueError('max_nodes must be an integer in [1,50000]')
        if type(max_bits) is not int or not 1 <= max_bits <= 8192:
            raise ValueError('max_bits must be an integer in [1,8192]')
        self.deadline = time.monotonic() + seconds
        self.max_nodes, self.max_bits, self.work = max_nodes, max_bits, 0

    def remaining(self):
        return self.deadline - time.monotonic()

    def check(self):
        self.work += 1
        if self.remaining() <= 0:
            raise TimeoutError('source-face total deadline reached')
        if self.work > 20 * self.max_nodes:
            raise Unsupported('combined arithmetic/traversal work guard exceeded')

    def nodes(self, count, label):
        self.check()
        if count > self.max_nodes:
            raise Unsupported(label + ' node/term guard exceeded')

    def number(self, value):
        self.check()
        if type(value) is not int or abs(value).bit_length() > self.max_bits:
            raise Unsupported('integer bit guard exceeded')
        return value

    def decimal(self, value):
        self.check()
        if type(value) is not str or not DECIMAL.fullmatch(value):
            raise ValueError('expected canonical exact decimal integer')
        if len(value.lstrip('-')) > self.max_bits * 30103 // 100000 + 2:
            raise Unsupported('decimal bit guard exceeded')
        return self.number(int(value))


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path, budget):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise Unsupported('input file size/type guard')
    data = bytearray()
    with path.open('rb') as handle:
        while True:
            budget.check()
            block = handle.read(65536)
            if not block:
                break
            data.extend(block)
            if len(data) > MAX_BYTES:
                raise Unsupported('growing input file size guard')
    return path, bytes(data)


def _load_frozen(path, expected):
    path = Path(path).resolve(strict=True)
    if _digest(path) != expected:
        raise ValueError('frozen dependency hash mismatch: ' + str(path))
    name = '_source_face_dependency_' + expected + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _lexer(helper):
    path = (ROOT / 'NIA_LS/converter/omt_to_ls.py') if helper is None else Path(getattr(helper, '__file__', helper))
    return _load_frozen(path, LEXER_SHA)


def _accumulate(poly, factors, value, budget):
    budget.check()
    if value:
        total = budget.number(poly.get(factors, 0) + value)
        if total:
            poly[factors] = total
        else:
            poly.pop(factors, None)
        budget.nodes(len(poly), 'canonical polynomial')


def _add(left, right, budget, sign=1):
    result = dict(left)
    for factors, value in right.items():
        _accumulate(result, factors, sign * value, budget)
    return result


def _multiply(left, right, budget):
    result = {}
    for lf, lv in left.items():
        for rf, rv in right.items():
            budget.check()
            factors = tuple(sorted(lf + rf))
            if len(factors) > 2:
                raise Unsupported('source polynomial degree exceeds two')
            _accumulate(result, factors, budget.number(lv * rv), budget)
    return result


def _substitute(poly, anchors, budget):
    result = {}
    for factors, coefficient in poly.items():
        budget.check()
        free = []
        for name in factors:
            if name in anchors:
                coefficient = budget.number(coefficient * anchors[name])
            else:
                free.append(name)
        _accumulate(result, tuple(free), coefficient, budget)
    return result


def _cover_portfolio(polynomials, objective, max_faces, budget):
    graph, mandatory = {}, set()
    for polynomial in list(polynomials) + [objective]:
        for factors, coefficient in polynomial.items():
            budget.check()
            if not coefficient or len(factors) < 2:
                continue
            if len(factors) > 2:
                raise Unsupported('cover received higher-degree monomial')
            x, y = factors
            graph.setdefault(x, set()); graph.setdefault(y, set())
            if x == y:
                mandatory.add(x)
            else:
                graph[x].add(y); graph[y].add(x)
    protected = {f[0] for f, c in objective.items() if c and len(f) == 1}
    single_linear = len(protected) == 1 and all(len(f) <= 1 for f in objective)
    if single_linear and protected & mandatory:
        return []
    active = set(graph)
    result = []
    for variant in range(4):
        budget.check()
        order = sorted(active - mandatory, reverse=variant >= 2)
        order.sort(key=lambda v: len(graph[v]), reverse=variant % 2 == 1)
        order.sort(key=lambda v: v not in protected)
        free = set()
        for name in order:
            budget.check()
            if not graph[name] & free:
                free.add(name)
        cover = sorted(active - free)
        if single_linear and set(cover) & protected:
            continue
        if cover not in result:
            result.append(cover)
        if len(result) >= max_faces:
            break
    return result


def _raw_validate(forms, budget):
    declarations, aliases, bodies = set(), {}, []
    for form in forms:
        budget.check()
        if not isinstance(form, list) or not form:
            raise Unsupported('invalid raw source command')
        head = form[0]
        if head == 'declare-const' and len(form) == 3 and form[2] == 'Int':
            name = form[1]
        elif head == 'declare-fun' and len(form) == 4 and form[2] == [] and form[3] == 'Int':
            name = form[1]
        elif head == 'define-fun' and len(form) == 5 and form[2] == [] and form[3] == 'Int':
            name = form[1]; bodies.append((form[4], 'Int'))
            aliases[name] = form[4]
        elif head == 'assert' and len(form) == 2:
            bodies.append((form[1], 'Bool')); continue
        elif head in ('minimize', 'maximize') and len(form) == 2:
            bodies.append((form[1], 'Int')); continue
        elif head == 'set-logic' and len(form) == 2:
            continue
        elif head in ('check-sat', 'exit', 'set-option', 'set-info', 'echo') or str(head).startswith('get-'):
            # The frozen lexical normalizer separately rejects incremental or
            # post-check mutation. These commands are never executed.
            continue
        else:
            raise Unsupported('unsupported raw source command/sort: ' + str(head))
        if not isinstance(name, str):
            raise Unsupported('invalid source symbol')
        canonical = name[1:-1] if name.startswith('|') and name.endswith('|') else name
        if canonical in declarations:
            raise Unsupported('duplicate source declaration')
        declarations.add(canonical)
    seen = 0
    for root, expected in bodies:
        stack = [(root, expected, 0, False)]; degrees = {}
        while stack:
            node, sort, depth, ready = stack.pop(); budget.check()
            if depth > 256:
                raise Unsupported('raw source depth guard exceeded')
            if not ready:
                seen += 1; budget.nodes(seen, 'raw source')
            if isinstance(node, str):
                if sort == 'Bool' and node in ('true', 'false'):
                    degrees[id(node)] = 0; continue
                name = node[1:-1] if node.startswith('|') and node.endswith('|') else node
                if sort == 'Int' and re.fullmatch(r'-?[0-9]+', node):
                    budget.decimal(node); degrees[id(node)] = 0; continue
                if sort == 'Int' and name in declarations:
                    degrees[id(node)] = 1; continue
                raise Unsupported('unsupported raw atom/sort: ' + node)
            if not isinstance(node, list) or not node or not isinstance(node[0], str):
                raise Unsupported('invalid raw expression')
            head, children = node[0], node[1:]
            if sort == 'Int' and head in ('+', '-', '*') and children:
                child_sort = 'Int'
            elif sort == 'Bool' and head in ('=', '<', '<=', '>', '>=') and len(children) == 2:
                child_sort = 'Int'
            elif sort == 'Bool' and head in ('and', 'or'):
                child_sort = 'Bool'
            elif sort == 'Bool' and head == 'not' and len(children) == 1:
                child_sort = 'Bool'
            else:
                raise Unsupported('unsupported raw operator/sort: ' + head)
            if not ready:
                stack.append((node, sort, depth, True))
                stack.extend((child, child_sort, depth + 1, False) for child in reversed(children))
            elif sort == 'Int':
                child_degrees = [degrees[id(child)] for child in children]
                degree = sum(child_degrees) if head == '*' else max(child_degrees)
                if degree > 2:
                    raise Unsupported('raw syntactic degree exceeds two')
                degrees[id(node)] = degree
            else:
                degrees[id(node)] = 0


def _read_hints(native_path, model_path, names, budget):
    native_path, raw = _read(native_path, budget)
    model_path, model_raw = _read(model_path, budget)
    lines = iter(raw.decode('utf-8').splitlines())

    def row(label, length=None):
        budget.check()
        while True:
            text = next(lines, None)
            if text is None:
                raise ValueError('truncated native ' + label)
            fields = text.split()
            if fields:
                break
        if length is not None and len(fields) != length:
            raise ValueError('malformed native ' + label)
        return fields

    def header(label):
        fields = row(label, 2)
        if fields[0] != label:
            raise ValueError('expected native ' + label)
        return fields[1]

    def count(label):
        value = budget.decimal(header(label))
        if value < 0:
            raise ValueError('negative native count')
        budget.nodes(value, label)
        return value

    def decode(text, allow_empty=False):
        if allow_empty and text == '-':
            return ''
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', text):
            raise ValueError('invalid native name hex')
        return bytes.fromhex(text).decode('utf-8')

    if header('NIA_LS_OMT') != '1' or header('STATUS') not in ('ready', 'unsat'):
        raise ValueError('invalid native format/status')
    decode(header('SOURCE'), True); decode(header('ORIGINAL_OBJECTIVE'))
    if header('DIRECTION') not in ('min', 'max'):
        raise ValueError('invalid native direction')
    variables, kinds = [], {}
    for index in range(count('VARIABLES')):
        number, kind, encoded = row('variable', 3); name = decode(encoded)
        if budget.decimal(number) != index or kind not in ('I', 'B') or name in kinds:
            raise ValueError('invalid/duplicate native variable')
        variables.append(name); kinds[name] = kind

    def polynomial(constant, terms):
        budget.decimal(constant); terms = budget.decimal(terms)
        if terms < 0:
            raise ValueError('negative native term count')
        budget.nodes(terms, 'native terms')
        for _ in range(terms):
            fields = row('term'); budget.decimal(fields[0])
            factors = budget.decimal(fields[1]) if len(fields) >= 2 else -1
            if factors < 0 or len(fields) != 2 + 2 * factors:
                raise ValueError('truncated native factors')
            budget.nodes(factors, 'native factors')
            for i in range(2, len(fields), 2):
                budget.check(); variable = budget.decimal(fields[i]); exponent = budget.decimal(fields[i + 1])
                if not 0 <= variable < len(variables) or kinds[variables[variable]] != 'I' or exponent <= 0:
                    raise ValueError('invalid native polynomial factor')

    literal_count = count('LITERALS')
    for index in range(1, literal_count + 1):
        fields = row('literal')
        if len(fields) < 2 or budget.decimal(fields[0]) != index:
            raise ValueError('invalid native literal')
        if fields[1] == 'B' and len(fields) == 3:
            variable = budget.decimal(fields[2])
            if not 0 <= variable < len(variables) or kinds[variables[variable]] != 'B':
                raise ValueError('invalid native Boolean literal')
        elif fields[1] in ('E', 'L') and len(fields) == 4:
            polynomial(fields[2], fields[3])
        else:
            raise ValueError('malformed native literal')
    for _ in range(count('CLAUSES')):
        fields = row('clause'); length = budget.decimal(fields[0])
        if length < 0 or len(fields) != length + 1:
            raise ValueError('truncated native clause')
        for value in fields[1:]:
            number = budget.decimal(value)
            if not number or abs(number) > literal_count:
                raise ValueError('invalid native clause literal')
    fields = row('objective', 3)
    if fields[0] != 'OBJECTIVE':
        raise ValueError('missing native objective')
    polynomial(fields[1], fields[2])
    if row('END', 1) != ['END'] or any(line.strip() for line in lines):
        raise ValueError('missing END/trailing native data')
    values = {}
    for line in model_raw.decode('utf-8').splitlines():
        budget.check(); fields = line.split('\t')
        if len(fields) != 2 or fields[0] in values or fields[0] not in kinds:
            raise ValueError('invalid/duplicate/extra model name')
        name, value = fields
        if kinds[name] == 'I':
            budget.decimal(value)
        elif value not in ('true', 'false'):
            raise ValueError('invalid native Boolean model value')
        values[name] = value
    if set(values) != set(kinds):
        raise ValueError('native model is missing variables')
    hints = {}
    for name in names:
        budget.check()
        if name in kinds and kinds[name] != 'I':
            raise ValueError('same-name native/source sort mismatch')
        hints[name] = dict(value=values.get(name, '0'), origin='native-name-sort' if name in values else 'missing-zero')
    return hints, dict(native=str(native_path), native_sha256=hashlib.sha256(raw).hexdigest(),
                       model=str(model_path), model_sha256=hashlib.sha256(model_raw).hexdigest(),
                       missing_source_names=sorted(set(names) - set(kinds)),
                       native_extra_names=sorted(set(kinds) - set(names)),
                       source_model_recovered=False, native_feasibility_checked=False)


def _threshold(coefficients):
    values = list(coefficients)
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    if len(values) <= 1:
        return 1
    lead = abs(values[-1]); lower = sum(abs(c) for c in values[:-1])
    return 1 + (lower + lead - 1) // lead


def _eventual(coefficients, relation):
    value = next((c for c in reversed(coefficients) if c), 0)
    return {'=': value == 0, '<': value < 0, '<=': value <= 0, '>': value > 0, '>=': value >= 0}[relation]


def _remove_owned(path, identity):
    try:
        stat = Path(path).lstat()
        if (stat.st_dev, stat.st_ino) == identity:
            Path(path).unlink()
    except FileNotFoundError:
        pass


def _publish(path, content, remaining):
    remaining(); path = Path(path); temporary = path.with_name(path.name + '.own-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    stat = os.fstat(fd); identity = (stat.st_dev, stat.st_ino); published = False
    try:
        raw = content.encode('utf-8'); offset = 0
        while offset < len(raw):
            remaining(); offset += os.write(fd, raw[offset:offset + 65536])
        os.close(fd); fd = -1; remaining(); os.link(temporary, path); published = True; remaining()
        _remove_owned(temporary, identity); remaining(); return identity
    except BaseException:
        if published:
            _remove_owned(path, identity)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        _remove_owned(temporary, identity)


def _finish_report(output, result, candidate, identity, budget, started):
    """Revoke late candidates on both sides of final diagnostic publication.

    Reports/SDK destruction are not preemptible. A late report can be rewritten
    only after removing its own inode; foreign files are never overwritten.
    The caller still needs a hard child deadline including context destruction.
    """
    report = Path(output) / 'result.json'
    report_identity = None

    def revoke_if_late():
        if result['status'] == 'candidate' and time.monotonic() >= budget.deadline:
            if identity is not None:
                _remove_owned(candidate, identity)
            result.update(status='timeout', reason='total deadline reached during final publication',
                          candidate_path=None, candidate_sha256=None)
            return True
        return False

    try:
        revoke_if_late()
        result['elapsed_seconds'] = time.monotonic() - started
        report_identity = _publish(report, json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', lambda: None)
        if revoke_if_late():
            _remove_owned(report, report_identity)
            result['elapsed_seconds'] = time.monotonic() - started
            _publish(report, json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', lambda: None)
    except Exception as error:
        result['report_write_error'] = f'{type(error).__name__}: {error}'
        if result['status'] == 'candidate':
            if identity is not None:
                _remove_owned(candidate, identity)
            result.update(status='error', reason='final report publication failed', candidate_path=None, candidate_sha256=None)


def _source_depth(order, budget):
    depths = {}
    for node in order:
        budget.check()
        depth = max((depths[c.get_id()] + 1 for c in node.children()), default=0)
        if depth > 256:
            raise Unsupported('expanded original source AST depth guard exceeded')
        depths[node.get_id()] = depth
    return max(depths.values(), default=0)


def _canonical(z3, order, budget):
    polynomials, degrees, variables = {}, {}, {}
    for node in order:
        budget.check(); identity = node.get_id()
        if not z3.is_int(node):
            continue
        children = node.children(); kind = node.decl().kind()
        if z3.is_int_value(node):
            value = {(): budget.number(node.as_long())}; degree = 0
        elif kind == z3.Z3_OP_UNINTERPRETED:
            name = str(node.decl().name())
            if name in variables and not node.eq(variables[name]):
                raise Unsupported('ambiguous source constant name')
            variables[name] = node; value = {(name,): 1}; degree = 1
        else:
            child_degrees = [degrees[c.get_id()] for c in children]
            degree = sum(child_degrees) if kind == z3.Z3_OP_MUL else max(child_degrees)
            if degree > 2:
                raise Unsupported('original source syntactic degree exceeds two')
            if kind == z3.Z3_OP_MUL:
                value = {(): 1}
                for child in children:
                    value = _multiply(value, polynomials[child.get_id()], budget)
            else:
                value = polynomials[children[0].get_id()]
                if len(children) == 1 and kind in (z3.Z3_OP_SUB, z3.Z3_OP_UMINUS):
                    value = {f: -c for f, c in value.items()}
                for child in children[1:]:
                    value = _add(value, polynomials[child.get_id()], budget, -1 if kind == z3.Z3_OP_SUB else 1)
        polynomials[identity], degrees[identity] = value, degree
    budget.nodes(len(order), 'source DAG')
    return polynomials, variables


def _affine(z3, polynomial, coordinates, context, budget):
    intercept, slope = z3.IntVal(0, ctx=context), z3.IntVal(0, ctx=context)
    for factors, coefficient in polynomial.items():
        budget.check(); number = z3.IntVal(str(coefficient), ctx=context)
        if len(factors) > 1:
            raise Unsupported('fixed face still contains nonlinear monomial')
        if factors:
            a, d = coordinates[factors[0]]
            intercept = intercept + number * a; slope = slope + number * d
        else:
            intercept = intercept + number
    return intercept, slope


def _condition(z3, intercept, slope, relation):
    if relation == '=':
        return z3.And(slope == 0, intercept == 0)
    value = {'<': intercept < 0, '<=': intercept <= 0, '>': intercept > 0, '>=': intercept >= 0}[relation]
    sign = slope < 0 if relation in ('<', '<=') else slope > 0
    return z3.Or(sign, z3.And(slope == 0, value))


def _assert_qflia(z3, assertions, budget):
    seen, pending = set(), list(assertions)
    arithmetic = {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS}
    boolean = {z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT}
    comparisons = {z3.Z3_OP_EQ, z3.Z3_OP_LT, z3.Z3_OP_LE, z3.Z3_OP_GT, z3.Z3_OP_GE}
    while pending:
        budget.check(); node = pending.pop()
        if node.get_id() in seen:
            continue
        seen.add(node.get_id()); budget.nodes(len(seen), 'coefficient query DAG')
        if not z3.is_app(node):
            raise Unsupported('nonapplication coefficient query')
        children = node.children(); kind = node.decl().kind()
        if z3.is_int_value(node) or z3.is_true(node) or z3.is_false(node):
            continue
        if kind == z3.Z3_OP_UNINTERPRETED:
            valid = not children and z3.is_int(node)
        elif kind in arithmetic:
            valid = bool(children) and z3.is_int(node) and all(z3.is_int(c) for c in children)
        elif kind == z3.Z3_OP_MUL:
            valid = bool(children) and z3.is_int(node) and all(z3.is_int(c) for c in children) and sum(not z3.is_int_value(c) for c in children) <= 1
        elif kind in boolean:
            valid = z3.is_bool(node) and all(z3.is_bool(c) for c in children) and (kind != z3.Z3_OP_NOT or len(children) == 1)
        elif kind in comparisons:
            valid = z3.is_bool(node) and len(children) == 2 and all(z3.is_int(c) for c in children)
        else:
            valid = False
        if not valid:
            raise Unsupported('actual coefficient query is not QF_LIA')
        pending.extend(children)
    return len(seen)


def analyze_face(source_path, direction, seconds, output_dir, z3_module, *, native_input_path,
                 native_model_path, strict_helper=None, max_faces=4, max_nodes=50000, max_bits=8192):
    """Return a ray candidate only; require caller child hard deadline.

    strict_helper is the frozen8129 pure lexer (or same-SHA copy), not the old
    strict-bound wrapper. max_nodes bounds each AST/term set and 20*max_nodes
    bounds cumulative traversal/arithmetic work. Input files are capped at8MiB.
    Every face shares seconds, including setup/publication. A final report
    deadline gate revokes late candidates. The caller must also reject late
    child receipts: SDK/context destruction is not preemptible by this API.
    """
    started = time.monotonic(); output = None; owned = False; candidate_identity = None; candidate = None
    result = dict(status='unknown', reason='', candidate_path=None, candidate_sha256=None,
                  checks=0, check_seconds=0., setup_seconds=0., faces=[], hints={}, query_is_qflia=False,
                  diagnostic_only=True, is_unbounded=False, is_optimal=False, formal_A1=False)
    try:
        budget = Budget(seconds, max_nodes, max_bits)
        if direction not in ('min', 'max') or type(max_faces) is not int or not 1 <= max_faces <= 4:
            raise ValueError('direction must be min/max and max_faces an integer in [1,4]')
        budget.check(); source, raw = _read(source_path, budget)
        source_sha = hashlib.sha256(raw).hexdigest(); text = raw.decode('utf-8')
        output = Path(output_dir).absolute()
        if output.exists() or output.is_symlink():
            raise ValueError('output directory must be new')
        budget.check(); output.mkdir(parents=False, exist_ok=False); owned = True; output = output.resolve()
        budget.check(); old = _load_frozen(OLD, OLD_SHA)
        if _digest(CHECKER) != CHECKER_SHA:
            raise ValueError('frozen independent checker hash mismatch')
        budget.check(); lexer = _lexer(strict_helper); budget.check()
        _raw_validate(lexer._commands(text), budget)
        filtered, actual_direction, _ = lexer._query(text)
        if direction != actual_direction:
            raise ValueError('source direction differs from requested direction')
        result.update(source=str(source), source_sha256=source_sha, direction=direction,
                      module_sha256=_digest(__file__), old_producer_sha256=OLD_SHA,
                      checker_sha256=CHECKER_SHA, lexer_sha256=LEXER_SHA)
        z3 = z3_module; budget.check(); sdk = old._sdk_identity(z3, budget.check); budget.check()
        if not sdk['libraries'] or any(item['sha256'] != LIBRARY_SHA for item in sdk['libraries']):
            raise ValueError('mapped SDK library is not frozen2641')
        result['sdk'] = sdk
        context = z3.Context(); parser = z3.Optimize(ctx=context); parser.from_string(filtered); budget.check()
        objectives, hard = list(parser.objectives()), list(parser.assertions())
        if len(objectives) != 1 or not z3.is_int(objectives[0]):
            raise Unsupported('expected one Int original source objective')
        objective = objectives[0]
        try:
            order = old._source_dag(z3, hard + [objective], budget.check)
        except old.Unsupported as error:
            raise Unsupported(str(error)) from error
        result['source_ast_depth'] = _source_depth(order, budget)
        polynomials, variables = _canonical(z3, order, budget); names = sorted(variables)
        hints, metadata = _read_hints(native_input_path, native_model_path, names, budget)
        result.update(hints=hints, hint_artifacts=metadata, objective=objective.sexpr(),
                      integer_variable_count=len(names), source_dag_nodes=len(order))
        relations = {z3.Z3_OP_EQ: '=', z3.Z3_OP_LT: '<', z3.Z3_OP_LE: '<=', z3.Z3_OP_GT: '>', z3.Z3_OP_GE: '>='}
        # A comparison is the canonical difference of its two sides. Covering
        # intermediate syntax would retain canceled quadratic terms and can
        # wrongly freeze a linear objective, e.g. x*x-x*x=0, minimize x.
        atoms_by_id = {}
        for node in order:
            budget.check()
            if node.decl().kind() in relations:
                atoms_by_id[node.get_id()] = _add(polynomials[node.arg(0).get_id()],
                                                  polynomials[node.arg(1).get_id()], budget, -1)
        objective_poly = polynomials[objective.get_id()]
        covers = _cover_portfolio(list(atoms_by_id.values()), objective_poly, max_faces, budget)
        if not covers:
            raise Unsupported('no cover preserving the linear objective')
        result.update(cover_count=len(covers), source_comparison_count=len(atoms_by_id))
        prefix = old._coefficient_prefix(text)
        for index, cover in enumerate(covers):
            budget.check(); face_started = time.monotonic()
            allowance = budget.remaining() / (len(covers) - index)
            face_deadline = min(budget.deadline, face_started + allowance)

            def within_face():
                budget.check()
                if time.monotonic() >= face_deadline:
                    raise TimeoutError('allocated face deadline reached')

            anchors = {name: budget.decimal(hints[name]['value']) for name in cover}
            face = dict(index=index, fixed=[dict(name=name, **hints[name]) for name in cover],
                        free_count=len(names) - len(cover), status='setup', answer=None, check_started=False,
                        check_seconds=0., allowance_seconds=allowance)
            result['faces'].append(face)
            try:
                residuals = {identity: _substitute(poly, anchors, budget) for identity, poly in atoms_by_id.items()}
                residuals[objective.get_id()] = _substitute(objective_poly, anchors, budget)
                if any(len(f) > 1 for poly in residuals.values() for f in poly):
                    raise Unsupported('cover did not linearize every original arithmetic expression')
                residual_objective = residuals[objective.get_id()]
                if not any(f and c for f, c in residual_objective.items()):
                    face.update(status='constant_face_objective', reason='fixed face target is constant'); continue
                coordinates = {name: (z3.Int(prefix + f'face{index}_a{i}', ctx=context),
                                      z3.Int(prefix + f'face{index}_d{i}', ctx=context))
                               for i, name in enumerate(names) if name not in anchors}
                affine = {identity: _affine(z3, poly, coordinates, context, budget) for identity, poly in residuals.items()}
                booleans, atoms = {}, []
                for node in order:
                    within_face()
                    if z3.is_int(node):
                        continue
                    identity, kind, children = node.get_id(), node.decl().kind(), node.children()
                    if z3.is_true(node) or z3.is_false(node):
                        booleans[identity] = node
                    elif kind in relations:
                        intercept, slope = affine[identity]
                        booleans[identity] = _condition(z3, intercept, slope, relations[kind])
                        atoms.append((intercept, slope))
                    elif kind == z3.Z3_OP_NOT:
                        booleans[identity] = z3.Not(booleans[children[0].get_id()])
                    else:
                        values = [booleans[c.get_id()] for c in children]
                        booleans[identity] = z3.And(*values) if kind == z3.Z3_OP_AND else z3.Or(*values)
                target_intercept, target_slope = affine[objective.get_id()]
                assertions = [booleans[h.get_id()] for h in hard]
                assertions.append(target_slope <= -1 if direction == 'min' else target_slope >= 1)
                face['query_nodes'] = _assert_qflia(z3, assertions, budget)
                result['query_is_qflia'] = True
                query = z3.SolverFor('QF_LIA', ctx=context); query.add(*assertions)
                query_path = output / f'face-{index}.smt2'; _publish(query_path, query.to_smt2(), within_face)
                face.update(query_path=str(query_path), query_sha256=_digest(query_path), query_is_qflia=True)
                result['setup_seconds'] = time.monotonic() - started - result['check_seconds']
                within_face(); query.set(timeout=max(1, int(1000 * (face_deadline - time.monotonic()))))
                within_face(); face['check_started'] = True; result['checks'] += 1; checked = time.monotonic()
                try:
                    answer = query.check()
                finally:
                    face['check_seconds'] = time.monotonic() - checked
                    result['check_seconds'] += face['check_seconds']
                within_face(); face['answer'] = str(answer)
                if answer != z3.sat:
                    face.update(status='unsat' if answer == z3.unsat else 'unknown',
                                reason='this restricted face template only' if answer == z3.unsat else query.reason_unknown())
                    continue
                model = query.model()
                if not all(z3.is_true(model.eval(form, model_completion=True)) for form in assertions):
                    raise ValueError('coefficient model fails complete actual query')

                def exact(expression):
                    within_face(); value = model.eval(expression, model_completion=True)
                    if not z3.is_int_value(value):
                        raise ValueError('noninteger face coefficient')
                    return budget.number(value.as_long())

                threshold = 1
                for intercept, slope in atoms + [(target_intercept, target_slope)]:
                    threshold = max(threshold, budget.number(_threshold((exact(intercept), exact(slope)))))
                actual_slope = exact(target_slope)
                if (actual_slope >= 0 if direction == 'min' else actual_slope <= 0):
                    raise ValueError('target slope does not strictly improve')
                entries, point = [], []
                for name in names:
                    within_face()
                    a, d = (anchors[name], 0) if name in anchors else tuple(exact(e) for e in coordinates[name])
                    entries.append(dict(name=name, intercept=str(a), slope=str(d)))
                    point.append((variables[name], z3.IntVal(str(budget.number(a + d * threshold)), ctx=context)))
                for form in hard:
                    within_face()
                    if not z3.is_true(z3.simplify(z3.substitute(form, *point))):
                        raise ValueError('complete original source false at common threshold')
                certificate = dict(schema='source-affine-ray-v1', source=str(source), source_sha256=source_sha,
                                   direction=direction, objective=objective.sexpr(), integer_variables=entries,
                                   threshold=str(threshold))
                model_path = output / f'face-{index}-coefficients.json'
                _publish(model_path, json.dumps(dict(integer_variables=entries, threshold=str(threshold)), indent=2) + '\n', within_face)
                face.update(model_path=str(model_path), model_sha256=_digest(model_path))
                # Native hints never enter this mathematical identity check.
                for path, expected in [(source, source_sha), (Path(metadata['native']), metadata['native_sha256']),
                                       (Path(metadata['model']), metadata['model_sha256']), (OLD, OLD_SHA),
                                       (CHECKER, CHECKER_SHA), (Path(lexer.__file__), LEXER_SHA)]:
                    within_face()
                    if _digest(path) != expected:
                        raise ValueError('input/dependency changed during analysis')
                candidate = output / 'candidate.json'
                candidate_identity = _publish(candidate, json.dumps(certificate, indent=2, ensure_ascii=False) + '\n', within_face)
                within_face(); face.update(status='candidate', threshold=str(threshold))
                result.update(status='candidate', candidate_path=str(candidate), candidate_sha256=_digest(candidate),
                              reason='QF_LIA face SAT candidate; unchanged independent source checker required')
                within_face(); break
            except TimeoutError as error:
                face.update(status='timeout', answer=None, reason=str(error))
                if candidate_identity is not None:
                    _remove_owned(candidate, candidate_identity); candidate_identity = None
                result.update(candidate_path=None, candidate_sha256=None)
                budget.check()
            finally:
                face['elapsed_seconds'] = time.monotonic() - face_started
        else:
            statuses = {face['status'] for face in result['faces']}
            result.update(status='no_face_ray' if statuses <= {'unsat', 'constant_face_objective'} else 'unknown',
                          reason='only these restricted affine faces; no original boundedness conclusion')
        budget.check()
        if _digest(source) != source_sha:
            raise ValueError('source changed during analysis')
        budget.check(); result['work'] = budget.work
    except TimeoutError as error:
        result.update(status='timeout', reason=str(error))
    except Unsupported as error:
        result.update(status='unsupported', reason=str(error))
    except Exception as error:
        result.update(status='error', reason=f'{type(error).__name__}: {error}')
    finally:
        if result['status'] != 'candidate':
            if candidate_identity is not None:
                _remove_owned(candidate, candidate_identity)
            result.update(candidate_path=None, candidate_sha256=None)
        if owned:
            _finish_report(output, result, candidate, candidate_identity, budget, started)
        else:
            result['elapsed_seconds'] = time.monotonic() - started
    return result
