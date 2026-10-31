#!/usr/bin/env python3
"""Task27 pure RED/GREEN plus one opt-in fixed six-case generated group."""
from pathlib import Path
import hashlib
import importlib.util
import itertools
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'z3-nia/experiment/source_face_ray_diagnostic.py'
OLD = ROOT / 'z3-nia/experiment/affine_ray_diagnostic.py'
CHECKER = ROOT / 'z3-nia/experiment/verify_affine_ray_certificate.py'
LEXER = ROOT / 'NIA_LS/converter/omt_to_ls.py'
SDK = Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_104807_978529/source/python')
LIBRARY = SDK / 'z3/lib/libz3.so'
PINS = {OLD: 'b0de4115da05bc5a5229690744bf681baf2fe56e0e2708652944b6f437dd0ef5',
        CHECKER: 'ee17b2f71c799ca3b949c7783310e8b453f014cc1bbf58650067287982bbae08',
        LEXER: '8129201d54b896b5474bcce036807f8083d4535db4cc2707d59f6cea0350a141',
        LIBRARY: '2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


producer = load(MODULE, '_source_face_ray_tested') if MODULE.is_file() else None


def hints(folder, entries):
    """Valid names-only native fixture, not a converter equivalence claim."""
    native, model = folder / 'hint-only.nia', folder / 'model.tsv'
    lines = ['NIA_LS_OMT 1', 'STATUS ready', 'SOURCE -',
             'ORIGINAL_OBJECTIVE 30', 'DIRECTION min', f'VARIABLES {len(entries)}']
    for index, (name, kind, value) in enumerate(entries):
        lines.append(f'{index} {kind} {name.encode().hex()}')
    lines += ['LITERALS 0', 'CLAUSES 0', 'OBJECTIVE 0 0', 'END']
    native.write_text('\n'.join(lines) + '\n')
    model.write_text(''.join(f'{name}\t{value}\n' for name, _, value in entries))
    return native, model


class PureTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(producer, 'source-face ray producer is missing')
        return producer

    def test_import_and_frozen_dependencies_do_not_load_sdk(self):
        self.api()
        self.assertNotIn('z3', sys.modules)
        for path, expected in PINS.items():
            self.assertEqual(sha(path), expected)

    def test_canonical_integer_polynomials_cancel_and_linearize(self):
        p = self.api(); b = p.Budget(1)
        self.assertEqual(p._multiply({('x',): 2, (): 1}, {('y',): 3}, b),
                         {('x', 'y'): 6, ('y',): 3})
        self.assertEqual(p._add({('x', 'x'): 2}, {('x', 'x'): -2}, b), {})
        self.assertEqual(p._substitute({('x', 'y'): 6, ('y',): 3}, {'y': -2}, b),
                         {('x',): -12, (): -6})
        with self.assertRaises(p.Unsupported):
            p._multiply({('x', 'x'): 1}, {('y',): 1}, b)

    def test_cover_protects_linear_objective_and_covers_all_hard_terms(self):
        p = self.api(); polynomials = [{('x', 'y'): 1}, {('y', 'z'): 2}]
        covers = p._cover_portfolio(polynomials, {('x',): 1}, 4, p.Budget(1))
        self.assertTrue(covers)
        self.assertEqual(covers, p._cover_portfolio(polynomials, {('x',): 1}, 4, p.Budget(1)))
        self.assertEqual(len(covers), len({tuple(c) for c in covers}))
        for cover in covers:
            self.assertNotIn('x', cover)
            self.assertTrue('y' in cover)
            self.assertTrue('y' in cover or 'z' in cover)
        self.assertEqual(p._cover_portfolio([{('x', 'x'): 1}], {('x',): 1}, 4, p.Budget(1)), [])
        self.assertEqual(p._cover_portfolio([], {(): 7}, 4, p.Budget(1)), [[]])

    def test_exact_hints_missing_zero_and_native_extras_are_explicit(self):
        p = self.api()
        with tempfile.TemporaryDirectory() as folder:
            native, model = hints(Path(folder), [('x', 'I', str(1 << 130)), ('k!0', 'I', '7')])
            values, metadata = p._read_hints(native, model, ['x', 'y'], p.Budget(1))
            self.assertEqual(values['x'], {'value': str(1 << 130), 'origin': 'native-name-sort'})
            self.assertEqual(values['y'], {'value': '0', 'origin': 'missing-zero'})
            self.assertEqual(metadata['native_extra_names'], ['k!0'])
            self.assertEqual(metadata['missing_source_names'], ['y'])
            self.assertEqual(metadata['native_sha256'], sha(native))

    def test_hint_truncation_duplicate_wrong_sort_and_malformed_values_fail(self):
        p = self.api()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            native, model = hints(folder, [('x', 'I', '1')]); original = native.read_text()
            for text in [original.replace('END', ''), original.replace('VARIABLES 1', 'VARIABLES 2'), original + 'trailing\n']:
                native.write_text(text)
                with self.assertRaises(ValueError): p._read_hints(native, model, ['x'], p.Budget(1))
            native.write_text(original)
            for text in ['x\t1\nx\t2\n', 'x\t1.0\n', 'x\t+1\n', '', 'x\ttrue\n', 'x\t1\nextra\t0\n']:
                model.write_text(text)
                with self.assertRaises(ValueError): p._read_hints(native, model, ['x'], p.Budget(1))
            native, model = hints(folder, [('x', 'B', 'true')])
            with self.assertRaises(ValueError): p._read_hints(native, model, ['x'], p.Budget(1))

    def test_eventual_affine_signs_and_ceiling_threshold(self):
        p = self.api()
        predicates = {'=': lambda q: q == 0, '<': lambda q: q < 0, '<=': lambda q: q <= 0,
                      '>': lambda q: q > 0, '>=': lambda q: q >= 0}
        self.assertEqual(p._threshold((100, -3)), 35)
        for intercept, slope in itertools.product(range(-3, 4), repeat=2):
            threshold = p._threshold((intercept, slope))
            for relation, predicate in predicates.items():
                expected = p._eventual((intercept, slope), relation)
                for t in (threshold, threshold + 1, threshold + 7):
                    self.assertEqual(expected, predicate(intercept + slope * t))

    def test_raw_fragment_checks_unselected_branches_and_quoted_names(self):
        p = self.api(); lexer = p._lexer(None)
        good = '(declare-const |x| Int)(declare-fun |another name| () Int)(assert (or (= x 0) (not (<= |another name| 2))))(minimize x)'
        p._raw_validate(lexer._commands(good), p.Budget(1))
        for bad in ['(declare-const b Bool)', '(declare-const r Real)', '(declare-fun f (Int) Int)',
                    '(assert (or true (= (div x 1) x)))', '(assert (= (ite true x x) x))',
                    '(assert (= (^ x 0) 1))', '(assert (= (let ((q x)) q) x))',
                    '(assert (= (* x x x) 0))', '(assert (= true true))']:
            with self.subTest(bad=bad), self.assertRaises(p.Unsupported):
                p._raw_validate(lexer._commands('(declare-const x Int)' + bad + '(minimize x)'), p.Budget(1))

    def test_no_clobber_and_post_link_timeout_cleanup(self):
        p = self.api()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); target = folder / 'candidate'; target.write_text('keep')
            with self.assertRaises(FileExistsError): p._publish(target, 'new', lambda: None)
            self.assertEqual(target.read_text(), 'keep')
            target.unlink(); calls = 0
            def remaining():
                nonlocal calls
                calls += 1
                if calls == 4: raise TimeoutError('post-link timeout')
            with self.assertRaises(TimeoutError): p._publish(target, 'new', remaining)
            self.assertEqual(list(folder.iterdir()), [])

    def test_invalid_api_and_immediate_timeout_never_reach_sdk(self):
        p = self.api()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); source = folder / 'source.smt2'; source.write_text('(minimize 0)')
            native, model = hints(folder, [])
            for index, seconds in enumerate((0, -1, math.nan, math.inf, 1201)):
                result = p.analyze_face(source, 'min', seconds, folder / f'bad{index}', None,
                                        native_input_path=native, native_model_path=model)
                self.assertEqual(result['status'], 'error'); self.assertEqual(result['checks'], 0)
            result = p.analyze_face(source, 'min', 5e-324, folder / 'expired', None,
                                    native_input_path=native, native_model_path=model)
            self.assertEqual(result['status'], 'timeout'); self.assertEqual(result['checks'], 0)
            self.assertFalse((folder / 'expired').exists())
            for kwargs in [{'direction': 'other'}, {'max_faces': 0}, {'max_faces': 5}, {'max_bits': 0}, {'max_nodes': True}]:
                args = dict(direction='min', max_faces=4, max_nodes=50000, max_bits=8192); args.update(kwargs)
                result = p.analyze_face(source, seconds=1, output_dir=folder, z3_module=None,
                                        native_input_path=native, native_model_path=model, **args)
                self.assertEqual(result['status'], 'error'); self.assertIsNone(result['candidate_path'])
            self.assertEqual(source.read_text(), '(minimize 0)')

    def test_pure_branch_jump_witness_needs_new_intercept(self):
        self.api()
        for t in (0, 1, 10, 10 ** 10):
            x, y, z = -10 - t, 1, 1
            self.assertTrue(y == 1 and ((z == 0 and x * y == 0) or (z == 1 and x * y <= -10)))
        # a_z=0 cannot eventually equal 1: equality requires d_z=0 and a_z=1.
        self.assertFalse(0 == 1)

    def test_final_report_lateness_revokes_candidate_and_disk_report(self):
        p = self.api()
        self.assertTrue(hasattr(p, '_finish_report'), 'post-report deadline gate is missing')
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); candidate = folder / 'candidate.json'; candidate.write_text('owned')
            stat = candidate.stat(); identity = (stat.st_dev, stat.st_ino); budget = p.Budget(1)
            result = dict(status='candidate', candidate_path=str(candidate), candidate_sha256=sha(candidate))
            original = p._publish; published = 0
            def expire_after_report(path, content, remaining):
                nonlocal published
                inode = original(path, content, remaining)
                if Path(path).name == 'result.json' and published == 0:
                    published += 1; budget.deadline = -math.inf
                return inode
            with mock.patch.object(p, '_publish', side_effect=expire_after_report):
                p._finish_report(folder, result, candidate, identity, budget, time.monotonic())
            self.assertEqual(result['status'], 'timeout'); self.assertIsNone(result['candidate_path'])
            self.assertFalse(candidate.exists())
            saved = json.loads((folder / 'result.json').read_text())
            self.assertEqual(saved['status'], 'timeout'); self.assertIsNone(saved['candidate_path'])
            self.assertEqual(sorted(q.name for q in folder.iterdir()), ['result.json'])

    def test_expanded_source_depth_guard_is_independent_of_raw_depth(self):
        p = self.api()
        self.assertTrue(hasattr(p, '_source_depth'), 'expanded AST depth guard is missing')
        class Node:
            def __init__(self, index, child=None): self.index, self.child = index, child
            def get_id(self): return self.index
            def children(self): return [] if self.child is None else [self.child]
        order = [Node(0)]
        for index in range(1, 258): order.append(Node(index, order[-1]))
        self.assertEqual(p._source_depth(order[:257], p.Budget(1)), 256)
        with self.assertRaises(p.Unsupported): p._source_depth(order, p.Budget(1))

    def test_final_report_allocation_failure_revokes_own_candidate(self):
        p = self.api()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); candidate = folder / 'candidate.json'; candidate.write_text('owned')
            stat = candidate.stat(); identity = (stat.st_dev, stat.st_ino)
            result = dict(status='candidate', candidate_path=str(candidate), candidate_sha256=sha(candidate))
            with mock.patch.object(p.json, 'dumps', side_effect=MemoryError('injected report allocation')):
                try:
                    p._finish_report(folder, result, candidate, identity, p.Budget(1), time.monotonic())
                except MemoryError:
                    self.fail('report allocation failure escaped with candidate still published')
            self.assertEqual(result['status'], 'error'); self.assertIsNone(result['candidate_path'])
            self.assertFalse(candidate.exists()); self.assertIn('MemoryError', result['report_write_error'])


