"""Native optimization integration tests, using Z3 only to evaluate models.

Run after building NIA_LS/src/nia_ls::

    python3 NIA_LS/tests/test_optimization.py -v

These small cases exercise improving search and the JSONL incumbent protocol.
No external solver is used to find or optimize any assignment.
Set NIA_LS_DATASET_SMOKE=1 to include the optional ten-second Hanoi case.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_first_solution as first_solution_tests
from test_omt_import import NATIVE, smt


class OptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        first_solution_tests.FirstSolutionTests.setUpClass.__func__(cls)

    # Reuse the independent native-formula evaluator without inheriting all
    # first-solution test methods (and running that suite a second time).
    convert = first_solution_tests.FirstSolutionTests.convert
    assert_feasible_model = first_solution_tests.FirstSolutionTests.assert_feasible_model

    def run_native(self, native, *options, expect_success=True, wall_timeout=10):
        with tempfile.TemporaryDirectory(prefix="nia-optimize-") as directory:
            path = Path(directory) / "input.nia"
            model_path = Path(directory) / "model.tsv"
            path.write_text(native, encoding="utf-8")
            completed = subprocess.run(
                [str(NATIVE), str(path), *options,
                 "--model-output=" + str(model_path)],
                text=True, capture_output=True, timeout=wall_timeout,
            )
            model = model_path.read_text(encoding="utf-8") if model_path.exists() else None
        if not expect_success:
            self.assertEqual(completed.returncode, 2, (completed.stdout, completed.stderr))
            self.assertTrue(completed.stderr.strip(), "invalid CLI must explain the rejection")
            self.assertIsNone(model)
            return completed, model
        self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
        try:
            events = [json.loads(line) for line in completed.stdout.splitlines()]
        except ValueError:
            self.fail("optimization must emit JSONL events: " + repr(completed.stdout))
        self.assertTrue(events, "optimization must emit a final event")
        self.assertEqual(events[-1].get("event"), "final", events)
        self.assertEqual(sum(event.get("event") == "final" for event in events), 1)
        elapsed = -1
        incumbents = []
        for event in events:
            self.assertIn(event["event"], ("incumbent", "final"))
            self.assertIsInstance(event["elapsed_seconds"], (int, float))
            self.assertGreaterEqual(event["elapsed_seconds"], 0)
            self.assertGreaterEqual(event["elapsed_seconds"], elapsed, events)
            elapsed = event["elapsed_seconds"]
            if event["event"] == "incumbent":
                self.assertEqual(event["status"], "sat")
                self.assertIn(event["direction"], ("min", "max"))
                self.assertIs(event["model_validated"], True)
                self.assertIsInstance(event["objective_value"], str)
                self.assertRegex(event["objective_value"], r"^-?\d+$")
                if incumbents:
                    previous = incumbents[-1]
                    self.assertEqual(event["direction"], previous["direction"])
                    self.assertTrue(
                        int(event["objective_value"]) < int(previous["objective_value"])
                        if event["direction"] == "min" else
                        int(event["objective_value"]) > int(previous["objective_value"]),
                        events,
                    )
                incumbents.append(event)
        final = events[-1]
        self.assertIn(final["status"], ("sat", "unknown", "unsat", "error"))
        self.assertIs(final["is_optimal"], False)
        if final["status"] == "sat":
            self.assertTrue(incumbents, "SAT must publish its validated incumbent")
            self.assertEqual(final["objective_value"], incumbents[-1]["objective_value"])
            self.assert_feasible_model(native, final, model)
        else:
            self.assertIsNone(final["objective_value"])
            self.assertIs(final["model_validated"], False)
            self.assertIsNone(model)
        return events, model

    def optimize(self, source, *, target=None, timeout="2", preprocess=False):
        native = self.convert(source, preprocess=preprocess)
        options = ["--optimize", "--timeout=" + timeout,
                   "--initialize=off", "--restart-repair=off"]
        if target is not None:
            options.append("--target=" + str(target))
        events, model = self.run_native(native, *options)
        return native, events, model

    def test_minimum_improves_initial_feasible_quadratic(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 10))",
            "(* (- x 5) (- x 5))",
        ), target=1)
        self.assertEqual(events[0]["objective_value"], "25")
        self.assertEqual(events[0]["direction"], "min")
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 0)

    def test_maximum_improves_and_preserves_negative_objective_sign(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 4))",
            "(- (+ (* (- x 2) (- x 2)) 7))", "maximize",
        ), target=-8)
        self.assertEqual(events[0]["objective_value"], "-11")
        self.assertEqual(events[0]["direction"], "max")
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), -7)

    def test_minimum_can_reach_an_arbitrary_nonzero_objective(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 10))",
            "(+ (* (- x 5) (- x 5)) 37)",
        ), target=38)
        self.assertEqual(events[0]["objective_value"], "62")
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 37)

    def test_already_beaten_target_stops_at_initial_incumbent(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 10))",
            "(* (- x 5) (- x 5))",
        ), target=26)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 25)

    def test_search_without_target_keeps_improving(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 10))",
            "(* (- x 5) (- x 5))",
        ), timeout="0.1")
        self.assertEqual(events[-1]["termination"], "timeout")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 0)
        self.assertGreaterEqual(len(events), 3)

    def test_large_target_is_inclusive_and_never_claims_infinity_or_optimality(self):
        for direction, goal in (("minimize", -10**10), ("maximize", 10**10)):
            with self.subTest(direction=direction):
                native = self.convert(smt(
                    "(declare-fun x () Int)",
                    f"(assert (<= {min(0, goal)} x)) (assert (<= x {max(0, goal)}))",
                    "x", direction,
                ), preprocess=False)
                events, model = self.run_native(
                    native, "--optimize", "--initialize=off", "--timeout=0.1",
                    "--large-target=10000000000",
                )
                final = events[-1]
                self.assertEqual(self.assert_feasible_model(native, final, model), goal)
                self.assertEqual(final["large_target_magnitude"], "10000000000")
                self.assertIs(final["large_target_reached"], True)
                self.assertEqual(final["large_target_status"], "reached")
                self.assertEqual(final["large_target_attempts"], 0)
                self.assertIs(final["is_optimal"], False)
                self.assertEqual(final["termination"], "timeout")

    def test_large_target_unreachable_does_not_change_bounded_answer(self):
        native = self.convert(smt(
            "(declare-fun x () Int)",
            "(assert (<= 0 x)) (assert (<= x 10))", "x",
        ), preprocess=False)
        events, model = self.run_native(
            native, "--optimize", "--initialize=off", "--timeout=0.1",
            "--large-target=10000000000",
        )
        final = events[-1]
        self.assertEqual(self.assert_feasible_model(native, final, model), 0)
        self.assertIs(final["large_target_reached"], False)
        self.assertEqual(final["large_target_status"], "waiting")
        self.assertIs(final["is_optimal"], False)

    def test_large_target_attempts_after_sustained_progress_and_preserves_best(self):
        declarations = " ".join(f"(declare-fun x{i} () Int)" for i in range(5))
        constraints = " ".join(
            f"(assert (<= 0 x{i})) (assert (<= x{i} 100))" for i in range(5))
        objective = "(+ " + " ".join(
            f"(* (- x{i} {10*(i+1)}) (- x{i} {10*(i+1)}))" for i in range(5)) + ")"
        native = self.convert(smt(declarations, constraints, objective), preprocess=False)
        events, model = self.run_native(
            native, "--optimize", "--initialize=off", "--timeout=0.5",
            "--large-target=10000000000",
        )
        final = events[-1]
        self.assertEqual(final["large_target_attempts"], 1)
        self.assertGreaterEqual(final["large_target_slices"], 1)
        self.assertGreater(final["large_target_seconds"], 0)
        self.assertIs(final["large_target_reached"], False)
        self.assertEqual(self.assert_feasible_model(native, final, model), 0)
        self.assertIs(final["is_optimal"], False)

    def test_large_target_invalid_cli(self):
        native = self.convert(smt("(declare-fun x () Int)", "", "x"))
        for value in ("0", "-1", "1e10", "inf", "", str(2**127), str(2**127+1)):
            with self.subTest(value=value):
                self.run_native(native, "--optimize", "--timeout=0.01", "--large-target=" + value,
                                expect_success=False)
        self.run_native(native, "--import-only", "--large-target=10000000000",
                        expect_success=False)

    def test_large_target_accepts_signed128_max_and_preserves_both_signs(self):
        magnitude=2**127-1
        for direction,objective in (("minimize","x"),("maximize","x")):
            with self.subTest(direction=direction):
                native=self.convert(smt("(declare-fun x () Int)","",objective,direction),
                                    preprocess=False)
                events,model=self.run_native(
                    native,"--optimize","--initialize=off","--timeout=0.2",
                    "--large-target="+str(magnitude),
                )
                value=self.assert_feasible_model(native,events[-1],model)
                self.assertLessEqual(value,-magnitude) if direction=="minimize" else self.assertGreaterEqual(value,magnitude)
                self.assertEqual(events[-1]["large_target_magnitude"],str(magnitude))
                self.assertIs(events[-1]["large_target_reached"],True)
                self.assertIs(events[-1]["is_optimal"],False)

    def test_coupled_equality_requires_feasible_two_variable_improvement(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int) (declare-fun y () Int)",
            "(assert (= (+ x y) 10)) "
            "(assert (<= 0 x)) (assert (<= x 10)) "
            "(assert (<= 0 y)) (assert (<= y 10))",
            "(+ (* x x) (* y y))",
        ), target=51)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 50)

    def test_conditional_equality_scales_coupled_improvements_to_large_target(self):
        # This equality is guarded in a clause rather than a unit literal.
        # Improving x requires y to follow it in the same direction; fixed
        # unit-equality projection and opposite-sign pair exchanges cannot
        # scale that feasible direction to a large historical target.
        target = -(1 << 80)
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int) (declare-fun y () Int) (declare-fun b () Bool)",
            "(assert b) (assert (or (not b) (= x y)))",
            "x",
        ), target=target)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertLess(self.assert_feasible_model(native, events[-1], model), target)
        self.assertIn("b\ttrue", model.splitlines())

    @unittest.skipUnless(os.environ.get("NIA_LS_DATASET_SMOKE") == "1", "optional dataset smoke")
    def test_hanoi_guarded_equalities_keep_valid_incumbents(self):
        source = Path("/data/shared/lbh/OMT/benchmarks") / (
            "QF_NIA/20170427-VeryMax/CInteger/"
            "Ton_Chanh_15__Hanoi_3vars_false-termination.c__p27016_terminationG_0@0.smt2"
        )
        if not source.is_file():
            self.skipTest("canonical Hanoi benchmark is unavailable")
        # Use the standard frontend and initialization, matching a real batch
        # case. Paired runs preserved identical first assignments but varied
        # after wall-clock repair boundaries: this short dataset check covers
        # incumbent soundness. The strict large-target scaling regression is
        # exercised separately by the deterministic synthetic guarded case.
        native = self.convert(source.read_text(encoding="utf-8"))
        events, model = self.run_native(
            native, "--optimize", "--timeout=10", "--target=-162711111",
            wall_timeout=15,
        )
        value = self.assert_feasible_model(native, events[-1], model)
        for event in events:
            if event["event"] == "incumbent":
                self.assertLessEqual(event["elapsed_seconds"], 10)
        if events[-1]["termination"] == "target_beaten":
            self.assertLess(value, -162711111)
        else:
            self.assertEqual(events[-1]["termination"], "timeout")

    def test_finite_bounds_above_int64_allow_improving_int128_assignments(self):
        lower = 1 << 80
        center = lower + 3
        optimum = -(center * center)
        # Drop the additive center^2 from (x - center)^2: that constant
        # exceeds the native coefficient format, but exact objective values
        # and targets may exceed signed-128. The minimizing x is unchanged.
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int)",
            f"(assert (<= {lower} x)) (assert (<= x {lower + 6}))",
            f"(- (* x x) (* {2 * center} x))",
        ), target=optimum + 1)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), optimum)
        self.assertIn(f"x\t{center}", model.splitlines())

    def test_rejected_pair_move_preserves_other_candidate_evaluations(self):
        # All four variables lie in [0, 2] and sum to 4. The objective is
        # 5*(x+1)^2 + 4*(y-4)^2 + 3*z^2 + 4*(w-1)^2, whose minimum is 24.
        # An unreachable target keeps search in its pair-exchange phase.
        # Rejecting a pair whose second component violates a bound used to
        # leave its first component changed, corrupting subsequent deltas.
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 30\nDIRECTION min\n"
            "VARIABLES 4\n0 I 78\n1 I 79\n2 I 7a\n3 I 77\n"
            "LITERALS 9\n"
            "1 L 0 1\n-1 1 0 1\n2 L -2 1\n1 1 0 1\n"
            "3 L 0 1\n-1 1 1 1\n4 L -2 1\n1 1 1 1\n"
            "5 L 0 1\n-1 1 2 1\n6 L -2 1\n1 1 2 1\n"
            "7 L 0 1\n-1 1 3 1\n8 L -2 1\n1 1 3 1\n"
            "9 E -4 4\n1 1 0 1\n1 1 1 1\n1 1 2 1\n1 1 3 1\n"
            "CLAUSES 9\n1 1\n1 2\n1 3\n1 4\n1 5\n1 6\n1 7\n1 8\n1 9\n"
            "OBJECTIVE 73 8\n5 1 0 2\n10 1 0 1\n"
            "4 1 1 2\n-32 1 1 1\n3 1 2 2\n0 1 2 1\n"
            "4 1 3 2\n-8 1 3 1\nEND\n"
        )
        events, model = self.run_native(
            native, "--optimize", "--timeout=0.1", "--target=-1",
            "--initialize=off", "--restart-repair=off",
        )
        self.assertEqual(events[-1]["termination"], "timeout")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 24)

    def test_objective_alias_boolean_and_ite_remain_feasible(self):
        native, events, model = self.optimize(smt(
            "(declare-fun x () Int) (declare-fun cost () Int) (declare-fun b () Bool)",
            "(assert (<= 0 x)) (assert (<= x 4)) (assert b) "
            "(assert (= cost (ite b (+ (* (- x 3) (- x 3)) 7) 100)))",
            "cost",
        ), target=8)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 7)
        self.assertIn("b\ttrue", model)

    def test_equal_target_times_out_and_preserves_validated_incumbent(self):
        for direction, objective, target in (
            ("minimize", "(* x x)", 9),
            ("maximize", "(- (* x x))", -9),
        ):
            with self.subTest(direction=direction):
                native, events, model = self.optimize(smt(
                    "(declare-fun x () Int)", "(assert (= x 3))",
                    objective, direction,
                ), target=target, timeout="0.03")
                self.assertEqual(events[-1]["termination"], "timeout")
                self.assertEqual(self.assert_feasible_model(native, events[-1], model), target)

    def test_constant_objective_has_empty_validated_model(self):
        value = 12345678901234567890
        native, events, model = self.optimize(smt("", "", str(value)), target=value + 1)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), value)
        self.assertEqual(model, "")

    def test_objective_only_free_variable_can_improve(self):
        native, events, model = self.optimize(smt(
            "(declare-fun free () Int)", "", "free",
        ), target=-2)
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertLess(self.assert_feasible_model(native, events[-1], model), -2)
        self.assertIn("free\t", model)

    def test_objective_and_target_larger_than_int128_are_exact(self):
        coefficient = str(1 << 126)
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 78\nDIRECTION max\n"
            "VARIABLES 1\n0 I 78\n"
            "LITERALS 2\n1 L 0 1\n-1 1 0 1\n"
            "2 L -2 1\n1 1 0 1\nCLAUSES 2\n1 1\n1 2\n"
            "OBJECTIVE 0 1\n" + coefficient + " 1 0 2\nEND\n"
        )
        events, model = self.run_native(
            native, "--optimize", "--timeout=2", "--target=" + str((1 << 128) - 1),
            "--initialize=off", "--restart-repair=off",
        )
        self.assertEqual(events[-1]["termination"], "target_beaten")
        self.assertEqual(self.assert_feasible_model(native, events[-1], model), 1 << 128)

    def test_preprocessing_unsat_emits_final_without_incumbent(self):
        native = self.convert(smt(
            "(declare-fun x () Int)",
            "(assert (= x 1)) (assert (= x 2))", "x",
        ))
        events, model = self.run_native(native, "--optimize", "--timeout=0.03")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[-1]["status"], "unsat")
        self.assertEqual(events[-1]["termination"], "preprocessing_unsat")
        self.assertIsNone(model)

    def test_no_feasible_incumbent_times_out_unknown(self):
        native = (
            "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
            "ORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
            "VARIABLES 1\n0 I 78\n"
            "LITERALS 1\n1 E 1 1\n1 1 0 2\n"
            "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n"
        )
        events, model = self.run_native(
            native, "--optimize", "--timeout=0.03",
            "--initialize=off", "--restart-repair=off",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertEqual(events[-1]["termination"], "timeout")
        self.assertIsNone(model)

    def test_incompatible_modes_and_target_without_optimization_are_rejected(self):
        native = self.convert(smt("", "", "7"))
        for options in (
            ("--optimize", "--first-solution"),
            ("--optimize", "--import-only"),
            ("--first-solution", "--target=7"),
            ("--target=7",),
        ):
            with self.subTest(options=options):
                self.run_native(native, *options, expect_success=False)

    def test_noninteger_targets_are_rejected(self):
        native = self.convert(smt("", "", "7"))
        for target in ("", "1.5", "nan", "1junk"):
            with self.subTest(target=target):
                completed, _ = self.run_native(
                    native, "--optimize", "--target=" + target, expect_success=False,
                )
                self.assertIn("target", completed.stderr.lower())


if __name__ == "__main__":
    unittest.main()
