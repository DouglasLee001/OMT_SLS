"""Source product-graph QF_LIA over-approximations, with replayable receipts.

Cuts are classical necessary product lemmas. Seeds choose their constants only;
neither a seed coordinate nor a heuristic domain is an asserted assumption.
The coordinator never imports Z3; each arm has its own killable SDK process.
"""
import importlib.util
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sys
import time

_path = Path(__file__).with_name('closed_bound_diagnostic.py')
_spec = importlib.util.spec_from_file_location('product_graph_closed_runner', _path)
runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)
Config = runner.Config
ARMS = ['lia_plain', 'lia_seeded']
MAX_PRODUCTS = 10000
MAX_CUT_RECORDS = 100000
MAX_REFINEMENTS = 256
MAX_ANCHOR_BITS = 4096

def arm_order(index):
    return runner.arm_order(index, ARMS)


def cut_assertions(z3, node, record):
    """Expand one public certificate schema, without source-specific premises."""
    p, x, y = node['p'], node['left'], node['right']
    kind = record['kind']
    if kind == 'zero':
        return [(p == 0) == z3.Or(x == 0, y == 0)]
    if kind == 'square_nonnegative':
        runner._require(x.eq(y), 'square lemma requires identical factors')
        return [p >= 0]
    a = int(record['a'])
    if kind == 'line_left':
        return [z3.Or(z3.Not(x == a), p == a*y)]
    if kind == 'line_right':
        return [z3.Or(z3.Not(y == a), p == a*x)]
    if kind == 'integer_square':
        runner._require(x.eq(y), 'integer square lemma requires identical factors')
        return [p >= (2*a+1)*x - a*(a+1)]
    runner._require(kind == 'orthant', 'unknown product cut schema')
    b = int(record['b'])
    plane = a*y + b*x - a*b
    return [z3.Implies(z3.And(x >= a, y >= b), p >= plane),
            z3.Implies(z3.And(x <= a, y <= b), p >= plane),
            z3.Implies(z3.And(x >= a, y <= b), p <= plane),
            z3.Implies(z3.And(x <= a, y >= b), p <= plane)]


def _query(solver):
    # The standard serializer adds only this harmless status annotation. Omit
    # it so the independent checker accepts a small declarative command set.
    return '(set-logic QF_LIA)\n' + '\n'.join(
        line for line in solver.to_smt2().splitlines()
        if line.strip() != '(set-info :status unknown)') + '\n'


