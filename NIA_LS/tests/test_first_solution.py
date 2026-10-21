"""End-to-end checks for native NIA_LS first-feasible-model mode.

Run after building NIA_LS/src/nia_ls::

    python3 NIA_LS/tests/test_first_solution.py -v

Only small temporary instances are solved.  The official Z3 SDK is used to
evaluate exported native formulas at the returned model, never to find a model.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_omt_import import CONVERTER, NATIVE, decode_native, smt


class FirstSolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import z3
        cls.z3 = z3
        if not CONVERTER.is_file() or not NATIVE.is_file():
            raise RuntimeError("build NIA_LS/src/nia_ls before running these tests")

    def convert(self, source, *, preprocess=True):
        with tempfile.TemporaryDirectory(prefix="nia-first-convert-") as directory:
            source_path = Path(directory) / "input.smt2"
            source_path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(CONVERTER), str(source_path),
                 "--preprocess=" + ("on" if preprocess else "off")],
                text=True, capture_output=True, timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def run_native(self, native, *options, expect_success=True):
        with tempfile.TemporaryDirectory(prefix="nia-first-search-") as directory:
            path = Path(directory) / "input.nia"
            model_path = Path(directory) / "model.tsv"
            path.write_text(native, encoding="utf-8")
            completed = subprocess.run(
                [str(NATIVE), str(path), *options,
                 "--model-output=" + str(model_path)],
                text=True, capture_output=True, timeout=10,
            )
            model = model_path.read_text(encoding="utf-8") if model_path.exists() else None
        if expect_success is False:
            self.assertEqual(completed.returncode, 2, (completed.stdout, completed.stderr))
            return completed, model
        if expect_success is None:
            self.assertIn(completed.returncode, (0, 2), (completed.stdout, completed.stderr))
        else:
            self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
        try:
            result = json.loads(completed.stdout)
        except ValueError:
            self.fail("first-solution mode must emit one JSON object: " + repr(completed.stdout))
        self.assertIn(result["status"], ("sat", "unknown", "unsat", "error"))
        self.assertIs(result["is_optimal"], False)
        self.assertIsInstance(result["search_seconds"], (int, float))
        self.assertGreaterEqual(result["search_seconds"], 0)
        return result, model

    def assert_feasible_model(self, native, result, model):
        self.assertEqual(result["status"], "sat", result)
        self.assertIs(result["model_validated"], True)
        self.assertIsNotNone(model, "SAT must include the requested model file")
        values = {}
        for line in model.splitlines():
            name, value = line.rsplit("\t", 1)
            self.assertNotIn(name, values, "duplicate returned variable")
            values[name] = value
        decoded = decode_native(native, self.z3)
        substitutions = []
        for index, name in decoded["names"].items():
            self.assertIn(name, values, "missing native variable " + name)
            value = values[name]
            if value in ("true", "false"):
                variable = self.z3.Bool("native_b_" + str(index))
                replacement = self.z3.BoolVal(value == "true")
            else:
                variable = self.z3.Int("native_i_" + str(index))
                replacement = self.z3.IntVal(int(value))
            substitutions.append((variable, replacement))
        for constraint in decoded["constraints"]:
            evaluated = self.z3.simplify(self.z3.substitute(constraint, *substitutions))
            self.assertTrue(self.z3.is_true(evaluated), (constraint, model, result))
        objective = self.z3.simplify(self.z3.substitute(decoded["objective"], *substitutions))
        self.assertTrue(self.z3.is_int_value(objective), str(objective))
        self.assertEqual(result["objective_value"], str(objective.as_long()))
        return objective.as_long()

    def solve(self, source, *, preprocess=True, options=()):
        native = self.convert(source, preprocess=preprocess)
        result, model = self.run_native(native, "--first-solution", "--timeout=2", *options)
        self.assert_feasible_model(native, result, model)
        return native, result, model

    def test_stops_at_initial_feasible_assignment_before_maximization(self):
        source = smt(
            "(declare-fun x () Int)",
            "(assert (<= (- 2) x)) (assert (<= x 2))",
            "(+ (* x x) 10)", "maximize",
        )
        native, result, model = self.solve(source, options=("--initialize=off", "--restart-repair=off"))
        # Zero already satisfies the constraints.  The maximum would be 14;
        # first-solution mode must return immediately rather than optimize it.
        self.assertEqual(self.assert_feasible_model(native, result, model), 10)

    def test_min_objective_alias_with_search_and_boolean_clause(self):
        self.solve(smt(
            "(declare-fun x () Int) (declare-fun cost () Int) (declare-fun b () Bool)",
            "(assert (<= 1 x)) (assert (<= x 3)) (assert b) "
            "(assert (= cost (+ (* x x) 7)))", "cost",
        ), preprocess=False)

    def test_native_local_search_moves_to_nonzero_integer_and_true_boolean(self):
        native, result, model = self.solve(smt(
            "(declare-fun x () Int) (declare-fun b () Bool)",
            "(assert (<= 1 x)) (assert (<= x 3)) (assert b)",
            "(+ (* x x) 7)",
        ), preprocess=False, options=("--initialize=off", "--restart-repair=off"))
        self.assertEqual(result["stage"], "local_search")
        self.assertGreater(result["steps"], 1, "initial zero/false assignment is infeasible")
        self.assertIn("b\ttrue", model)
        self.assert_feasible_model(native, result, model)

    def test_maximum_reports_original_objective_sign(self):
        _, result, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 2))",
            "(- (+ (* x x) 7))", "maximize",
        ))
        self.assertEqual(result["objective_value"], "-11")

    def test_objective_only_free_variable_is_in_model(self):
        native, result, model = self.solve(smt("(declare-fun target () Int)", "", "target"))
        self.assertIn("target\t", model)
        self.assertEqual(self.assert_feasible_model(native, result, model), 0)

    def test_no_variables_no_clauses_constant_objective(self):
        _, result, model = self.solve(smt("", "", "12345678901234567890"))
        self.assertEqual(result["objective_value"], "12345678901234567890")
        self.assertEqual(model, "")

    def test_preprocessing_unsat_does_not_search_or_emit_model(self):
        native = self.convert(smt(
            "(declare-fun x () Int)",
            "(assert (= x 1)) (assert (= x 2))", "x",
        ))
        result, model = self.run_native(native, "--first-solution", "--timeout=2")
        self.assertEqual(result["status"], "unsat")
        self.assertIsNone(result["objective_value"])
        self.assertIsNone(model)

    def test_omt_without_mode_still_does_not_start_search(self):
        native = self.convert(smt("(declare-fun x () Int)", "", "x"))
        completed, model = self.run_native(native, expect_success=False)
        self.assertIn("first-solution", completed.stderr)
        self.assertIsNone(model)

    def test_import_only_remains_non_searching(self):
        native = self.convert(smt("(declare-fun x () Int)", "", "x"))
        with tempfile.TemporaryDirectory(prefix="nia-first-import-") as directory:
            path = Path(directory) / "input.nia"
            path.write_text(native, encoding="utf-8")
            completed = subprocess.run(
                [str(NATIVE), str(path), "--import-only"],
                text=True, capture_output=True, timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIs(json.loads(completed.stdout)["search_started"], False)

    def test_first_solution_and_import_only_are_mutually_exclusive(self):
        native = self.convert(smt("", "", "7"))
        completed, model = self.run_native(
            native, "--first-solution", "--import-only", expect_success=False,
        )
        self.assertTrue(completed.stderr.strip())
        self.assertIsNone(model)

    def test_invalid_timeout_is_rejected(self):
        native = self.convert(smt("", "", "7"))
        for timeout in ("", "0", "-1", "nan", "inf", "1junk"):
            with self.subTest(timeout=timeout):
                completed, model = self.run_native(
                    native, "--first-solution", "--timeout=" + timeout, expect_success=False,
                )
                self.assertIn("timeout", completed.stderr.lower())
                self.assertIsNone(model)

    def test_int128_overflow_cannot_turn_contradiction_into_sat(self):
        # At x=2, 2^126*x*x is 2^128, not zero.  An unchecked int128
        # feasibility check can wrap to zero and falsely accept this model.
        coefficient = str(1 << 126)
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 30\nDIRECTION min\n"
            "VARIABLES 1\n0 I 78\n"
            "LITERALS 2\n1 E -2 1\n1 1 0 1\n"
            "2 E 0 1\n" + coefficient + " 1 0 2\n"
            "CLAUSES 2\n1 1\n1 2\nOBJECTIVE 0 0\nEND\n"
        )
        result, model = self.run_native(
            native, "--first-solution", "--timeout=0.1", expect_success=None,
        )
        self.assertNotEqual(result["status"], "sat", (result, model))
        self.assertIsNone(result["objective_value"])
        self.assertIsNone(model)

    def test_local_search_timeout_is_unknown_without_false_unsat_proof(self):
        # x*x + 1 = 0 has no integer solution, but local search does not
        # establish that proof.  Its exhausted budget must remain unknown.
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
            "VARIABLES 1\n0 I 78\n"
            "LITERALS 1\n1 E 1 1\n1 1 0 2\n"
            "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n"
        )
        result, model = self.run_native(
            native, "--first-solution", "--timeout=0.01",
            "--initialize=off", "--restart-repair=off",
        )
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["termination"], "timeout")
        self.assertFalse(result["model_validated"])
        self.assertIsNone(result["objective_value"])
        self.assertIsNone(model)

    def test_objective_value_larger_than_int128_is_exact(self):
        coefficient = str(1 << 126)
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 78\nDIRECTION max\n"
            "VARIABLES 1\n0 I 78\n"
            "LITERALS 1\n1 E -2 1\n1 1 0 1\n"
            "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n"
            + coefficient + " 1 0 2\nEND\n"
        )
        result, model = self.run_native(native, "--first-solution", "--timeout=2")
        self.assert_feasible_model(native, result, model)
        self.assertEqual(result["objective_value"], str(1 << 128))


if __name__ == "__main__":
    unittest.main()
