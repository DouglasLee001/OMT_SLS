"""Independent optimality checks after incumbent stagnation, on real SMT sources."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_omt_import import CONVERTER, NATIVE, smt


class OptimalityOptimizationTests(unittest.TestCase):
    def solve(self, source, *options, timeout="0.6", preprocess=True,
              remove_source=False):
        with tempfile.TemporaryDirectory(prefix="nia-optimality-") as directory:
            directory = Path(directory)
            source_path = directory / "input.smt2"
            native_path = directory / "input.nia"
            proof_path = directory / "optimality.smt2"
            source_path.write_text(source, encoding="utf-8")
            converted = subprocess.run(
                [sys.executable, str(CONVERTER), str(source_path), "-o", str(native_path),
                 "--preprocess=" + ("on" if preprocess else "off")],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(converted.returncode, 0, converted.stderr)
            if remove_source:
                source_path.unlink()
            completed = subprocess.run(
                [str(NATIVE), str(native_path), "--optimize", "--timeout=" + timeout,
                 "--model-output=" + str(directory / "model.tsv"), *options,
                 "--optimality-query-output=" + str(proof_path)],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
            events = [json.loads(line) for line in completed.stdout.splitlines()]
            query = proof_path.read_text() if proof_path.exists() else None
            return events, query

    def assert_proved(self, events, value):
        final = events[-1]
        self.assertEqual(final["event"], "final")
        self.assertEqual(final["status"], "sat")
        self.assertEqual(final["termination"], "optimality_proved", final)
        self.assertIs(final["is_optimal"], True)
        self.assertIs(final["is_unbounded"], False)
        self.assertEqual(final["objective_value"], str(value))
        self.assertEqual(final["optimality_proved_value"], str(value))
        self.assertEqual(final["optimality_status"], "proved")
        self.assertGreaterEqual(final["optimality_attempts"], 1)
        incumbent_time = [e["elapsed_seconds"] for e in events if e["event"] == "incumbent"][-1]
        self.assertGreaterEqual(final["time_to_optimality_seconds"], incumbent_time + 0.04)
        self.assertLessEqual(final["time_to_optimality_seconds"], final["elapsed_seconds"])
        self.assertLess(final["elapsed_seconds"], 0.6)

    def test_minimum_after_stall_and_historical_target_cannot_stop_it(self):
        events, query = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 10))",
            "(* (- x 5) (- x 5))"),
            "--prove-optimal", "--optimality-stall=0.05", "--optimality-timeout=0.2",
            "--target=26")
        self.assert_proved(events, 0)
        self.assertIsNotNone(query)
        self.assertIn("check-sat", query)
        self.assertNotIn("minimize", query)

    def test_maximum_retains_negative_objective_sense(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 4))",
            "(- (+ (* (- x 2) (- x 2)) 7))", "maximize"),
            "--prove-optimal", "--optimality-stall=0.05", "--optimality-timeout=0.2")
        self.assert_proved(events, -7)

    def test_preprocessing_can_remove_all_search_variables(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 7))", "x"),
            "--prove-optimal", "--optimality-stall=0.05", "--optimality-timeout=0.2")
        self.assert_proved(events, 7)

    def test_no_attempt_before_stagnation_deadline(self):
        events, query = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 4))", "x"),
            "--prove-optimal", "--optimality-stall=10", timeout="0.08", preprocess=False)
        final = events[-1]
        self.assertIs(final["is_optimal"], False)
        self.assertEqual(final["optimality_attempts"], 0)
        self.assertIsNone(query)

    def test_missing_source_does_not_claim_proof_or_discard_incumbent(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 4))", "x"),
            "--prove-optimal", "--optimality-stall=0.05", "--optimality-timeout=0.2",
            remove_source=True, preprocess=False, timeout="0.2")
        final = events[-1]
        self.assertEqual(final["status"], "sat")
        self.assertIs(final["is_optimal"], False)
        self.assertEqual(final["optimality_status"], "error")
        self.assertEqual(final["optimality_attempts"], 1)

    def test_unbounded_proof_still_takes_precedence(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (>= x 1000000))", "x", "maximize"),
            "--prove-optimal", "--optimality-stall=0.05", "--prove-unbounded")
        final = events[-1]
        self.assertIs(final["is_unbounded"], True)
        self.assertIs(final["is_optimal"], False)
        self.assertEqual(final["optimality_attempts"], 0)

    def test_invalid_optimality_options_rejected(self):
        with tempfile.TemporaryDirectory(prefix="nia-optimality-options-") as directory:
            path = Path(directory) / "input.nia"
            path.write_text("NIA_LS_OMT 1\n")
            for options in (("--prove-optimal", "--first-solution"),
                            ("--optimize", "--optimality-stall=1"),
                            ("--optimize", "--prove-optimal", "--optimality-stall=0"),
                            ("--optimize", "--prove-optimal", "--optimality-timeout=nan")):
                with self.subTest(options=options):
                    completed = subprocess.run([str(NATIVE), str(path), *options],
                                               text=True, capture_output=True, timeout=5)
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("optimal", completed.stderr)


if __name__ == "__main__":
    unittest.main()
