import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))


class StrictBoundTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            (SCRIPT / "run_strict_bound.py").is_file(),
            "strict-bound query runner is not implemented",
        )
        self.runner = importlib.import_module("run_strict_bound")
        self.smtlib = importlib.import_module("smtlib")

    def test_minimum_keeps_compound_objective_and_all_hard_constraints(self):
        source = '''
            ; Ignore this fake command: (maximize bogus)
            (set-logic ALL)
            (set-info :status sat)
            (set-info :source "description ""(check-sat)"" ; (exit)")
            (set-option :timeout 1)
            (set-option :rlimit 2)
            (declare-const |x (cost);| Int)
            (declare-const y Int)
            (declare-const note String)
            (assert (>= |x (cost);| (- 11)))
            (assert (= note "keep (; ) ""quoted"""))
            (assert (= y 2))
            (minimize (+ |x (cost);| (* 2 y)))
            (check-sat)
            (get-model)
            (get-objectives)
            (get-value (|x (cost);|))
            (echo "sat")
            (exit)
        '''
        query, objective = self.runner.build_query(source, "-7", "min")
        expression = ["+", "|x (cost);|", ["*", "2", "y"]]
        self.assertEqual(
            self.smtlib.parse("(minimize " + objective + ")"),
            [["minimize", expression]],
        )
        original = self.smtlib.parse(source)
        retained = [
            form for form in original
            if form[0] in {"set-logic", "declare-const", "assert"}
        ]
        self.assertEqual(
            self.smtlib.parse(query),
            retained + [
                ["assert", ["<", expression, ["-", "7"]]],
                ["check-sat"],
                ["exit"],
            ],
        )

        # A fresh interpreter must be able to build the query with every Z3
        # import blocked, even when local Python bindings are available.
        child = textwrap.dedent(f'''
            import importlib.abc
            import sys
            sys.dont_write_bytecode = True
            sys.path.insert(0, {str(SCRIPT)!r})
            class RejectZ3(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "z3" or fullname.startswith("z3."):
                        raise AssertionError("query generation imported Python z3")
            sys.meta_path.insert(0, RejectZ3())
            import run_strict_bound
            run_strict_bound.build_query({source!r}, "-7", "min")
            assert not any(n == "z3" or n.startswith("z3.") for n in sys.modules)
        ''')
        completed = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_maximum_uses_greater_than_without_negating_the_objective(self):
        for objective, best, bound in (
            ("profit", "9", "9"),
            ("(- profit)", "-3", ["-", "3"]),
        ):
            with self.subTest(objective=objective):
                source = (
                    "(declare-const profit Int)"
                    "(assert (>= profit (- 20)))"
                    "(assert (<= profit 20))"
                    f"(maximize {objective})(check-sat)(exit)"
                )
                query, actual_objective = self.runner.build_query(source, best, "max")
                self.assertEqual(actual_objective, objective)
                expression = self.smtlib.parse(f"(maximize {objective})")[0][1]
                self.assertEqual(
                    self.smtlib.parse(query),
                    self.smtlib.parse(source)[:3] + [
                        ["assert", [">", expression, bound]],
                        ["check-sat"],
                        ["exit"],
                    ],
                )

    def test_rejects_direction_mismatch_multiple_objectives_and_query_mutation(self):
        invalid = (
            ("(minimize x)(check-sat)", "max"),
            ("(maximize x)(check-sat)", "min"),
            ("(minimize x)(minimize y)(check-sat)", "min"),
            ("(minimize x)(maximize y)(check-sat)", "min"),
            ("(push 1)(minimize x)(check-sat)", "min"),
            ("(minimize x)(pop 1)(check-sat)", "min"),
            ("(minimize x)(check-sat)(assert (> x 0))", "min"),
            ("(minimize x)(check-sat)(check-sat)", "min"),
        )
        for source, direction in invalid:
            with self.subTest(source=source, direction=direction):
                with self.assertRaises(ValueError):
                    self.runner.build_query(source, "0", direction)

    def test_runner_uses_smt2_cli_records_logs_and_enforces_wall_timeout(self):
        with tempfile.TemporaryDirectory(prefix="omt-strict-bound-test-") as temp:
            root = Path(temp)
            prepared = root / "prepared"
            (prepared / "instance").mkdir(parents=True)
            (prepared / "instance/000.smt2").write_text(
                "(declare-const objective Int)"
                "(assert (>= objective 3))"
                "(assert (< objective 3))(check-sat)(exit)",
                encoding="utf-8",
            )
            case = {
                "query_file": "instance/000.smt2",
                "filename": "sample.smt2",
                "M": "3",
                "direction": "min",
                "category": "test",
                "proved_by": ["z3", "optimathsat"],
                "z3_reference_state": "optimal",
                "optimathsat_reference_state": "optimal",
                "nia_time_to_best_seconds": 0.01,
                "nia_optimality_seconds": 0.02,
                "nia_optimality_attempts": 1,
            }
            for mode, expected, code in (
                ("unsat", "unsat", 0),
                ("unknown", "unknown", 0),
                ("exiterror", "error", 7),
                ("timeout", "timeout", None),
            ):
                with self.subTest(mode=mode):
                    output = root / mode
                    output.mkdir()
                    pid_file = root / (mode + ".pid")
                    solver = root / (mode + "_solver.py")
                    solver.write_text(
                        f"#!{sys.executable}\n" + textwrap.dedent(f'''
                            import os
                            from pathlib import Path
                            import sys
                            import time
                            assert len(sys.argv) == 3, sys.argv
                            assert sys.argv[1] == "-smt2", sys.argv
                            query = Path(sys.argv[2]).read_text()
                            assert "(check-sat)" in query, query
                            assert "(assert (< objective 3))" in query, query
                            assert "minimize" not in query, query
                            assert "get-model" not in query, query
                            Path({str(pid_file)!r}).write_text(str(os.getpid()))
                            print("diagnostic: {mode}", file=sys.stderr, flush=True)
                            if {mode!r} == "timeout":
                                print("started", flush=True)
                                time.sleep(10)
                            print({('unsat' if mode == 'exiterror' else mode)!r}, flush=True)
                            sys.exit({code if code is not None else 0})
                        '''),
                        encoding="utf-8",
                    )
                    solver.chmod(0o755)
                    started = time.monotonic()
                    result = self.runner.run_case(
                        case, prepared, solver, 0.1 if mode == "timeout" else 3.0, output,
                    )
                    elapsed = time.monotonic() - started
                    self.assertEqual(result["status"], expected, result)
                    self.assertIsInstance(result["time_seconds"], (int, float))
                    self.assertGreaterEqual(result["time_seconds"], 0)
                    self.assertIn("returncode", result)
                    self.assertIn("error", result)
                    if code is not None:
                        self.assertEqual(result["returncode"], code, result)
                    if mode == "exiterror":
                        self.assertTrue(result["error"], result)
                    if mode == "timeout":
                        self.assertGreaterEqual(elapsed, 0.08)
                        self.assertLess(elapsed, 3.0)
                        self.assertTrue(pid_file.is_file(), "fake solver did not start")
                        pid = int(pid_file.read_text())
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)

                    logs = output / "logs"
                    self.assertTrue(logs.is_dir(), "runner did not create logs directory")
                    files = [path for path in logs.rglob("*") if path.is_file()]
                    stdout = [path for path in files if "stdout" in path.name]
                    stderr = [path for path in files if "stderr" in path.name]
                    self.assertTrue(stdout, files)
                    self.assertTrue(stderr, files)
                    self.assertIn(
                        "started" if mode == "timeout" else ("unsat" if mode == "exiterror" else mode),
                        "\n".join(path.read_text() for path in stdout),
                    )
                    self.assertIn(
                        "diagnostic: " + mode,
                        "\n".join(path.read_text() for path in stderr),
                    )


if __name__ == "__main__":
    unittest.main()
