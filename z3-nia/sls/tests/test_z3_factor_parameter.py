"""Exercise the local factor parameter against the actual built Z3 5.1 runtime."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "competitor/z3-5.1.0/build"


class Z3FactorParameterTests(unittest.TestCase):
    def run_z3(self, code):
        bootstrap = r'''
import builtins
from pathlib import Path
import sys

build = Path(sys.argv[1]).resolve()
loads = []
sys.addaudithook(lambda event, args: loads.append(args[0])
                if event == "ctypes.dlopen" and args[0] and "libz3" in args[0] else None)
builtins.Z3_LIB_DIRS = [str(build)]
sys.path.insert(0, str(build / "python"))
import z3

assert z3.get_version()[:3] == (5, 1, 0), z3.get_version_string()
assert Path(z3.__file__).resolve().parent == build / "python/z3", z3.__file__
assert len(loads) == 1, loads
assert Path(loads[0]).resolve() == (build / "libz3.so").resolve(), loads

x, y, z = z3.Ints("x y z")

def rewrite(atom, **params):
    goal = z3.Goal()
    goal.add(atom)
    tactic = z3.With(z3.Tactic("simplify"), **params) if params else z3.Tactic("simplify")
    result = tactic(goal)
    assert len(result) == 1, result
    return result[0].as_expr()
'''
        result = subprocess.run(
            [sys.executable, "-B", "-c", bootstrap + textwrap.dedent(code), str(BUILD)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_retains_51_product_factoring(self):
        self.run_z3('''
            result = rewrite(x * y == 0)
            assert z3.is_or(result), result.sexpr()
            assert result.eq(z3.Or(x == 0, y == 0)), result.sexpr()
        ''')

    def test_local_parameter_is_accepted_and_documented_as_default_true(self):
        self.run_z3('''
            import contextlib
            import io

            # With validates the key; simplify(expr, **kwargs) silently ignores unknown keys.
            z3.With(z3.Tactic("simplify"), factor_nonlinear=False)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                z3.Tactic("simplify").help()
            lines = [line for line in output.getvalue().splitlines()
                     if line.startswith("factor_nonlinear ")]
            assert len(lines) == 1, output.getvalue()
            assert "(bool)" in lines[0] and "(default: true)" in lines[0], lines[0]
        ''')

    def test_local_false_preserves_product_and_common_factor_atoms(self):
        self.run_z3('''
            for term in (x * y, x * y + x * z):
                for atom in (term == 0, term <= 0, term >= 0):
                    result = rewrite(atom, factor_nonlinear=False)
                    assert result.eq(atom), (atom.sexpr(), result.sexpr())
                    assert z3.is_or(rewrite(atom)), atom.sexpr()
            # The new switch leaves earlier arithmetic normalization active.
            result = rewrite(2 * x + 4 <= 10, factor_nonlinear=False)
            assert result.eq(x <= 3), result.sexpr()
        ''')

    def test_local_false_does_not_change_later_default_rewrites(self):
        self.run_z3('''
            atom = x * y == 0
            before = rewrite(atom)
            preserved = rewrite(atom, factor_nonlinear=False)
            after = rewrite(atom)
            assert preserved.eq(atom), preserved.sexpr()
            assert z3.is_or(before) and after.eq(before), (before, after)
            assert z3.simplify(atom).eq(before), z3.simplify(atom)
            assert rewrite(atom, factor_nonlinear=True).eq(before)
        ''')

    def test_both_modes_match_independent_finite_arithmetic_truth(self):
        self.run_z3('''
            from itertools import product
            from operator import eq, ge, le

            cases = [
                (x * y, lambda a, b, c: a * b),
                (x * y + x * z, lambda a, b, c: a * b + a * c),
            ]
            for term, evaluate in cases:
                for relation in (eq, le, ge):
                    atom = relation(term, 0)
                    rewrites = (rewrite(atom), rewrite(atom, factor_nonlinear=False))
                    for values in product(range(-2, 3), repeat=3):
                        substitutions = tuple((var, z3.IntVal(value))
                                              for var, value in zip((x, y, z), values))
                        expected = relation(evaluate(*values), 0)
                        for rewritten in rewrites:
                            actual = z3.simplify(z3.substitute(rewritten, *substitutions))
                            assert z3.is_true(actual) if expected else z3.is_false(actual), (
                                atom.sexpr(), rewritten.sexpr(), values, expected, actual)
        ''')


if __name__ == "__main__":
    unittest.main()
