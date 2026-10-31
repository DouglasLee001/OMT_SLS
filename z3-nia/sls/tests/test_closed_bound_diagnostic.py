"""Tests only for the isolated three-arm closed-bound diagnostic.

Default tests launch no process. The opt-in generated group requests at most
11.25 seconds of positive-arm windows and 6 seconds of unsupported parse-only
windows; unsupported inputs must never start a solver check.
"""
from pathlib import Path
from fractions import Fraction
import hashlib
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = Path("/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_033506_585455")
SDK = ARTIFACT / "source/python"
LIBRARY = SDK / "z3/lib/libz3.so"
INITIAL = ARTIFACT / "source/script/run_initial.py"
STRICT = ARTIFACT / "source/script/run_strict_bound.py"
FIRST = ARTIFACT / "source/z3-nia/experiment/strict_first_diagnostic.py"
MODULE = ROOT / "z3-nia/experiment/closed_bound_diagnostic.py"
ARMS = ["int_strict", "int_closed", "real_closed"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ClosedBoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_modules = set(sys.modules)
        cls.module = load(Path(os.environ.get("NIA_CLOSED_BOUND_MODULE", MODULE)), "closed_bound_diagnostic_under_test")

    def setUp(self):
        evidence = os.environ.get("NIA_CLOSED_BOUND_TEST_ARTIFACT_DIR")
        if evidence and self._testMethodName == "test_generated_three_arm_semantics_once":
            self.directory = Path(evidence).resolve() / "generated-cases"
            self.directory.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="closed-bound-test-")
            self.addCleanup(temporary.cleanup)
            self.directory = Path(temporary.name)

    def tearDown(self):
        self.assertFalse(any(name == "z3" or name.startswith("z3.")
                             for name in set(sys.modules) - self.old_modules),
                         "the parent diagnostic/test process must not import Z3")

    def config(self, **changes):
        values = dict(sdk_python=SDK, sdk_library=LIBRARY, initial_helper=INITIAL,
                      strict_helper=STRICT, first_helper=FIRST, timeout=3.0,
                      arm_seconds=.75)
        values.update(changes)
        return self.module.Config(**values)

    def record(self, text="(declare-const x Int)(assert (>= x 0))(minimize x)(check-sat)",
               M="0", direction="min", index=0):
        source = self.directory / f"source-{index}.smt2"
        source.write_text(text)
        return dict(index=index, filename=f"generated-{index}.smt2", category="generated",
                    source=str(source), source_sha256=digest(source), direction=direction, M=M)

    def no_proof(self, result):
        self.assertIs(result["diagnostic_only"], True)
        self.assertIs(result["is_optimal"], False)
        for arm in result["arms"]:
            self.assertIsNone(arm["answer"])
            self.assertIs(arm["integer_sat_verified"], False)
            self.assertIs(arm["integer_bound_unsat_verified"], False)

    def test_rotation_is_exact_and_reproducible(self):
        for index in range(12):
            expected = ARMS[index % 3:] + ARMS[:index % 3]
            self.assertEqual(self.module.arm_order(index), expected)
            self.assertEqual(self.module.arm_order(index), expected)

    def test_import_does_not_load_z3(self):
        self.assertFalse(any(name == "z3" or name.startswith("z3.")
                             for name in set(sys.modules) - self.old_modules))

    def test_stop_leaves_all_arms_unchecked(self):
        record = self.record(); stop = threading.Event(); stop.set()
        source = Path(record["source"]).read_bytes()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("stop must not launch")) as launch:
            result = self.module.run_case(record, self.config(), self.directory / "stopped", stop)
        launch.assert_not_called()
        self.assertEqual(result["status"], "interrupted")
        self.no_proof(result)
        self.assertEqual(Path(record["source"]).read_bytes(), source)

    def test_source_sha_mismatch_is_rejected_before_child(self):
        record = self.record(); record["source_sha256"] = "0" * 64
        with mock.patch("subprocess.Popen", side_effect=AssertionError("wrong identity must not launch")) as launch:
            result = self.module.run_case(record, self.config(), self.directory / "bad-sha", threading.Event())
        launch.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.no_proof(result)

    def test_invalid_timing_is_rejected_before_child(self):
        for index, values in enumerate(({"timeout": 0}, {"timeout": float("nan")},
                                        {"arm_seconds": 0}, {"arm_seconds": float("inf")})):
            with self.subTest(values=values):
                record = self.record(index=index)
                with mock.patch("subprocess.Popen", side_effect=AssertionError("invalid config must not launch")) as launch:
                    result = self.module.run_case(record, self.config(**values),
                                                  self.directory / f"bad-config-{index}", threading.Event())
                launch.assert_not_called()
                self.assertEqual(result["status"], "error")
                self.no_proof(result)

    def test_invalid_exact_M_and_direction_are_rejected(self):
        for index, (M, direction) in enumerate((("nan", "min"), ("1.5", "min"),
                                               ("-oo", "min"), ("0", "minimize"))):
            with self.subTest(M=M, direction=direction):
                record = self.record(M=M, direction=direction, index=index)
                with mock.patch("subprocess.Popen", side_effect=AssertionError("invalid metadata must not launch")) as launch:
                    result = self.module.run_case(record, self.config(),
                                                  self.directory / f"bad-meta-{index}", threading.Event())
                launch.assert_not_called()
                self.assertEqual(result["status"], "error")
                self.no_proof(result)

    def test_existing_output_directory_is_preserved(self):
        record = self.record(); occupied = self.directory / "occupied"; occupied.mkdir()
        sentinel = occupied / "keep.txt"; sentinel.write_bytes(b"do not overwrite\n")
        before = {path.name: path.read_bytes() for path in occupied.iterdir()}
        with mock.patch("subprocess.Popen", side_effect=AssertionError("occupied output must not launch")) as launch:
            result = self.module.run_case(record, self.config(), occupied, threading.Event())
        launch.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.no_proof(result)
        self.assertEqual({path.name: path.read_bytes() for path in occupied.iterdir()}, before)

    def mapped_libraries(self, library, text):
        def read_maps(path, *args, **kwargs):
            self.assertEqual(path, Path("/proc/self/maps"))
            return text
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=read_maps):
            with mock.patch("subprocess.Popen", side_effect=AssertionError("maps audit must not launch")) as launch:
                try:
                    return self.module._mapped_libraries(library)
                finally:
                    launch.assert_not_called()

    def test_mapped_library_accepts_escaped_space_and_deduplicates_segments(self):
        directory = self.directory / "sdk with space"; directory.mkdir()
        library = directory / "libz3.so"; library.write_bytes(b"identity-only fixture")
        escaped = str(library).replace(" ", r"\040")
        maps = (f"1000-2000 r--p 00000000 08:01 {library.stat().st_ino} {escaped}\n"
                f"2000-3000 r-xp 00001000 08:01 {library.stat().st_ino} {escaped}\n"
                "3000-4000 r--p 00000000 08:01 1 /usr/lib/libc.so.6\n")
        self.assertEqual(self.mapped_libraries(library, maps), [str(library)])

    def test_mapped_library_rejects_a_second_different_inode(self):
        library = self.directory / "libz3.so"; library.write_bytes(b"expected identity")
        other_dir = self.directory / "other"; other_dir.mkdir()
        other = other_dir / "libz3.so"; other.write_bytes(b"foreign identity")
        self.assertNotEqual(library.stat().st_ino, other.stat().st_ino)
        maps = (f"1000-2000 r--p 00000000 08:01 {library.stat().st_ino} {library}\n"
                f"2000-3000 r--p 00000000 08:01 {other.stat().st_ino} {other}\n")
        with self.assertRaisesRegex(ValueError, "mapped libz3 differs"):
            self.mapped_libraries(library, maps)

    def test_mapped_library_rejects_deleted_mapping(self):
        library = self.directory / "libz3.so"; library.write_bytes(b"expected identity")
        maps = f"1000-2000 r--p 00000000 08:01 {library.stat().st_ino} {library} (deleted)\n"
        with self.assertRaisesRegex(ValueError, "mapped libz3 differs"):
            self.mapped_libraries(library, maps)

    def test_mapped_library_rejects_empty_mapping_set(self):
        library = self.directory / "libz3.so"; library.write_bytes(b"expected identity")
        for text in ("", "1000-2000 r--p 00000000 08:01 1 /usr/lib/libc.so.6\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "no mapped frozen libz3"):
                self.mapped_libraries(library, text)

    @unittest.skipUnless(os.environ.get("NIA_CLOSED_BOUND_REAL_TOYS") == "1", "real generated group is explicit opt-in")
    def test_generated_three_arm_semantics_once(self):
        self.assertEqual(digest(LIBRARY), "01e8020b2e3283842f25e9dea77663a3a4af058c47c690ea065ccc0fe31443ef")
        big = 1 << 100
        square = "(* (- (* 2 x) 1) (- (* 2 x) 1))"
        boolean = ("(declare-const x Int)(declare-const b Bool)(declare-const c Bool)"
                   "(assert (= c (not b)))(assert (distinct b c))(assert (xor b c))"
                   "(assert (=> b (not c)))"
                   f"(assert (= x (ite b {big} {big + 2})))"
                   f"(assert (or (and b (= x {big})) (and (not b) (= x {big + 2}))))")
        # Source incumbent M=1 is feasible at integer x=0 or x=1 in the gap toy.
        positive = [
            (f"(declare-const x Int)(assert (>= x 0))(assert (<= x 1))(minimize {square})(check-sat)", "1", "min", ["unsat", "unsat", "sat"]),
            ("(declare-const x Int)(assert (>= (* 2 x) (- 1)))(minimize x)(check-sat)", "0", "min", ["unsat"] * 3),
            (boolean + "(minimize x)(check-sat)", str(big + 2), "min", ["sat"] * 3),
            (boolean + "(maximize x)(check-sat)", str(big), "max", ["sat"] * 3),
            ("(assert true)(minimize 7)(check-sat)", "7", "min", ["unsat"] * 3),
        ]
        start_index = int(os.environ.get("NIA_CLOSED_BOUND_TEST_START_AT", "0"))
        self.assertTrue(0 <= start_index <= len(positive), "test-only resume index is outside the original group")
        strict_parser = None
        for index, (source, M, direction, answers) in enumerate(positive):
            if index < start_index:
                continue
            with self.subTest(index=index):
                record = self.record(source, M, direction, index); before = Path(record["source"]).read_bytes()
                result = self.module.run_case(record, self.config(), self.directory / f"positive-{index}", threading.Event())
                print("CLOSED_BOUND_GENERATED", index, result, flush=True)
                self.assertEqual(result["status"], "complete", result)
                self.assertIs(result["diagnostic_only"], True); self.assertIs(result["is_optimal"], False)
                self.assertEqual([arm["arm"] for arm in result["arms"]], self.module.arm_order(index))
                by_arm = {arm["arm"]: arm for arm in result["arms"]}
                for arm_name, expected in zip(ARMS, answers):
                    arm = by_arm[arm_name]
                    self.assertEqual(arm["answer"], expected, arm)
                    self.assertEqual(digest(arm["query_path"]), arm["query_sha256"])
                    self.assertIs(arm["integer_sat_verified"], expected == "sat" and arm_name != "real_closed")
                    self.assertIs(arm["integer_bound_unsat_verified"], expected == "unsat")
                    query = Path(arm["query_path"]).read_text()
                    if arm_name == "real_closed":
                        self.assertNotIn("(to_real ", query); self.assertNotIn("(to_int ", query)
                        if index != 4: self.assertIn("Real", query)
                    if index == 1 and arm_name == "real_closed":
                        if strict_parser is None: strict_parser = load(STRICT, "closed_test_pure_smt_parser")
                        commands = strict_parser._commands(query)
                        def walk(node):
                            if not isinstance(node, list): return
                            yield node
                            for child in node: yield from walk(child)
                        forms = list(walk(commands))
                        self.assertFalse(any(form and form[0] == "<" for form in forms), "Real arm must use a closed unit bound")
                        def number(node):
                            if isinstance(node, str):
                                try: return Fraction(node)
                                except ValueError: return None
                            if len(node) == 2 and node[0] == "-":
                                value = number(node[1]); return -value if value is not None else None
                            return None
                        target = next(item["real_name"] for item in arm["real_constant_map"] if item["source_name"] == "x")
                        self.assertTrue(any(len(form) == 3 and
                                            ((form[0] == "<=" and form[1] == target and number(form[2]) == -1) or
                                             (form[0] == ">=" and form[2] == target and number(form[1]) == -1)) for form in forms),
                                        "M=0 Real closed query must explicitly use objective<=-1, not objective<0")
                self.assertEqual(Path(record["source"]).read_bytes(), before)
        unsupported = [
            "(declare-const x Int)(assert (= (div x 2) 0))(minimize x)(check-sat)",
            "(declare-const x Int)(declare-fun f (Int) Int)(assert (= (f x) 0))(minimize x)(check-sat)",
            "(declare-const x Int)(assert (= (to_int (to_real x)) x))(minimize x)(check-sat)",
            "(declare-const x Int)(assert (= (^ x 2) 0))(minimize x)(check-sat)",
        ]
        for offset, source in enumerate(unsupported):
            index = offset + len(positive)
            with self.subTest(unsupported=offset):
                record = self.record(source, "0", "min", index)
                result = self.module.run_case(record, self.config(timeout=2, arm_seconds=.5),
                                              self.directory / f"unsupported-{offset}", threading.Event())
                print("CLOSED_BOUND_UNSUPPORTED", offset, result, flush=True)
                self.assertEqual(result["status"], "complete", result)
                self.assertEqual(len(result["arms"]), 3)
                self.no_proof(result)
                for arm in result["arms"]:
                    self.assertEqual(arm["status"], "unsupported")
                    self.assertIs(arm["check_started"], False)
                    self.assertEqual(arm["check_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
