"""Small real-solver checks for the benchmark worker's result semantics."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "script" / "solver_worker.py"


class PurePythonSolverWorkerTests(unittest.TestCase):
    """Protocol tests with solver APIs and native processes replaced by Python fakes."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("omt_pure_solver_worker", WORKER)
        cls.worker = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(ROOT / "script"), *sys.path]):
            spec.loader.exec_module(cls.worker)

    def test_maximize_protocol_restores_values_bounds_and_first_timestamp(self):
        emitted = []
        tracker = self.worker.CandidateTracker(0, emitted.append, objective_sense="maximize")
        self.assertTrue(tracker.consider("(- 3)", "solver_progress", 1.0))
        self.assertTrue(tracker.consider("(- 9)", "solver_progress", 2.0))
        self.assertFalse(tracker.consider("(- 9)", "final_model", 3.0))
        self.assertFalse(tracker.consider("(- 4)", "solver_progress", 4.0))
        self.assertEqual([event["value"] for event in emitted], ["3", "9"])
        self.assertTrue(all(event["objective_sense"] == "maximize" for event in emitted))
        final = tracker.final(status="unknown", reported_optimum="(- 12)", model_value="(- 9)",
                              lower_bound="(- 12)", upper_bound="(- 9)")
        self.assertEqual(final["objective_sense"], "maximize")
        self.assertEqual(final["best_value"], "9")
        self.assertEqual(final["reported_optimum"], "12")
        self.assertEqual(final["model_value"], "9")
        self.assertEqual(final["lower_bound"], "9")
        self.assertEqual(final["upper_bound"], "12")
        self.assertEqual(final["time_to_best_seconds"], 2.0)

    def test_maximize_extended_reports_keep_direction_and_consistency(self):
        for reported, model, lower, upper, expected in (
            ("(- oo)", "(- 9)", "(- oo)", "(- oo)", "unbounded"),
            ("(+ (- 10) epsilon)", "(- 9)", None, None, "supremum"),
            ("(+ (- 10) epsilon)", "(- 11)", None, None, "inconsistent"),
            ("oo", "(- 9)", "oo", "oo", "inconsistent"),
            ("(- 9)", "(- 8)", "(- 9)", "(- 9)", "inconsistent"),
            ("(- 9)", "(- 9)", "(- 8)", "(- 10)", "inconsistent"),
        ):
            with self.subTest(reported=reported, model=model, expected=expected):
                tracker = self.worker.CandidateTracker(0, lambda event: None, objective_sense="maximize")
                tracker.consider(model, "final_model", 1.0)
                result, _ = self.worker.classify("sat", reported, model, tracker.best_value,
                                                 lower, upper, optimal=True)
                final = tracker.final(status="sat", reported_optimum=reported, model_value=model,
                                      lower_bound=lower, upper_bound=upper, optimality_status=result)
                self.assertEqual(final["optimality_status"], expected)
                if expected == "unbounded":
                    self.assertEqual(self.worker.numeric_value(final["reported_optimum"]), (1, 0, 0))

    def test_z3_maximize_loads_normalized_input_and_restores_final_protocol(self):
        worker = self.worker
        emitted, loaded = [], []

        class Number:
            def __init__(self, value):
                self.value = value

            def sexpr(self):
                return f"(- {-self.value})" if self.value < 0 else str(self.value)

            def __lt__(self, other):
                return self.value < other.value

        class Model:
            def __init__(self, value):
                self.value = value

            def eval(self, objective, model_completion):
                self.asserted_objective = objective
                return Number(self.value)

        class Optimizer:
            def from_file(self, path):
                raise AssertionError("maximize must be normalized before loading Z3")

            def from_string(self, text):
                loaded.append(worker.parse(text))

            def objectives(self):
                return ["normalized-objective"]

            def set_on_model(self, callback):
                self.callback = callback

            def check(self):
                self.callback(Model(-3))
                self.callback(Model(-9))
                return "sat"

            def model(self):
                return Model(-9)

        fake_z3 = SimpleNamespace(
            Optimize=Optimizer, Z3Exception=RuntimeError,
            is_int_value=lambda value: isinstance(value, Number),
            is_rational_value=lambda value: False, is_algebraic_value=lambda value: False,
            is_true=bool, simplify=lambda value: value,
            OptimizeObjective=lambda *args, **kwargs: SimpleNamespace(
                lower=lambda: Number(-9), upper=lambda: Number(-9)),
        )
        tracker = worker.CandidateTracker(time.monotonic_ns(), emitted.append, objective_sense="maximize")
        with tempfile.TemporaryDirectory(prefix="omt-pure-worker-") as directory:
            source = Path(directory) / "input.smt2"
            source.write_text("(declare-fun cost () Int)(assert (<= cost 9))(maximize cost)(check-sat)")
            with patch.dict(sys.modules, {"z3": fake_z3}), patch.object(sys, "path", list(sys.path)):
                final = worker.run_z3(source, tracker)
        self.assertIn(["minimize", ["-", "cost"]], loaded[0])
        self.assertNotIn(["maximize", "cost"], loaded[0])
        self.assertFalse(any(form[0] in ("check-sat", "get-value", "get-objectives") for form in loaded[0]))
        self.assertEqual([event["value"] for event in emitted], ["3", "9"])
        self.assertEqual(final["best_value"], "9")
        self.assertEqual(final["reported_optimum"], "9")
        self.assertEqual(final["model_value"], "9")
        self.assertEqual(final["lower_bound"], "9")
        self.assertEqual(final["upper_bound"], "9")
        self.assertEqual(final["optimality_status"], "attained")
        self.assertTrue(final["solver_reported_optimal"])
        self.assertEqual(final["time_to_reported_optimum_seconds"], final["time_to_best_seconds"])

    def test_optimathsat_maximize_stream_restores_protocol_without_native_process(self):
        worker = self.worker
        emitted = []
        tracker = worker.CandidateTracker(0, emitted.append, objective_sense="maximize")
        observed = [0.0]

        class Pipe:
            def __init__(self, descriptor):
                self.descriptor, self.closed = descriptor, False

            def fileno(self):
                return self.descriptor

            def close(self):
                self.closed = True

        class Selector:
            def __init__(self):
                self.entries = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def register(self, pipe, events, data):
                self.entries[pipe] = SimpleNamespace(fileobj=pipe, data=data)

            def unregister(self, pipe):
                del self.entries[pipe]

            def get_map(self):
                return self.entries

            def select(self):
                return [(key, None) for key in reversed(list(self.entries.values()))]

        process = SimpleNamespace(stdout=Pipe(101), stderr=Pipe(102), wait=lambda: 0, poll=lambda: 0)
        chunks = {
            101: [b"sat\n(objectives ((- cost) (- 9)))\n(((- cost) (- 9)))\n", b""],
            102: [b"# obj(0) - new: -3\n# obj(0) - new: -9\n"
                  b"# obj(0) [ -12, -9 ]\n# obj(0) [ -9, -9 ] sat_optimal\n", b""],
        }

        def read(descriptor, count):
            observed[0] += 1.0
            return chunks[descriptor].pop(0)

        def start_fake(command, **kwargs):
            normalized = Path(command[-1]).read_text()
            self.assertIn(["minimize", ["-", "cost"]], worker.parse(normalized))
            return process

        generated, objective = worker.optimization_input("(maximize cost)(check-sat)", normalize_maximize=True)
        with tempfile.TemporaryDirectory(prefix="omt-pure-opti-") as directory:
            case_dir = Path(directory)
            with patch.object(worker.subprocess, "Popen", start_fake), \
                    patch.object(worker.selectors, "DefaultSelector", Selector), \
                    patch.object(worker.os, "read", read), \
                    patch.object(tracker, "elapsed", lambda: observed[0]):
                final = worker.run_optimathsat(case_dir / "original.smt2", case_dir,
                                               generated, objective, tracker)
            self.assertFalse((case_dir / "input.generated.smt2").exists())
        self.assertEqual([event["value"] for event in emitted], ["3", "9"])
        self.assertEqual(final["best_value"], "9")
        self.assertEqual(final["reported_optimum"], "9")
        self.assertEqual(final["model_value"], "9")
        self.assertEqual(final["lower_bound"], "9")
        self.assertEqual(final["upper_bound"], "9")
        self.assertEqual(final["optimality_status"], "attained")
        self.assertTrue(final["solver_reported_optimal"])
        self.assertEqual(final["time_to_best_seconds"], 1.0)
        self.assertEqual(final["time_to_reported_optimum_seconds"], 1.0)
        self.assertEqual(final["time_to_optimal_seconds"], 1.0)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_main_sets_source_direction_before_dispatching_solver(self):
        worker = self.worker
        protocol = io.StringIO()

        def run_fake(input_path, case_dir, generated, objective, tracker):
            self.assertEqual(tracker.objective_sense, "maximize")
            self.assertEqual(objective, "(- cost)")
            self.assertIn(["minimize", ["-", "cost"]], worker.parse(generated))
            tracker.consider("(- 9)", "solver_progress", 1.0)
            return tracker.final(status="unknown", model_value="(- 9)")

        with tempfile.TemporaryDirectory(prefix="omt-pure-main-") as directory:
            source = Path(directory) / "source.smt2"
            source.write_text("(maximize cost)(check-sat)")
            args = [str(WORKER), "--solver", "optimathsat", "--input", str(source),
                    "--case-dir", str(Path(directory) / "case"), "--start-ns", "0"]
            with patch.object(sys, "argv", args), \
                    patch.object(worker, "run_optimathsat", run_fake), \
                    patch.object(worker.os, "dup", return_value=1), \
                    patch.object(worker.os, "fdopen") as opened:
                opened.return_value.__enter__.return_value = protocol
                self.assertEqual(worker.main(), 0)
        events = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(events[0]["value"], "9")
        self.assertEqual(events[-1]["best_value"], "9")
        self.assertEqual(events[-1]["model_value"], "9")
        self.assertTrue(all(event["objective_sense"] == "maximize" for event in events))

    def test_minimize_protocol_and_consistency_remain_unchanged(self):
        emitted = []
        tracker = self.worker.CandidateTracker(0, emitted.append)
        self.assertTrue(tracker.consider("9", "solver_progress", 1.0))
        self.assertTrue(tracker.consider("3", "solver_progress", 2.0))
        self.assertFalse(tracker.consider("3", "final_model", 3.0))
        self.assertFalse(tracker.consider("9", "solver_progress", 4.0))
        classification, optimal = self.worker.classify("sat", "3", "3", tracker.best_value,
                                                       "3", "3", optimal=True)
        final = tracker.final(status="sat", reported_optimum="3", model_value="3",
                              lower_bound="3", upper_bound="3", optimality_status=classification)
        self.assertEqual([event["value"] for event in emitted], ["9", "3"])
        self.assertEqual(final["objective_sense"], "minimize")
        self.assertEqual(final["best_value"], "3")
        self.assertEqual(final["reported_optimum"], "3")
        self.assertEqual(final["model_value"], "3")
        self.assertEqual(final["lower_bound"], "3")
        self.assertEqual(final["upper_bound"], "3")
        self.assertEqual(final["time_to_best_seconds"], 2.0)
        self.assertEqual(final["optimality_status"], "attained")
        self.assertTrue(optimal)


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