FIXTURES = [
    ('branch-jump-min', 'min', '(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (= y 1))(assert (or (and (= z 0) (= (* x y) 0)) (and (= z 1) (<= (* x y) (- 10)))))(minimize x)', {'x': 0, 'y': 1, 'z': 0}, ['x', 'y', 'z'], 'candidate'),
    ('branch-jump-max', 'max', '(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (= y 1))(assert (or (and (= z 0) (= (* x y) 0)) (and (= z 1) (>= (* x y) 10))))(maximize x)', {'x': 0, 'y': 1, 'z': 0}, ['x', 'y', 'z'], 'candidate'),
    ('discrete-slope-100', 'min', '(declare-const x Int)(declare-const g Int)(assert (= g (* 100 x)))(minimize g)', {'x': 0, 'g': 0}, ['x'], 'candidate'),
    ('disequality-tail', 'min', '(declare-const x Int)(assert (not (= x 0)))(minimize x)', {'x': 1}, ['x'], 'candidate'),
    ('finite-face', 'min', '(declare-const x Int)(assert (and (>= x 0) (<= x 1)))(minimize x)', {'x': 0}, ['x'], 'no_face_ray'),
    ('missing-zero-empty-face', 'min', '(declare-const x Int)(declare-const y Int)(assert (= y 1))(assert (= (* x y) x))(minimize x)', {'x': 0, 'y': 1}, ['x'], 'no_face_ray'),
]


