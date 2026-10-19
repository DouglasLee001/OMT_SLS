"""Geometric regression tests independent of the SMT encoding."""
import copy
import importlib.util
import pathlib
import unittest


def load_model():
    path = pathlib.Path(__file__).with_name("model.py")
    spec = importlib.util.spec_from_file_location("packing_model_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackingSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model()

    def test_touching_square_optimum_and_source_domains(self):
        case = self.model.make("squares", n=3)[0]
        self.assertEqual(self.model.check(case["data"], case["witness"]["decision"])["cost"], 15)
        self.assertEqual(case["metadata"]["expected_optimum"], "15")
        domains = self.model.bounds(case["data"])
        self.assertEqual(domains, {"width": (3, 6), "height": (3, 6), "x": (0, 6), "y": (0, 6), "area": (14, 18)})
        text = self.model.build(case["data"]).base()
        self.assertIn("(* W H)", text)
        self.assertIn("(or ", text)
        self.assertNotIn("(check-sat)", text)

    def test_n_is_an_explicit_list_not_an_interval(self):
        cases = self.model.make("squares", n="1,3,10")
        self.assertEqual([case["data"]["n"] for case in cases], [1, 3, 10])
        for case in cases:
            self.model.check(case["data"], case["witness"]["decision"])

    def test_checker_rejects_overlap_outside_and_wrong_cost(self):
        case = self.model.make("squares", n=3)[0]
        for mutate in (lambda d: d["positions"].__setitem__(0, [0, 0]),
                       lambda d: d["positions"].__setitem__(0, [5, 0]),
                       lambda d: d.update(cost="16"),
                       lambda d: d.update(width=5.0),
                       lambda d: d.update(width=True)):
            decision = copy.deepcopy(case["witness"]["decision"])
            mutate(decision)
            with self.assertRaises(ValueError):
                self.model.check(case["data"], decision)

    def test_rectangles_have_optimization_preserving_bounds(self):
        case = self.model.make("rectangles", items=12, max_side=8, seed=1)[0]
        result = self.model.check(case["data"], case["witness"]["decision"])
        self.assertEqual(result["cost"], int(case["witness"]["cost"]))
        self.assertIn("optimization_preserving_bound", str(case["metadata"]["model_changes"]))
        self.assertEqual(case["metadata"]["objective"]["sense"], "minimize")

    def test_permutations_share_multiset_fingerprint(self):
        first = self.model.make("rectangles", dimensions=[[2, 3], [1, 4], [2, 3]])[0]
        second = self.model.make("rectangles", dimensions=[[2, 3], [2, 3], [1, 4]])[0]
        self.assertEqual(first["metadata"]["semantic_fingerprint"], second["metadata"]["semantic_fingerprint"])
        self.assertNotEqual(first["data"]["dimensions"], second["data"]["dimensions"])

    def test_invalid_size_and_domain_rejected(self):
        for kwargs in ({"n": 0}, {"n": "2,0"}):
            with self.assertRaises(ValueError):
                self.model.make("squares", **kwargs)
        for dimensions in ([[0, 3]], [[1.2, 3]], []):
            with self.assertRaises(ValueError):
                self.model.make("rectangles", dimensions=dimensions)

    def test_unknown_parameters_and_changed_upstream_are_rejected(self):
        from unittest.mock import patch
        with self.assertRaises(ValueError):
            self.model.make("squares", n=3, ignored_parameter=99)
        record = self.model.source_record(self.model.SOURCE, self.model.SOURCE_URL, "source_model")
        record["sha256"] = "0" * 64
        with patch.object(self.model, "source_record", return_value=record):
            with self.assertRaises(ValueError):
                self.model.make("squares", n=3)

    def test_assignment_roundtrip_and_determinism(self):
        a = self.model.make("rectangles", items=6, max_side=8, seed=2)[0]
        b = self.model.make("rectangles", items=6, max_side=8, seed=2)[0]
        self.assertEqual(a, b)
        decision = a["witness"]["decision"]
        self.assertEqual(self.model.decode(a["data"], self.model.assignment(a["data"], decision)), decision)

    def test_small_square_optimum_by_geometric_enumeration(self):
        case = self.model.make("squares", n=3)[0]
        data = case["data"]
        found = []
        # For areas <=15, integer axis bounds leave only 3x5 and 5x3.
        for width in range(3, 7):
            for height in range(3, 7):
                if width * height < 14 or width * height > 15:
                    continue
                positions = [[0, 0] for _ in range(3)]
                def visit(i):
                    if i == 3:
                        try:
                            found.append(self.model.check(data, {"width": width, "height": height, "positions": copy.deepcopy(positions)})["cost"])
                        except ValueError:
                            pass
                        return
                    side = i + 1
                    for x in range(width - side + 1):
                        for y in range(height - side + 1):
                            positions[i] = [x, y]
                            visit(i + 1)
                visit(0)
        self.assertTrue(found)
        self.assertEqual(min(found), 15)


if __name__ == "__main__":
    unittest.main()
