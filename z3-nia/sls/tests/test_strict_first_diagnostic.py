"""Generated-only strict first-solution diagnostic tests.

The default suite never launches a process. Enable the four real generated
toys explicitly with NIA_STRICT_DIAG_REAL_TOYS=1; each total case window is
1.5 seconds and each source-check limit is one second. No benchmark is read.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "z3-nia/experiment/strict_first_diagnostic.py"
STRICT = ROOT / "script/run_strict_bound.py"
INITIAL = ROOT / "script/run_initial.py"
VERIFY = ROOT / "script/verify_optimize.py"
SDK = ROOT / "competitor/z3-5.1.0/build/python"
LIBRARY = SDK.parent / "libz3.so"
FROZEN = Path(os.environ.get("NIA_STRICT_DIAG_SOLVER",
    "/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/source/nia_ls"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact(text):
    return "".join(text.split())


def load_module():
    path = Path(os.environ.get("NIA_STRICT_DIAG_MODULE", MODULE))
    spec = importlib.util.spec_from_file_location("strict_first_diagnostic_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        before = set(sys.modules)
        cls.diag = load_module()
        cls.parent_loaded_z3 = any(name == "z3" or name.startswith("z3.")
                                  for name in set(sys.modules) - before)

    def setUp(self):
        evidence = os.environ.get("NIA_STRICT_DIAG_TEST_ARTIFACT_DIR")
        if evidence and self._testMethodName == "test_four_real_generated_toys_separate_native_and_source_verification":
            self.directory = Path(evidence).resolve() / "generated-cases"
            self.directory.mkdir(parents=True, exist_ok=False)
        else:
            self.temporary = tempfile.TemporaryDirectory(prefix="strict-first-test-")
            self.directory = Path(self.temporary.name)
            self.addCleanup(self.temporary.cleanup)

    def config(self, solver=None, **updates):
        values = dict(solver=solver or Path(sys.executable), initial_helper=INITIAL,
                      verify_helper=VERIFY, sdk_python=SDK, sdk_library=LIBRARY,
                      strict_helper=STRICT, timeout=1.5, source_check_seconds=1.0, seed=0)
        values.update(updates)
        return self.diag.Config(**values)

    def record(self, text="(declare-const x Int)(assert (>= x 0))(minimize x)(check-sat)",
               M="1", direction="min", index=0):
        source = self.directory / (f"source-{index}.smt2")
        source.write_text(text)
        query_text, objective = self.diag.build_strict_omt(text, M, direction, helper_path=STRICT)
        query = self.directory / (f"strict-{index}.smt2")
        query.write_text(query_text)
        return dict(index=index, filename=f"generated-{index}.smt2", category="generated",
                    source=str(source), source_sha256=digest(source), direction=direction,
                    M=M, query=str(query), query_sha256=digest(query), objective=objective)

    def assert_no_verified_claim(self, result):
        self.assertIs(result["diagnostic_only"], True)
        self.assertIs(result["native_verified"], False)
        self.assertIs(result["source_model_verified"], False)
        self.assertIs(result["source_bound_unsat_verified"], False)

    def test_import_does_not_load_z3_in_parent(self):
        self.assertFalse(self.parent_loaded_z3)

    def test_builder_keeps_min_and_max_objective_and_complete_boolean_hard(self):
        hard = "(assert (or (and b (>= x 0)) (and (not b) (<= x 2))))"
        for direction, command, relation in [("min", "minimize", "<"), ("max", "maximize", ">")]:
            with self.subTest(direction=direction):
                text = f"(declare-const x Int)(declare-const b Bool){hard}({command} x)(check-sat)(get-model)"
                query, objective = self.diag.build_strict_omt(text, "-3", direction, helper_path=STRICT)
                self.assertEqual(objective, "x")
                self.assertIn(compact(hard), compact(query))
                self.assertIn(compact(f"(assert ({relation} x (- 3)))"), compact(query))
                self.assertEqual(compact(query).count(f"({command}x)"), 1)
                self.assertEqual(compact(query).count("(check-sat)"), 1)
                self.assertNotIn("(exit)", compact(query))
                self.assertNotIn("(get-model)", compact(query))

    def test_builder_keeps_constant_and_quoted_objective(self):
        query, objective = self.diag.build_strict_omt("(assert true)(minimize 7)(check-sat)", "7", "min", helper_path=STRICT)
        self.assertEqual(objective, "7")
        self.assertIn("(assert(<77))", compact(query))
        query, objective = self.diag.build_strict_omt(
            "(declare-const |x ; ()| Int)(assert (>= |x ; ()| 0))(maximize |x ; ()|)(check-sat)",
            "0", "max", helper_path=STRICT)
        self.assertEqual(objective, "|x ; ()|")
        self.assertIn("(maximize |x ; ()|)", query)

    def test_builder_rejects_nonexact_incumbents_and_direction_mismatch(self):
        source = "(declare-const x Int)(minimize x)(check-sat)"
        for M in ("", "nan", "1.5", "1e3", "-oo", "0) (assert false)"):
            with self.subTest(M=M), self.assertRaises((ValueError, RuntimeError)):
                self.diag.build_strict_omt(source, M, "min", helper_path=STRICT)
        for direction in ("max", "minimize", ""):
            with self.subTest(direction=direction), self.assertRaises((ValueError, RuntimeError)):
                self.diag.build_strict_omt(source, "0", direction, helper_path=STRICT)

    def test_builder_rejects_state_changing_and_multiple_objective_sources(self):
        prefix = "(declare-const x Int)"
        for body in ("(push 1)(minimize x)(check-sat)", "(reset)(minimize x)(check-sat)",
                     "(minimize x)(maximize x)(check-sat)",
                     "(minimize x)(check-sat)(assert (= x 0))",
                     "(assert-soft (> x 0))(minimize x)(check-sat)"):
            with self.subTest(body=body), self.assertRaises((ValueError, RuntimeError)):
                self.diag.build_strict_omt(prefix + body, "0", "min", helper_path=STRICT)

    def test_stop_is_interrupted_pending_without_starting_native(self):
        record = self.record(); stop = threading.Event(); stop.set()
        before = {key: Path(record[key]).read_bytes() for key in ("source", "query")}
        case = self.directory / "stopped"
        with mock.patch("subprocess.Popen", side_effect=AssertionError("stop must not launch a process")) as launch:
            result = self.diag.run_case(record, self.config(), case, stop)
        launch.assert_not_called()
        self.assertEqual(result["native_outcome"], "interrupted")
        self.assertEqual(result["source_verification_status"], "pending")
        self.assert_no_verified_claim(result)
        self.assertFalse((case / "native.stdout").exists())
        self.assertFalse((case / "native.stderr").exists())
        for key, contents in before.items(): self.assertEqual(Path(record[key]).read_bytes(), contents)

    def test_source_and_query_sha_mismatch_never_launch_or_claim_proof(self):
        for field in ("source_sha256", "query_sha256"):
            with self.subTest(field=field):
                record = self.record(index=0 if field.startswith("source") else 1)
                record[field] = "0" * 64
                with mock.patch("subprocess.Popen", side_effect=AssertionError("bad identity must not launch")) as launch:
                    result = self.diag.run_case(record, self.config(), self.directory / field, threading.Event())
                launch.assert_not_called()
                self.assertIn(result["native_outcome"], ("error", "invalid", "rejected"))
                self.assert_no_verified_claim(result)

    def test_missing_solver_is_error_without_verification(self):
        record = self.record()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("missing solver must not launch")) as launch:
            result = self.diag.run_case(record, self.config(solver=self.directory / "no-solver"),
                                        self.directory / "missing-solver", threading.Event())
        launch.assert_not_called()
        self.assertEqual(result["native_outcome"], "error")
        self.assert_no_verified_claim(result)

    def test_expired_stage_stays_pending_without_source_check(self):
        record = self.record()
        # No fake native model or proof: emulate only the standard stage timeout.
        helper = self.diag._helper(INITIAL)
        with mock.patch.object(helper, "_stage", side_effect=TimeoutError("per-case wallclock limit reached")) as stage:
            result = self.diag.run_case(record, self.config(), self.directory / "expired", threading.Event())
        self.assertEqual(stage.call_count, 1)
        self.assertIn(result["native_outcome"], ("timeout", "budget"))
        self.assertEqual(result["source_verification_status"], "pending")
        self.assert_no_verified_claim(result)

    def test_query_preparation_exhausts_total_budget_without_process(self):
        record = self.record()
        record["query_preparation_seconds"] = 2.0
        with mock.patch("subprocess.Popen", side_effect=AssertionError("preparation used the whole case budget")) as launch:
            result = self.diag.run_case(record, self.config(timeout=1.5),
                                        self.directory / "preparation-expired", threading.Event())
        launch.assert_not_called()
        self.assertEqual(result["native_outcome"], "timeout")
        self.assertEqual(result["source_verification_status"], "pending")
        self.assertGreaterEqual(result["elapsed_seconds"], 2.0)
        self.assert_no_verified_claim(result)

    def test_query_preparation_rejects_negative_and_nonfinite_time(self):
        for index, seconds in enumerate((-1.0, float("nan"))):
            with self.subTest(seconds=seconds):
                record = self.record(index=index)
                record["query_preparation_seconds"] = seconds
                with mock.patch("subprocess.Popen", side_effect=AssertionError("invalid preparation time must not launch")) as launch:
                    result = self.diag.run_case(record, self.config(),
                                                self.directory / f"preparation-invalid-{index}", threading.Event())
                launch.assert_not_called()
                self.assertEqual(result["native_outcome"], "error")
                self.assert_no_verified_claim(result)

    @unittest.skipUnless(os.environ.get("NIA_STRICT_DIAG_REAL_TOYS") == "1", "four real generated toys are explicit opt-in")
    def test_four_real_generated_toys_separate_native_and_source_verification(self):
        self.assertTrue(FROZEN.is_file())
        big = 1 << 100
        prefix = ("(declare-const x Int)(declare-const g Int)(declare-const b Bool)(assert b)"
                  f"(assert (>= x 0))(assert (<= x 2))(assert (= g (+ x {big})))")
        cases = [(prefix + "(minimize g)(check-sat)", str(big + 2), "min", "sat"),
                 (prefix + "(maximize g)(check-sat)", str(big), "max", "sat"),
                 ("(assert true)(minimize 7)(check-sat)", "7", "min", "unsat"),
                 ("(assert true)(maximize 7)(check-sat)", "7", "max", "unsat")]
        for index, (text, M, direction, expected) in enumerate(cases):
            with self.subTest(index=index):
                record = self.record(text, M, direction, index)
                before = {key: Path(record[key]).read_bytes() for key in ("source", "query")}
                result = self.diag.run_case(record, self.config(solver=FROZEN),
                                            self.directory / f"real-{index}", threading.Event())
                print("REAL_GENERATED_DIAGNOSTIC", index, result, flush=True)
                self.assertEqual(result["native_outcome"], expected, result)
                self.assertIs(result["diagnostic_only"], True)
                self.assertIs(result.get("is_optimal", False), False)
                if expected == "sat":
                    native_lines = (self.directory / f"real-{index}" / "input.nia").read_text().splitlines()
                    variables_at = next(i for i, line in enumerate(native_lines) if line.startswith("VARIABLES "))
                    count = int(native_lines[variables_at].split()[1])
                    names = [bytes.fromhex(line.split()[2]).decode()
                             for line in native_lines[variables_at + 1:variables_at + count + 1]]
                    self.assertNotIn("g", names, "this fixture must actually exercise eliminated source objective g")
                    print("REAL_ELIMINATED_OBJECTIVE", index, "native_variables", names, flush=True)
                    self.assertIs(result["native_verified"], True)
                    self.assertIs(result["strict_improvement"], True)
                    self.assertIs(result["source_model_verified"], True)
                    self.assertIs(result["source_bound_unsat_verified"], False)
                    self.assertEqual(result["source_verification_status"], "sat_verified")
                    value = int(result["native_value"])
                    self.assertTrue(value < int(M) if direction == "min" else value > int(M))
                else:
                    self.assertIs(result["source_model_verified"], False)
                    self.assertIs(result["source_bound_unsat_verified"], True)
                    self.assertEqual(result["source_verification_status"], "unsat_verified")
                self.assertIsInstance(result["hashes"], dict)
                for key, contents in before.items(): self.assertEqual(Path(record[key]).read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()
