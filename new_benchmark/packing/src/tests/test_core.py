import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nia_bench import core


class CoreTests(unittest.TestCase):
    def test_instance_contains_only_flat_optimization_smt(self):
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('test',self.tiny_case(),self.tiny_module(),Path(folder))
            instance = Path(folder)/'test/instance'
            children = list(instance.iterdir())
            self.assertEqual(len(children),1)
            self.assertTrue(children[0].is_file())
            self.assertEqual(children[0].name,record['instance_id']+'.smt2')
            self.assertIn('(minimize cost)',children[0].read_text())
            self.assertNotIn('z3',[p.name for p in Path(folder).rglob('*')])

    def tiny_module(self):
        def build(data):
            m = core.Model('test bounded quadratic')
            m.integer('x', 0, 3)
            m.integer('cost', role='auxiliary')
            m.assertion('(= cost (* x x))')
            return m
        def check(data, decision):
            x = decision['x']
            if type(x) is not int or not 0 <= x <= 3:
                raise ValueError('invalid application decision')
            return {'cost': x*x}
        return types.SimpleNamespace(build=build, check=check,
            assignment=lambda data, decision: {'x': decision['x']},
            decode=lambda data, values: {'x': values['x']})

    def tiny_case(self):
        return {'label': 'quadratic', 'data': {'bound': 3},
                'witness': {'decision': {'x': 2}, 'cost': '4'},
                'metadata': {'profile': 'test', 'origin': 'new_application_family',
                    'objective': {'sense': 'minimize', 'scale': '1'},
                    'nonlinearity': {'application_degree': 2}, 'replay': False}}

    def test_exact_integer_and_empty_expressions(self):
        self.assertEqual(core.num(-123), '(- 123)')
        self.assertEqual(core.summation([]), '0')
        self.assertEqual(core.disjunction([]), 'false')
        with self.assertRaises(ValueError):
            core.num(0.5)

    def test_base_and_opt_only_differ_by_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            case = core.write_case('test', self.tiny_case(), self.tiny_module(), Path(folder))
            p = Path(case['path'])
            base = (p/'base.smt2').read_text()
            self.assertEqual((p/'opt.smt2').read_text(), base+'(minimize cost)\n(check-sat)\n')
            self.assertNotIn('(= x 2)', base)
            self.assertEqual(json.loads((p/'witness.json').read_text())['cost'], '4')

    def test_outputs_reproduce_byte_for_byte_and_dont_overwrite(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ca = core.write_case('test', self.tiny_case(), self.tiny_module(), Path(a))
            cb = core.write_case('test', self.tiny_case(), self.tiny_module(), Path(b))
            for name in ('data.json', 'base.smt2', 'opt.smt2', 'metadata.json', 'witness.json'):
                self.assertEqual((Path(ca['path'])/name).read_bytes(), (Path(cb['path'])/name).read_bytes())
            (Path(ca['path'])/'data.json').write_text('changed')
            with self.assertRaises(FileExistsError):
                core.write_case('test', self.tiny_case(), self.tiny_module(), Path(a))

    def test_reject_incorrect_witness_before_publishing(self):
        bad = self.tiny_case()
        bad['witness']['cost'] = '5'
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                core.write_case('test', bad, self.tiny_module(), Path(folder))
            self.assertEqual(list(Path(folder).rglob('opt.smt2')), [])

    def test_theory_audit_rejects_real_or_division(self):
        for text in ('(declare-fun x () Real)', '(assert (= x (div y 2)))',
                     '(assert (forall ((x Int)) (= x x)))'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                core.audit_smt('(set-logic QF_NIA)\n'+text+'\n')

    def test_theory_audit_rejects_wrong_sort_and_arity(self):
        for expression in ('(+ x 1)', '(= x true)', '(ite x 1 2)', '(not true false)'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                core.audit_smt('(set-logic QF_NIA)\n(declare-fun x () Int)\n(assert '+expression+')\n')

    def test_float_claimed_witness_cost_is_not_truncated(self):
        bad = self.tiny_case()
        bad['witness']['cost'] = 4.75
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            core.write_case('test',bad,self.tiny_module(),Path(folder))

    def test_smt_values_keep_negative_and_large_integers(self):
        values = core.parse_values('sat\n((x (- 4)) (cost 9007199254740995))\n')
        self.assertEqual(values, {'x': -4, 'cost': 9007199254740995})
        with self.assertRaises(ValueError):
            core.parse_values('sat\n((x (/ 1 2)))\n')


if __name__ == '__main__':
    unittest.main()
