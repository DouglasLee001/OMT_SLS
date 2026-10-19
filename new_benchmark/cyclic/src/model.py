"""Exact integer conversion of MiniZinc cyclic-rcpsp/rcmsp.mzn.

The source's reified precedence formula is the specification. This module
intentionally does not implement a general cyclic scheduling compiler.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import heapq
import json
from pathlib import Path
import random
import re

from nia_bench.core import Model, num, summation, disjunction, source_record

ROOT = Path(__file__).resolve().parents[3]
HANDOFF = ROOT / "papers/generate_benchmark/omt_nia_codex_handoff"
SOURCES = ROOT / "papers/generate_benchmark/sources/cyclic-rcpsp"
UPSTREAM = "https://raw.githubusercontent.com/MiniZinc/minizinc-benchmarks/master/cyclic-rcpsp/"
DEFAULT_IDS = ("easy_1", "easy_2", "medium_1", "hard_1")


def _integer(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, not bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return int(value)
    raise ValueError(f"{name} must be an exact integer")


def _data(data):
    """Validate parameter shape only; no application feasibility calculation."""
    if not isinstance(data, dict):
        raise ValueError("cyclic data must be an object")
    try:
        for key in ("capacities", "requirements", "precedences"):
            if not isinstance(data[key], list):
                raise ValueError(f"{key} must be a JSON array")
        if any(not isinstance(row, list) for row in data["requirements"] + data["precedences"]):
            raise ValueError("requirements and precedences must be arrays of arrays")
        n = _integer(data["n_tasks"], "n_tasks")
        cap = [_integer(x, "capacity") for x in data["capacities"]]
        req = [[_integer(x, "requirement") for x in row] for row in data["requirements"]]
        prec = [[_integer(x, "precedence field") for x in row] for row in data["precedences"]]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing or malformed cyclic parameters") from exc
    if n < 2 or not cap or any(c <= 0 for c in cap):
        raise ValueError("need distinct source/sink tasks and positive resource capacities")
    if "n_res" in data and _integer(data["n_res"], "n_res") != len(cap):
        raise ValueError("n_res differs from capacity count")
    if len(req) != n or any(len(row) != len(cap) for row in req):
        raise ValueError("requirement array dimensions do not match tasks/resources")
    if any(x < 0 for row in req for x in row):
        raise ValueError("resource requirements must be nonnegative")
    if "n_prec" in data and _integer(data["n_prec"], "n_prec") != len(prec):
        raise ValueError("n_prec differs from precedence count")
    if any(len(row) != 4 for row in prec):
        raise ValueError("each precedence needs (source,target,latency,distance)")
    if any(not (1 <= row[0] <= n and 1 <= row[1] <= n) for row in prec):
        raise ValueError("precedence task index outside 1..n_tasks")
    # Latency and distance remain signed integers, exactly as in rcmsp.mzn.
    bound = sum(max(row[2], 0) for row in prec)
    return n, cap, req, prec, bound


def parse_dzn(text):
    """Read only the six literal assignments used by the cached upstream data.

    Unsupported expressions, duplicate names, fractional literals, dangling
    text, and unrecognised parameters are rejected rather than ignored.
    """
    text = re.sub(r"%[^\n]*", "", text)
    fields = {}
    position = 0
    while position < len(text):
        if not text[position:].strip():
            break
        match = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*)\s*=\s*([^;]*);", text[position:], re.S)
        if match is None:
            raise ValueError("unsupported MiniZinc data syntax")
        key, raw = match.group(1), match.group(2).strip()
        if key in fields:
            raise ValueError(f"duplicate MiniZinc parameter {key}")
        fields[key] = raw
        position += match.end()
    required = {"n_tasks", "n_res", "rcap", "rreq", "n_prec", "prec"}
    if set(fields) != required:
        raise ValueError(f"expected exactly {sorted(required)}, got {sorted(fields)}")

    def scalar(raw):
        if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
            raise ValueError(f"unsupported integer literal: {raw!r}")
        return int(raw)

    def row(raw):
        return [] if not raw.strip() else [scalar(x.strip()) for x in raw.split(",")]

    def array(raw):
        if not raw.startswith("[") or not raw.endswith("]") or "|" in raw:
            raise ValueError("expected literal one-dimensional array")
        return row(raw[1:-1])

    def matrix(raw):
        if not raw.startswith("[|") or not raw.endswith("|]"):
            raise ValueError("expected literal MiniZinc matrix [|...|]")
        inner = raw[2:-2]
        return [] if not inner.strip() else [row(x.strip()) for x in inner.split("|")]

    result = {"schema_version": "1", "n_tasks": scalar(fields["n_tasks"]),
              "n_res": scalar(fields["n_res"]), "n_prec": scalar(fields["n_prec"]),
              "capacities": array(fields["rcap"]), "requirements": matrix(fields["rreq"]),
              "precedences": matrix(fields["prec"])}
    _data(result)
    return result


def build(data):
    n, cap, req, prec, bound = _data(data)
    model = Model(description="Exact unit-duration MiniZinc rcmsp cyclic scheduling")
    for i in range(1, n + 1):
        model.integer(f"s_{i}", 0, bound, role="application", unit="time_slot")
        model.integer(f"k_{i}", 0, n, role="application", unit="iteration")
    model.integer("T", 0, bound, role="auxiliary", unit="time_slot")
    model.assertion(f"(= T s_{n})")
    for i, j, latency, distance in prec:
        condition = f"(<= (+ s_{i} {num(latency)}) s_{j})"
        model.assertion(f"(<= (+ k_{i} (ite {condition} 0 1)) (+ k_{j} {num(distance)}))")
    for i in range(1, n + 1):
        for j in range(i + 1, n + 1):
            if any(req[i-1][r] + req[j-1][r] > cap[r] for r in range(len(cap))):
                di, dj = int(1 < i < n), int(1 < j < n)
                model.assertion(disjunction([f"(<= (+ s_{i} {di}) s_{j})",
                                             f"(<= (+ s_{j} {dj}) s_{i})"]))
    for r, capacity in enumerate(cap):
        tasks = [i for i in range(2, n) if req[i-1][r] > 0]
        if sum(req[i-1][r] for i in tasks) > capacity:
            for i in tasks:
                terms = [f"(ite (= s_{j} s_{i}) {num(req[j-1][r])} 0)" for j in tasks]
                model.assertion(f"(<= {summation(terms)} {num(capacity)})")
    for i in range(1, n):
        model.assertion(f"(>= k_{n} k_{i})")
        model.assertion(f"(<= (+ s_{i} {int(i > 1)}) T)")
    model.assertion(disjunction([f"(= k_{n} k_{i})" for i in range(1, n)]))
    model.assertion("(= s_1 0)")
    model.assertion("(= k_1 0)")
    for i in range(1, n):
        model.integer(f"u_{i}", -n * bound, bound, role="auxiliary", unit="time_slot")
        model.assertion(f"(= u_{i} (- s_{i} (* k_{i} T)))")
    model.integer("hi", -n * bound, bound, role="auxiliary", unit="time_slot")
    model.integer("lo", -n * bound, bound, role="auxiliary", unit="time_slot")
    for i in range(1, n):
        model.assertion(f"(>= hi u_{i})")
        model.assertion(f"(<= lo u_{i})")
    model.assertion(disjunction([f"(= hi u_{i})" for i in range(1, n)]))
    model.assertion(disjunction([f"(= lo u_{i})" for i in range(1, n)]))
    model.integer("C", 0, bound, role="auxiliary", unit="time_slot")
    model.assertion("(= C (+ (- hi lo) 1))")
    model.integer("cost", 0, bound * (bound + 1), role="auxiliary", unit="weighted_time_slot")
    model.assertion(f"(= cost (+ (* {num(bound)} T) C))")
    return model


def check(data, decision):
    """Recompute application semantics from s,k, without SMT auxiliaries.

    Resource usage is counted by explicit occupied integer time slots, not by
    reusing the expression generator's event-equality construction.
    """
    n, capacities, requirements, precedences, bound = _data(data)
    if not isinstance(decision, dict):
        raise ValueError("cyclic decision must be an object with s and k")
    try:
        if not isinstance(decision["s"], list) or not isinstance(decision["k"], list):
            raise ValueError("decision s and k must be JSON arrays")
        starts = [_integer(v, "s") for v in decision["s"]]
        iterations = [_integer(v, "k") for v in decision["k"]]
    except (KeyError, TypeError) as exc:
        raise ValueError("decision needs integer arrays s and k") from exc
    if len(starts) != n or len(iterations) != n:
        raise ValueError("decision arrays must have n_tasks entries")
    if any(v < 0 or v > bound for v in starts):
        raise ValueError("start time outside source domain 0..U")
    if any(v < 0 or v > n for v in iterations):
        raise ValueError("iteration outside source domain 0..n")
    if starts[0] != 0 or iterations[0] != 0:
        raise ValueError("source must have s_1=k_1=0")
    period = starts[-1]
    if iterations[-1] != max(iterations[:-1]):
        raise ValueError("sink iteration is not exactly max of predecessor iterations")
    for i in range(n-1):
        if starts[i] + int(i > 0) > period:
            raise ValueError(f"task {i+1} finishes beyond period")
    for edge, (i, j, latency, distance) in enumerate(precedences, 1):
        b = starts[i-1] + latency <= starts[j-1]
        if iterations[i-1] + (0 if b else 1) > iterations[j-1] + distance:
            raise ValueError(f"precedence {edge} violates original reified formula")
    # Preserve even source/sink redundant disjunctions when dummy demands
    # are nonzero: their durations stay zero, just as in the original model.
    durations = [0] + [1] * (n-2) + [0]
    for i in range(n):
        for j in range(i+1, n):
            if any(requirements[i][r] + requirements[j][r] > capacities[r]
                   for r in range(len(capacities))):
                if not (starts[i] + durations[i] <= starts[j] or starts[j] + durations[j] <= starts[i]):
                    raise ValueError(f"redundant nonoverlap fails for tasks {i+1},{j+1}")
    loads = [{} for _ in capacities]
    for i in range(1, n-1):
        for t in range(starts[i], starts[i] + durations[i]):
            for r, capacity in enumerate(capacities):
                loads[r][t] = loads[r].get(t, 0) + requirements[i][r]
                if loads[r][t] > capacity:
                    raise ValueError(f"resource {r+1} overloaded at time {t}")
    offsets = [starts[i] - iterations[i] * period for i in range(n-1)]
    high, low = max(offsets), min(offsets)
    span = high - low + 1
    if not 0 <= span <= bound:
        raise ValueError("actual one-iteration span outside source domain 0..U")
    cost = bound * period + span
    if not 0 <= cost <= bound * (bound + 1):
        raise ValueError("actual cost outside source objective domain")
    result = {"U": bound, "T": period, "C": span, "lo": low, "hi": high,
              "cost": cost, "offsets": offsets,
              "peak_resource_loads": [max(load.values(), default=0) for load in loads]}
    for key in ("U", "T", "C", "lo", "hi", "cost"):
        if key in decision and _integer(decision[key], key) != result[key]:
            raise ValueError(f"reported {key} differs from recomputed application value")
    if "offsets" in decision:
        try:
            supplied = [_integer(x, "offset") for x in decision["offsets"]]
        except TypeError as exc:
            raise ValueError("offsets must be an integer array") from exc
        if supplied != offsets:
            raise ValueError("reported offsets differ from s_i-k_i*T")
    return result


def assignment(data, decision):
    n, _, _, _, _ = _data(data)
    try:
        starts, iterations = decision["s"], decision["k"]
        if not isinstance(starts, list) or not isinstance(iterations, list):
            raise ValueError("decision s and k must be JSON arrays")
        if len(starts) != n or len(iterations) != n:
            raise ValueError("decision length differs from task count")
        return {**{f"s_{i+1}": _integer(v, "s") for i, v in enumerate(starts)},
                **{f"k_{i+1}": _integer(v, "k") for i, v in enumerate(iterations)}}
    except (KeyError, TypeError) as exc:
        raise ValueError("decision needs arrays s and k") from exc


def decode(data, values):
    n, _, _, _, _ = _data(data)
    try:
        return {"s": [_integer(values[f"s_{i}"], f"s_{i}") for i in range(1, n+1)],
                "k": [_integer(values[f"k_{i}"], f"k_{i}") for i in range(1, n+1)]}
    except KeyError as exc:
        raise ValueError(f"solver assignment missing {exc.args[0]}") from exc


def _source(path, url, role):
    record = source_record(path, url, role)
    inventory = json.loads((HANDOFF / "references/source_inventory.json").read_text())
    prior = next((entry for entry in inventory["records"] if entry["name"] == path.name), None)
    if prior is not None:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != prior["sha256"]:
            raise ValueError(f"SHA256 mismatch for pinned upstream cache {path.name}: expected {prior['sha256']}, got {actual}; source variant requires explicit review")
        record["prior_inventory_sha256"] = prior["sha256"]
        record["matches_prior_inventory"] = True
        # The cached inputs were previously retrieved on this recorded date;
        # generation itself makes no fresh-download claim.
        record["retrieved_date"] = prior["retrieved_date"]
        record["retrieval_provenance"] = "prior handoff inventory"
    else:
        record["retrieved_date"] = None
        record["retrieval_provenance"] = "local cache; original retrieval date unknown"
    return record


def _metadata(profile, data, origin, parameters, records, replay=False):
    n, cap, req, prec, bound = _data(data)
    return {"profile": profile, "origin": origin, "model_changes": [],
            "objective": {"sense": "minimize", "scale": "1", "name": "period then one-iteration span",
                          "unit": "weighted_time_slot", "recover": "cost = U*T + C; T=s_n; C=max(s_i-k_i*T)-min(s_i-k_i*T)+1 for i<n"},
            "nonlinearity": {"application_degree": 2, "encoded_local_degree": 2,
                             "multivalued_integer_products": n-2, "raw_k_times_T_products": n-1,
                             "boolean_selector_products": 0,
                             "domain_degenerate": bound == 0 or n == 2,
                             "note": "k_1=0 is a source symmetry equation; remaining real-task k retain 0..n domains"},
            "parameters": parameters, "source_records": records, "replay": replay,
            "background_papers": [{"title": "A Constraint Based Approach to Cyclic RCPSP", "year": 2011,
                                    "url": "https://cris.unibo.it/handle/11585/119540",
                                    "role": "application background; not claimed to be this MiniZinc implementation or planted generator"}],
            "input_semantics": {"indexing": "precedence task indices are one-based; JSON arrays zero-based",
                                "durations": "source and sink zero, every real task one",
                                "signed_latency_and_distance": "supported literally in the original reified formula",
                                "U": str(bound), "max_min_encoding": "envelope plus attainment disjunction",
                                "auxiliary_bounds": "u,lo,hi in [-n*U,U] derived from source domains",
                                "scope": "exact rcmsp.mzn unit-duration source semantics, not a general cyclic RCPSP compiler"},
            "statistics": {"real_tasks": n-2, "resources": len(cap), "precedences": len(prec),
                           "positive_distance_edges": sum(e[3] > 0 for e in prec), "U": str(bound)}}


def _tiny(profile):
    fid = "cyclic_tiny_a" if profile == "tiny-a" else "cyclic_tiny_b"
    path = HANDOFF / "references/tiny_fixtures.json"
    fixture = next(f for f in json.loads(path.read_text())["fixtures"] if f["id"] == fid)
    data = copy.deepcopy(fixture["input"])
    data["schema_version"] = "1"
    witness = fixture["witness"] if profile == "tiny-a" else fixture["witnesses"][0]
    decision = {"s": witness["s"], "k": witness["k"]}
    checked = check(data, decision)
    metadata = _metadata(profile, data, "new_application_family", {},
                         [_source(SOURCES / "rcmsp.mzn", UPSTREAM + "rcmsp.mzn", "source_model"),
                          _source(path, None, "semantic_regression_fixture")])
    if profile == "tiny-a":
        metadata["expected_optimum"] = "8"
        metadata["optimum_argument"] = "Two unit tasks on capacity one force T>=2 and C>=2; U=3; witness attains cost=8. Complete bounded enumeration and strict-improvement tests are in test_model.py."
    else:
        metadata["comparison_witnesses"] = [{"decision": {"s": w["s"], "k": w["k"]},
                                             "cost": str(check(data, w)["cost"])} for w in fixture["witnesses"]]
        metadata["optimality_note"] = "23 and 24 compare identical phases; neither is declared optimal"
    return {"label": profile, "data": data, "witness": {"decision": decision, "cost": str(checked["cost"])}, "metadata": metadata}


def _serial_witness(data):
    """Try a deterministic k=0, T=U serial schedule; never certify on failure."""
    n, _, _, prec, bound = _data(data)
    successors = [[] for _ in range(n)]
    predecessors = [[] for _ in range(n)]
    indegree = [0] * n
    for i, j, latency, distance in prec:
        if distance == 0:
            successors[i-1].append(j-1)
            predecessors[j-1].append((i-1, latency))
            indegree[j-1] += 1
    ready = [i for i in range(n) if indegree[i] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in successors[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(ready, j)
    if len(order) != n:
        raise ValueError("zero-distance graph is cyclic; simple serial constructor unavailable")
    starts = [0] * n
    previous_real = -1
    for i in order:
        if i in (0, n-1):
            starts[i] = 0 if i == 0 else bound
        else:
            starts[i] = max([0, previous_real + 1] + [starts[j] + latency for j, latency in predecessors[i]])
            previous_real = starts[i]
    decision = {"s": starts, "k": [0] * n}
    checked = check(data, decision)
    return {"decision": decision, "cost": str(checked["cost"])}


def _planted(real_tasks, resources, period, extra_edges, seed):
    q, nr, p, extra, seed = (_integer(v, key) for key, v in
                            (("real_tasks", real_tasks), ("resources", resources), ("period", period),
                             ("extra_edges", extra_edges), ("seed", seed)))
    if q < 2 or nr < 1 or p < 2 or extra < 1:
        raise ValueError("planted needs real_tasks>=2, resources>=1, period>=2, extra_edges>=1 (including mandatory back edge)")
    rng = random.Random(seed)
    n = q + 2
    # Bounded retries preserve the requested period. Each task is sampled
    # with small positive demands; capacities grow with requested density.
    baseline = max(2, (q + p - 1) // p * 2)
    for attempt in range(1, 65):
        cap = [rng.randint(baseline, baseline + 2) for _ in range(nr)]
        req = [[0] * nr]
        phases = [0]
        loads = [[0] * nr for _ in range(p)]
        success = True
        for task in range(q):
            demand = [rng.randint(0, min(2, cap[r])) for r in range(nr)]
            if not any(demand):
                demand[rng.randrange(nr)] = 1
            feasible = [phase for phase in range(p)
                        if all(loads[phase][r] + demand[r] <= cap[r] for r in range(nr))]
            if task < 2:
                # Anchors ensure distinct early and late phases and hence a
                # real back edge, rather than calling a DAG cyclic coverage.
                phase = 0 if task == 0 else p-1
                if phase not in feasible:
                    success = False
                    break
            elif feasible:
                phase = rng.choice(feasible)
            else:
                success = False
                break
            req.append(demand)
            phases.append(phase)
            for r in range(nr):
                loads[phase][r] += demand[r]
        if success:
            break
    else:
        raise ValueError("unable to place demands in requested period after 64 bounded resamples")
    req.append([0] * nr)
    phases.append(p)
    cut = rng.randrange(1, p)
    iterations = [0] + [int(phase >= cut) for phase in phases[1:-1]] + [1]
    ordered = [0] + sorted(range(1, n-1), key=lambda i: (phases[i], i)) + [n-1]
    prec = [[i+1, j+1, phases[j] - phases[i], 0] for i, j in zip(ordered, ordered[1:])]
    construction = [{"kind": "skeleton", "edge": index+1} for index in range(len(prec))]
    # The first added edge has strictly descending real phases; its exact
    # minimal distance is positive by the source reified formula.
    late = max(range(1, n-1), key=lambda i: (phases[i], i))
    early = min(range(1, n-1), key=lambda i: (phases[i], i))
    for index in range(extra):
        if index == 0:
            i, j = late, early
        else:
            i, j = rng.sample(range(1, n-1), 2)
        latency = rng.randint(0, p)
        distance = max(0, iterations[i] - iterations[j] + int(phases[i] + latency > phases[j]))
        prec.append([i+1, j+1, latency, distance])
        construction.append({"kind": "mandatory_back_edge" if index == 0 else "random_extra",
                             "edge": len(prec), "distance_rule": "max(0,k_i-k_j+I[s_i+latency>s_j])", "distance_slack": 0})
    data = {"schema_version": "1", "n_tasks": n, "n_res": nr, "n_prec": len(prec),
            "capacities": cap, "requirements": req, "precedences": prec}
    decision = {"s": phases, "k": iterations}
    checked = check(data, decision)
    parameters = {"real_tasks": q, "resources": nr, "period": p, "extra_edges": extra, "seed": seed}
    metadata = _metadata("planted", data, "new_application_family", parameters,
                         [_source(SOURCES / "rcmsp.mzn", UPSTREAM + "rcmsp.mzn", "source_model"),
                          _source(HANDOFF / "prompts/02_cyclic_scheduling.txt", None, "new_construction_specification")])
    metadata["seed"] = seed
    metadata["generator_algorithm"] = "planted-unit-rcmsp-v1; Python random.Random"
    utilization = [Fraction(sum(row[r] for row in loads), p*cap[r]) for r in range(nr)]
    metadata["planted"] = {"cut": cut, "witness_period": p, "phases": phases,
                           "iterations": iterations, "phase_resource_loads": loads,
                           "placement_attempts": attempt, "retry_limit": 64,
                           "capacity_range": [baseline, baseline+2], "demand_range": [0, 2],
                           "skeleton_edges": n-1, "extra_edges_including_mandatory_back_edge": extra,
                           "edge_construction": construction, "nonzero_real_iterations": sum(iterations[1:-1]),
                           "resource_utilization": [{"num": str(value.numerator), "den": str(value.denominator)}
                                                    for value in utilization],
                           "bias": "planted feasible schedule, sorted skeleton chain, and guaranteed back edge; no hardness claim"}
    return {"label": f"planted-q{q}-r{nr}-p{p}-e{extra}-s{seed}", "data": data,
            "witness": {"decision": decision, "cost": str(checked["cost"])}, "metadata": metadata}


def make(profile, **params):
    if profile in ("tiny-a", "tiny-b"):
        if params:
            raise ValueError(f"{profile} accepts no parameters")
        return [_tiny(profile)]
    if profile == "minizinc":
        unknown = set(params) - {"ids", "cache"}
        if unknown:
            raise ValueError(f"unknown minizinc parameters: {sorted(unknown)}")
        ids = params.get("ids") or DEFAULT_IDS
        if isinstance(ids, str):
            ids = ids.split(",")
        ids = list(ids)
        for name in ids:
            if not isinstance(name, str) or not re.fullmatch(r"(?:easy|medium|hard)_[1-9][0-9]*", name):
                raise ValueError(f"unsupported cyclic source id {name!r}")
        directory = Path(params.get("cache") or SOURCES)
        # Fetch writes CACHE/cyclic; explicitly supplied family directories
        # also remain usable. Prefer the direct directory when all requested
        # files exist there, avoiding a silent mixture of cache locations.
        if params.get("cache") and not all((directory / (name + ".dzn")).is_file() for name in ids):
            if (directory / "cyclic").is_dir():
                directory = directory / "cyclic"
        model_path = directory / "rcmsp.mzn"
        if not model_path.is_file():
            model_path = SOURCES / "rcmsp.mzn"
        result = []
        for name in ids:
            path = directory / (name + ".dzn")
            data = parse_dzn(path.read_text())
            metadata = _metadata(profile, data, "upstream_model_instance", {"id": name},
                                 [_source(model_path, UPSTREAM + "rcmsp.mzn", "source_model"),
                                  _source(path, UPSTREAM + path.name, "source_model_instance")], replay=True)
            try:
                witness = _serial_witness(data)
                metadata["witness_method"] = "deterministic zero-distance DAG serial construction; k=0,T=U; independently checked"
            except ValueError as exc:
                witness = None
                metadata["witness_method"] = "simple serial construction failed; no infeasibility claim"
                metadata["witness_failure"] = str(exc)
            result.append({"label": name, "data": data, "witness": witness, "metadata": metadata})
        return result
    if profile == "planted":
        allowed = {"real_tasks", "resources", "period", "extra_edges", "seed"}
        if set(params) - allowed:
            raise ValueError(f"unknown planted parameters: {sorted(set(params)-allowed)}")
        defaults = {"real_tasks": 12, "resources": 2, "period": 10, "extra_edges": 16, "seed": 1}
        defaults.update(params)
        return [_planted(**defaults)]
    raise ValueError(f"unknown cyclic profile: {profile}")


def smoke_specs():
    specs = [{"profile": "minizinc", "ids": ",".join(DEFAULT_IDS)},
             {"profile": "tiny-a"}, {"profile": "tiny-b"}]
    for q in (6, 12, 24):
        for seed in (1, 2):
            specs.append({"profile": "planted", "real_tasks": q, "resources": 1 if q == 6 else 2,
                          "period": max(4, q // 2 + 1), "extra_edges": q+2, "seed": seed})
    return specs
