"""Exact integer/rational D-optimal design and independent matrix validation.

Cauchy--Binet coefficients use constant integer Bareiss elimination. The checker
forms the original rational information matrix and uses Fraction Gaussian
elimination. It never consumes the generator's coefficient table.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
from itertools import combinations, permutations
import json
from math import comb, factorial, gcd, lcm, prod
from pathlib import Path
import random
import re

from nia_bench.core import Model, num, source_record, summation

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "papers/generate_benchmark/omt_nia_codex_handoff/reference_starter/generated/manifest.json"
PROMPT = REPO / "papers/generate_benchmark/omt_nia_codex_handoff/prompts/04_d_optimal_design.txt"
FIXTURES = REPO / "papers/generate_benchmark/omt_nia_codex_handoff/references/tiny_fixtures.json"
PAPER_URL = "https://arxiv.org/html/2312.11200v7"
MAX_COMBINATIONS = 20_000
MAX_NONZERO_TERMS = 20_000
MAX_BASE_BYTES = 4_000_000
MAX_DETERMINANT_DIMENSION = 6


def _integer(value, name):
    if isinstance(value, bool) or not (isinstance(value, int) or
            isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value)):
        raise ValueError(name + " must be an exact integer")
    return int(value)


def _rational(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("rational inputs must be exact text, integers, or numerator/denominator objects")
    try:
        if isinstance(value, dict) and set(value) == {"num", "den"}:
            denominator = _integer(value["den"], "rational denominator")
            if denominator <= 0:
                raise ValueError("rational denominator must be positive")
            return Fraction(_integer(value["num"], "rational numerator"), denominator)
        if isinstance(value, (int, str, Fraction)):
            return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid exact rational input") from exc
    raise ValueError("invalid exact rational input")


def _fraction_json(value):
    return {"num": str(value.numerator), "den": str(value.denominator)}


def _parsed(data):
    rows = data.get("vectors")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list) or not rows[0]:
        raise ValueError("vectors must be a nonempty rectangular matrix")
    d, m = len(rows[0]), len(rows)
    if any(not isinstance(row, list) or len(row) != d for row in rows):
        raise ValueError("vector dimensions differ")
    vectors = [[_integer(x, "vector coefficient") for x in row] for row in rows]
    if _integer(data.get("dimension", d), "dimension") != d:
        raise ValueError("dimension differs from vector length")
    runs = _integer(data.get("runs"), "runs")
    denominator = _integer(data.get("common_vector_denominator", "1"), "common vector denominator")
    if m < d or runs < d or denominator <= 0:
        raise ValueError("require candidates>=dimension, runs>=dimension, and denominator>0")
    upper = data.get("upper_bounds", [runs] * m)
    if not isinstance(upper, list) or len(upper) != m:
        raise ValueError("upper_bounds must have one value per candidate")
    caps = [_integer(value, "count upper bound") for value in upper]
    if any(value < 0 for value in caps) or sum(caps) < runs:
        raise ValueError("count upper bounds cannot accommodate the run budget")
    encoding = data.get("encoding", "cauchy-binet")
    if encoding not in ("cauchy-binet", "determinant"):
        raise ValueError("encoding must be cauchy-binet or determinant")
    return vectors, runs, denominator, caps, encoding


def _constant_determinant(rows):
    """Fraction-free Bareiss for constant integer minors, with exact divisions."""
    n = len(rows)
    if n == 0:
        return 1
    a = [list(row) for row in rows]
    previous, sign = 1, 1
    for k in range(n - 1):
        pivot_row = next((r for r in range(k, n) if a[r][k] != 0), None)
        if pivot_row is None:
            return 0
        if pivot_row != k:
            a[k], a[pivot_row] = a[pivot_row], a[k]
            sign = -sign
        pivot = a[k][k]
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                numerator = pivot * a[i][j] - a[i][k] * a[k][j]
                quotient, remainder = divmod(numerator, previous)
                if remainder:
                    raise ArithmeticError("constant Bareiss division was not exact")
                a[i][j] = quotient
            a[i][k] = 0
        previous = pivot
    return sign * a[-1][-1]


def _fraction_determinant(rows):
    """Independent Gaussian elimination used by application validation."""
    a = [[Fraction(x) for x in row] for row in rows]
    result = Fraction(1)
    for column in range(len(a)):
        pivot = next((i for i in range(column, len(a)) if a[i][column]), None)
        if pivot is None:
            return Fraction(0)
        if pivot != column:
            a[column], a[pivot] = a[pivot], a[column]
            result = -result
        diagonal = a[column][column]
        result *= diagonal
        for row in range(column + 1, len(a)):
            multiplier = a[row][column] / diagonal
            for j in range(column + 1, len(a)):
                a[row][j] -= multiplier * a[column][j]
            a[row][column] = Fraction(0)
    return result


def _encoding_limits(d, m, encoding):
    if encoding == "cauchy-binet" and comb(m, d) > MAX_COMBINATIONS:
        raise ValueError(f"Cauchy-Binet expansion exceeds {MAX_COMBINATIONS} candidate subsets")
    if encoding == "determinant" and d > MAX_DETERMINANT_DIMENSION:
        raise ValueError(f"permutation determinant encoding is limited to dimension {MAX_DETERMINANT_DIMENSION}")


def cauchy_binet_terms(data):
    vectors, _, _, _, _ = _parsed(data)
    d, m = len(vectors[0]), len(vectors)
    if comb(m, d) > MAX_COMBINATIONS:
        raise ValueError(f"constant-minor analysis exceeds {MAX_COMBINATIONS} subsets")
    result = []
    for indices in combinations(range(m), d):
        minor = _constant_determinant([vectors[i] for i in indices])
        if minor:
            result.append((indices, minor * minor))
            if len(result) > MAX_NONZERO_TERMS:
                raise ValueError("nonzero polynomial term limit exceeded")
    return result


def _counts(data, counts):
    vectors, runs, _, caps, _ = _parsed(data)
    if not isinstance(counts, list) or len(counts) != len(vectors):
        raise ValueError("one integer count is required per candidate")
    counts = [_integer(value, "experiment count") for value in counts]
    if any(value < 0 or value > cap for value, cap in zip(counts, caps)):
        raise ValueError("experiment count violates its bounds")
    if sum(counts) != runs:
        raise ValueError("experiment counts do not sum to the run budget")
    return counts


def _information(vectors, counts, denominator=1):
    d = len(vectors[0])
    return [[sum(Fraction(count * row[p] * row[q], denominator * denominator)
                 for count, row in zip(counts, vectors)) for q in range(d)] for p in range(d)]


def determinant_value(data, counts):
    """Scaled determinant, including zero for degenerate allocations."""
    vectors, _, _, _, _ = _parsed(data)
    counts = _counts(data, counts)
    result = _fraction_determinant(_information(vectors, counts))
    if result.denominator != 1:
        raise ArithmeticError("integer information matrix had noninteger determinant")
    return result.numerator


def polynomial_value(data, counts):
    counts = _counts(data, counts)
    return sum(coefficient * prod(counts[i] for i in indices)
               for indices, coefficient in cauchy_binet_terms(data))


def _product(terms):
    terms = list(terms)
    return "1" if not terms else terms[0] if len(terms) == 1 else "(* " + " ".join(terms) + ")"


def _matrix_entry(vectors, p, q):
    return summation(_product((num(row[p] * row[q]), f"n_{i}"))
                     for i, row in enumerate(vectors) if row[p] and row[q])


def build(data):
    vectors, runs, denominator, caps, encoding = _parsed(data)
    d, m = len(vectors[0]), len(vectors)
    _encoding_limits(d, m, encoding)
    model = Model("Exact D-optimal design; nonnegative integer repetition counts; positive Gram determinant.")
    for i, cap in enumerate(caps):
        model.integer(f"n_{i}", 0, min(runs, cap), unit="experiment_runs")
    model.assertion(f"(= {summation(f'n_{i}' for i in range(m))} {num(runs)})")
    # The product of diagonal upper bounds bounds a positive-semidefinite determinant.
    diagonal_upper = [runs * max(row[p] * row[p] for row in vectors) for p in range(d)]
    cost_upper = prod(diagonal_upper)
    model.integer("cost", 0, cost_upper, role="objective", unit="scaled_information_determinant")
    model.assertion("(> cost 0)")
    if encoding == "cauchy-binet":
        polynomial = summation(_product([num(coefficient)] + [f"n_{i}" for i in indices])
                               for indices, coefficient in cauchy_binet_terms(data))
    else:
        for p in range(d):
            for q in range(p, d):
                magnitude = runs * max(abs(row[p] * row[q]) for row in vectors)
                lo = 0 if p == q else -magnitude
                model.integer(f"M_{p}_{q}", lo, magnitude, role="auxiliary", unit="scaled_information")
                model.assertion(f"(= M_{p}_{q} {_matrix_entry(vectors, p, q)})")
        terms = []
        for permutation in permutations(range(d)):
            inversions = sum(permutation[i] > permutation[j] for i in range(d) for j in range(i + 1, d))
            product = _product(f"M_{min(p, q)}_{max(p, q)}" for p, q in enumerate(permutation))
            terms.append(f"(- {product})" if inversions % 2 else product)
        polynomial = summation(terms)
    model.assertion(f"(= cost {polynomial})")
    if len(model.base().encode()) > MAX_BASE_BYTES:
        raise ValueError(f"SMT base exceeds {MAX_BASE_BYTES} bytes")
    return model


def check(data, decision):
    if not isinstance(decision, dict):
        raise ValueError("design decision must be an object")
    vectors, _, denominator, _, _ = _parsed(data)
    counts = _counts(data, decision.get("counts"))
    # Work in original rational units so the scaling relation is also checked.
    original = _fraction_determinant(_information(vectors, counts, denominator))
    if original <= 0:
        raise ValueError("information matrix is singular; a positive determinant is required")
    scale = denominator ** (2 * len(vectors[0]))
    scaled = original * scale
    if scaled.denominator != 1:
        raise ValueError("scaled determinant is not integral")
    cost = scaled.numerator
    if "cost" in decision and _integer(decision["cost"], "cost") != cost:
        raise ValueError("claimed cost differs from the matrix determinant")
    if "determinant" in decision and _rational(decision["determinant"]) != original:
        raise ValueError("claimed original determinant is incorrect")
    return {"cost": cost, "determinant": _fraction_json(original),
            "method": "original rational information matrix; Fraction Gaussian elimination"}


def assignment(data, decision):
    result = check(data, decision)
    vectors, _, _, _, encoding = _parsed(data)
    counts = _counts(data, decision["counts"])
    values = {f"n_{i}": count for i, count in enumerate(counts)}
    values["cost"] = result["cost"]
    if encoding == "determinant":
        matrix = _information(vectors, counts)
        for p in range(len(matrix)):
            for q in range(p, len(matrix)):
                values[f"M_{p}_{q}"] = int(matrix[p][q])
    return values


def decode(data, values):
    return {"counts": [values[f"n_{i}"] for i in range(len(_parsed(data)[0]))]}


def _initial_counts(data):
    vectors, runs, _, caps, _ = _parsed(data)
    d = len(vectors[0])
    available = [i for i, cap in enumerate(caps) if cap >= 1]
    if len(available) < d:
        raise ValueError("upper bounds do not permit a full-rank support")
    # Finite, explicitly bounded support search; no unbounded resampling.
    if comb(len(available), d) > MAX_COMBINATIONS:
        raise ValueError("full-rank witness search exceeds the subset limit")
    support = next((indices for indices in combinations(available, d)
                    if _constant_determinant([vectors[i] for i in indices]) != 0), None)
    if support is None:
        raise ValueError("candidate vectors and count bounds do not permit a full-rank witness")
    counts = [int(i in support) for i in range(len(vectors))]
    remaining = runs - d
    for i, cap in enumerate(caps):
        added = min(remaining, cap - counts[i])
        counts[i] += added
        remaining -= added
    if remaining:
        raise ValueError("insufficient count capacity for the run budget")
    return counts


def _data(profile, vectors, runs, encoding="cauchy-binet", denominator=1, caps=None):
    rows = [[str(_integer(x, "vector coefficient")) for x in row] for row in vectors]
    runs = _integer(runs, "runs")
    data = {"schema_version": "1.0", "profile": profile, "dimension": len(rows[0]) if rows else 0,
            "vectors": rows, "runs": runs, "common_vector_denominator": str(denominator),
            "upper_bounds": [str(runs)] * len(rows) if caps is None else [str(_integer(x, "upper bound")) for x in caps],
            "encoding": encoding}
    parsed = _parsed(data)
    _encoding_limits(len(parsed[0][0]), len(parsed[0]), encoding)
    return data


def _directions(vectors):
    counts = {}
    for row in vectors:
        divisor = 0
        for value in row:
            divisor = gcd(divisor, abs(value))
        if not divisor:
            direction = tuple(0 for _ in row)
        else:
            direction = tuple(value // divisor for value in row)
            if next(value for value in direction if value) < 0:
                direction = tuple(-value for value in direction)
        counts[direction] = counts.get(direction, 0) + 1
    return sum(value - 1 for value in counts.values())


def _case(data, label, seed=None, coefficient_bound=None, replay=False, witness=None):
    vectors, runs, denominator, caps, encoding = _parsed(data)
    d, m = len(vectors[0]), len(vectors)
    terms = cauchy_binet_terms(data)
    if not terms:
        raise ValueError("candidate matrix does not have full column rank")
    decision = {"counts": _initial_counts(data) if witness is None else witness}
    checked = check(data, decision)
    primitive = [{"num": str(Fraction(x, denominator).numerator), "den": str(Fraction(x, denominator).denominator)}
                 for row in vectors for x in row]
    application = {"dimension": d, "candidates": m, "runs": runs, "vectors_flat": primitive,
                   "upper_bounds": caps, "positive_determinant": True}
    fingerprint = hashlib.sha256(json.dumps(application, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    records = [source_record(PROMPT, "local-specification:prompts/04_d_optimal_design.txt", "conversion_and_generation_specification"),
               {"url": PAPER_URL, "role": "application_mathematical_model", "version": "arxiv:2312.11200v7", "cached": False},
               {"url": "https://doi.org/10.4230/LIPIcs.SEA.2024.16", "role": "related_publication", "cached": False}]
    if replay:
        records.append(source_record(MANIFEST, "local-reference:reference_starter/generated/manifest.json", "prior_synthetic_integer_input"))
    if data["profile"] in ("tiny", "tiny-rational"):
        records.append(source_record(FIXTURES, "local-regression:references/tiny_fixtures.json", "semantic_regression_input"))
    coefficients = [coefficient for _, coefficient in terms]
    metadata = {
        "profile": data["profile"], "origin": "new_application_family", "replay": replay,
        "model_changes": ["New exact integer/rational candidate vectors; not the paper's floating-point experimental data.",
                          "For PSD information matrices with determinant>0, maximize determinant is equivalent to minimize -log determinant.",
                          "NIA encoding implemented in this project; not attributed to the paper authors."] +
                         ([f"Exact vector denominator clearing by D={denominator}; determinant scale D^(2*d)."] if denominator != 1 else []),
        "objective": {"sense": "maximize", "symbol": "cost", "name": "information matrix determinant",
                      "unit": "feature_unit^(2*dimension)", "scale": str(denominator ** (2 * d)),
                      "recover": f"det(M_original) = cost / {denominator ** (2*d)}"},
        "nonlinearity": {"application_degree": d, "local_equation_degree": d,
                         "multivalued_integer_products": len(terms) if encoding == "cauchy-binet" else factorial(d),
                         "boolean_selection_products": 0},
        "source_records": records, "seed": seed, "semantic_fingerprint": fingerprint,
        "parameters": {"dimension": d, "candidates": m, "runs": runs, "seed": seed,
                       "coefficient_bound": coefficient_bound, "upper_bounds": list(map(str, caps)),
                       "encoding": encoding, "common_vector_denominator": str(denominator)},
        "generator_algorithm": "standard_basis_then_python.Random.randint/v1" if seed is not None and not replay else "explicit_exact_input/v1",
        "indexing": "zero-based candidate indices and SMT suffixes",
        "domain_source": "nonnegative counts and total run budget; optional explicit count upper bounds",
        "design": {"nonzero_cauchy_binet_terms": len(terms), "candidate_subsets": comb(m, d),
                   "maximum_coefficient": str(max(coefficients)), "maximum_coefficient_bits": max(coefficients).bit_length(),
                   "duplicate_vectors": m - len(set(map(tuple, vectors))),
                   "duplicate_or_proportional_vectors": _directions(vectors),
                   "zero_vectors": sum(not any(row) for row in vectors),
                   "limits": {"candidate_subsets": MAX_COMBINATIONS, "nonzero_terms": MAX_NONZERO_TERMS,
                              "base_bytes": MAX_BASE_BYTES, "determinant_dimension": MAX_DETERMINANT_DIMENSION}},
    }
    if data["profile"] in ("tiny", "tiny-rational"):
        metadata["expected_optimum"] = "3"
    elif replay and d == 3:
        metadata["expected_optimum"] = "1152"
    # Enforce the byte limit during generation, before publishing a case.
    build(data)
    return {"label": label, "data": data, "witness": {"decision": decision, "cost": str(checked["cost"])}, "metadata": metadata}


def make(profile, **params):
    accepted = {
        "tiny": {"encoding"}, "tiny-rational": {"encoding"},
        "reference": {"dimension", "encoding"},
        "integer": {"dimension", "candidates", "runs", "seed", "coefficient_bound", "upper_bounds", "encoding"},
        "rational": {"vectors", "integer_vectors", "common_vector_denominator", "runs", "upper_bounds", "encoding"},
    }
    if profile not in accepted:
        raise ValueError("unsupported design profile: " + str(profile))
    unknown = set(params) - accepted[profile]
    if unknown:
        raise ValueError("unknown design parameters: " + ", ".join(sorted(unknown)))
    encoding = params.get("encoding", "cauchy-binet")
    if profile in ("tiny", "tiny-rational"):
        denominator = 2 if profile == "tiny-rational" else 1
        data = _data(profile, [[1, 0], [0, 1], [1, 1]], 3, encoding, denominator)
        return [_case(data, profile + "-" + encoding, witness=[1, 1, 1])]
    if profile == "reference":
        references = [row for row in json.loads(MANIFEST.read_text()) if row["family"] == "design"]
        if params.get("dimension") is not None:
            dimension = _integer(params["dimension"], "dimension")
            references = [row for row in references if row["dimension"] == dimension]
            if not references:
                raise ValueError("reference dimension must be 3, 4, or 5")
        result = []
        for row in references:
            data = _data(profile, row["vectors"], row["budget"], encoding)
            witness = [2, 0, 0, 2, 2, 0] if row["dimension"] == 3 else None
            result.append(_case(data, row["name"] + "-reference-" + encoding,
                                seed=row["seed"], coefficient_bound=3, replay=True, witness=witness))
        return result
    if profile == "integer":
        dimension = _integer(params.get("dimension", 3), "dimension")
        candidates = _integer(params.get("candidates", 6), "candidates")
        runs = _integer(params.get("runs", 6), "runs")
        seed = _integer(params.get("seed", 1), "seed")
        coefficient_bound = _integer(params.get("coefficient_bound", 3), "coefficient_bound")
        if dimension < 1 or candidates < dimension or runs < dimension or coefficient_bound < 1:
            raise ValueError("require dimension>=1, candidates>=dimension, runs>=dimension, coefficient_bound>=1")
        _encoding_limits(dimension, candidates, encoding)
        # Metadata computes minors for either encoding, so bound that analysis too.
        if comb(candidates, dimension) > MAX_COMBINATIONS:
            raise ValueError("candidate-minor analysis would exceed the subset limit")
        vectors = [[int(i == j) for j in range(dimension)] for i in range(dimension)]
        rng = random.Random(seed)
        for _ in range(candidates - dimension):
            for attempt in range(128):
                vector = [rng.randint(-coefficient_bound, coefficient_bound) for _ in range(dimension)]
                if any(vector):
                    vectors.append(vector)
                    break
            else:
                raise ValueError("nonzero-vector generation exhausted 128 attempts")
        data = _data(profile, vectors, runs, encoding, caps=params.get("upper_bounds"))
        return [_case(data, f"integer-d{dimension}-m{candidates}-N{runs}-seed{seed}-{encoding}",
                      seed=seed, coefficient_bound=coefficient_bound)]
    if profile == "rational":
        if params.get("integer_vectors") is not None and params.get("vectors") is not None:
            raise ValueError("provide integer_vectors or rational vectors, not both")
        if params.get("vectors") is not None and "common_vector_denominator" in params:
            raise ValueError("a common denominator accompanies integer_vectors; rational text vectors determine their own denominator")
        if params.get("integer_vectors") is not None:
            denominator = _integer(params.get("common_vector_denominator", 1), "common vector denominator")
            data = _data(profile, params["integer_vectors"], params.get("runs"), encoding,
                         denominator, params.get("upper_bounds"))
        else:
            raw = params.get("vectors")
            if not isinstance(raw, list) or not raw or any(not isinstance(row, list) or not row for row in raw):
                raise ValueError("rational vectors must be a nonempty matrix")
            fractions = [[_rational(x) for x in row] for row in raw]
            denominator = lcm(*(value.denominator for row in fractions for value in row))
            vectors = [[int(value * denominator) for value in row] for row in fractions]
            data = _data(profile, vectors, params.get("runs"), encoding, denominator, params.get("upper_bounds"))
            data["original_vectors"] = [[_fraction_json(value) for value in row] for row in fractions]
        return [_case(data, f"rational-d{data['dimension']}-m{len(data['vectors'])}-N{data['runs']}-{encoding}")]
    raise ValueError("unsupported design profile: " + str(profile))


def smoke_specs():
    specs = [{"profile": "tiny"}, {"profile": "tiny-rational"}, {"profile": "reference"}]
    for dimension in (2, 3, 4, 5):
        for candidates in sorted({dimension + 2, 2 * dimension}):
            for runs in (dimension, 2 * dimension, 4 * dimension):
                for seed in (1, 2):
                    # This exact application is already replayed as reference d3.
                    if (dimension, candidates, runs, seed) == (3, 6, 6, 1):
                        continue
                    specs.append({"profile": "integer", "dimension": dimension, "candidates": candidates,
                                  "runs": runs, "seed": seed, "encoding": "cauchy-binet"})
    # Keep one explicit encoding cross-check sharing the tiny application's fingerprint.
    specs.append({"profile": "tiny", "encoding": "determinant"})
    return specs
