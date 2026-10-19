import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nia_bench import core, solver
import test_core

Z3 = core.REPO/'competitor/z3-5.1.0/build/z3'


@unittest.skipUnless(Z3.is_file(), 'Local Z3 required for solver adapter tests')
class SolverTests(unittest.TestCase):
    def test_z3_internal_timeout_error_is_kept_as_timeout(self):
        stdout = '(error "line 30 column 10: push canceled")\n((cost 42))\n'
        self.assertEqual(solver.status_of(stdout,1),'timeout')
        self.assertEqual(solver.status_of('unsupported\n'+stdout,1),'error')

    def test_unsat_with_no_model_is_not_a_frontend_error(self):
        fixture = test_core.CoreTests()
        module = fixture.tiny_module()
        original_build = module.build
        def impossible(data):
            model = original_build(data)
            model.assertion('false')
            return model
        module.build = impossible
        case = fixture.tiny_case()
        case['witness'] = None
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('test',case,module,Path(folder))
            result = solver.probe_case(Path(record['path']),Z3,2,module=module)
            self.assertEqual(result['optimization']['status'],'unsat')
            self.assertEqual(result['optimality']['status'],'unsat_solver_checked')

    def test_frontend_unsupported_cannot_be_mistaken_for_unsat(self):
        with tempfile.TemporaryDirectory() as folder:
            result = solver.query('(foobar)\n(assert false)\n(check-sat)\n', Z3, 2, Path(folder), 'unsupported')
            self.assertEqual(result['status'], 'error')

    def test_actual_z3_checked_model_and_strict_improvement(self):
        fixture = test_core.CoreTests()
        case = fixture.tiny_case()
        case['metadata']['expected_optimum'] = '0'
        case['witness'] = {'decision': {'x': 0}, 'cost': '0'}
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('test', case, fixture.tiny_module(), Path(folder))
            result = solver.probe_case(Path(record['path']), Z3, 2, module=fixture.tiny_module())
            self.assertEqual(result['pinned_witness']['status'], 'sat')
            self.assertEqual(result['optimization']['cost'], '0')
            self.assertEqual(result['optimality']['status'], 'optimal_solver_checked')
            self.assertEqual(result['strict_improvement']['status'], 'unsat')
            self.assertTrue((Path(record['path'])/'z3/optimization.stdout.txt').is_file())

    def test_sat_feasible_model_does_not_automatically_claim_optimal(self):
        fixture = test_core.CoreTests()
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('test', fixture.tiny_case(), fixture.tiny_module(), Path(folder))
            result = solver.probe_case(Path(record['path']), Z3, 2, module=fixture.tiny_module())
            self.assertEqual(result['optimization']['application_check'], 'passed')
            self.assertEqual(result['optimality']['status'], 'feasible_only')

    def test_impossible_claim_is_refuted_by_strict_query(self):
        fixture = test_core.CoreTests()
        case = fixture.tiny_case()
        case['metadata']['expected_optimum'] = '4'
        with tempfile.TemporaryDirectory() as folder:
            record = core.write_case('test', case, fixture.tiny_module(), Path(folder))
            result = solver.probe_case(Path(record['path']), Z3, 2, module=fixture.tiny_module())
            self.assertEqual(result['strict_improvement']['status'], 'sat')
            self.assertNotEqual(result['optimality']['status'], 'optimal_solver_checked')


if __name__ == '__main__':
    unittest.main()
