"""Semantic regressions for the exact, unit-duration rcmsp conversion."""
from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
MODULE = Path(__file__).with_name("model.py")
FIXTURES = ROOT / "papers/generate_benchmark/omt_nia_codex_handoff/references/tiny_fixtures.json"
Z3 = ROOT / "competitor/z3-5.1.0/build/z3"
if MODULE.exists():
    SPEC = importlib.util.spec_from_file_location("cyclic_model_tested", MODULE)
    cyclic = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(cyclic)
else:
    cyclic = None


def fixture(name):
    fixtures = json.loads(FIXTURES.read_text())["fixtures"]
    return next(f for f in fixtures if f["id"] == name)


class CyclicSemantics(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cyclic, "cyclic/model.py implementation is missing")

    def test_nonzero_iteration_fixture_and_same_phase_comparison(self):
        a = fixture("cyclic_tiny_a")
        result = cyclic.check(a["input"], a["witness"])
        self.assertEqual((result["U"], result["T"], result["C"], result["cost"]), (3, 2, 2, 8))
        self.assertEqual(result["offsets"], [0, 0, -1])
        b = fixture("cyclic_tiny_b")
        self.assertEqual([cyclic.check(b["input"], w)["cost"] for w in b["witnesses"]], [23, 24])

    def test_tiny_a_optimum_by_complete_source_domain_enumeration(self):
        data = fixture("cyclic_tiny_a")["input"]
        costs = []
        # Source fixed by the source model; real phases restricted exactly by
        # s_i+1 <= T; sink iteration determined exactly by its max equation.
        for period in range(4):
            for phases in itertools.product(range(period), repeat=2):
                for iters in itertools.product(range(5), repeat=2):
                    decision = {"s": [0, *phases, period], "k": [0, *iters, max(iters)]}
                    try:
                        costs.append(cyclic.check(data, decision)["cost"])
                    except ValueError:
                        pass
        self.assertTrue(costs)
        self.assertEqual(min(costs), 8)

    def test_rejects_resource_conflict_bad_span_bad_max_and_nonintegers(self):
        a = fixture("cyclic_tiny_a")
        for changes in ({"s": [0, 0, 0, 2]}, {"C": 1}, {"cost": 9},
                        {"k": [0, 0, 1, 0]}, {"s": [0, 0, 1.0, 2]},
                        {"s": [0, False, 1, 2]}, {"s": "0012"}):
            decision = copy.deepcopy(a["witness"])
            decision.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                cyclic.check(a["input"], decision)

    def test_original_reified_precedence_not_elapsed_time_replacement(self):
        # b=false only contributes 1 to k. A large latency is not elapsed
        # time across distance*period; the exact source permits this point.
        data = copy.deepcopy(fixture("cyclic_tiny_a")["input"])
        data["precedences"].append([2, 3, 100, 0])
        decision = {"s": [0, 0, 1, 2], "k": [0, 0, 1, 1]}
        self.assertEqual(cyclic.check(data, decision)["cost"], 208)
        # Negative input latency/distance is preserved, not absolutized.
        data["precedences"].append([3, 2, -1, -1])
        with self.assertRaises(ValueError):
            cyclic.check(data, decision)

    def test_zero_duration_dummy_does_not_consume_cumulative_capacity(self):
        data = copy.deepcopy(fixture("cyclic_tiny_a")["input"])
        data["requirements"][0] = [1]
        self.assertEqual(cyclic.check(data, {"s": [0, 0, 1, 2], "k": [0, 0, 1, 1]})["cost"], 8)

    def test_input_validation_and_strict_dzn_parser(self):
        data = fixture("cyclic_tiny_a")["input"]
        for field, value in (("n_tasks", 4.0), ("capacities", [0]),
                             ("requirements", [[0], [1], [1]]),
                             ("requirements", ["0", "1", "1", "0"]),
                             ("precedences", [[1, 5, 1, 0]])):
            invalid = copy.deepcopy(data)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cyclic.build(invalid)
        with self.assertRaises(ValueError):
            cyclic.parse_dzn("n_tasks=4; n_res=1; rcap=[1]; rreq=[|0|1|1|0|]; n_prec=0; prec=[||]; hidden=1;")
        with self.assertRaises(ValueError):
            cyclic.parse_dzn("n_tasks=4; n_res=1; rcap=[1]; rreq=[|0|1|1|0|]; n_prec=1; prec=[|1,2,1.5,0|];")

    def test_four_upstream_data_arrays_bounds_and_checked_witnesses(self):
        cases = cyclic.make("minizinc")
        self.assertEqual(len(cases), 4)
        expected = {"easy_1": (46, 119), "easy_2": (26, 68),
                    "medium_1": (15, 44), "hard_1": (86, 246)}
        for case in cases:
            self.assertIn(case["label"], expected)
            n, bound = expected[case["label"]]
            self.assertEqual(case["data"]["n_tasks"], n)
            self.assertEqual(sum(max(0, e[2]) for e in case["data"]["precedences"]), bound)
            self.assertEqual(len(case["data"]["requirements"]), n)
            self.assertEqual(case["metadata"]["origin"], "upstream_model_instance")
            self.assertEqual(len(case["metadata"]["source_records"]), 2)
            if case["witness"] is not None:
                checked = cyclic.check(case["data"], case["witness"]["decision"])
                self.assertEqual(str(checked["cost"]), case["witness"]["cost"])

    def test_fetch_cache_root_and_direct_family_layout_are_equivalent(self):
        with tempfile.TemporaryDirectory(prefix="cyclic-cache-layout-") as directory:
            cache = Path(directory) / "raw"
            family_cache = cache / "cyclic"
            family_cache.mkdir(parents=True)
            for name in ("rcmsp.mzn", "easy_1.dzn"):
                (family_cache / name).write_bytes((cyclic.SOURCES / name).read_bytes())
            nested = cyclic.make("minizinc", ids="easy_1", cache=str(cache))
            direct = cyclic.make("minizinc", ids="easy_1", cache=str(family_cache))
            self.assertEqual(nested, direct)
            self.assertEqual(nested[0]["metadata"]["source_records"][0]["file"], "rcmsp.mzn")

    def test_rejects_changed_model_in_explicit_cache(self):
        with tempfile.TemporaryDirectory(prefix="cyclic-changed-model-") as directory:
            cache = Path(directory)
            for name in ("rcmsp.mzn", "easy_1.dzn"):
                (cache / name).write_bytes((cyclic.SOURCES / name).read_bytes())
            model = cache / "rcmsp.mzn"
            model.write_bytes(model.read_bytes() + b"\n% edited model\n")
            with self.assertRaisesRegex(ValueError, "SHA256.*rcmsp"):
                cyclic.make("minizinc", ids="easy_1", cache=str(cache))

    def test_rejects_changed_pinned_upstream_cache(self):
        with tempfile.TemporaryDirectory(prefix="cyclic-changed-source-") as directory:
            changed = Path(directory) / "easy_1.dzn"
            changed.write_bytes((cyclic.SOURCES / "easy_1.dzn").read_bytes() + b"\n% edited cache\n")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                cyclic.make("minizinc", ids="easy_1", cache=directory)

    def test_planted_multiple_scales_seed_determinism_and_back_edges(self):
        for q in (6, 12, 24):
            for seed in (1, 2):
                params = dict(real_tasks=q, resources=2, period=q, extra_edges=q + 2, seed=seed)
                cases = cyclic.make("planted", **params)
                self.assertEqual(cases, cyclic.make("planted", **params))
                case = cases[0]
                self.assertEqual(case["metadata"]["origin"], "new_application_family")
                for fraction in case["metadata"]["planted"]["resource_utilization"]:
                    self.assertEqual(math.gcd(int(fraction["num"]), int(fraction["den"])), 1)
                decision = case["witness"]["decision"]
                result = cyclic.check(case["data"], decision)
                self.assertGreaterEqual(result["U"], q)
                self.assertLessEqual(result["C"], q)
                self.assertIn(1, decision["k"][1:-1])
                self.assertIn(0, decision["k"][1:-1])
                self.assertTrue(any(decision["s"][i-1] > decision["s"][j-1] and d > 0
                                    for i, j, _, d in case["data"]["precedences"]))
                text = cyclic.build(case["data"]).base()
                self.assertIn("(* k_2 T)", text)
                self.assertNotIn(f"(assert (= T {q}))", text)

    def test_source_model_allows_only_two_virtual_tasks(self):
        data = {"n_tasks": 2, "capacities": [1], "requirements": [[0], [0]],
                "precedences": [[1, 2, 1, 0]]}
        self.assertEqual(cyclic.check(data, {"s": [0, 1], "k": [0, 0]})["cost"], 2)
        self.assertIn("(declare-fun cost () Int)", cyclic.build(data).base())

    def test_unit_duration_start_event_encoding_equals_time_occupancy(self):
        import random
        rng = random.Random(19)
        for _ in range(100):
            starts = [rng.randrange(8) for _ in range(8)]
            demands = [rng.randrange(4) for _ in starts]
            capacity = rng.randrange(1, 9)
            occupancy = all(sum(d for s, d in zip(starts, demands) if s <= t < s + 1) <= capacity
                            for t in range(8))
            event_encoding = all(sum(d for s, d in zip(starts, demands) if s == event) <= capacity
                                 for event in starts)
            self.assertEqual(occupancy, event_encoding)

    @unittest.skipUnless(Z3.exists(), "configured Z3 binary unavailable")
    def test_smt_tiny_a_strict_improvement_and_auxiliaries_are_exact(self):
        a = fixture("cyclic_tiny_a")
        model = cyclic.build(a["input"]).base()
        pins = "\n".join(f"(assert (= {key} {value}))" for key, value in cyclic.assignment(a["input"], a["witness"]).items())
        query = (model + "(push)\n(assert (< cost 8))\n(check-sat)\n(pop)\n" + pins +
                 "\n(check-sat)\n(push)\n(assert (= C 1))\n(check-sat)\n(pop)\n" +
                 "(push)\n(assert (= hi 1))\n(check-sat)\n(pop)\n" +
                 "(push)\n(assert (= lo (- 2)))\n(check-sat)\n(pop)\n")
        result = subprocess.run([str(Z3), "-in"], input=query, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.split(), ["unsat", "sat", "unsat", "unsat", "unsat"])

    @unittest.skipUnless(Z3.exists(), "configured Z3 binary unavailable")
    def test_tiny_a_application_and_smt_feasibility_differential(self):
        data = fixture("cyclic_tiny_a")["input"]
        queries = [cyclic.build(data).base()]
        expected = []
        for period in range(4):
            for phases in itertools.product(range(period), repeat=2):
                for iters in itertools.product(range(5), repeat=2):
                    d = {"s": [0, *phases, period], "k": [0, *iters, max(iters)]}
                    try:
                        cyclic.check(data, d)
                        expected.append("sat")
                    except ValueError:
                        expected.append("unsat")
                    queries.append("(push)")
                    queries.extend(f"(assert (= {key} {value}))" for key, value in cyclic.assignment(data, d).items())
                    queries.extend(["(check-sat)", "(pop)"])
        result = subprocess.run([str(Z3), "-in"], input="\n".join(queries), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.split(), expected)


if __name__ == "__main__":
    unittest.main()
