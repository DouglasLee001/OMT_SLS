"""Exact matrix checks and exhaustive allocation regressions."""
import copy
import importlib.util
import itertools
import pathlib
import random
import unittest
from fractions import Fraction


def load_model():
    path = pathlib.Path(__file__).with_name("model.py")
    spec = importlib.util.spec_from_file_location("design_model_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allocations(total, size):
    if size == 1:
        yield [total]
    else:
        for head in range(total + 1):
            for rest in allocations(total - head, size - 1):
                yield [head] + rest


class DesignSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model()

    def test_tiny_all_ten_allocations_unique_optimum(self):
        case = self.model.make("tiny")[0]
        values = []
        for counts in allocations(3, 3):
            direct = self.model.determinant_value(case["data"], counts)
            self.assertEqual(direct, self.model.polynomial_value(case["data"], counts))
            if direct > 0:
                values.append((self.model.check(case["data"], {"counts": counts})["cost"], counts))
            else:
                with self.assertRaises(ValueError):
                    self.model.check(case["data"], {"counts": counts})
        self.assertEqual(len(list(allocations(3, 3))), 10)
        self.assertEqual([c for value, c in values if value == 3], [[1, 1, 1]])
        self.assertEqual(max(value for value, _ in values), 3)

    def test_rational_scale_is_sixteen(self):
        case = self.model.make("tiny-rational")[0]
        self.assertEqual(case["metadata"]["objective"]["scale"], "16")
        checked = self.model.check(case["data"], {"counts": [1, 1, 1]})
        self.assertEqual(checked["cost"], 3)
        self.assertEqual(checked["determinant"], {"num": "3", "den": "16"})

    def test_reference_three_complete_462_allocation_optimum(self):
        case = self.model.make("reference", dimension=3)[0]
        values = []
        for counts in allocations(6, 6):
            direct = self.model.determinant_value(case["data"], counts)
            self.assertEqual(direct, self.model.polynomial_value(case["data"], counts))
            values.append((direct, counts))
        self.assertEqual(len(values), 462)
        self.assertEqual(max(value for value, _ in values), 1152)
        self.assertEqual(self.model.check(case["data"], {"counts": [2, 0, 0, 2, 2, 0]})["cost"], 1152)
        self.assertEqual(len(self.model.cauchy_binet_terms(case["data"])), 19)

    def test_reference_vectors_are_manifest_truth(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        import json
        manifest = json.loads((root / "papers/generate_benchmark/omt_nia_codex_handoff/reference_starter/generated/manifest.json").read_text())
        references = [row for row in manifest if row["family"] == "design"]
        cases = self.model.make("reference")
        self.assertEqual(len(cases), 3)
        for row, case in zip(references, cases):
            self.assertEqual([[int(x) for x in vector] for vector in case["data"]["vectors"]], row["vectors"])
            self.assertTrue(case["metadata"]["replay"])
            self.assertEqual(case["metadata"]["objective"]["sense"], "maximize")

    def test_checker_rejects_invalid_and_false_cost(self):
        case = self.model.make("tiny")[0]
        for decision in ({"counts": [0, 0, 3]}, {"counts": [-1, 2, 2]}, {"counts": [1, 1, 2]},
                         {"counts": [True, 1, 1]}, {"counts": [1.0, 1, 1]},
                         {"counts": [1, 1, 1], "cost": "4"}):
            with self.assertRaises(ValueError):
                self.model.check(case["data"], decision)

    def test_random_matrix_polynomial_crosscheck(self):
        for d in (2, 3, 4, 5):
            case = self.model.make("integer", dimension=d, candidates=d+2, runs=2*d, seed=8)[0]
            self.assertEqual(case["metadata"]["nonlinearity"]["application_degree"], d)
            counts = case["witness"]["decision"]["counts"]
            self.assertEqual(self.model.determinant_value(case["data"], counts), self.model.polynomial_value(case["data"], counts))
            self.model.check(case["data"], case["witness"]["decision"])
            dense = self.model.make("integer", dimension=d, candidates=d+2, runs=4*d, seed=8)[0]
            rng = random.Random(17 + d)
            for _ in range(12):
                counts = [0] * (d + 2)
                for run in range(4*d):
                    counts[rng.randrange(d + 2)] += 1
                self.assertEqual(self.model.determinant_value(dense["data"], counts),
                                 self.model.polynomial_value(dense["data"], counts))

    def test_application_fingerprint_ignores_encoding_and_profile(self):
        first = self.model.make("reference", dimension=3)[0]
        second = self.model.make("integer", dimension=3, candidates=6, runs=6, seed=1, encoding="determinant")[0]
        self.assertEqual(first["metadata"]["semantic_fingerprint"], second["metadata"]["semantic_fingerprint"])

    def test_both_smt_encodings_are_bounded_pure_integer_polynomials(self):
        for encoding in ("cauchy-binet", "determinant"):
            case = self.model.make("integer", dimension=3, candidates=5, runs=6, seed=2, encoding=encoding)[0]
            text = self.model.build(case["data"]).base()
            self.assertIn("(set-logic QF_NIA)", text)
            self.assertIn("(> cost 0)", text)
            self.assertNotIn("(check-sat)", text)
            for bad in ("(div ", "(to_real ", "(log ", "(det "):
                self.assertNotIn(bad, text)

    def test_rational_text_is_exact_and_float_rejected(self):
        case = self.model.make("rational", vectors=[["0.1", "0"], ["0", "0.2"], ["0.1", "0.2"]], runs=3)[0]
        checked = self.model.check(case["data"], {"counts": [1, 1, 1]})
        self.assertEqual(checked["determinant"], {"num": "3", "den": "2500"})
        with self.assertRaises(ValueError):
            self.model.make("rational", vectors=[[0.1, 0], [0, 1]], runs=2)

    def test_generation_and_assignment_are_deterministic(self):
        a = self.model.make("integer", dimension=3, candidates=6, runs=6, seed=4)[0]
        b = self.model.make("integer", dimension=3, candidates=6, runs=6, seed=4)[0]
        self.assertEqual(a, b)
        self.assertEqual(self.model.decode(a["data"], self.model.assignment(a["data"], a["witness"]["decision"])), a["witness"]["decision"])

    def test_limits_reject_uncontrolled_expansion(self):
        with self.assertRaises(ValueError):
            self.model.make("integer", dimension=7, candidates=12, runs=7, encoding="determinant")
        with self.assertRaises(ValueError):
            self.model.make("integer", dimension=5, candidates=100, runs=5)
        with self.assertRaises(ValueError):
            self.model.make("integer", dimension=3, candidates=2, runs=3)
        with self.assertRaises(ValueError):
            self.model.make("integer", dimension=3, candidates=4, runs=2)

    def test_unknown_parameters_and_invalid_encoding_are_rejected(self):
        with self.assertRaises(ValueError):
            self.model.make("tiny", ignored_parameter=99)
        with self.assertRaises(ValueError):
            self.model.make("integer", encoding="other")

    def test_caps_preserve_a_full_rank_witness_or_refuse(self):
        case = self.model.make("integer", dimension=3, candidates=4, runs=5, upper_bounds=[1, 1, 1, 2], seed=1)[0]
        self.model.check(case["data"], case["witness"]["decision"])
        with self.assertRaises(ValueError):
            self.model.make("integer", dimension=3, candidates=4, runs=5, upper_bounds=[1, 1, 1, 1], seed=1)


if __name__ == "__main__":
    unittest.main()
