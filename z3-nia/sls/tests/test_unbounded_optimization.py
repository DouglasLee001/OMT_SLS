"""End-to-end tests of optional, certificate-backed LIA unboundedness proofs."""
import sys
from pathlib import Path
import unittest

import test_optimization as optimization_tests
from test_omt_import import smt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class UnboundedOptimizationTests(unittest.TestCase):
    setUpClass = classmethod(optimization_tests.OptimizationTests.setUpClass.__func__)
    convert = optimization_tests.OptimizationTests.convert
    assert_feasible_model = optimization_tests.OptimizationTests.assert_feasible_model
    run_native = optimization_tests.OptimizationTests.run_native

    def solve(self, source, *options, timeout="3"):
        native = self.convert(source, preprocess=False)
        events, model = self.run_native(native, "--optimize", "--timeout=" + timeout,
                                        *options)
        return native, events[-1], model

    def assert_proof(self, native, final, model):
        from script.verify_optimize import parse_native, verify_unbounded_certificate
        self.assertEqual(final["status"], "sat")
        self.assertEqual(final["termination"], "unbounded_proved", final)
        self.assertIs(final["is_unbounded"], True)
        self.assertIs(final["is_optimal"], False)
        self.assertEqual(final["unbounded_status"], "proved")
        self.assertEqual(final["unbounded_attempts"], 1)
        self.assertGreater(final["unbounded_queries"], 0)
        self.assertGreaterEqual(final["time_to_unbounded_seconds"],
                                final["time_to_best_seconds"])
        self.assertLessEqual(final["time_to_unbounded_seconds"], final["elapsed_seconds"])
        self.assert_feasible_model(native, final, model)
        verify_unbounded_certificate(parse_native(native), final["unbounded_certificate"])

    def test_maximum_proves_unbounded_after_large_bilinear_model(self):
        native, final, model = self.solve(smt(
            "(declare-fun x () Int) (declare-fun y () Int)",
            "(assert (= x 2)) (assert (>= y 1000000)) "
            "(assert (>= (* x y) 1))", "y", "maximize"), "--prove-unbounded")
        self.assert_proof(native, final, model)
        self.assertGreaterEqual(int(final["objective_value"]), 1000000)

    def test_minimum_uses_negative_threshold_and_direction(self):
        native, final, model = self.solve(smt(
            "(declare-fun x () Int) (declare-fun y () Int)",
            "(assert (= x 2)) (assert (<= y (- 1000000))) "
            "(assert (<= (* x y) (- 1)))", "y"), "--prove-unbounded")
        self.assert_proof(native, final, model)
        self.assertLessEqual(int(final["unbounded_certificate"]["objective_slope"]), -1)

    def test_negative_boolean_and_disequality_are_preserved(self):
        native, final, model = self.solve(smt(
            "(declare-fun b () Bool) (declare-fun x () Int)",
            "(assert (not b)) (assert (or b (>= x 1000000))) "
            "(assert (not (= x 0)))", "x", "maximize"), "--prove-unbounded")
        self.assert_proof(native, final, model)

    def test_large_finite_optimum_is_not_unbounded(self):
        _, final, _ = self.solve(smt(
            "(declare-fun x () Int)",
            "(assert (>= x 1000000)) (assert (<= x 1000003))",
            "x", "maximize"), "--prove-unbounded", timeout="0.5")
        self.assertIs(final["is_unbounded"], False)
        self.assertNotEqual(final["termination"], "unbounded_proved")
        self.assertIsNone(final["unbounded_certificate"])
        self.assertEqual(final["unbounded_attempts"], 1)

    def test_no_attempt_before_threshold(self):
        _, final, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 999999))",
            "x", "maximize"), "--prove-unbounded", timeout="0.1")
        self.assertIs(final["is_unbounded"], False)
        self.assertEqual(final["unbounded_attempts"], 0)
        self.assertEqual(final["unbounded_queries"], 0)
        self.assertEqual(final["unbounded_status"], "waiting")

    def test_disabled_by_default(self):
        _, final, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (>= x 1000000))",
            "x", "maximize"), timeout="0.1")
        self.assertIs(final["is_unbounded"], False)
        self.assertEqual(final["unbounded_status"], "disabled")
        self.assertIsNone(final["unbounded_threshold"])
        self.assertEqual(final["unbounded_attempts"], 0)

    def test_adjustable_trigger_and_historical_target_does_not_preempt_proof(self):
        native, final, model = self.solve(smt(
            "(declare-fun x () Int)", "(assert (>= x 10))",
            "x", "maximize"), "--prove-unbounded", "--unbounded-threshold=10",
            "--target=0")
        self.assert_proof(native, final, model)
        self.assertEqual(final["unbounded_threshold"], "10")

    def test_invalid_thresholds_and_mode_are_rejected(self):
        native = self.convert(smt("(declare-fun x () Int)", "", "x"), preprocess=False)
        for value in ("0", "-1", "1.0", str(2**127)):
            with self.subTest(value=value):
                self.run_native(native, "--optimize", "--prove-unbounded",
                                "--unbounded-threshold=" + value, expect_success=False)
        self.run_native(native, "--first-solution", "--prove-unbounded", expect_success=False)
        self.run_native(native, "--optimize", "--unbounded-threshold=10", expect_success=False)


if __name__ == "__main__":
    unittest.main()