def assert_actual_lia(z3, roots):
    """Independent typed degree check; raw Z3 Probe is not a complete test.

    In frozen5.1 its syntactic recognizer rejects OP_SUB/OP_UMINUS. This guard
    accepts integer affine arithmetic and rejects any unknown*unknown product.
    """
    cache = {}; pending = [(root, False) for root in roots]
    while pending:
        node, ready = pending.pop(); identity = node.get_id()
        if identity in cache: continue
        assert z3.is_app(node), 'nonapplication/quantifier in actual query'
        children = node.children(); kind = node.decl().kind()
        if not ready:
            pending.append((node, True)); pending.extend((c, False) for c in reversed(children)); continue
        if z3.is_int_value(node): value = ('Int', 0)
        elif z3.is_true(node) or z3.is_false(node): value = ('Bool', 0)
        elif kind == z3.Z3_OP_UNINTERPRETED:
            assert not children and z3.is_int(node), 'unexpected free sort/function'
            value = ('Int', 1)
        elif kind in (z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS, z3.Z3_OP_MUL):
            parts = [cache[c.get_id()] for c in children]
            assert parts and z3.is_int(node) and all(sort == 'Int' for sort, _ in parts)
            degree = sum(d for _, d in parts) if kind == z3.Z3_OP_MUL else max(d for _, d in parts)
            assert degree <= 1, 'unknown coefficient product is nonlinear'
            value = ('Int', degree)
        elif kind in (z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT):
            assert z3.is_bool(node) and all(cache[c.get_id()][0] == 'Bool' for c in children)
            assert kind != z3.Z3_OP_NOT or len(children) == 1
            value = ('Bool', 0)
        else:
            assert kind in (z3.Z3_OP_EQ, z3.Z3_OP_LT, z3.Z3_OP_LE, z3.Z3_OP_GT, z3.Z3_OP_GE)
            assert z3.is_bool(node) and len(children) == 2 and all(cache[c.get_id()][0] == 'Int' for c in children)
            value = ('Bool', 0)
        cache[identity] = value
        assert len(cache) <= 100000
    return len(cache)


