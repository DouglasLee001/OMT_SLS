"""Small real-solver checks for the benchmark worker's result semantics."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "script" / "solver_worker.py"


class SolverWorkerTests(unittest.TestCase):
    def run_worker(self, solver, text):
        with tempfile.TemporaryDirectory(prefix="omt-worker-test-") as directory:
            case_dir = Path(directory) / "case"
            case_dir.mkdir()
            source = Path(directory) / "example.smt2"
            source.write_text(text)
            started = time.monotonic_ns() - 200_000_000
            result = subprocess.run(
                [sys.executable, str(WORKER), "--solver", solver,
                 "--input", str(source), "--case-dir", str(case_dir),
                 "--start-ns", str(started)],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertTrue(events, result.stdout)
            self.assertEqual(events[-1]["event"], "final")
            self.assertTrue((case_dir / "stdout.txt").exists())
            self.assertTrue((case_dir / "stderr.txt").exists())
            self.assertFalse((case_dir / "input.generated.smt2").exists())
            logs = {name: (case_dir / name).read_text() for name in ("stdout.txt", "stderr.txt")}
            return events, logs

    def test_integer_optimum_and_first_found_time(self):
        source = """(set-logic QF_LIA)
(declare-fun x () Int)
(assert (and (>= x 3) (<= x 12)))
(minimize x)
(check-sat)
(exit)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                final = events[-1]
                self.assertEqual(final["status"], "sat")
                self.assertEqual(final["best_value"], "3")
                self.assertEqual(final["optimality_status"], "attained")
                self.assertTrue(final["solver_reported_optimal"])
                self.assertGreaterEqual(final["time_to_best_seconds"], .2)
                candidates = [event for event in events if event["event"] == "candidate"]
                best = next(event for event in candidates if event["value"] == "3")
                self.assertEqual(final["time_to_best_seconds"], best["time_seconds"])
                self.assertEqual(final["best_value_source"], best["source"])
                self.assertEqual(final.get("time_to_reported_optimum_seconds"), best["time_seconds"])
                self.assertEqual(final.get("lower_bound"), "3")
                self.assertEqual(final.get("upper_bound"), "3")
                self.assertGreaterEqual(final["time_to_optimal_seconds"], best["time_seconds"])
                self.assertGreaterEqual(final.get("completed_seconds", -1), best["time_seconds"])

    def test_exact_rational_objective_is_preserved(self):
        source = """(set-logic QF_LRA)
(declare-fun x () Real)
(assert (>= x (/ 1 3)))
(minimize x)
(check-sat)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                final = events[-1]
                self.assertEqual(final["status"], "sat")
                self.assertIn(final["best_value"], {"(/ 1 3)", "(/ 1.0 3.0)"})
                self.assertEqual(final["optimality_status"], "attained")

    def test_unsat_remains_unsat_after_value_query(self):
        source = """(set-logic QF_LIA)
(declare-fun x () Int)
(assert (and (> x 3) (< x 2)))
(minimize x)
(check-sat)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                final = events[-1]
                self.assertEqual(final["status"], "unsat")
                self.assertIsNone(final["best_value"])
                self.assertIsNone(final["reported_optimum"], "An unsat status is not an objective value")
                self.assertEqual(final["optimality_status"], "none")
                self.assertFalse(final["solver_reported_optimal"])

    def test_strict_real_bound_is_infimum_not_attained(self):
        source = """(set-logic QF_LRA)
(declare-fun x () Real)
(assert (> x 0))
(minimize x)
(check-sat)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                final = events[-1]
                self.assertEqual(final["status"], "sat")
                self.assertEqual(final["optimality_status"], "infimum")
                self.assertIn("epsilon", final["reported_optimum"])
                self.assertIsNone(final["time_to_optimal_seconds"])
                self.assertIsNotNone(final.get("time_to_reported_optimum_seconds"))
                self.assertIsNotNone(final["model_value"])
                self.assertIsNotNone(final["best_value"])

    def test_minimizing_unbounded_objective_has_concrete_model(self):
        source = """(set-logic QF_LRA)