class SolverFacade:
    """A bounded product-graph refinement loop using one incremental LIA solver."""
    def __init__(self, z3, arm, payload, receipt_path, *, strategy='orthant'):
        runner._require(strategy in ('orthant', 'line'), 'unknown product cut strategy')
        allowed = ARMS if strategy == 'orthant' else ('line_plain','line_seeded')
        runner._require(arm in allowed, 'unknown product graph arm')
        self.strategy, self.seeded = strategy, arm in ('lia_seeded','line_seeded')
        self.z3, self.arm, self.payload = z3, arm, payload
        self.started, self.last_progress = time.monotonic(), float('-inf')
        self.record, self.receipt = payload['record'], Path(receipt_path)
        self.progress_path, self.phase = self.receipt.with_suffix('.progress.json'), 'graph_setup'
        self.deadline = payload['deadline']
        self.solver = z3.SolverFor('QF_LIA')
        self.nodes, self.cuts, self.cut_keys = [], [], set()
        self.ground = {}
        self.original, self.lifted = [], []
        self.answer_model, self.reason = None, 'not checked'
        self.policy = dict(tactic='product-graph-qf-lia' if strategy == 'orthant' else 'product-line-qf-lia', seed_anchors=self.seeded,
                           products=0, cut_records=0, refinements=0, queries=0,
                           max_refinements=MAX_REFINEMENTS, added_source_bounds=False,
                           final_query_path=None, final_query_sha256=None,
                           certificate_path=None, certificate_sha256=None,
                           model_path=None, model_sha256=None, progress_path=str(self.progress_path.resolve()))
        self._progress('graph_setup', force=True)

    def _progress(self, phase, *, force=False):
        self.phase = phase
        now = time.monotonic()
        if not force and now-self.last_progress < 1:
            return
        data = dict(phase=phase, elapsed_seconds=now-self.started,
                    **{key:self.policy.get(key,0) for key in ('queries','refinements','products','cut_records')})
        temporary = self.progress_path.with_suffix('.progress.tmp')
        with temporary.open('x') as stream:
            json.dump(data,stream,allow_nan=False)
            stream.write('\n')
        os.replace(temporary,self.progress_path)
        self.last_progress = now

    def remaining(self):
        self._progress(self.phase)
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('product graph wallclock limit reached')
        return value

    def _is_ground(self, expression):
        """Syntactic groundness only; do not simplify the authoritative source."""
        todo = [(expression, False)]
        while todo:
            self.remaining()
            node, ready = todo.pop()
            identity = node.get_id()
            if identity in self.ground: continue
            if ready:
                self.ground[identity] = (node.decl().kind() != self.z3.Z3_OP_UNINTERPRETED
                                         and all(self.ground[c.get_id()] for c in node.children()))
            else:
                todo.append((node, True))
                todo.extend((child, False) for child in node.children() if child.get_id() not in self.ground)
        return self.ground[expression.get_id()]

    def _product(self, left, right, names, cache):
        z3 = self.z3
        if self._is_ground(left) or self._is_ground(right):
            return left*right
        key = left.get_id(), right.get_id()
        if key not in cache:
            if len(self.nodes) >= MAX_PRODUCTS:
                raise runner.Unsupported('product graph node guard exceeded')
            number = len(self.nodes)
            name = '__product_graph_' + str(number)
            while name in names:
                number += 1
                name = '__product_graph_' + str(number)
            names.add(name)
            node = dict(p=z3.Int(name), left=left, right=right)
            self.nodes.append(node)
            self.policy['products'] = len(self.nodes)
            cache[key] = node['p']
        return cache[key]

    def add(self, *assertions):
        runner._require(not self.original, 'product facade accepts one complete source query')
        self.original = list(assertions)
        bound = self.original[-1]
        # IntNumRef's reflected comparison can serialize g<=N as N>=g.
        objective_left = ((bound.decl().kind() == self.z3.Z3_OP_LE and self.record['direction'] == 'min')
                          or (bound.decl().kind() == self.z3.Z3_OP_GE and self.record['direction'] == 'max'))
        self.objective = bound.arg(0 if objective_left else 1)
        order = runner._supported_dag(self.z3, self.original, self.remaining)
        names = {str(e.decl().name()) for e in order if e.decl().kind() == self.z3.Z3_OP_UNINTERPRETED}
        self.constants = [e for e in order if e.decl().kind() == self.z3.Z3_OP_UNINTERPRETED]
        rebuilt, cache = {}, {}
        for expression in order:
            self.remaining()
            children = [rebuilt[c.get_id()] for c in expression.children()]
            if expression.decl().kind() == self.z3.Z3_OP_MUL:
                value = children[0]
                for child in children[1:]:
                    self.remaining()
                    value = self._product(value, child, names, cache)
            else:
                value = expression.decl()(*children) if children else expression
            rebuilt[expression.get_id()] = value
        self.lifted = [rebuilt[e.get_id()] for e in self.original]
        self.solver.add(*self.lifted)
        for node in self.nodes:
            self._cut(node, 'zero')
            if node['left'].eq(node['right']):
                self._cut(node, 'square_nonnegative')
                for a in (-1, 0, 1):
                    self._cut(node, 'integer_square', a=a)
            if self.strategy == 'orthant':
                for a, b in itertools.product((-1, 0, 1), repeat=2):
                    self._cut(node, 'orthant', a=a, b=b)
        if self.seeded:
            self._seed_corners()
        self.policy.update(products=len(self.nodes), initial_cut_records=len(self.cuts))

    def _cut(self, node, kind, *, a=None, b=None, model=None):
        self.remaining()
        record = dict(kind=kind, product=str(node['p'].decl().name()))
        if a is not None: record['a'] = str(a)
        if b is not None: record['b'] = str(b)
        key = kind, record['product'], record.get('a'), record.get('b')
        if key in self.cut_keys:
            return False
        if len(self.cuts) >= MAX_CUT_RECORDS:
            raise runner.Unsupported('product cut resource guard exceeded')
        forms = cut_assertions(self.z3, node, record)
        if model is not None:
            runner._require(any(self.z3.is_false(model.eval(f, model_completion=True)) for f in forms),
                            'refinement does not exclude its spurious model')
        self.solver.add(*forms)
        self.cuts.append(record)
        self.cut_keys.add(key)
        self.policy['cut_records'] = len(self.cuts)
        return True

    def _seed_corners(self):
        self._progress('seed_setup', force=True)
        z3, seed = self.z3, self.record.get('seed', {})
        runner._require(isinstance(seed, dict), 'seed must be a coordinate object')
        if self.record.get('seed_path') is not None:
            first = runner._first(self.payload['paths']['first_helper'])
            self.policy['seed_sha256'] = first._file(self.record['seed_path'], self.record['seed_sha256'])
            self.remaining()
        substitutions = []
        for constant in self.constants:
            self.remaining()
            raw = seed.get(str(constant.decl().name()))
            if constant.sort().kind() == z3.Z3_BOOL_SORT:
                value = z3.BoolVal(raw is True or raw == 'true')
            else:
                raw = str(raw) if type(raw) is int else raw
                if not isinstance(raw, str) or not re.fullmatch('-?[0-9]{1,1200}', raw):
                    raw = '0'
                value = z3.IntVal(raw)
            substitutions.append((constant, value))
        for node in self.nodes:
            self.remaining()
            values = [z3.simplify(z3.substitute(node[key], *substitutions)) for key in ('left','right')]
            if not all(z3.is_int_value(value) and value.as_long().bit_length() <= MAX_ANCHOR_BITS for value in values):
                self.policy['seed_incomplete'] = True
                return
            a,b = [value.as_long() for value in values]
            if self.strategy == 'line':
                self._cut(node, 'line_left', a=a)
                self._cut(node, 'line_right', a=b)
            else:
                self._cut(node, 'orthant', a=a, b=b)
                if node['left'].eq(node['right']): self._cut(node, 'integer_square', a=a)
            substitutions.append((node['p'], z3.IntVal(a*b)))

    def to_smt2(self):
        return _query(self.solver)

    def set(self, **parameters):
        # Preserve the shared caller's supplied finite wall limit, then update
        # the backend timeout before each actual incremental check.
        if 'timeout' in parameters:
            self.deadline = min(self.deadline, time.monotonic()+float(parameters['timeout'])/1000)
        self.solver.set(**parameters)

    def _persist(self, answer):
        self._progress('persist', force=True)
        self.remaining()
        query = self.to_smt2()
        query_hash = hashlib.sha256(query.encode()).hexdigest()
        path = self.receipt.with_suffix('.final.smt2')
        with path.open('x') as stream: stream.write(query)
        self.policy.update(final_query_path=str(path.resolve()), final_query_sha256=query_hash)
        self.remaining()
        first = runner._first(self.payload['paths']['first_helper'])
        first._file(self.record['source'], self.record['source_sha256'])
        if answer == self.z3.sat:
            assignments, sorts = {}, {}
            for constant in self.constants:
                self.remaining()
                name = str(constant.decl().name())
                value = self.answer_model.eval(constant, model_completion=True)
                runner._require(name not in assignments, 'overloaded source constant name in model receipt')
                if constant.sort().kind() == self.z3.Z3_INT_SORT:
                    runner._require(self.z3.is_int_value(value), 'noninteger source model value')
                    assignments[name], sorts[name] = str(value.as_long()), 'Int'
                else:
                    runner._require(self.z3.is_true(value) or self.z3.is_false(value), 'nonboolean source model value')
                    assignments[name], sorts[name] = self.z3.is_true(value), 'Bool'
            model = dict(version='product-graph-source-model-v1', source_path=self.record['source'],
                         source_sha256=self.record['source_sha256'], direction=self.record['direction'],
                         M=str(self.record['M']), assignments=assignments, sorts=sorts,
                         objective_value=self.answer_model.eval(self.objective, model_completion=True).sexpr(),
                         diagnostic_only=True, is_optimal=False)
            model_path = self.receipt.with_suffix('.model.json')
            data = json.dumps(model, indent=2, ensure_ascii=False, allow_nan=False)+'\n'
            with model_path.open('x') as stream: stream.write(data)
            self.policy.update(model_path=str(model_path.resolve()), model_sha256=hashlib.sha256(data.encode()).hexdigest())
        if answer == self.z3.unsat:
            source = first._file(self.record['source'], self.record['source_sha256'], content=True).decode('utf-8')
            strict = first._helper(self.payload['paths']['strict_helper'])
            _, direction, objective_text = strict._query(source)
            runner._require(direction == self.record['direction'], 'certificate source direction changed')
            certificate = dict(version='product-graph-lia-v1', source_path=self.record['source'],
                               source_sha256=self.record['source_sha256'], direction=direction, M=str(self.record['M']),
                               original_objective=objective_text, arm=self.arm, answer='unsat',
                               diagnostic_only=True, is_optimal=False, iterations=self.policy['refinements'],
                               products=[dict(name=str(n['p'].decl().name()), left=n['left'].sexpr(), right=n['right'].sexpr())
                                         for n in self.nodes],
                               lifted_assertions=[f.sexpr() for f in self.lifted], cuts=self.cuts,
                               query_smt2=query, query_sha256=query_hash, query_path=str(path.resolve()))
            certificate_path = self.receipt.with_suffix('.certificate.json')
            data = json.dumps(certificate, indent=2, ensure_ascii=False, allow_nan=False)+'\n'
            with certificate_path.open('x') as stream: stream.write(data)
            self.policy.update(certificate_path=str(certificate_path.resolve()),
                               certificate_sha256=hashlib.sha256(data.encode()).hexdigest())
        self.remaining()
        self._progress('return', force=True)

    def check(self):
        z3 = self.z3
        try:
            for iteration in range(MAX_REFINEMENTS+1):
                self.solver.set(timeout=max(1, int(1000*self.remaining())))
                self.policy['queries'] += 1
                self._progress('lia_check', force=True)
                answer = self.solver.check()
                self._progress('model_validation', force=True)
                self.remaining()
                if answer != z3.sat:
                    self.reason = self.solver.reason_unknown() if answer == z3.unknown else ''
                    self._persist(answer)
                    return answer
                model = self.solver.model()
                if all(z3.is_true(model.eval(form, model_completion=True)) for form in self.original):
                    self.answer_model = model
                    self._persist(z3.sat)
                    return z3.sat
                if iteration == MAX_REFINEMENTS:
                    self.reason = 'product refinement resource guard exceeded'
                    break
                added = False
                self._progress('refinement', force=True)
                for node in self.nodes:
                    self.remaining()
                    values = [model.eval(node[key], model_completion=True) for key in ('left','right','p')]
                    if not all(z3.is_int_value(value) for value in values):
                        self.reason = 'unevaluable product model'
                        return z3.unknown
                    a,b,p = [value.as_long() for value in values]
                    if p == a*b: continue
                    if max(a.bit_length(),b.bit_length()) > MAX_ANCHOR_BITS:
                        self.reason = 'product anchor arithmetic resource guard exceeded'
                        return z3.unknown
                    if self.strategy == 'line':
                        added = self._cut(node, 'line_left', a=a, model=model) or added
                    else:
                        added = self._cut(node, 'orthant', a=a, b=b, model=model) or added
                    if node['left'].eq(node['right']): self._cut(node, 'integer_square', a=a)
                    if added: break
                if not added:
                    self.reason = 'spurious source model without a new excluding product cut'
                    break
                self.policy['refinements'] += 1
            self._persist(z3.unknown)
        except (TimeoutError, runner.Unsupported, OSError, ValueError) as error:
            self.reason = str(error)
        return z3.unknown

    def model(self):
        runner._require(self.answer_model is not None, 'no complete source model')
        return self.answer_model

    def reason_unknown(self):
        return self.reason

def run_case(record, config, case_dir, stop):
    return runner.run_case(record, config, case_dir, stop, arms=ARMS, worker_path=Path(__file__),
                           run_metadata=dict(version='product-graph-diagnostic-v1', tactic='product-graph-qf-lia'))


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--arm':
        raise SystemExit('only private --arm PAYLOAD RECEIPT is supported')
    payload = json.loads(Path(sys.argv[2]).read_text())
    def factory(z3, arm):
        facade = SolverFacade(z3, arm, payload, sys.argv[3])
        return facade, facade.policy
    runner._run_arm(sys.argv[2], sys.argv[3], solver_factory=factory)