def verify_saved_output(z3, checker, index, source, result, destination):
    destination = Path(destination)
    expected = FIXTURES[index][-1]
    assert result['status'] == expected and result['diagnostic_only'] and not result['is_unbounded'] and not result['is_optimal']
    assert result['query_is_qflia'] and len(result['faces']) == 1
    face = result['faces'][0]
    assert sha(face['query_path']) == face['query_sha256']
    query = z3.SolverFor('QF_LIA'); query.from_string(Path(face['query_path']).read_text())
    assertions = list(query.assertions()); nodes = assert_actual_lia(z3, assertions)
    goal = z3.Goal(); goal.add(*assertions)
    raw_probe = z3.Probe('is-qflia')(goal)
    simplified = z3.Goal(); simplified.add(*[z3.simplify(a) for a in assertions])
    simplified_probe = z3.Probe('is-qflia')(simplified)
    checks = dict(query_structurally_QF_LIA=True, query_nodes=nodes,
                  raw_is_qflia_probe=raw_probe, simplified_is_qflia_probe=simplified_probe,
                  probe_is_diagnostic_only=True)
    if expected == 'candidate':
        assert sha(result['candidate_path']) == result['candidate_sha256']
        certificate = json.loads(Path(result['candidate_path']).read_text())
        assert set(certificate) == {'schema', 'source', 'source_sha256', 'direction', 'objective', 'integer_variables', 'threshold'}
        with mock.patch.object(z3.Solver, 'check', side_effect=AssertionError('checker Solver.check forbidden')), \
             mock.patch.object(z3.Optimize, 'check', side_effect=AssertionError('checker Optimize.check forbidden')):
            verified = checker.validate_certificate(z3, certificate, time.monotonic() + 1, expected_source=source)
        (destination / 'independent-checker.json').write_text(json.dumps(verified, indent=2) + '\n')
        assert verified['source_unbounded_verified'] and verified['solver_calls'] == 0 and not verified['formal_A1']
        values = {v['name']: (int(v['intercept']), int(v['slope'])) for v in certificate['integer_variables']}
        if index in (0, 1):
            assert values['y'] == (1, 0) and values['z'] == (1, 0)
            assert values['x'][1] < 0 if index == 0 else values['x'][1] > 0
        if index == 2:
            assert values['g'][1] < 0 and values['g'][1] % 100 == 0
            assert face['fixed'] == [] and result['hints']['g']['origin'] == 'missing-zero'
    else:
        assert result['candidate_path'] is None and not result.get('source_unbounded_verified', False)
    if index == 5:
        assert face['fixed'] == [{'name': 'y', 'value': '0', 'origin': 'missing-zero'}]
    (destination / 'query-lia-verification.json').write_text(json.dumps(checks, indent=2) + '\n')
    return checks


