"""Independent optimality checks after incumbent stagnation, on real SMT sources."""
import json
import re
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
                 *(["--optimality-query-output=" + str(proof_path)]
                   if "--prove-optimal" in options else [])],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
            self.last_native = native_path.read_text()
            model = directory / "model.tsv"
            self.last_model = model.read_text() if model.exists() else None
            self.last_model_inode = model.stat().st_ino if model.exists() else None
            previous = Path(str(model) + ".prev")
            self.last_previous_model_inode = previous.stat().st_ino if previous.exists() else None
            self.last_stderr = completed.stderr
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

    def test_windowed_schedule_keeps_independent_optimality_proof(self):
        events, query = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 10))",
            "(* (- x 5) (- x 5))"),
            "--prove-optimal", "--optimality-stall=0.05", "--optimality-timeout=0.2",
            "--optimality-search-window=0.08")
        self.assert_proved(events, 0)
        self.assertEqual(events[-1]["optimality_schedule"], "windowed_v1")
        self.assertEqual(events[-1]["optimality_search_window_seconds"], .08)
        self.assertNotIn("minimize", query)

    def test_source_z3_can_supply_first_model_without_claiming_optimality(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= (* x x) 49))", "x"),
            "--prove-optimal", "--initialize=off", "--optimality-stall=10",
            "--initial-z3-after=0.000000001", "--initial-z3-timeout=0.5",
            preprocess=False, timeout="0.8")
        first = next(e for e in events if e["event"] == "incumbent")
        self.assertEqual(first["origin"], "z3_initial")
        self.assertIn(int(first["objective_value"]), (-7, 7))
        final = events[-1]
        self.assertEqual(final["initial_z3_attempts"], 1)
        self.assertTrue(final["initial_z3_model_accepted"])
        self.assertFalse(final["is_optimal"])
        self.assertEqual(final["optimality_attempts"], 0)

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
        self.assertGreaterEqual(final["optimality_attempts"], 2)

    def test_stagnation_retries_with_doubled_budget(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 4))", "x"),
            "--prove-optimal", "--optimality-stall=0.025", "--optimality-timeout=0.01",
            remove_source=True, preprocess=False, timeout="0.25")
        final = events[-1]
        budgets = [float(v) for v in re.findall(r"omt_optimality_probe best=-?\d+ seconds=([0-9.eE+-]+)", self.last_stderr)]
        self.assertGreaterEqual(len(budgets), 3, self.last_stderr)
        self.assertEqual(budgets[:3], [0.01, 0.02, 0.04])
        self.assertEqual(final["optimality_schedule"], "adaptive_v1")
        self.assertIs(final["is_optimal"], False)

    def test_sat_query_model_updates_native_incumbent_and_saved_model(self):
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from script.verify_optimize import parse_native, evaluate_model
        for direction, objective, optimum in (
                ("minimize", "(* (- x 7) (- x 7))", 0),
                ("maximize", "(- (+ (* (- x 7) (- x 7)) 3))", -3)):
            with self.subTest(direction=direction):
                events, _ = self.solve(smt(
                    "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 20))",
                    objective, direction), "--prove-optimal", "--initialize=off",
                    "--optimality-stall=0.000000001", "--optimality-timeout=0.4",
                    preprocess=False, timeout="2")
                final = events[-1]
                self.assertGreater(final["optimality_sat_models_accepted"], 0, (events, self.last_stderr))
                sat_models = [e for e in events if e.get("origin") == "z3"]
                self.assertTrue(sat_models, events)
                self.assertEqual(len(sat_models), final["optimality_sat_models_accepted"])
                self.assertEqual(int(final["objective_value"]), optimum)
                self.assertEqual(evaluate_model(parse_native(self.last_native), self.last_model), optimum)

    def test_published_models_keep_current_and_previous_event_identity(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 20))",
            "(* (- x 7) (- x 7))"), "--prove-optimal", "--initialize=off",
            "--optimality-stall=0.000000001", "--optimality-timeout=0.4",
            preprocess=False, timeout="2")
        incumbents = [event for event in events if event.get("event") == "incumbent"]
        self.assertGreaterEqual(len(incumbents), 2, events)
        self.assertTrue(all(type(event.get("model_inode")) is int and event["model_inode"] > 0
                            for event in incumbents), events)
        self.assertEqual(self.last_model_inode, incumbents[-1]["model_inode"])
        self.assertEqual(self.last_previous_model_inode, incumbents[-2]["model_inode"])

    def test_sat_model_survives_preprocessing_and_boolean_ite(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from script.verify_optimize import parse_native, evaluate_model
        events, _ = self.solve(smt(
            "(declare-fun x () Int) (declare-fun y () Int) (declare-fun b () Bool)",
            "(assert (<= 0 x)) (assert (<= x 10)) "
            "(assert (or b (> x 1))) "
            "(assert (= y (ite b (* x x) (+ x 3))))",
            "(* (- y 4) (- y 4))"), "--prove-optimal", "--initialize=off",
            "--optimality-stall=0.000000001", "--optimality-timeout=0.5",
            preprocess=True, timeout="3")
        final = events[-1]
        self.assertGreater(final["optimality_sat_models_accepted"], 0, (events, self.last_stderr))
        self.assertEqual(int(final["objective_value"]), 0)
        self.assertEqual(evaluate_model(parse_native(self.last_native), self.last_model), 0)

    def test_unbounded_proof_still_takes_precedence(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (>= x 1000000))", "x", "maximize"),
            "--prove-optimal", "--optimality-stall=0.05", "--prove-unbounded")
        final = events[-1]
        self.assertIs(final["is_unbounded"], True)
        self.assertIs(final["is_optimal"], False)
        self.assertEqual(final["optimality_attempts"], 0)

    def test_zero_search_window_keeps_legacy_mode_without_proof_opt_in(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 7))", "x"),
            "--optimality-search-window=0", timeout="0.03")
        self.assertEqual(events[-1]["objective_value"], "7")
        self.assertIs(events[-1]["prove_optimal"], False)

    def test_default_initial_z3_options_keep_legacy_mode_without_proof_opt_in(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 7))", "x"),
            "--initial-z3-after=0", "--initial-z3-timeout=120", timeout="0.03")
        self.assertEqual(events[-1]["objective_value"], "7")
        self.assertIs(events[-1]["prove_optimal"], False)

    def test_resume_opt_in_is_reported_before_any_finite_proof_attempt(self):
        events, _ = self.solve(smt(
            "(declare-fun x () Int)", "(assert (= x 7))", "x"),
            "--prove-optimal", "--optimality-resume", "--optimality-stall=10", timeout="0.03")
        final = events[-1]
        self.assertIs(final["optimality_resume"], True)
        self.assertEqual(final["optimality_schedule"], "persistent_v1")
        self.assertEqual(final["optimality_attempts"], 0)
        self.assertIs(final["is_optimal"], False)

    def test_initial_z3_invalid_timings_and_unapproved_modes_rejected(self):
        with tempfile.TemporaryDirectory(prefix="nia-initial-z3-options-") as directory:
            path = Path(directory) / "input.nia"
            path.write_text("NIA_LS_OMT 1\n")
            for option in ("--initial-z3-after=-1", "--initial-z3-after=nan",
                           "--initial-z3-timeout=0", "--initial-z3-timeout=inf"):
                with self.subTest(option=option):
                    completed = subprocess.run([str(NATIVE), str(path), "--optimize",
                        "--prove-optimal", option], text=True, capture_output=True, timeout=5)
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("invalid initial Z3 timing", completed.stderr)
            for option in ("--initial-z3-after=1", "--initial-z3-timeout=60"):
                with self.subTest(option=option):
                    completed = subprocess.run([str(NATIVE), str(path), "--optimize", option],
                        text=True, capture_output=True, timeout=5)
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("require --prove-optimal", completed.stderr)

    def test_invalid_optimality_options_rejected(self):
        with tempfile.TemporaryDirectory(prefix="nia-optimality-options-") as directory:
            path = Path(directory) / "input.nia"
            path.write_text("NIA_LS_OMT 1\n")
            for options in (("--prove-optimal", "--first-solution"),
                            ("--optimize", "--optimality-stall=1"),
                            ("--optimize", "--optimality-resume"),
                            ("--optimize", "--prove-optimal", "--optimality-stall=0"),
                            ("--optimize", "--prove-optimal", "--optimality-timeout=nan"),
                            ("--optimize", "--optimality-search-window=1"),
                            ("--optimize", "--prove-optimal", "--optimality-search-window=-1"),
                            ("--optimize", "--prove-optimal", "--optimality-search-window=nan")):
                with self.subTest(options=options):
                    completed = subprocess.run([str(NATIVE), str(path), *options],
                                               text=True, capture_output=True, timeout=5)
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("optimal", completed.stderr)


if __name__ == "__main__":
    unittest.main()
