"""Exercise soft integer hints through the explicit SMT tactic's real C API.

The driver links the shared library built from the embedded solver's Z3 sources.
Set NIA_SMT_INITIAL_VALUES_LIB to select another build of that same library.
No user propagator is registered, and consumption is checked through Z3's
actual ``initialize ... := ...`` verbose messages rather than model coincidence.
"""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = Path(os.environ.get("NIA_SMT_INITIAL_VALUES_LIB", ROOT / "build/libz3.so")).resolve()

DRIVER = r'''
#include <z3++.h>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    try {
        if (argc != 5) return 2;
        const std::string backend = argv[1], fixture = argv[2];
        const unsigned arithmetic = unsigned(std::stoul(argv[3]));
        const bool hints = std::string(argv[4]) == "on";
        // Level 100 also exposes smt.configuring: hints must retain the
        // tactic's setup_and_check / static-feature auto-configuration path.
        z3::set_param("verbose", 100);
        z3::context context;
        unsigned descriptors = 0;
        auto factory = [&]() -> z3::solver {
            if (backend == "smt") {
                z3::tactic strategy(context, "smt");
                descriptors = strategy.get_param_descrs().size();
                return strategy.mk_solver();
            }
            if (backend == "simple")
                return z3::solver(context, z3::solver::simple{});
            return z3::solver(context);
        };
        z3::solver query = factory();
        query.set("arith.solver", arithmetic);
        query.set("timeout", 1000u);
        const z3::expr x = context.int_const("x"), y = context.int_const("y");
        if (fixture == "infeasible") {
            query.add(x >= 0 && x <= 20 && y >= 0 && y <= 20);
            query.add(x * y == 15);
            query.add((x - 3) * (x - 3) + (y - 5) * (y - 5) < 2);
        } else if (fixture == "strict") {
            query.add(x >= 0 && x < 1);
        } else if (fixture == "unsat") {
            query.add(x >= -2 && x <= 2 && x * x == 2);
        } else return 2;
        const std::string before = query.to_smt2();
        if (hints) {
            query.set_initial_value(x, context.int_val(fixture == "infeasible" ? 4 : 1));
            if (fixture == "infeasible") query.set_initial_value(y, context.int_val(4));
        }
        const bool unchanged = before == query.to_smt2();
        const auto answer = query.check();
        std::cout << "backend=" << backend << '\n'
                  << "descriptors=" << descriptors << '\n'
                  << "assertions_unchanged=" << unchanged << '\n'
                  << "answer=" << answer << '\n';
        if (answer == z3::sat) {
            const auto model = query.get_model();
            bool valid = true;
            for (const auto& hard : query.assertions())
                valid = valid && model.eval(hard, true).is_true();
            std::cout << "model_validated=" << valid << '\n'
                      << "x=" << model.eval(x, true) << '\n';
            if (fixture == "infeasible") std::cout << "y=" << model.eval(y, true) << '\n';
        }
        return 0;
    } catch (const z3::exception& error) {
        std::cerr << error.msg() << '\n';
        return 2;
    }
}
'''


class SmtInitialValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("CXX", "g++"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("a C++ compiler is required for the real Z3 API probe")
        if not LIBRARY.is_file():
            raise RuntimeError("build libz3 or set NIA_SMT_INITIAL_VALUES_LIB: " + str(LIBRARY))
        cls.temporary = tempfile.TemporaryDirectory(prefix="nia-smt-initial-values-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        source = directory / "probe.cpp"
        source.write_text(DRIVER)
        cls.probe = directory / "probe"
        compiled = subprocess.run([
            *compiler, "-std=gnu++17", "-O1", "-w",
            "-I" + str(ROOT / "source/src/api"),
            "-I" + str(ROOT / "source/src/api/c++"),
            str(source), str(LIBRARY), "-Wl,-rpath," + str(LIBRARY.parent),
            "-o", str(cls.probe),
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError(compiled.stdout + compiled.stderr)

    def run_probe(self, fixture="infeasible", *, backend="smt", arithmetic=2, hints=True):
        completed = subprocess.run([
            str(self.probe), backend, fixture, str(arithmetic), "on" if hints else "off",
        ], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        fields = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        self.assertEqual(fields["assertions_unchanged"], "1", completed.stdout)
        if fields["answer"] == "sat":
            self.assertEqual(fields["model_validated"], "1", completed.stdout)
        if backend == "smt":
            self.assertGreater(int(fields["descriptors"]), 0)
            self.assertIn("(smt.configuring)", completed.stderr)
        return fields, completed.stderr

    def assert_initialized(self, stderr, variable, value):
        self.assertRegex(stderr, rf"(?m)^initialize {re.escape(variable)} := {value}$")

    def test_explicit_smt_consumes_infeasible_integer_hints(self):
        for arithmetic in (2, 6):
            with self.subTest(arithmetic=arithmetic):
                fields, stderr = self.run_probe(arithmetic=arithmetic)
                self.assertEqual(fields["answer"], "sat")
                self.assertEqual((int(fields["x"]), int(fields["y"])), (3, 5))
                self.assert_initialized(stderr, "x", 4)
                self.assert_initialized(stderr, "y", 4)

    def test_incumbent_hint_does_not_block_a_strict_improvement(self):
        fields, stderr = self.run_probe("strict")
        self.assertEqual(fields["answer"], "sat")
        self.assertEqual(fields["x"], "0")
        self.assert_initialized(stderr, "x", 1)

    def test_bad_hint_does_not_change_unsat(self):
        for hints in (False, True):
            with self.subTest(hints=hints):
                fields, _ = self.run_probe("unsat", hints=hints)
                self.assertEqual(fields["answer"], "unsat")

    def test_without_hints_smt_never_initializes_a_value(self):
        fields, stderr = self.run_probe(hints=False)
        self.assertEqual(fields["answer"], "sat")
        self.assertNotRegex(stderr, r"(?m)^initialize ")

    def test_default_solver_without_hints_has_no_initialization(self):
        fields, stderr = self.run_probe(backend="default", hints=False)
        self.assertEqual(fields["answer"], "sat")
        self.assertNotRegex(stderr, r"(?m)^initialize ")

    def test_simple_solver_is_a_positive_logging_control(self):
        fields, stderr = self.run_probe(backend="simple")
        self.assertEqual(fields["answer"], "sat")
        self.assert_initialized(stderr, "x", 4)
        self.assert_initialized(stderr, "y", 4)


if __name__ == "__main__":
    unittest.main()