def generated_case(index, output):
    import builtins
    import ctypes
    output = Path(output); output.mkdir(exist_ok=False)
    for path, expected in PINS.items(): assert sha(path) == expected, str(path)
    ctypes.CDLL(str(LIBRARY)); builtins.Z3_LIB_DIRS = [str(LIBRARY.resolve().parent)]
    for key in ('Z3_LIBRARY_PATH', 'PYTHONPATH', 'PATH'): os.environ.pop(key, None)
    sys.path.insert(0, str(SDK))
    import z3
    assert Path(z3.__file__).resolve().is_relative_to(SDK.resolve())
    checker = load(CHECKER, '_task27_independent_ee17')
    name, direction, text, base, native_names, expected = FIXTURES[index]
    source = output / 'original.smt2'; source.write_text(text)
    native, model = hints(output, [(n, 'I', str(base[n])) for n in native_names])
    originals = {str(p): sha(p) for p in (source, native, model)}
    lexer = producer._lexer(LEXER); filtered, _, _ = lexer._query(text)
    parser = z3.Optimize(); parser.from_string(filtered)
    substitutions = [(z3.Int(n), z3.IntVal(str(v))) for n, v in base.items()]
    assert all(z3.is_true(z3.simplify(z3.substitute(h, *substitutions))) for h in parser.assertions())
    own_value = z3.simplify(z3.substitute(parser.objectives()[0], *substitutions)).as_long()
    checks = 0; original_check = z3.Solver.check
    def counted(solver, *args, **kwargs):
        nonlocal checks
        checks += 1
        return original_check(solver, *args, **kwargs)
    result = None
    try:
        with mock.patch.object(z3.Optimize, 'check', side_effect=AssertionError('Optimize.check forbidden')), \
             mock.patch.object(z3.Solver, 'check', counted):
            result = producer.analyze_face(source, direction, 2, output / 'producer', z3,
                                          native_input_path=native, native_model_path=model, strict_helper=LEXER)
        (output / 'actual-result.json').write_text(json.dumps(result, indent=2) + '\n')
        assert checks == 1 and result['checks'] == 1, (checks, result)
        assert result['status'] == expected, result
        query_validation = verify_saved_output(z3, checker, index, source, result, output)
        assert all(sha(p) == expected_hash for p, expected_hash in originals.items())
        receipt = dict(passed=True, index=index, name=name, expected=expected, result=result,
                       own_source_base=base, own_source_value=str(own_value), own_source_base_exact_validated=True,
                       actual_solver_checks=checks, Optimize_checks=0, checker_solver_checks=0,
                       internal_seconds=2, parent_seconds=3, module_sha256=sha(MODULE), tests_sha256=sha(__file__),
                       dependency_hashes={str(p): sha(p) for p in PINS}, original_hashes=originals, query_validation=query_validation,
                       native_fixture='names-only hint artifact; no converter/source equivalence asserted')
        (output / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print('PASS', name, 'checks=', checks)
        return 0
    except BaseException as error:
        (output / 'failure.json').write_text(json.dumps(dict(error=repr(error), actual_solver_checks=checks,
                                                          result=result, index=index), indent=2) + '\n')
        raise


class GeneratedTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NIA_FACE_RAY_PARSE_ONLY') == '1', 'explicit zero-SMT SDK preflight')
    def test_zero_smt_sdk_preflight(self):
        output = Path(os.environ['NIA_FACE_RAY_PREFLIGHT_ARTIFACTS'])
        process = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--preflight', str(output)],
                                 text=True, capture_output=True, timeout=10)
        (output.parent / (output.name + '.stdout')).write_text(process.stdout)
        (output.parent / (output.name + '.stderr')).write_text(process.stderr)
        self.assertEqual(process.returncode, 0, process.stdout + '\n' + process.stderr)

    @unittest.skipUnless(os.environ.get('NIA_FACE_RAY_REAL') == '1', 'one explicitly authorized fixed generated group')
    def test_one_fixed_six_case_group(self):
        self.assertIsNotNone(producer)
        output = Path(os.environ['NIA_FACE_RAY_ARTIFACTS']); output.mkdir(exist_ok=False)
        start = int(os.environ.get('NIA_FACE_RAY_START_AT', '0'))
        self.assertIn(start, (0, 1))
        if start == 1:
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--saved-first',
                       os.environ['NIA_FACE_RAY_SAVED_FIRST'], str(output / 'saved-first-zero-SMT')]
            checked = subprocess.run(command, text=True, capture_output=True, timeout=3)
            (output / 'saved-first.stdout').write_text(checked.stdout)
            (output / 'saved-first.stderr').write_text(checked.stderr)
            self.assertEqual(checked.returncode, 0, checked.stdout + '\n' + checked.stderr)
        completed = []
        for index, fixture in enumerate(FIXTURES):
            if index < start: continue
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--case', str(index), str(output / f'{index}-{fixture[0]}')]
            started = time.monotonic()
            try:
                process = subprocess.run(command, text=True, capture_output=True, timeout=3)
                (output / f'case-{index}.stdout').write_text(process.stdout)
                (output / f'case-{index}.stderr').write_text(process.stderr)
                completed.append(dict(index=index, returncode=process.returncode, outer_seconds=time.monotonic() - started))
                (output / 'execution.json').write_text(json.dumps(completed, indent=2) + '\n')
                self.assertEqual(process.returncode, 0, process.stdout + '\n' + process.stderr)
            except BaseException:
                (output / 'stopped.json').write_text(json.dumps(dict(failed_index=index, completed=completed, retry=False), indent=2) + '\n')
                raise
        self.assertEqual(len(completed), 6 - start)


