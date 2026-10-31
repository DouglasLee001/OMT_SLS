"""Generated-only BV budget tests; opt-in solver windows total at most 12s."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_042232_924488')
SOURCE = ARTIFACT / 'source'
MODULE = ROOT / 'z3-nia/experiment/bv_budget_diagnostic.py'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BvBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = set(sys.modules)
        cls.module = load(MODULE, 'bv_budget_under_test')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bv-budget-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.assertFalse(any(name == 'z3' or name.startswith('z3.') for name in set(sys.modules) - self.before))

    def config(self):
        return self.module.Config(sdk_python=SOURCE / 'python', sdk_library=SOURCE / 'python/z3/lib/libz3.so',
                                  initial_helper=SOURCE / 'script/run_initial.py',
                                  strict_helper=SOURCE / 'script/run_strict_bound.py',
                                  first_helper=SOURCE / 'z3-nia/experiment/strict_first_diagnostic.py',
                                  timeout=3, arm_seconds=.75)

    def record(self, text, best='0', direction='min', index=0):
        source = self.directory / f'source-{index}.smt2'
        source.write_text(text)
        return dict(index=index, filename=f'generated-{index}.smt2', category='generated', source=str(source),
                    source_sha256=digest(source), direction=direction, M=best)

    def test_two_arm_rotation_and_default_closed_rotation(self):
        for index in range(8):
            expected = ['bv_control', 'bv_extended'] if index % 2 == 0 else ['bv_extended', 'bv_control']
            self.assertEqual(self.module.arm_order(index), expected)
        self.assertEqual(self.module.runner.arm_order(1), ['int_closed', 'real_closed', 'int_strict'])

    def test_exact_policy_limits_and_common_pipeline(self):
        control, extended = [self.module.policy(name) for name in ('bv_control', 'bv_extended')]
        self.assertEqual(control.get('sat_max_conflicts'), 500)
        self.assertEqual(control.get('engine_max_memory_mb'), 100)
        self.assertEqual(extended.get('sat_max_conflicts'), 4294967295)
        self.assertIsNone(extended.get('engine_max_memory_mb'))
        self.assertEqual(control.get('pipeline'), extended.get('pipeline'))
        self.assertEqual(control.get('nla2bv_max_bv_size'), 64)
        self.assertEqual(control.get('heuristic_bv_bits'), 4)

    def test_stop_and_source_identity_never_launch(self):
        record = self.record('(declare-const x Int)(minimize x)(check-sat)')
        stop = threading.Event(); stop.set()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('stopped/bad source must not launch')):
            result = self.module.run_case(record, self.config(), self.directory / 'stop', stop)
            self.assertEqual(result['status'], 'interrupted')
            self.assertEqual(result.get('tactic'), 'qfnia-bv')
            record['source_sha256'] = '0' * 64
            result = self.module.run_case(record, self.config(), self.directory / 'bad-sha', threading.Event())
            self.assertEqual(result['status'], 'error')
        self.assertIs(result['diagnostic_only'], True)
        self.assertIs(result['is_optimal'], False)

    @unittest.skipUnless(os.environ.get('NIA_BV_BUDGET_REAL_TOYS') == '1', 'explicit generated-only solver group')
    def test_generated_bounded_models_and_under_guard(self):
        evidence = Path(os.environ['NIA_BV_BUDGET_TEST_ARTIFACT_DIR']).resolve()
        evidence.mkdir(parents=True, exist_ok=False)
        self.directory = evidence
        self.assertEqual(digest(SOURCE / 'python/z3/lib/libz3.so'),
                         '01e8020b2e3283842f25e9dea77663a3a4af058c47c690ea065ccc0fe31443ef')
        hard = ('(declare-const x Int)(declare-const g Int)(declare-const b Bool)'
                '(assert (>= x (- 2)))(assert (<= x 2))'
                '(assert (or (and b (>= x 0)) (and (not b) (<= x 0))))'
                '(assert (= g (* x x)))')
        cases = [(hard + '(minimize g)(check-sat)', '1', 'min', 'sat'),
                 (hard + '(minimize g)(check-sat)', '0', 'min', 'unsat'),
                 (hard + '(maximize g)(check-sat)', '1', 'max', 'sat'),
                 (hard + '(maximize g)(check-sat)', '4', 'max', 'unsat'),
                 ('(declare-const x Int)(assert (= (* x x) 10000))(minimize x)(check-sat)',
                  '100', 'min', 'not-unsat')]
        for index, (text, best, direction, expected) in enumerate(cases):
            with self.subTest(index=index):
                record = self.record(text, best, direction, index)
                result = self.module.run_case(record, self.config(), evidence / f'case-{index}', threading.Event())
                self.assertEqual(result['status'], 'complete', result)
                self.assertEqual([a['arm'] for a in result['arms']], self.module.arm_order(index))
                for arm in result['arms']:
                    self.assertEqual(arm['status'], 'complete', arm)
                    self.assertEqual(arm['policy'], self.module.policy(arm['arm']))
                    self.assertEqual(digest(arm['query_path']), arm['query_sha256'])
                    self.assertIs(arm['real_sat_verified'], False)
                    self.assertIs(result['is_optimal'], False)
                    if expected == 'not-unsat':
                        self.assertNotEqual(arm['answer'], 'unsat', arm)
                        self.assertIs(arm['integer_bound_unsat_verified'], False)
                    else:
                        self.assertEqual(arm['answer'], expected, arm)
                    self.assertIs(arm['integer_sat_verified'], arm['answer'] == 'sat')
                    if index < 4:
                        self.assertEqual(arm['original_objective'], 'g')
                        if expected == 'sat':
                            self.assertEqual(arm['objective_value'], '0' if direction == 'min' else '4')
        # Execute the raw primitive in one externally bounded child; its goal
        # precision is never stripped by reasserting ApplyResult.as_expr().
        first = self.module.runner._first(self.config().first_helper)
        initial = first._helper(self.config().initial_helper)
        import time
        code = initial._stage([sys.executable, str(Path(__file__).resolve()), '--under-probe', str(evidence)],
                              time.monotonic() + 2.0, threading.Event(), evidence / 'under.stdout', evidence / 'under.stderr')
        self.assertEqual(code, 0, (evidence / 'under.stderr').read_text())
        raw = json.loads((evidence / 'under.json').read_text())
        self.assertEqual(raw['precision'], 'UNDER')
        self.assertIs(raw['box_inconsistent'], True)
        self.assertIs(raw['box_global_unsat'], False)
        self.assertEqual(raw['preserving_solver_answer'], 'unknown')
        self.assertIs(raw['original_witness_valid'], True)
        self.assertEqual(raw['positive_precision'], 'UNDER')
        self.assertEqual(raw['positive_answer'], 'sat')
        self.assertEqual(raw['positive_source_value'], '-3')
        self.assertIs(raw['positive_full_source_verified'], True)
        # One real default-runner regression, without BV hooks: same source and
        # original qfnra policy must still separate Int UNSAT from Real SAT.
        gap = ('(declare-const x Int)(assert (>= x 0))(assert (<= x 1))'
               '(minimize (* (- (* 2 x) 1) (- (* 2 x) 1)))(check-sat)')
        record = self.record(gap, '1', 'min', 20)
        result = self.module.runner.run_case(record, self.config(), evidence / 'default-closed', threading.Event())
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['tactic'], 'qfnra-nlsat')
        self.assertIs(result['factor'], False)
        self.assertEqual({a['arm']: a['answer'] for a in result['arms']},
                         dict(int_strict='unsat', int_closed='unsat', real_closed='sat'))
        self.assertTrue(all(a['tactic'] == 'qfnra-nlsat' and a['factor'] is False for a in result['arms']))


def sdk_for_probe():
    module = load(MODULE, 'bv_budget_probe')
    first = module.runner._first(SOURCE / 'z3-nia/experiment/strict_first_diagnostic.py')
    import builtins
    import ctypes
    library = SOURCE / 'python/z3/lib/libz3.so'
    ctypes.CDLL(str(library)); builtins.Z3_LIB_DIRS = [str(library.parent)]
    for name in ('Z3_LIBRARY_PATH', 'PATH', 'PYTHONPATH'): os.environ.pop(name, None)
    sys.path.insert(0, str(SOURCE / 'python'))
    import z3
    module.runner._mapped_libraries(library)
    return module, first, z3


def constructor_probe(output):
    module, first, z3 = sdk_for_probe()
    checks = []
    for arm in module.ARMS:
        solver, policy = module.solver_factory(z3, arm)
        solver.set(timeout=500)
        x = z3.Int('constructor_x')
        solver.add(x * x >= 0)
        # Force public solver initialization/parameter validation, without
        # invoking any tactic application or solver.check.
        query = solver.to_smt2()
        checks.append(dict(arm=arm, policy=policy, query=query,
                           lia2card_actual={name: z3.get_param(name) for name in policy['lia2card_module_parameters']}))
    Path(output).mkdir(parents=True, exist_ok=False)
    first._write(Path(output) / 'constructor.json', dict(solver_checks=0, factory_constructions=checks))


def under_probe(output):
    module, first, z3 = sdk_for_probe()
    x, y = z3.Ints('x y')
    formulas = [z3.Or(x == 10, x == 11), y == x * x, x <= 10]
    witness = all(z3.is_true(z3.simplify(z3.substitute(form, (x, z3.IntVal(10)),
                                                                    (y, z3.IntVal(100))))) for form in formulas)
    goal = z3.Goal(); goal.add(*formulas)
    primitive = z3.With(z3.Tactic('nla2bv'), nla2bv_max_bv_size=64)
    translated = primitive(goal)
    assert len(translated) == 1 and translated[0].prec() == z3.Z3_GOAL_UNDER
    widths, seen, todo = set(), set(), list(translated[0])
    while todo:
        node = todo.pop()
        if node.get_id() in seen: continue
        seen.add(node.get_id())
        if z3.is_bv(node): widths.add(node.size())
        todo.extend(node.children())
    boxed = module._bv_cleanup(z3, 'bv_control')(translated[0])
    assert len(boxed) == 1 and boxed[0].prec() == z3.Z3_GOAL_UNDER and boxed[0].inconsistent()
    preserving = z3.Then(primitive, module._bv_cleanup(z3, 'bv_control'), z3.Tactic('fail-if-undecided')).solver()
    preserving.set(timeout=500); preserving.add(*formulas)
    answer = preserving.check()
    positive_x = z3.Int('positive_x')
    positive_forms = [positive_x * positive_x == 9, positive_x <= 2]
    positive_goal = z3.Goal(); positive_goal.add(*positive_forms)
    positive_translated = primitive(positive_goal)
    assert len(positive_translated) == 1 and positive_translated[0].prec() == z3.Z3_GOAL_UNDER
    positive = z3.Then(primitive, module._bv_cleanup(z3, 'bv_control'), z3.Tactic('fail-if-undecided')).solver()
    positive.set(timeout=500); positive.add(*positive_forms)
    positive_answer = positive.check()
    assert positive_answer == z3.sat
    positive_model = positive.model()
    positive_verified = all(z3.is_true(positive_model.eval(form, model_completion=True)) for form in positive_forms)
    first._write(Path(output) / 'under.json', dict(precision='UNDER', box_inconsistent=boxed[0].inconsistent(),
                                                 bitvector_widths=sorted(widths),
                                                 box_global_unsat=bool(z3.z3core.Z3_goal_is_decided_unsat(boxed[0].ctx.ref(), boxed[0].goal)),
                                                 preserving_solver_answer=str(answer), original_witness_valid=witness,
                                                 positive_precision='UNDER', positive_answer=str(positive_answer),
                                                 positive_source_value=str(positive_model.eval(positive_x).as_long()),
                                                 positive_full_source_verified=positive_verified))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--under-probe':
        under_probe(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == '--constructor-probe':
        constructor_probe(sys.argv[2])
    else:
        unittest.main()
