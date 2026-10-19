"""Exact minimum-area packing with a separate geometric solution checker.

Array positions and SMT suffixes are zero based. Rectangle directions are fixed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import re

from nia_bench.core import Model, disjunction, num, source_record

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "papers/generate_benchmark/sources/square-packing/square_pack.mzn"
SOURCE_URL = "https://raw.githubusercontent.com/MiniZinc/minizinc-examples/master/square_pack.mzn"
PAPER_URL = "https://doi.org/10.1007/978-3-540-85958-1_4"


def _integer(value, name):
    if isinstance(value, bool) or not (isinstance(value, int) or
            isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value)):
        raise ValueError(name + " must be an exact integer")
    return int(value)


def _sizes(data):
    rows = data.get("dimensions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("dimensions must be a nonempty list")
    result = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("each dimension must contain width and height")
        width, height = (_integer(row[0], "item width"), _integer(row[1], "item height"))
        if min(width, height) <= 0:
            raise ValueError("item dimensions must be positive")
        result.append((width, height))
    if data.get("profile") == "squares":
        n = _integer(data.get("n"), "n")
        if n < 1 or result != [(i, i) for i in range(1, n + 1)]:
            raise ValueError("squares must be exactly the ordered sides 1 through n")
    elif data.get("profile") != "rectangles":
        raise ValueError("unsupported packing profile")
    return result


def bounds(data):
    """Preserve upstream square domains; rectangles use a known-solution bound."""
    sizes = _sizes(data)
    area_min = sum(w * h for w, h in sizes)
    if data["profile"] == "squares":
        n = data["n"]
        side_sum = n * (n + 1) // 2
        return {"width": (n, side_sum), "height": (n, side_sum),
                "x": (0, side_sum), "y": (0, side_sum),
                "area": (area_min, n * side_sum)}
    width_min = max(w for w, _ in sizes)
    height_min = max(h for _, h in sizes)
    area_max = sum(w for w, _ in sizes) * height_min
    width_max, height_max = area_max // height_min, area_max // width_min
    return {"width": (width_min, width_max), "height": (height_min, height_max),
            "x": (0, width_max), "y": (0, height_max),
            "area": (area_min, area_max)}


def build(data):
    sizes = _sizes(data)
    domain = bounds(data)
    model = Model("Integer minimum-area packing; fixed item orientations; touching boundaries allowed.")
    model.integer("W", *domain["width"], unit="grid_unit")
    model.integer("H", *domain["height"], unit="grid_unit")
    for i in range(len(sizes)):
        model.integer(f"x_{i}", *domain["x"], unit="grid_unit")
        model.integer(f"y_{i}", *domain["y"], unit="grid_unit")
    model.integer("area", *domain["area"], role="auxiliary", unit="grid_unit^2")
    model.integer("cost", *domain["area"], role="objective", unit="grid_unit^2")
    model.assertion("(= area (* W H))")
    model.assertion("(= cost area)")
    for i, (width, height) in enumerate(sizes):
        model.assertion(f"(<= (+ x_{i} {num(width)}) W)")
        model.assertion(f"(<= (+ y_{i} {num(height)}) H)")
    for i, (wi, hi) in enumerate(sizes):
        for j in range(i + 1, len(sizes)):
            wj, hj = sizes[j]
            model.assertion(disjunction((f"(<= (+ x_{i} {num(wi)}) x_{j})",
                                         f"(<= (+ x_{j} {num(wj)}) x_{i})",
                                         f"(<= (+ y_{i} {num(hi)}) y_{j})",
                                         f"(<= (+ y_{j} {num(hj)}) y_{i})")))
    return model


def check(data, decision):
    """Check intersection lengths directly, without evaluating the SMT disjunction."""
    sizes = _sizes(data)
    if not isinstance(decision, dict):
        raise ValueError("packing decision must be an object")
    width = _integer(decision.get("width"), "width")
    height = _integer(decision.get("height"), "height")
    positions = decision.get("positions")
    if not isinstance(positions, list) or len(positions) != len(sizes):
        raise ValueError("one position is required for each item")
    domain = bounds(data)
    for name, value in (("width", width), ("height", height)):
        if not domain[name][0] <= value <= domain[name][1]:
            raise ValueError(name + " violates the model domain")
    rectangles = []
    for i, ((w, h), position) in enumerate(zip(sizes, positions)):
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise ValueError("a position must contain x and y")
        x, y = _integer(position[0], "x"), _integer(position[1], "y")
        if not (domain["x"][0] <= x <= domain["x"][1] and domain["y"][0] <= y <= domain["y"][1]):
            raise ValueError("item coordinate violates its domain")
        if x + w > width or y + h > height:
            raise ValueError("item falls outside the container")
        rectangles.append((x, y, x + w, y + h))
    for i, (left, bottom, right, top) in enumerate(rectangles):
        for l2, b2, r2, t2 in rectangles[i + 1:]:
            intersection_width = min(right, r2) - max(left, l2)
            intersection_height = min(top, t2) - max(bottom, b2)
            if intersection_width > 0 and intersection_height > 0:
                raise ValueError("positive-area item overlap")
    area = width * height
    if not domain["area"][0] <= area <= domain["area"][1]:
        raise ValueError("area violates the model domain")
    if "cost" in decision and _integer(decision["cost"], "cost") != area:
        raise ValueError("claimed cost differs from container area")
    return {"cost": area, "method": "integer rectangle intersection geometry"}


def assignment(data, decision):
    checked = check(data, decision)
    values = {"W": _integer(decision["width"], "width"),
              "H": _integer(decision["height"], "height"),
              "area": checked["cost"], "cost": checked["cost"]}
    for i, (x, y) in enumerate(decision["positions"]):
        values[f"x_{i}"], values[f"y_{i}"] = _integer(x, "x"), _integer(y, "y")
    return values


def decode(data, values):
    return {"width": values["W"], "height": values["H"],
            "positions": [[values[f"x_{i}"], values[f"y_{i}"]] for i in range(len(_sizes(data)))]}


def _case(data, label, seed=None, max_side=None):
    sizes = _sizes(data)
    width, height = sum(w for w, _ in sizes), max(h for _, h in sizes)
    offset, positions = 0, []
    for w, _ in sizes:
        positions.append([offset, 0])
        offset += w
    decision = {"width": width, "height": height, "positions": positions}
    if data["profile"] == "squares" and data["n"] == 3:
        decision = {"width": 5, "height": 3, "positions": [[3, 2], [3, 0], [0, 0]]}
    checked = check(data, decision)
    fingerprint = hashlib.sha256(json.dumps({"fixed_orientation_dimensions": sorted(sizes)}, separators=(",", ":")).encode()).hexdigest()
    square = data["profile"] == "squares"
    source = source_record(SOURCE, SOURCE_URL, "source_model" if square else "model_background")
    inventory_path = REPO / "papers/generate_benchmark/omt_nia_codex_handoff/references/source_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    prior = next(row for row in inventory["records"] if row["name"] == "square_pack.mzn")
    source["retrieved_date"] = prior["retrieved_date"]
    source["inventory_sha256"] = prior["sha256"]
    source["matches_inventory"] = source["sha256"] == prior["sha256"]
    if not source["matches_inventory"]:
        raise ValueError("cached square_pack.mzn differs from the pinned source inventory; preserve it as a separately reviewed changed variant")
    domain = bounds(data)
    changes = (["Exact integer conversion of square_pack.mzn; search annotations omitted; cost aliases area."] if square else
               ["Fixed-orientation positive integer rectangles replace sides 1..n.",
                {"kind": "optimization_preserving_bound", "reason": "The horizontal-strip witness supplies UB; domains exclude worse feasible containers while preserving the global optimum.",
                 "bound": str(domain["area"][1]), "preserves_all_feasible_solutions": False}])
    metadata = {
        "profile": data["profile"],
        "origin": "upstream_model_generated_parameter" if square else "new_application_family",
        "model_changes": changes,
        "objective": {"sense": "minimize", "symbol": "cost", "scale": "1", "name": "container area", "unit": "grid_unit^2", "recover": "area = cost"},
        "nonlinearity": {"application_degree": 2, "local_equation_degree": 2,
                         "multivalued_integer_products": 1, "boolean_selection_products": 0},
        "source_records": [source, {"url": PAPER_URL, "role": "application_background", "cached": False}],
        "parameters": {"n": data.get("n"), "items": len(sizes), "seed": seed, "max_side": max_side,
                       "dimensions": data["dimensions"], "orientation": "fixed", "distribution": "uniform_integer" if seed is not None else "explicit"},
        "seed": seed, "generator_algorithm": "python.Random.randint/v1" if seed is not None else "explicit_integer_dimensions/v1",
        "replay": square and 3 <= data["n"] <= 10,
        "semantic_fingerprint": fingerprint, "indexing": "zero-based item indices and SMT suffixes",
        "domain_source": "upstream square model" if square else "optimization_preserving_bound from strip witness",
        "packing": {"items": len(sizes), "nonoverlap_disjunctions": len(sizes) * (len(sizes) - 1) // 2,
                    "area_lower": str(domain["area"][0]), "area_upper": str(domain["area"][1]),
                    "width_domain": list(map(str, domain["width"])), "height_domain": list(map(str, domain["height"])),
                    "exact_tiling_easy": checked["cost"] == domain["area"][0]},
    }
    if square and data["n"] in (1, 2, 3):
        metadata["expected_optimum"] = str({1: 1, 2: 6, 3: 15}[data["n"]])
    return {"label": label, "data": data, "witness": {"decision": decision, "cost": str(checked["cost"])}, "metadata": metadata}


def make(profile, **params):
    accepted = {"squares": {"n"}, "rectangles": {"items", "max_side", "seed", "dimensions"}}
    if profile not in accepted:
        raise ValueError("unsupported packing profile: " + str(profile))
    unknown = set(params) - accepted[profile]
    if unknown:
        raise ValueError("unknown packing parameters: " + ", ".join(sorted(unknown)))
    if profile == "squares":
        raw = params.get("n", 3)
        values = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else [raw]
        result = []
        for value in values:
            n = _integer(value.strip() if isinstance(value, str) else value, "n")
            if n < 1:
                raise ValueError("n must be positive")
            data = {"schema_version": "1.0", "profile": "squares", "n": n,
                    "dimensions": [[str(i), str(i)] for i in range(1, n + 1)]}
            result.append(_case(data, f"squares-n{n:02d}"))
        return result
    if profile == "rectangles":
        rows = params.get("dimensions")
        seed = None
        max_side = None
        if rows is not None and any(key in params for key in ("items", "max_side", "seed")):
            raise ValueError("explicit dimensions cannot be combined with random-generation parameters")
        if rows is None:
            count = _integer(params.get("items", 12), "items")
            max_side = _integer(params.get("max_side", 8), "max_side")
            seed = _integer(params.get("seed", 1), "seed")
            if min(count, max_side) < 1:
                raise ValueError("items and max_side must be positive")
            rng = random.Random(seed)
            rows = [[rng.randint(1, max_side), rng.randint(1, max_side)] for _ in range(count)]
        data = {"schema_version": "1.0", "profile": "rectangles", "dimensions": rows}
        sizes = _sizes(data)
        data["dimensions"] = [[str(w), str(h)] for w, h in sizes]
        label = f"rectangles-n{len(sizes)}" + (f"-max{max_side}-seed{seed}" if seed is not None else "-explicit")
        return [_case(data, label, seed=seed, max_side=max_side)]
    raise ValueError("unsupported packing profile: " + str(profile))


def smoke_specs():
    return ([{"profile": "squares", "n": ",".join(str(n) for n in range(1, 11))}] +
            [{"profile": "rectangles", "items": n, "max_side": 8, "seed": seed}
             for n in (6, 12, 24) for seed in (1, 2)])