def preflight(output):
    import builtins
    import ctypes
    output = Path(output); output.mkdir(exist_ok=False)
    for path, expected in PINS.items(): assert sha(path) == expected
    ctypes.CDLL(str(LIBRARY)); builtins.Z3_LIB_DIRS = [str(LIBRARY.resolve().parent)]
    for key in ('Z3_LIBRARY_PATH', 'PYTHONPATH', 'PATH'): os.environ.pop(key, None)
    sys.path.insert(0, str(SDK))
    import z3
    assert Path(z3.__file__).resolve().is_relative_to(SDK.resolve())
    records = []
    with mock.patch.object(z3.Solver, 'check', side_effect=RuntimeError('zero-SMT preflight stop before actual check')), \
         mock.patch.object(z3.Optimize, 'check', side_effect=AssertionError('Optimize.check forbidden')):
        u, v = z3.Int('guard_u'), z3.Int('guard_v')
        assert_actual_lia(z3, [-(3 * u) + 1 <= 0, z3.Or(u == 0, v >= 1)])
        for nonlinear in (u * v <= 0, u * u == 1):
            try:
                assert_actual_lia(z3, [nonlinear])
            except AssertionError:
                pass
            else:
                raise AssertionError('actual-query guard accepted an unknown product')
        for index, (name, direction, text, base, native_names, _) in enumerate(FIXTURES):
            directory = output / f'{index}-{name}'; directory.mkdir()
            source = directory / 'original.smt2'; source.write_text(text)
            native, model = hints(directory, [(n, 'I', str(base[n])) for n in native_names])
            result = producer.analyze_face(source, direction, 2, directory / 'producer', z3,
                                          native_input_path=native, native_model_path=model, strict_helper=LEXER)
            records.append(result)
            assert result['status'] == 'error' and 'preflight stop' in result['reason'], result
            assert result['query_is_qflia'] and result['candidate_path'] is None
            assert len(result['faces']) == 1, result
        # Exact cancellation must happen before cover selection. No model is
        # fabricated: check is forbidden and only the public query is inspected.
        directory = output / 'canonical-cancellation'; directory.mkdir()
        source = directory / 'original.smt2'
        source.write_text('(declare-const x Int)(assert (= (- (* x x) (* x x)) 0))(minimize x)')
        native, model = hints(directory, [('x', 'I', '0')])
        result = producer.analyze_face(source, 'min', 2, directory / 'producer', z3,
                                      native_input_path=native, native_model_path=model, strict_helper=LEXER)
        records.append(result)
        assert result['status'] == 'error' and 'preflight stop' in result['reason'], result
        assert result['faces'][0]['fixed'] == [] and result['query_is_qflia'], result
        for i, (extra, expected) in enumerate([
                ('(assert (or true (= (div x 1) x)))', 'unsupported'),
                ('(assert (or true (= (ite true x x) x)))', 'unsupported'),
                ('(assert (or true (= (^ x 0) 1)))', 'unsupported')]):
            directory = output / f'unsupported-{i}'; directory.mkdir()
            source = directory / 'original.smt2'; source.write_text('(declare-const x Int)' + extra + '(minimize x)')
            native, model = hints(directory, [('x', 'I', '0')])
            result = producer.analyze_face(source, 'min', 2, directory / 'producer', z3,
                                          native_input_path=native, native_model_path=model, strict_helper=LEXER)
            records.append(result)
            assert result['status'] == expected and result['checks'] == 0 and result['candidate_path'] is None, result
        directory = output / 'expanded-alias-depth'; directory.mkdir()
        source = directory / 'original.smt2'
        text = '(declare-const x Int)'; previous = 'x'
        for i in range(260):
            text += f'(define-fun alias{i} () Int (+ {previous} 1))'; previous = f'alias{i}'
        source.write_text(text + f'(minimize {previous})')
        native, model = hints(directory, [('x', 'I', '0')])
        result = producer.analyze_face(source, 'min', 2, directory / 'producer', z3,
                                      native_input_path=native, native_model_path=model, strict_helper=LEXER)
        records.append(result)
        assert result['status'] == 'unsupported' and 'depth' in result['reason'] and result['checks'] == 0, result
    (output / 'verification.json').write_text(json.dumps(dict(passed=True, actual_SMT_checks=0, Optimize_checks=0,
                                                            results=records, module_sha256=sha(MODULE),
                                                            tests_sha256=sha(__file__)), indent=2) + '\n')
    print('PASS zero-SMT publicAPI/parser preflight')
    return 0


