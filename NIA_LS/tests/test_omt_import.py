"""Regression tests for OMT -> native import, without running NIA_LS search.

Run from the repository root:
    python3 -m unittest discover -s NIA_LS/tests -p test_omt_import.py -v

The optional representative dataset checks are enabled with
NIA_LS_DATASET_SMOKE=1.  Only temporary directories receive generated files.
The fork is used as a frontend; the independent finite-domain oracle below
uses the separately built official Z3 Python SDK, never the fork's solver.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
NIA = REPO / "NIA_LS"
CONVERTER = NIA / "converter" / "omt_to_ls.py"
NATIVE = NIA / "src" / "nia_ls"
SDK = REPO / "competitor" / "z3-5.1.0" / "install" / "lib"
for _site_packages in sorted(SDK.glob("python*/site-packages")):
    sys.path.insert(0, str(_site_packages))


def smt(declarations, assertions, objective, direction="minimize"):
    return "\n".join([
        "(set-logic QF_NIA)", declarations, assertions,
        f"({direction} {objective})", "(check-sat)", "(exit)", "",
    ])


def decode_native(text, z3):
    """An independent, deliberately small decoder for the public text format."""
    lines = iter(line.strip() for line in text.splitlines() if line.strip())

    def header(name):
        fields = next(lines).split()
        if not fields or fields[0] != name:
            raise AssertionError(f"expected {name}: {fields!r}")
        return fields[1:]

    if header("NIA_LS_OMT") != ["1"]:
        raise AssertionError("unexpected native format version")
    status, = header("STATUS")
    source, = header("SOURCE")
    original_objective, = header("ORIGINAL_OBJECTIVE")
    direction, = header("DIRECTION")
    count, = header("VARIABLES")
    variables = {}
    names = {}
    for _ in range(int(count)):
        index, kind, encoded_name = next(lines).split()
        index = int(index)
        name = bytes.fromhex(encoded_name).decode("utf-8")
        if kind == "I":
            variable = z3.Int(f"native_i_{index}")
        elif kind == "B":
            variable = z3.Bool(f"native_b_{index}")
        else:
            raise AssertionError(f"unexpected native variable sort {kind}")
        if index in variables:
            raise AssertionError("duplicate native variable")
        variables[index] = variable
        names[index] = name

    def polynomial(constant, term_count):
        terms = [z3.IntVal(int(constant))]
        for _ in range(int(term_count)):
            fields = next(lines).split()
            coefficient, factors = map(int, fields[:2])
            if len(fields) != 2 + 2 * factors:
                raise AssertionError("malformed polynomial term")
            term = z3.IntVal(coefficient)
            for offset in range(2, len(fields), 2):
                index, exponent = map(int, fields[offset:offset + 2])
                # Expand as multiplication, rather than Z3's Real-sorted power.
                for _ in range(exponent):
                    term = term * variables[index]
            terms.append(term)
        return z3.Sum(terms)

    count, = header("LITERALS")
    literals = {}
    for _ in range(int(count)):
        fields = next(lines).split()
        index, kind = int(fields[0]), fields[1]
        if kind == "B":
            literal = variables[int(fields[2])]
        else:
            expression = polynomial(fields[2], fields[3])
            if kind == "L":
                literal = expression <= 0
            elif kind == "E":
                literal = expression == 0
            else:
                raise AssertionError(f"unexpected native literal {kind}")
        if index in literals:
            raise AssertionError("duplicate native literal")
        literals[index] = literal
    count, = header("CLAUSES")
    constraints = []
    for _ in range(int(count)):
        fields = list(map(int, next(lines).split()))
        if fields[0] != len(fields) - 1:
            raise AssertionError("malformed native clause")
        clause = [literals[x] if x > 0 else z3.Not(literals[-x])
                  for x in fields[1:]]
        constraints.append(z3.Or(clause))
    constant, term_count = header("OBJECTIVE")
    objective = polynomial(constant, term_count)
    if header("END") or next(lines, None) is not None:
        raise AssertionError("trailing native input")
    if status == "unsat":
        constraints.append(z3.BoolVal(False))
    elif status != "ready":
        raise AssertionError(f"unexpected native status {status}")
    return {
        "status": status, "direction": direction, "names": names,
        "constraints": constraints, "objective": objective,
        "objective_constant": constant, "objective_terms": int(term_count),
        "source": source, "original_objective": original_objective,
    }


class OmtImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (CONVERTER, NATIVE):
            if not path.is_file():
                raise RuntimeError(f"required import component missing: {path}")
        import z3
        cls.z3 = z3
        # Catch accidental use of the converter fork's incompatible Python SDK.
        if "competitor/z3-5.1.0" not in str(Path(z3.__file__).resolve()):
            raise RuntimeError(f"expected local official Z3 SDK, got {z3.__file__}")

    def convert_text(self, source, preprocess=True):
        # The frontend must load its own fork library.  Keep that library in a
        # child process, isolated from the official SDK used by the oracle.
        with tempfile.TemporaryDirectory(prefix="nia-omt-convert-") as directory:
            path = Path(directory) / "input.smt2"
            path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(CONVERTER), str(path), "--preprocess=" + ("on" if preprocess else "off")],
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def native_summary(self, native_text, *, success=True):
        with tempfile.TemporaryDirectory(prefix="nia-omt-reader-") as directory:
            path = Path(directory) / "case.nia"
            path.write_text(native_text, encoding="utf-8")
            completed = subprocess.run(
                [str(NATIVE), str(path), "--import-only"],
                text=True, capture_output=True, timeout=20,
            )
        if not success:
            # A crash, signal, or OOM kill must not count as input rejection.
            self.assertEqual(completed.returncode, 2, (completed.stdout, completed.stderr))
            return completed
        self.assertEqual(completed.returncode, 0, completed.stderr)
        try:
            summary = json.loads(completed.stdout)
        except (ValueError, TypeError):
            self.fail(f"import-only must print one JSON object: {completed.stdout!r}")
        self.assertIsInstance(summary, dict)
        return summary

    def convert(self, source, preprocess=True):
        native = self.convert_text(source, preprocess)
        self.assertIsInstance(native, str)
        decoded = decode_native(native, self.z3)
        summary = self.native_summary(native)
        expected_status = "preprocessing_unsat" if decoded["status"] == "unsat" else "imported"
        self.assertEqual(summary["status"], expected_status)
        self.assertEqual(summary["direction"], decoded["direction"])
        self.assertEqual(summary["objective_constant"], decoded["objective_constant"])
        self.assertEqual(summary["objective_terms"], decoded["objective_terms"])
        self.assertIs(summary["native_solver_state"], True)
        self.assertIs(summary["search_started"], False)
        return native, decoded, summary

    def objective_values(self, constraints, objective):
        solver = self.z3.Solver()
        solver.set(timeout=5000)
        solver.add(*constraints)
        values = set()
        while True:
            status = solver.check()
            if status == self.z3.unsat:
                return values
            self.assertEqual(status, self.z3.sat, solver.reason_unknown())
            value = solver.model().eval(objective, model_completion=True)
            self.assertTrue(self.z3.is_int_value(value), str(value))
            value = value.as_long()
            values.add(value)
            self.assertLessEqual(len(values), 400, "finite-domain fixture became unbounded")
            solver.add(objective != value)

    def assert_equivalent_objective_values(self, source, preprocess=True):
        original = self.z3.Optimize()
        original.from_string(source)
        self.assertEqual(len(original.objectives()), 1)
        native, decoded, summary = self.convert(source, preprocess)
        objective = original.objectives()[0]
        if decoded["direction"] == "max":
            objective = -objective  # Optimize exposes maximization in min form.
        expected = self.objective_values(original.assertions(), objective)
        actual = self.objective_values(decoded["constraints"], decoded["objective"])
        self.assertEqual(actual, expected)
        return native, decoded, summary, actual

    def test_eliminated_objective_alias_keeps_all_values(self):
        source = smt(
            "(declare-fun x () Int) (declare-fun cost () Int)",
            "(assert (<= (- 2) x)) (assert (<= x 2)) "
            "(assert (= cost (* x x)))", "cost",
        )
        for preprocess in (False, True):
            with self.subTest(preprocess=preprocess):
                _, _, _, values = self.assert_equivalent_objective_values(source, preprocess)
                self.assertEqual(values, {0, 1, 4})

    def test_frontend_does_not_call_fork_solver_or_optimizer_check(self):
        source = smt(
            "(declare-fun x () Int) (declare-fun cost () Int)",
            "(assert (<= (- 2) x)) (assert (<= x 2)) "
            "(assert (= cost (* x x)))", "cost",
        )
        child_code = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(CONVERTER.parent)!r})",
            "import omt_to_ls",
            "z3 = omt_to_ls.load_z3()",
            "def forbidden_check(*args, **kwargs):",
            "    raise AssertionError('the OMT frontend must not invoke fork solving')",
            "z3.Solver.check = forbidden_check",
            "z3.Optimize.check = forbidden_check",
            "sys.stdout.write(omt_to_ls.convert_text(sys.stdin.read(), "
            "source_name='no fork solving regression', preprocess=True))",
        ])
        completed = subprocess.run(
            [sys.executable, "-c", child_code], input=source,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith("NIA_LS_OMT 1\n"), completed.stdout)
        decoded = decode_native(completed.stdout, self.z3)
        self.assertEqual(self.objective_values(decoded["constraints"], decoded["objective"]), {0, 1, 4})
        summary = self.native_summary(completed.stdout)
        self.assertEqual(summary["status"], "imported")
        self.assertIs(summary["search_started"], False)

    def test_fixed_objective_is_kept_as_exact_constant(self):
        source = smt(
            "(declare-fun x () Int) (declare-fun cost () Int)",
            "(assert (= x 7)) (assert (= cost (* x x)))", "cost",
        )
        _, decoded, summary, values = self.assert_equivalent_objective_values(source)
        self.assertEqual(values, {49})
        self.assertEqual(decoded["objective_constant"], "49")
        self.assertEqual(summary["objective_terms"], 0)

    def test_direct_polynomial_objective_and_maximize_sign(self):
        for direction in ("minimize", "maximize"):
            for preprocess in (False, True):
                with self.subTest(direction=direction, preprocess=preprocess):
                    source = smt(
                        "(declare-fun x () Int) (declare-fun y () Int)",
                        "(assert (<= (- 2) x)) (assert (<= x 2)) "
                        "(assert (<= 0 y)) (assert (<= y 2))",
                        "(+ (* x x) (* (- 3) y) 7)", direction,
                    )
                    _, decoded, _, _ = self.assert_equivalent_objective_values(source, preprocess)
                    self.assertEqual(decoded["direction"], "max" if direction == "maximize" else "min")

    def test_target_used_only_by_objective_is_not_dropped(self):
        for direction in ("minimize", "maximize"):
            with self.subTest(direction=direction):
                source = smt("(declare-fun target () Int)", "", "target", direction)
                _, decoded, summary = self.convert(source)
                self.assertEqual(summary["objective_terms"], 1)
                self.assertIn("target", decoded["names"].values())
                # Add a test-only bound to the exported objective, not the source.
                values = self.objective_values(
                    decoded["constraints"] + [decoded["objective"] >= -2, decoded["objective"] <= 2],
                    decoded["objective"],
                )
                self.assertEqual(values, {-2, -1, 0, 1, 2})

    def test_bool_ite_and_guarded_arithmetic(self):
        source = smt(
            "(declare-fun b () Bool) (declare-fun x () Int) (declare-fun cost () Int)",
            "(assert (<= (- 2) x)) (assert (<= x 2)) "
            "(assert (or b (<= x 0))) "
            "(assert (= cost (ite b (* x x) (+ x 5))))", "cost",
        )
        for preprocess in (False, True):
            with self.subTest(preprocess=preprocess):
                _, _, _, values = self.assert_equivalent_objective_values(source, preprocess)
                self.assertEqual(values, {0, 1, 3, 4, 5})

    def test_quoted_unicode_and_space_names(self):
        source = smt(
            "(declare-fun |x with spaces| () Int) (declare-fun |费用 α| () Int)",
            "(assert (<= (- 1) |x with spaces|)) (assert (<= |x with spaces| 1)) "
            "(assert (= |费用 α| (+ (* |x with spaces| |x with spaces|) 2)))",
            "|费用 α|",
        )
        for preprocess in (False, True):
            with self.subTest(preprocess=preprocess):
                _, _, _, values = self.assert_equivalent_objective_values(source, preprocess)
                self.assertEqual(values, {2, 3})

    def test_degree_five_and_coefficients_beyond_int64(self):
        coefficient = 2 ** 80 + 7
        source = smt(
            "(declare-fun x () Int)",
            "(assert (<= (- 2) x)) (assert (<= x 2))",
            f"(+ (* {coefficient} x x x x x) 11)", "maximize",
        )
        _, _, _, values = self.assert_equivalent_objective_values(source)
        self.assertEqual(values, {coefficient * x ** 5 + 11 for x in range(-2, 3)})

    def test_preprocessing_unsat_keeps_direction_metadata(self):
        for direction in ("minimize", "maximize"):
            with self.subTest(direction=direction):
                source = smt("(declare-fun cost () Int)", "(assert false)", "cost", direction)
                _, _, summary, values = self.assert_equivalent_objective_values(source)
                self.assertEqual(summary["status"], "preprocessing_unsat")
                self.assertEqual(values, set())

    def test_cli_writes_only_native_file_and_preserves_semantics(self):
        source = smt(
            "(declare-fun x () Int)", "(assert (<= 0 x)) (assert (<= x 2))", "(* x x)",
        )
        for preprocess in ("on", "off"):
            with self.subTest(preprocess=preprocess), tempfile.TemporaryDirectory(prefix="nia-omt-cli-") as directory:
                directory = Path(directory)
                input_path, output_path = directory / "input.smt2", directory / "output.nia"
                input_path.write_text(source, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(CONVERTER), str(input_path), "-o", str(output_path),
                     f"--preprocess={preprocess}"],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual({x.name for x in directory.iterdir()}, {"input.smt2", "output.nia"})
                decoded = decode_native(output_path.read_text(), self.z3)
                self.assertEqual(self.objective_values(decoded["constraints"], decoded["objective"]), {0, 1, 4})
                self.native_summary(output_path.read_text())

    def test_native_rejects_truncation_bad_indices_overflow_and_trailing_data(self):
        source = smt("(declare-fun x () Int)", "", "x")
        native = self.convert_text(source, preprocess=False)
        lines = native.splitlines()
        objective_index = next(i for i, line in enumerate(lines) if line.startswith("OBJECTIVE "))
        fields = lines[objective_index + 1].split()
        self.assertEqual(int(fields[1]), 1)
        bad_index = lines[:]
        bad_fields = fields[:]
        bad_fields[2] = "999999"
        bad_index[objective_index + 1] = " ".join(bad_fields)
        overflow_coefficient = lines[:]
        overflow_fields = fields[:]
        overflow_fields[0] = str(2 ** 127)
        overflow_coefficient[objective_index + 1] = " ".join(overflow_fields)
        overflow_constant = lines[:]
        overflow_constant[objective_index] = f"OBJECTIVE {2 ** 127} 1"
        variable_index = next(i for i, line in enumerate(lines) if line.startswith("VARIABLES "))
        sparse_variable = lines[:]
        sparse_variable[variable_index] = "VARIABLES 2147483600"
        declaration = sparse_variable[variable_index + 1].split()
        declaration[0] = "2147483599"
        sparse_variable[variable_index + 1] = " ".join(declaration)
        literal_index = next(i for i, line in enumerate(lines) if line.startswith("LITERALS "))
        sparse_literal = lines[:]
        sparse_literal[literal_index] = "LITERALS 2147483600"
        sparse_literal.insert(literal_index + 1, "2147483600 B 0")
        variants = {
            "truncated": "\n".join(lines[:-1]) + "\n",
            "undefined-variable": "\n".join(bad_index) + "\n",
            "coefficient-overflow": "\n".join(overflow_coefficient) + "\n",
            "constant-overflow": "\n".join(overflow_constant) + "\n",
            "trailing-data": native.rstrip() + "\nTRAILING\n",
            "sparse-variable-id-before-allocation": "\n".join(sparse_variable) + "\n",
            "sparse-literal-id-before-allocation": "\n".join(sparse_literal) + "\n",
        }
        for label, text in variants.items():
            with self.subTest(label=label):
                self.native_summary(text, success=False)

    def test_frontend_rejects_out_of_range_integer_without_silent_truncation(self):
        source = smt("(declare-fun x () Int)", "", f"(* {2 ** 127} x)")
        with tempfile.TemporaryDirectory(prefix="nia-omt-range-") as directory:
            input_path = Path(directory) / "overflow.smt2"
            output_path = Path(directory) / "overflow.nia"
            input_path.write_text(source)
            completed = subprocess.run(
                [sys.executable, str(CONVERTER), str(input_path), "-o", str(output_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertRegex(completed.stderr.lower(), r"128|range|overflow")

    def test_commands_after_check_cannot_change_the_imported_query(self):
        source = smt(
            "(declare-fun cost () Int)",
            "(assert (<= 0 cost)) (assert (<= cost 2))", "cost",
        )
        for mutation in [
            "(assert (= cost 1))", "(declare-fun extra () Int)",
            "(minimize cost)", "(set-logic QF_NIA)",
            "(set-option :produce-models true)", "(check-sat)", "(push 1)",
        ]:
            with self.subTest(command=mutation), tempfile.TemporaryDirectory(prefix="nia-omt-scope-") as directory:
                path = Path(directory) / "scope.smt2"
                path.write_text(source.replace("(exit)", mutation + "\n(exit)"))
                completed = subprocess.run(
                    [sys.executable, str(CONVERTER), str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertRegex(completed.stderr.lower(), r"after check-sat|query state|single")

    def test_read_only_query_trailers_and_queryless_description_are_supported(self):
        source = smt(
            "(declare-fun cost () Int)",
            "(assert (<= 0 cost)) (assert (<= cost 2))", "cost",
        )
        readonly = source.replace(
            "(exit)", '(get-model)\n(get-objectives)\n(get-value (cost))\n'
            '(echo "ignored frontend output")\n(set-info :source "test")\n(exit)',
        )
        queryless = source.replace("(check-sat)\n", "")
        for label, description in [("read-only-trailer", readonly), ("no-query", queryless)]:
            with self.subTest(description=label):
                _, _, _, values = self.assert_equivalent_objective_values(description)
                self.assertEqual(values, {0, 1, 2})

    def test_omt_cannot_silently_fall_through_to_feasibility_search(self):
        source = smt("(declare-fun cost () Int)", "(assert (= cost 2))", "cost")
        native = self.convert_text(source, preprocess=True)
        with tempfile.TemporaryDirectory(prefix="nia-omt-search-guard-") as directory:
            path = Path(directory) / "case.nia"
            path.write_text(native)
            completed = subprocess.run(
                [str(NATIVE), str(path)], capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertNotIn("sat", completed.stdout.split())
        self.assertRegex(completed.stderr.lower(), r"optim|import.only")

    @unittest.skipUnless(os.environ.get("NIA_LS_DATASET_SMOKE") == "1", "optional dataset smoke")
    def test_existing_benchmark_representatives_import(self):
        original = Path("/data/shared/lbh/OMT/benchmarks/QF_NIA")
        paths = [original / relative for relative in [
            "calypto/problem-005895.cvc.1@1.smt2",
            "AProVE/aproveSMT3634932254166362860@0.smt2",
            "leipzig/term-PGdOdF@4.smt2",
            "mcm/45@0.smt2",
            "20170427-VeryMax/SAT14/88@0.smt2",
            "LassoRanker/Swingers_false-termination.c_Iteration1_Loop+nonterminationTemplate_0@2.smt2",
            "UltimateLassoRanker/PodelskiRybalchenko-2004VMCAI-Ex2-alloca_false-termination.c.i_Iteration1_Lasso+nonterminationTemplate@4.smt2",
        ]]
        benchmark_root = REPO / "new_benchmark"
        for family, pattern in [
            ("portfolio", "gram-*.smt2"),
            ("portfolio", "smallinvDAXr5b200-220--e969e11a521d.smt2"),
            ("cyclic", "tiny-a--7ceaa9761184.smt2"),
            ("packing", "squares-n02--8d5fbe62518a.smt2"),
            ("design", "tiny-cauchy-binet--f57a37aa3a23.smt2"),
            ("design", "integer-d5-m14-N25-seed1100-cauchy-binet--372eccef4df0.smt2"),
        ]:
            matching = list((benchmark_root / family / "instance").glob(pattern))
            self.assertTrue(matching, (family, pattern))
            paths.append(min(matching, key=lambda path: path.stat().st_size))
        for path in paths:
            with self.subTest(benchmark=str(path)):
                self.assertTrue(path.is_file(), path)
                native = self.convert_text(path.read_text(), preprocess=True)
                summary = self.native_summary(native)
                self.assertIn(summary["status"], {"imported", "preprocessing_unsat"})


if __name__ == "__main__":
    unittest.main()