(declare-fun x () Real)
(minimize x)
(check-sat)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                final = events[-1]
                self.assertEqual(final["status"], "sat")
                self.assertEqual(final["optimality_status"], "unbounded")
                self.assertIsNotNone(final["model_value"])
                self.assertIsNone(final["time_to_optimal_seconds"])
                self.assertIsNotNone(final.get("time_to_reported_optimum_seconds"))

    def test_optimathsat_nra_contradiction_is_reported(self):
        source = """(set-logic QF_NRA)
(declare-fun x () Real)
(assert (and (>= x 0) (<= x 10) (>= (* x x) 9)))
(minimize x)
(check-sat)
"""
        events, logs = self.run_worker("optimathsat", source)
        final = events[-1]
        self.assertEqual(final["status"], "sat")
        self.assertEqual(final["optimality_status"], "inconsistent")
        self.assertTrue(final["solver_reported_optimal"])
        self.assertIn("epsilon", final["reported_optimum"])
        self.assertEqual(final["model_value"], "10")
        self.assertEqual(final["best_value"], "10")
        self.assertIsNone(final["time_to_optimal_seconds"])
        self.assertIn("[ oo, oo ]", logs["stderr.txt"])

    def test_z3_algebraic_model_preserves_inconsistent_bound_claim(self):
        source = """(set-logic QF_NRA)
(declare-fun x () Real)
(assert (and (>= x 0) (= (* x x) 2)))
(minimize x)
(check-sat)
"""
        events, _ = self.run_worker("z3", source)
        final = events[-1]
        self.assertEqual(final["status"], "sat")
        self.assertTrue(final["best_value"].startswith("(root-obj "))
        self.assertEqual(final["model_value"], final["best_value"])
        self.assertEqual(final["reported_optimum"], "oo")
        self.assertEqual(final["optimality_status"], "inconsistent")
        self.assertTrue(final["solver_reported_optimal"])
        self.assertIsNone(final["time_to_optimal_seconds"])

    def test_objective_symbol_may_contain_parentheses(self):
        source = """(set-logic QF_LIA)
(declare-fun |cost(a)| () Int)
(assert (>= |cost(a)| 3))
(minimize |cost(a)|)
(check-sat)
"""
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, source)
                self.assertEqual(events[-1]["best_value"], "3")
                self.assertEqual(events[-1]["optimality_status"], "attained")

    def test_invalid_input_emits_error_final(self):
        for solver in ("z3", "optimathsat"):
            with self.subTest(solver=solver):
                events, _ = self.run_worker(solver, "(not-a-command)\n")
                self.assertEqual(events[-1]["status"], "error")
                self.assertIsNotNone(events[-1]["reason"])

    def test_z3_check_cancellation_keeps_previous_candidate(self):
        spec = importlib.util.spec_from_file_location("omt_solver_worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "script"))
        sys.path.insert(0, str(ROOT / "competitor/z3-5.1.0/install/lib/python3.12/site-packages"))
        try:
            spec.loader.exec_module(module)
            import z3
        finally:
            sys.path.pop(0)
            sys.path.pop(0)
        original_check = z3.Optimize.check

        def canceled_check(optimizer, *args):
            original_check(optimizer, *args)
            raise z3.Z3Exception("canceled")

        emitted = []
        tracker = module.CandidateTracker(time.monotonic_ns(), emitted.append)
        with tempfile.TemporaryDirectory(prefix="omt-worker-cancellation-") as directory:
            source = Path(directory) / "example.smt2"
            source.write_text("(declare-fun x () Int)\n(assert (>= x 7))\n(minimize x)\n(check-sat)\n")
            with patch.object(z3.Optimize, "check", canceled_check):
                try:
                    final = module.run_z3(source, tracker)
                except z3.Z3Exception as error:
                    self.fail(f"check cancellation escaped and dropped the final event: {error}")
        self.assertEqual(final["status"], "unknown")
        self.assertEqual(final["best_value"], "7")
        self.assertIn("canceled", final["reason"])
        self.assertFalse(final["solver_reported_optimal"])
        self.assertTrue(emitted)

    def test_z3_callback_timestamp_precedes_objective_evaluation(self):
        spec = importlib.util.spec_from_file_location("omt_solver_worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "script"))
        sys.path.insert(0, str(ROOT / "competitor/z3-5.1.0/install/lib/python3.12/site-packages"))
        try:
            spec.loader.exec_module(module)
            import z3
        finally:
            sys.path.pop(0)
            sys.path.pop(0)
        original_eval = z3.ModelRef.eval
        clock_ns = [1_000_000_000]

        def evaluation_takes_time(model, *args, **kwargs):
            clock_ns[0] += 1_000_000_000
            return original_eval(model, *args, **kwargs)

        emitted = []
        tracker = module.CandidateTracker(0, emitted.append)
        with tempfile.TemporaryDirectory(prefix="omt-worker-callback-") as directory:
            source = Path(directory) / "example.smt2"
            source.write_text("(declare-fun x () Int)\n(assert (>= x 7))\n(minimize x)\n(check-sat)\n")
            with patch.object(module.time, "monotonic_ns", lambda: clock_ns[0]), \
                    patch.object(z3.ModelRef, "eval", evaluation_takes_time):
                final = module.run_z3(source, tracker)
        self.assertEqual(emitted[0]["time_seconds"], 1.0)
        self.assertGreaterEqual(final["time_to_optimal_seconds"], final["time_to_best_seconds"])

    def test_optimathsat_stream_error_stops_and_reaps_native_child(self):
        spec = importlib.util.spec_from_file_location("omt_solver_worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "script"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        original_popen = subprocess.Popen
        for ignore_termination in (False, True):
            with self.subTest(ignore_termination=ignore_termination):
                children = []
                child_code = "import time, signal; "
                if ignore_termination:
                    child_code += "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                child_code += "print('sat', flush=True); time.sleep(60)"

                def start_small_child(command, **kwargs):
                    child = original_popen([sys.executable, "-u", "-c", child_code], **kwargs)
                    children.append(child)
                    return child

                tracker = module.CandidateTracker(time.monotonic_ns(), lambda event: None)
                with tempfile.TemporaryDirectory(prefix="omt-worker-stream-error-") as directory:
                    case_dir = Path(directory)
                    try:
                        with patch.object(module.subprocess, "Popen", start_small_child), \
                                patch.object(module.OptiOutput, "stdout_chunk", side_effect=OSError("stream fault")):
                            with self.assertRaisesRegex(OSError, "stream fault"):
                                module.run_optimathsat(case_dir / "original.smt2", case_dir,
                                                       "(check-sat)\n", "x", tracker)
                        self.assertIsNotNone(children[0].poll(), "native child survived worker stream error")
                        self.assertTrue(children[0].stdout.closed)
                        self.assertTrue(children[0].stderr.closed)
                        self.assertFalse((case_dir / "input.generated.smt2").exists())
                    finally:
                        for child in children:
                            if child.poll() is None:
                                child.kill()
                            child.wait(timeout=2)
                            child.stdout.close()
                            child.stderr.close()

    def test_split_stdout_status_does_not_disappear(self):
        spec = importlib.util.spec_from_file_location("omt_solver_worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "script"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        emitted = []
        tracker = module.CandidateTracker(0, emitted.append)
        output = module.OptiOutput("|cost(a)|", tracker)
        output.stdout_chunk("s", 1.0)
        output.stdout_chunk("at\n(objectives (|cost(a)| 3))\n((|cost(a)|", 2.0)
        output.stdout_chunk(" 3))\n", 3.0)
        self.assertEqual(output.status, "sat")
        self.assertEqual(output.reported_optimum, "3")
        self.assertEqual(output.model_value, "3")
        self.assertEqual(tracker.time_to_best_seconds, 3.0)

    def test_duplicate_candidate_keeps_first_timestamp(self):
        self.assertTrue(WORKER.exists(), "candidate tracker is not implemented")
        spec = importlib.util.spec_from_file_location("omt_solver_worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "script"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        emitted = []
        tracker = module.CandidateTracker(0, emitted.append)
        self.assertTrue(tracker.consider("7", "solver_progress", 1.0))
        self.assertFalse(tracker.consider("7", "final_model", 3.0))
        self.assertFalse(tracker.consider("8", "solver_progress", 4.0))
        self.assertFalse(tracker.consider("(+ 0 epsilon)", "solver_progress", 5.0))
        self.assertTrue(tracker.consider("(/ 1 3)", "solver_progress", 6.0))
        self.assertFalse(tracker.consider("(/ 2 6)", "final_model", 7.0))
        self.assertEqual(tracker.time_to_best_seconds, 6.0)
        self.assertEqual(len(emitted), 2)


if __name__ == "__main__":
    unittest.main()