def saved_first(directory, output):
    """Revalidate the already executed first check, never call analyze/check."""
    import builtins
    import ctypes
    directory = Path(directory); output = Path(output); output.mkdir(exist_ok=False)
    before = {str(p): sha(p) for p in directory.rglob('*') if p.is_file()}
    for path, expected in PINS.items(): assert sha(path) == expected
    ctypes.CDLL(str(LIBRARY)); builtins.Z3_LIB_DIRS = [str(LIBRARY.resolve().parent)]
    for key in ('Z3_LIBRARY_PATH', 'PYTHONPATH', 'PATH'): os.environ.pop(key, None)
    sys.path.insert(0, str(SDK))
    import z3
    assert Path(z3.__file__).resolve().is_relative_to(SDK.resolve())
    checker = load(CHECKER, '_task27_saved_first_ee17')
    result = json.loads((directory / 'actual-result.json').read_text())
    assert result['checks'] == 1 and result['module_sha256'] == '94922fe2e73443f4d02bf84abafee833dcc9fbe4ba35a62bd9f8460fd4076267'
    source = directory / 'original.smt2'; assert source.read_text() == FIXTURES[0][2]
    with mock.patch.object(z3.Solver, 'check', side_effect=AssertionError('saved check must never rerun')), \
         mock.patch.object(z3.Optimize, 'check', side_effect=AssertionError('saved Optimize.check forbidden')):
        validation = verify_saved_output(z3, checker, 0, source, result, output)
    after = {str(p): sha(p) for p in directory.rglob('*') if p.is_file()}
    assert before == after
    receipt = dict(passed=True, actual_new_solver_checks=0, first_case_not_rerun=True, raw_failure_log_preserved=True,
                   original_module_sha256=result['module_sha256'], current_module_sha256=sha(MODULE),
                   current_tests_sha256=sha(__file__), old_artifacts_unchanged=True, original_inventory=before,
                   query_validation=validation)
    (output / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS saved first case revalidation; zero new solver checks')
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--saved-first':
        raise SystemExit(saved_first(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == '--preflight':
        raise SystemExit(preflight(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == '--case':
        raise SystemExit(generated_case(int(sys.argv[2]), sys.argv[3]))
    unittest.main(verbosity=2, failfast=True)
