#!/usr/bin/env python3
"""Independently audit a retained native optimization run with Python integers.

Usage: python3 script/verify_optimize.py result/optimize/RUN

The default report is RUN/verification.json. This verifier never invokes a
solver. It validates the final retained model against the exported native
formula, aligns CSV rows and artifacts to the manifest, and audits objective
events, accepted-model timing, and any retained exact unbounded ray certificate.
Earlier incumbent assignments are not
retained, so their feasibility cannot be independently checked here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ROOT = Path("/data/shared/lbh/OMT/benchmarks")


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def integer(value, label="integer"):
    require(isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value) is not None,
            f"{label} must be an exact decimal string: {value!r}")
    return int(value)


def seconds(value, label):
    require(not isinstance(value, bool) and isinstance(value, (str, int, float)),
            f"{label} is not a number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise VerificationError(f"invalid {label}: {value!r}") from exc
    require(math.isfinite(result) and result >= 0, f"invalid {label}: {value!r}")
    return result


def decode_hex(value, label):
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise VerificationError(f"invalid {label} hex encoding") from exc


def parse_native(text):
    """Parse public NIA_LS_OMT 1 without importing native or test code."""
    lines = iter(line.split() for line in text.splitlines() if line.strip())

    def fields(label, count=None):
        values = next(lines, None)
        require(values is not None, f"missing {label}")
        if count is not None:
            require(len(values) == count, f"malformed {label}: {values!r}")
        return values

    def header(name, count):
        values = fields(name, count + 1)
        require(values[0] == name, f"expected {name}: {values!r}")
        return values[1:]

    def count_header(name):
        result = integer(header(name, 1)[0], name)
        require(result >= 0, f"negative {name} count")
        return result

    require(header("NIA_LS_OMT", 1) == ["1"], "unsupported native format version")
    status = header("STATUS", 1)[0]
    require(status in ("ready", "unsat"), "invalid native status")
    source = header("SOURCE", 1)[0]
    source = None if source == "-" else decode_hex(source, "source")
    decode_hex(header("ORIGINAL_OBJECTIVE", 1)[0], "original objective")
    direction = header("DIRECTION", 1)[0]
    require(direction in ("min", "max"), "invalid native direction")
    variables, names = {}, set()
    for _ in range(count_header("VARIABLES")):
        index, kind, name = fields("variable", 3)
        index, name = integer(index, "variable index"), decode_hex(name, "variable name")
        require(index >= 0 and index not in variables, "duplicate or negative variable index")
        require(kind in ("I", "B"), "invalid variable sort")
        require(name not in names, "duplicate native variable name")
        variables[index] = (kind, name)
        names.add(name)

    def polynomial(constant, term_count):
        constant, term_count = integer(constant), integer(term_count, "term count")
        require(term_count >= 0, "negative polynomial term count")
        terms = []
        for _ in range(term_count):
            term = fields("polynomial term")
            require(len(term) >= 2, "malformed polynomial term")
            coefficient, factor_count = integer(term[0]), integer(term[1], "factor count")
            require(factor_count >= 0 and len(term) == 2 + factor_count * 2,
                    "malformed polynomial factors")
            factors = []
            for offset in range(2, len(term), 2):
                index, exponent = integer(term[offset]), integer(term[offset + 1])
                require(index in variables and variables[index][0] == "I",
                        "polynomial references a missing or Boolean variable")
                require(exponent > 0, "nonpositive polynomial exponent")
                factors.append((index, exponent))
            terms.append((coefficient, factors))
        return constant, terms

    literals = {}
    for _ in range(count_header("LITERALS")):
        values = fields("literal")
        require(len(values) >= 2, "malformed literal")
        index, kind = integer(values[0]), values[1]
        require(index > 0 and index not in literals, "duplicate or nonpositive literal index")
        if kind == "B":
            require(len(values) == 3, "malformed Boolean literal")
            variable = integer(values[2])
            require(variable in variables and variables[variable][0] == "B",
                    "Boolean literal references a missing or integer variable")
            literals[index] = kind, variable
        else:
            require(kind in ("L", "E") and len(values) == 4, "malformed arithmetic literal")
            literals[index] = kind, polynomial(values[2], values[3])
    clauses = []
    for _ in range(count_header("CLAUSES")):
        values = fields("clause")
        require(values, "malformed clause")
        count = integer(values[0])
        require(count >= 0 and len(values) == count + 1, "malformed clause length")
        clause = [integer(value) for value in values[1:]]
        require(all(abs(value) in literals for value in clause), "unknown clause literal")
        clauses.append(clause)
    objective = polynomial(*header("OBJECTIVE", 2))
    header("END", 0)
    require(next(lines, None) is None, "trailing native input")
    return {"status": status, "source": source, "direction": direction,
            "variables": variables, "literals": literals, "clauses": clauses,
            "objective": objective}


def canonical_monomials(expression):
    """Merge repeated factors and cancel like terms before any exponentiation."""
    constant, terms = expression
    monomials = {(): constant}
    for coefficient, factors in terms:
        powers = Counter()
        for index, exponent in factors:
            powers[index] += exponent
        key = tuple(sorted(powers.items()))
        monomials[key] = monomials.get(key, 0) + coefficient
    return {powers: coefficient for powers, coefficient in monomials.items() if coefficient}


def evaluate_model(native, model_text):
    supplied = {}
    for line in model_text.splitlines():
        require("\t" in line, "model line has no tab separator")
        name, value = line.rsplit("\t", 1)
        require(name not in supplied, f"duplicate model variable {name!r}")
        supplied[name] = value
    expected = {name for _, name in native["variables"].values()}
    require(set(supplied) == expected,
            f"model variable mismatch; missing={sorted(expected - set(supplied))!r}, "
            f"extra={sorted(set(supplied) - expected)!r}")
    values = {}
    for index, (kind, name) in native["variables"].items():
        if kind == "B":
            require(supplied[name] in ("true", "false"), f"invalid Boolean model value for {name!r}")
            values[index] = supplied[name] == "true"
        else:
            values[index] = integer(supplied[name], f"model variable {name!r}")

    def polynomial(expression):
        result = 0
        for factors, coefficient in canonical_monomials(expression).items():
            if any(values[index] == 0 for index, _ in factors):
                continue
            term = coefficient
            for index, exponent in factors:
                term *= values[index] ** exponent
            result += term
        return result

    require(native["status"] == "ready", "model supplied for preprocessing-UNSAT native formula")
    truths = {}
    for index, (kind, expression) in native["literals"].items():
        if kind == "B":
            truths[index] = values[expression]
        else:
            value = polynomial(expression)
            truths[index] = value == 0 if kind == "E" else value <= 0
    for index, clause in enumerate(native["clauses"]):
        require(any(truths[abs(value)] == (value > 0) for value in clause),
                f"model violates native hard clause {index}")
    return polynomial(native["objective"])


def verify_unbounded_certificate(native, certificate, model_text=None):
    """Check an integer affine ray with exact arithmetic; return its base objective.

    Only explicitly fixed integer variables are substituted before canonical
    monomials are combined. Every selected literal must then be affine, remain
    true for every integer t >= 0, and collectively cover the full hard CNF.
    """
    require(isinstance(certificate, dict), "unbounded certificate must be an object")
    require(certificate.get("kind") == "lia_ray" and type(certificate.get("version")) is int
            and certificate["version"] == 1, "unsupported unbounded certificate kind/version")
    variables = native["variables"]
    require(set(variables) == set(range(len(variables))), "ray requires contiguous native variable indices")
    base, direction = certificate.get("base"), certificate.get("direction")
    require(isinstance(base, list) and isinstance(direction, list)
            and len(base) == len(direction) == len(variables), "unbounded base/direction variable count mismatch")
    base = [integer(value, "unbounded base value") for value in base]
    direction = [integer(value, "unbounded direction value") for value in direction]
    fixed, chosen = certificate.get("fixed"), certificate.get("literals")
    require(isinstance(fixed, list) and all(type(index) is int and index in variables
            and variables[index][0] == "I" for index in fixed)
            and len(set(fixed)) == len(fixed), "invalid unbounded fixed variable set")
    require(isinstance(chosen, list) and all(type(literal) is int and abs(literal) in native["literals"]
            for literal in chosen) and len(set(chosen)) == len(chosen), "invalid unbounded chosen literal set")
    fixed, chosen = set(fixed), set(chosen)
    rows = []
    for index, (kind, name) in variables.items():
        if kind == "B":
            require(base[index] in (-1, 1), "unbounded Boolean base must be -1 or 1")
        require((index not in fixed and kind != "B") or direction[index] == 0,
                "unbounded fixed/Boolean variable has nonzero direction")
        rows.append(name + "\t" + (str(base[index]) if kind == "I" else
                                    ("true" if base[index] == 1 else "false")))
    base_objective = evaluate_model(native, "\n".join(rows) + ("\n" if rows else ""))
    if model_text is not None:
        require(evaluate_model(native, model_text) == base_objective,
                "unbounded certificate base objective differs from retained model")
        supplied = dict(line.rsplit("\t", 1) for line in model_text.splitlines())
        for index, (kind, name) in variables.items():
            actual = integer(supplied[name], "retained model value") if kind == "I" else (1 if supplied[name] == "true" else -1)
            require(actual == base[index], "unbounded certificate base differs from retained model")

    def affine(expression):
        monomials = {}
        for factors, coefficient in canonical_monomials(expression).items():
            if any(index in fixed and base[index] == 0 for index, _ in factors):
                continue
            powers = Counter()
            for index, exponent in factors:
                if index in fixed:
                    coefficient *= base[index] ** exponent
                else:
                    powers[index] += exponent
            if coefficient:
                key = tuple(sorted(powers.items()))
                monomials[key] = monomials.get(key, 0) + coefficient
        value, slope = 0, 0
        for powers, coefficient in monomials.items():
            if not coefficient:
                continue
            if not powers:
                value += coefficient
                continue
            require(len(powers) == 1 and powers[0][1] == 1,
                    "unbounded certificate contains a nonlinear residual polynomial")
            index = powers[0][0]
            value += coefficient * base[index]
            slope += coefficient * direction[index]
        return value, slope

    for signed in chosen:
        kind, expression = native["literals"][abs(signed)]
        if kind == "B":
            require((base[expression] == 1) == (signed > 0), "chosen Boolean literal is false at ray base")
            continue
        value, slope = affine(expression)
        if kind == "L":
            require((value <= 0 and slope <= 0) if signed > 0 else (value >= 1 and slope >= 0),
                    "chosen inequality does not hold along the unbounded ray")
        elif signed > 0:
            require(value == 0 and slope == 0, "chosen equality does not hold along the unbounded ray")
        else:
            require((value >= 1 and slope >= 0) or (value <= -1 and slope <= 0),
                    "chosen disequality crosses zero along the unbounded ray")
    require(all(any(literal in chosen for literal in clause) for clause in native["clauses"]),
            "unbounded chosen literals do not cover every hard clause")
    value, slope = affine(native["objective"])
    require(value == base_objective, "unbounded affine objective differs from base objective")
    require(slope == integer(certificate.get("objective_slope"), "unbounded objective slope"),
            "unbounded objective slope disagrees with exact direction")
    require(slope < 0 if native["direction"] == "min" else slope > 0,
            "unbounded objective slope does not strictly improve")
    return base_objective


def json_lines(path):
    if not path.is_file():
        return []
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise VerificationError(f"invalid JSON at {path.name}:{number}") from exc
        require(isinstance(value, dict), f"nonobject JSON at {path.name}:{number}")
        result.append(value)
    return result


def better(value, reference, direction):
    return value < reference if direction == "min" else value > reference


def compare(value, reference, direction):
    if reference is None:
        return "no_reference"
    if value is None:
        return "no_incumbent"
    return "tie" if value == reference else ("better" if better(value, reference, direction) else "worse")


def historical_integer(value):
    """Independently decode finite decimal and SMT-LIB unary-minus references."""
    if value is None or not value.strip():
        return None
    tokens = iter(re.findall(r"\(|\)|[^\s()]+", value))

    def expression():
        token = next(tokens, None)
        if token == "(":
            require(next(tokens, None) == "-", "reference is not a finite integer")
            result = -expression()
            require(next(tokens, None) == ")", "malformed unary-minus reference")
            return result
        require(isinstance(token, str) and re.fullmatch(r"[+-]?[0-9]+", token) is not None,
                f"reference is not a finite integer: {value!r}")
        return int(token)

    result = expression()
    require(next(tokens, None) is None, "trailing historical reference expression")
    return result


def historical_selection(subset, keys=None, include_proofs=False):
    """Read historical references and effective source directions without the runner."""
    require(subset in ("unsolved_bounded_sol", "bounded", "excluded", "all"), "invalid historical subset")
    try:
        from smtlib import objective_sense
    except ModuleNotFoundError:
        from script.smtlib import objective_sense
    selected, tables = {}, []
    for suite in ("original", "new"):
        def entries(listing, inspect_all=False):
            tables.append({"path": str(listing), "sha256": hashlib.sha256(listing.read_bytes()).hexdigest()})
            found, identities, sources = [], set(), set()
            with listing.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                required = {"filename", "z3_best_value", "optimathsat_best_value"}
                if suite == "new":
                    required.add("category")
                require(required.issubset(reader.fieldnames or []), "canonical subset table has missing columns")
                for row in reader:
                    name = row["filename"]
                    category = "original" if suite == "original" else row["category"]
                    key = suite, category, name
                    if not inspect_all and keys is not None and key not in keys:
                        continue
                    require(category and category not in (".", "..") and Path(category).name == category,
                            "invalid canonical category")
                    base = ORIGINAL_ROOT if suite == "original" else ROOT / "new_benchmark" / category / "instance"
                    source = (base / name).resolve() if name else base.resolve()
                    require(name and source.is_relative_to(base.resolve()) and source.is_file()
                            and source.suffix == ".smt2", "invalid canonical benchmark source")
                    if suite == "new":
                        require(Path(name).name == name, "generated canonical filename is not a basename")
                    require(key not in identities and source not in sources, "duplicate canonical case/source")
                    identities.add(key)
                    sources.add(source)
                    found.append((key, source, row))
            return found

        if subset in ("excluded", "all"):
            full = entries(ROOT / "result" / suite / f"{suite}.csv", inspect_all=True)
            chosen = full
            if subset == "excluded":
                bounded = entries(ROOT / "result" / suite / f"{suite}_bound.csv", inspect_all=True)
                full_sources = {source for _, source, _ in full}
                bounded_sources = {source for _, source, _ in bounded}
                require(bounded_sources <= full_sources, "historical bounded source is absent from full table")
                chosen = [entry for entry in full if entry[1] not in bounded_sources]
        else:
            suffix = "bound" if subset == "bounded" else subset
            chosen = entries(ROOT / "result" / suite / f"{suite}_{suffix}.csv")
        for key, source, row in chosen:
            if keys is not None and key not in keys:
                continue
            direction = {"minimize": "min", "maximize": "max"}[objective_sense(source.read_text(encoding="utf-8"))]
            references = [historical_integer(row[field]) for field in ("z3_best_value", "optimathsat_best_value")]
            if subset == "unsolved_bounded_sol":
                require(any(value is not None for value in references), "canonical case lacks a finite historical reference")
            require(key not in selected, "duplicate canonical case identity")
            selected[key] = {"source": str(source), "direction": direction,
                             "z3": None if references[0] is None else str(references[0]),
                             "optimathsat": None if references[1] is None else str(references[1])}
            if include_proofs:
                proofs = [(solver, value) for solver, value in zip(("z3", "optimathsat"), references)
                          if value is not None
                          and (row.get(solver + "_is_optimal") or "").strip().lower() == "true"]
                optimum, provenance = None, ""
                if proofs:
                    candidate = proofs[0][1]
                    if (all(value == candidate for _, value in proofs)
                            and not any(value is not None and better(value, candidate, direction)
                                        for value in references)):
                        optimum = str(candidate)
                        provenance = "+".join(solver for solver, _ in proofs)
                selected[key].update(known_optimum_value=optimum, known_optimum_source=provenance)
    require(len({case["source"] for case in selected.values()}) == len(selected), "duplicate canonical source")
    return selected, tables


def canonical_selection(subset="unsolved_bounded_sol"):
    """Read a complete canonical subset; preserve the historical default contract."""
    selected, tables = historical_selection(subset)
    expected = {"bounded": 4262, "all": 6310}.get(subset, 473)
    require(len(selected) == expected,
            f"canonical subset denominator is {len(selected)}, expected {expected}")
    return selected, tables


LARGE_TARGET_FIELDS = ("large_target_magnitude", "large_target_attempts", "large_target_slices",
                       "large_target_seconds", "large_target_reached", "large_target_status")
UNBOUNDED_FIELDS = ("is_unbounded", "unbounded_threshold", "unbounded_attempts", "unbounded_queries",
                    "unbounded_seconds", "unbounded_status", "time_to_unbounded_seconds")
UNBOUNDED_NATIVE_FIELDS = UNBOUNDED_FIELDS + ("unbounded_certificate",)
OPTIMALITY_FIELDS = ("prove_optimal", "optimality_stall_seconds", "optimality_timeout_seconds",
                     "optimality_attempts", "optimality_seconds", "optimality_status",
                     "time_to_optimality_seconds", "optimality_proved_value")
OPTIMALITY_ADAPTIVE_FIELDS = ("optimality_schedule", "optimality_next_timeout_seconds",
                             "optimality_sat_models_accepted")


def validate_optimality_adaptive_metadata(final, enabled, best):
    """Validate optional adaptive accounting without capping it at the initial slice."""
    if not any(field in final for field in OPTIMALITY_ADAPTIVE_FIELDS):
        return
    require(all(field in final for field in OPTIMALITY_ADAPTIVE_FIELDS),
            "incomplete native adaptive optimality metadata")
    require(final["optimality_schedule"] == "adaptive_v1", "unknown native optimality schedule")
    next_timeout = final["optimality_next_timeout_seconds"]
    require(isinstance(next_timeout, (int, float)) and not isinstance(next_timeout, bool)
            and math.isfinite(next_timeout) and next_timeout > 0,
            "invalid native optimality_next_timeout_seconds")
    accepted = final["optimality_sat_models_accepted"]
    require(type(accepted) is int and 0 <= accepted <= final["optimality_attempts"],
            "invalid native optimality_sat_models_accepted")
    require(not accepted or (enabled and best is not None),
            "adaptive optimality SAT model lacks enabled proof search and an incumbent")


def validate_optimality_metadata(final, enabled, stall, timeout, best, direction,
                                native_budget, best_elapsed=None):
    """Check the native finite-UNSAT report; this does not replay its proof."""
    require(all(field in final for field in OPTIMALITY_FIELDS), "incomplete native optimality metadata")
    require(type(final["prove_optimal"]) is bool and final["prove_optimal"] == enabled,
            "native prove_optimal disagrees with configuration")
    for field, expected in (("optimality_stall_seconds", stall), ("optimality_timeout_seconds", timeout)):
        value = final[field]
        require(isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0 and value == expected,
                "native " + field + " disagrees with configuration")
    attempts = final["optimality_attempts"]
    require(type(attempts) is int and attempts >= 0, "invalid native optimality_attempts")
    validate_optimality_adaptive_metadata(final, enabled, best)
    duration = final["optimality_seconds"]
    require(isinstance(duration, (int, float)) and not isinstance(duration, bool),
            "native optimality_seconds must be numeric")
    duration = seconds(duration, "native optimality_seconds")
    elapsed = seconds(final.get("elapsed_seconds"), "native elapsed_seconds")
    require(duration <= elapsed + 1e-6, "native optimality_seconds exceeds elapsed time")
    status = final["optimality_status"]
    require(status in ("disabled", "waiting", "sat", "unknown", "error", "budget", "proved"),
            "invalid native optimality_status")
    require((status == "disabled") == (not enabled), "native optimality status contradicts configuration")
    require(type(final.get("is_optimal")) is bool, "invalid native is_optimal flag")
    require((status == "proved") == final["is_optimal"], "native optimality proof flag/status disagree")
    require(attempts or duration == 0, "native optimality activity without an attempt")
    require(status != "waiting" or attempts == 0, "native optimality waiting status has an attempt")
    require(status not in ("proved", "sat", "unknown") or attempts > 0,
            "native optimality result lacks its attempt")
    if not enabled:
        require(attempts == 0 and duration == 0, "disabled optimality proof has activity")
    if final["is_optimal"]:
        require(enabled and best is not None and final.get("event") == "final"
                and final.get("status") == "sat" and final.get("model_validated") is True
                and final.get("termination") == "optimality_proved"
                and final.get("direction") == direction and final.get("is_unbounded") is not True,
                "native optimality proof lacks a validated finite SAT result and proof termination")
        require(integer(final.get("optimality_proved_value"), "native optimality_proved_value") == best
                and integer(final.get("objective_value"), "native optimality objective") == best,
                "native optimality proved value differs from retained incumbent")
        completion = final["time_to_optimality_seconds"]
        require(isinstance(completion, (int, float)) and not isinstance(completion, bool),
                "native time_to_optimality_seconds must be numeric")
        completion = seconds(completion, "native time_to_optimality_seconds")
        require(duration <= completion + 1e-6 and completion <= elapsed
                and elapsed <= native_budget, "native optimality proof completion is not timely")
        if best_elapsed is None:
            best_elapsed = final.get("time_to_best_seconds")
        require(best_elapsed is not None and completion + 1e-6 >= seconds(best_elapsed, "native best time"),
                "native optimality proof precedes its incumbent")
    else:
        require(final["time_to_optimality_seconds"] is None and final["optimality_proved_value"] is None,
                "native unproved result carries an optimality proof time/value")
        require(final.get("termination") != "optimality_proved", "optimality termination lacks proof")
    return final["is_optimal"]


def audit_optimality(final, row, enabled, stall, timeout, best, direction, budget, wall, offset):
    require(all(field in row for field in OPTIMALITY_FIELDS), "missing CSV optimality metadata")
    require(row["prove_optimal"] == str(enabled).lower(), "CSV prove_optimal disagrees with manifest")
    for field, value in (("optimality_stall_seconds", stall), ("optimality_timeout_seconds", timeout)):
        require(seconds(row[field], "CSV " + field) == value, "CSV " + field + " disagrees with manifest")
    require(row["is_optimal"] in ("true", "false"), "invalid CSV is_optimal")
    if final is None or not any(field in final for field in OPTIMALITY_FIELDS):
        require(not enabled or final is None, "proof-enabled native final lacks optimality metadata")
        require(row["is_optimal"] == "false" and row["time_to_optimality_seconds"] == ""
                and row["optimality_proved_value"] == "", "CSV claims optimality without native proof")
        for field in ("optimality_attempts", "optimality_seconds", "optimality_status"):
            expected = "" if enabled else ("disabled" if field == "optimality_status" else "0")
            require(row[field] == expected, "unexpected default CSV " + field)
        require(all(row.get(field, "") == "" for field in OPTIMALITY_ADAPTIVE_FIELDS),
                "CSV adaptive optimality metadata lacks a native final")
        return False
    claimed = validate_optimality_metadata(final, enabled, stall, timeout, best, direction,
                                           max(0, budget - (offset or 0)))
    for field in ("optimality_attempts", "optimality_seconds", "optimality_status"):
        require(row[field] == str(final[field]), "CSV/native " + field + " mismatch")
    for field in OPTIMALITY_ADAPTIVE_FIELDS:
        require(row.get(field, "") == (str(final[field]) if field in final else ""),
                "CSV/native " + field + " mismatch")
    accepted = (claimed and offset is not None and final["elapsed_seconds"] + offset <= budget
                and row["status"] == "sat"
                and row["termination"] not in ("wall_timeout", "interrupted", "error", "worker_error"))
    require(row["is_optimal"] == str(accepted).lower(), "CSV/native accepted optimality mismatch")
    if accepted:
        completion = seconds(row["time_to_optimality_seconds"], "CSV time_to_optimality_seconds")
        require(abs(completion - (final["time_to_optimality_seconds"] + offset)) <= .00000051
                and completion <= wall + 1e-6, "CSV optimality proof time mismatch")
        require(row["optimality_proved_value"] == final["optimality_proved_value"],
                "CSV optimality proved value mismatch")
    else:
        require(row["time_to_optimality_seconds"] == row["optimality_proved_value"] == "",
                "unaccepted optimality proof has CSV time/value")
    return accepted


def validate_unbounded_metadata(final, enabled, threshold, best, direction, native_budget, best_elapsed=None):
    """Validate native ray accounting independently of whether its final met our deadline."""
    require(all(field in final for field in UNBOUNDED_NATIVE_FIELDS), "incomplete native unbounded metadata")
    expected_threshold = str(threshold) if enabled else None
    require(final["unbounded_threshold"] == expected_threshold,
            "native unbounded_threshold disagrees with configuration")
    require(type(final["is_unbounded"]) is bool, "invalid native is_unbounded flag")
    for field in ("unbounded_attempts", "unbounded_queries"):
        require(type(final[field]) is int and final[field] >= 0, "invalid native " + field)
    attempts, queries = final["unbounded_attempts"], final["unbounded_queries"]
    require(attempts <= 1, "native unbounded proof attempted more than once")
    duration = final["unbounded_seconds"]
    require(isinstance(duration, (int, float)) and not isinstance(duration, bool),
            "native unbounded_seconds must be numeric")
    duration = seconds(duration, "native unbounded_seconds")
    elapsed = seconds(final.get("elapsed_seconds"), "native elapsed_seconds")
    # The probe allowance schedules work; GMP/Z3 steps and destruction cannot
    # be preempted. Acceptance instead enforces the complete case deadline.
    require(duration <= elapsed + 1e-6, "native unbounded_seconds exceeds native elapsed time")
    status = final["unbounded_status"]
    require(status in ("disabled", "waiting", "not_found", "unknown", "budget", "proved"),
            "invalid native unbounded_status")
    require((status == "disabled") == (not enabled), "native unbounded status contradicts configuration")
    require((status == "proved") == final["is_unbounded"], "native unbounded proof flag/status disagree")
    require(attempts or (queries == duration == 0), "native unbounded activity without an attempt")
    require(status != "waiting" or attempts == 0, "native unbounded waiting status has an attempt")
    require(status not in ("proved", "not_found", "unknown") or attempts == 1,
            "native unbounded result lacks its attempt")
    if attempts:
        require(enabled and best is not None and (best <= -threshold if direction == "min" else best >= threshold),
                "native unbounded attempt lacks a threshold-reaching incumbent")
    if not enabled:
        require(attempts == queries == duration == 0, "disabled unbounded proof has activity")
    if final["is_unbounded"]:
        require(queries > 0 and final.get("status") == "sat" and final.get("model_validated") is True
                and final.get("is_optimal") is False and final.get("event") == "final"
                and final.get("termination") == "unbounded_proved",
                "native unbounded proof lacks validated SAT result and proof termination")
        require(integer(final.get("objective_value"), "native unbounded objective") == best,
                "native unbounded objective differs from retained incumbent")
        require(isinstance(final["unbounded_certificate"], dict), "native unbounded proof lacks certificate")
        completion = final["time_to_unbounded_seconds"]
        require(isinstance(completion, (int, float)) and not isinstance(completion, bool),
                "native time_to_unbounded_seconds must be numeric")
        completion = seconds(completion, "native time_to_unbounded_seconds")
        require(duration <= completion + 1e-6 and completion <= elapsed,
                "native unbounded proof completion time contradicts elapsed time")
        if best_elapsed is None:
            best_elapsed = final.get("time_to_best_seconds")
        if best_elapsed is not None:
            require(completion - duration + 1e-6 >= seconds(best_elapsed, "native best incumbent time"),
                    "native unbounded proof starts before its validated base incumbent")
    else:
        require(final["time_to_unbounded_seconds"] is None and final["unbounded_certificate"] is None,
                "native unproved result carries a proof time/certificate")
        require(final.get("termination") != "unbounded_proved", "unbounded termination lacks proof")
    return final["is_unbounded"]


def audit_unbounded(final, row, enabled, threshold, native, model_text, best, direction, budget, wall, offset):
    require(all(field in row for field in UNBOUNDED_FIELDS), "missing CSV unbounded metadata")
    require(row["unbounded_threshold"] == (str(threshold) if enabled else ""),
            "CSV unbounded_threshold disagrees with manifest")
    require(row["is_unbounded"] in ("true", "false"), "invalid CSV is_unbounded")
    if "prove_unbounded" in row:
        require(row["prove_unbounded"] == str(enabled).lower(), "CSV prove_unbounded disagrees with manifest")
    if final is None or not any(field in final for field in UNBOUNDED_NATIVE_FIELDS):
        require(final is None or not enabled, "enabled native final lacks unbounded metadata")
        require(row["is_unbounded"] == "false" and row["time_to_unbounded_seconds"] == "",
                "CSV claims unobserved unbounded proof")
        expected = {"unbounded_attempts": "", "unbounded_queries": "", "unbounded_seconds": "", "unbounded_status": ""}
        if not enabled:
            expected.update(unbounded_attempts="0", unbounded_queries="0", unbounded_seconds="0", unbounded_status="disabled")
        require(all(row[field] == value for field, value in expected.items()),
                "CSV claims unobserved unbounded accounting")
        return False
    claimed = validate_unbounded_metadata(final, enabled, threshold, best, direction, max(0, budget - (offset or 0)))
    for field in ("unbounded_attempts", "unbounded_queries", "unbounded_status"):
        require(row[field] == str(final[field]), "CSV " + field + " disagrees with native final")
    require(abs(seconds(row["unbounded_seconds"], "CSV unbounded_seconds") - final["unbounded_seconds"]) <= 1e-9,
            "CSV unbounded_seconds disagrees with native final")
    accepted = False
    if claimed:
        require(native is not None and model_text is not None, "unbounded proof lacks retained native input/model")
        require(verify_unbounded_certificate(native, final["unbounded_certificate"], model_text) == best,
                "unbounded proof base differs from CSV best")
        require(offset is not None, "unbounded proof lacks conversion/native time offset")
        accepted = seconds(final["elapsed_seconds"], "native elapsed_seconds") + offset <= budget
        accepted = accepted and row["termination"] not in ("wall_timeout", "interrupted", "error", "worker_error")
        if accepted:
            require(row["termination"] == "unbounded_proved" and row["status"] == "sat",
                    "accepted unbounded proof requires SAT unbounded_proved termination")
            completion = final["time_to_unbounded_seconds"] + offset
            require(abs(seconds(row["time_to_unbounded_seconds"], "CSV time_to_unbounded_seconds") - completion) <= .00000051
                    and completion <= budget and completion <= wall + 1e-6,
                    "CSV unbounded completion time disagrees with within-budget native proof")
    if not accepted:
        require(row["time_to_unbounded_seconds"] == "", "CSV claims an unaccepted unbounded completion time")
    require(row["is_unbounded"] == str(accepted).lower(), "CSV is_unbounded disagrees with independently accepted proof")
    return accepted


def large_target_magnitude(value):
    if value is None:
        return None
    magnitude = integer(value, "large_target_magnitude")
    require(0 < magnitude <= 2**127 - 1, "large_target_magnitude must be positive and fit signed 128 bits")
    return magnitude


def audit_large_target(final, row, magnitude, best, direction, budget, wall):
    """Independently check finite-threshold accounting; it grants no proof status."""
    require(all(field in row for field in LARGE_TARGET_FIELDS), "missing CSV large_target metadata")
    require(row["large_target_magnitude"] == ("" if magnitude is None else str(magnitude)),
            "CSV large_target_magnitude disagrees with manifest")
    reached = magnitude is not None and best is not None and (
        best <= -magnitude if direction == "min" else best >= magnitude)
    native_fields = final is not None and any(field in final for field in LARGE_TARGET_FIELDS)
    if not native_fields:
        require(final is None or magnitude is None, "native final lacks large_target metadata")
        for field in ("large_target_attempts", "large_target_slices", "large_target_seconds", "large_target_status"):
            require(row[field] == "", f"CSV {field} claims unobserved accounting without a native final")
        require(row["large_target_reached"] == ("true" if reached else ""),
                "CSV large_target_reached must leave an unobserved failure unknown")
        return reached
    require(all(field in final for field in LARGE_TARGET_FIELDS), "incomplete native large_target metadata")
    require(large_target_magnitude(final["large_target_magnitude"]) == magnitude,
            "native large_target_magnitude disagrees with manifest")
    for field in ("large_target_attempts", "large_target_slices"):
        require(type(final[field]) is int and final[field] >= 0, f"invalid native {field}")
        require(row[field] == str(final[field]), f"CSV {field} disagrees with native final")
    duration = final["large_target_seconds"]
    require(isinstance(duration, (int, float)) and not isinstance(duration, bool),
            "native large_target_seconds must be numeric")
    duration = seconds(duration, "native large_target_seconds")
    require(duration <= budget + .25 and duration <= wall + .05
            and duration <= seconds(final["elapsed_seconds"], "native elapsed_seconds") + .05,
            "native large_target_seconds exceeds the available case time")
    require(abs(seconds(row["large_target_seconds"], "CSV large_target_seconds") - duration) <= 1e-9,
            "CSV large_target_seconds disagrees with native final")
    require(type(final["large_target_reached"]) is bool and final["large_target_reached"] == reached,
            "native large_target_reached disagrees with the retained validated objective")
    require(row["large_target_reached"] == str(reached).lower(), "incorrect CSV large_target_reached")
    status = final["large_target_status"]
    require(status in ("disabled", "waiting", "searching", "reached", "exhausted", "unsupported", "budget")
            and row["large_target_status"] == status,
            "CSV/native large_target_status disagrees or is missing")
    require((status == "reached") == reached and (status == "disabled") == (magnitude is None),
            "native large_target_status contradicts configuration or validated reached flag")
    if magnitude is None:
        require(final["large_target_attempts"] == final["large_target_slices"] == duration == 0
                and status == "disabled", "disabled large_target reports active search accounting")
    return reached


def verify_case(case, row, artifact, budget, use_target, known_optimum=None,
                known_stop_enabled=False, known_metadata=False, large_target=None, large_metadata=False,
                unbounded_enabled=False, unbounded_threshold=1000000, unbounded_metadata=False,
                optimality_enabled=False, optimality_stall=50.0, optimality_timeout=30.0,
                optimality_metadata=False):
    result = {"filename": case["filename"], "category": case["category"],
              "suite": case["suite"], "artifact": artifact.name, "ok": False,
              "model_checked": False, "incumbent_events_checked": 0}
    try:
        require(artifact.is_dir(), "missing case artifact directory")
        direction = case["direction"]
        require(direction in ("min", "max") and row["direction"] == direction,
                "CSV objective direction disagrees with manifest")
        require(row["is_optimal"] == "false" or optimality_enabled,
                "CSV claims unproved optimality without opt-in configuration")
        best = None if row["best_value"] == "" else integer(row["best_value"], "CSV best value")
        references = []
        for manifest_key, csv_key in (("z3", "z3_best_value"), ("optimathsat", "optimathsat_best_value")):
            raw = case.get(manifest_key)
            reference = None if raw is None else integer(raw, manifest_key + " reference")
            require(row[csv_key] == ("" if reference is None else str(reference)),
                    f"CSV {csv_key} disagrees with manifest")
            references.append(reference)
        available = [value for value in references if value is not None]
        reference = (min(available) if direction == "min" else max(available)) if available else None
        require(row["reference_best_value"] == ("" if reference is None else str(reference)),
                "incorrect CSV best reference")
        for key, opponent in zip(("vs_z3", "vs_optimathsat", "vs_best"), references + [reference]):
            require(row[key] == compare(best, opponent, direction), f"incorrect CSV {key}")
        declared_win = best is not None and reference is not None and better(best, reference, direction)
        require(row["strictly_better_than_best"] == str(declared_win).lower(), "incorrect CSV strict-win flag")
        wall = seconds(row["wall_seconds"], "CSV wall_seconds")
        result["wall_overhead_seconds"] = max(0.0, wall - budget)
        require(row["model_validated"] == ("true" if best is not None else "false"),
                "CSV model_validated disagrees with best value")
        require(row["status"] in ("sat", "unknown", "unsat", "timeout", "error", "interrupted"),
                "invalid CSV execution status")
        require(row["status"] != "sat" or best is not None, "CSV SAT has no incumbent")
        known_hit = False
        if known_metadata:
            require(row.get("known_optimum_value") == ("" if known_optimum is None else str(known_optimum)),
                    "CSV known optimum value disagrees with historical proof metadata")
            require(row.get("known_optimum_source") == case["known_optimum_source"],
                    "CSV known optimum source disagrees with historical proof metadata")
            known_hit = row["status"] == "sat" and best is not None and best == known_optimum
            # Historical-value equality remains a numeric comparison even if
            # an exact ray independently proves the native problem unbounded.
            require(row.get("known_optimum_reached") == str(known_hit).lower(),
                    "incorrect CSV known optimum reached flag")

        events = json_lines(artifact / "native.stdout")
        incumbents, final, previous_time = [], None, -1.0
        for event in events:
            require(final is None, "native event appears after the final record")
            require(event.get("event") in ("incumbent", "final"), "unknown native event kind")
            elapsed = seconds(event.get("elapsed_seconds"), "native elapsed_seconds")
            require(elapsed >= previous_time, "native event times decrease")
            previous_time = elapsed
            require(event.get("direction") == direction, "native event direction disagrees with manifest")
            require(event.get("is_optimal") is False or (optimality_enabled and event.get("event") == "final"),
                    "native event claims unproved optimality without opt-in final proof")
            if event["event"] == "incumbent":
                require(event.get("status") == "sat" and event.get("model_validated") is True,
                        "incumbent lacks a validated SAT status")
                value = integer(event.get("objective_value"), "incumbent objective")
                require(not incumbents or better(value, incumbents[-1][0], direction),
                        "incumbent objectives do not strictly improve")
                incumbents.append((value, elapsed))
            else:
                final = event
                require(event.get("status") in ("sat", "unknown", "unsat", "error"), "invalid final status")
                if event["status"] == "sat":
                    require(incumbents and event.get("model_validated") is True, "final SAT lacks an incumbent")
                    require(integer(event.get("objective_value"), "final objective") == incumbents[-1][0],
                            "final objective differs from last native incumbent")
                    if "time_to_best_seconds" in event:
                        require(abs(seconds(event["time_to_best_seconds"], "native time_to_best_seconds")
                                    - incumbents[-1][1]) <= 0.00000051,
                                "native final time_to_best_seconds differs from last incumbent")
                else:
                    require(event.get("objective_value") is None and event.get("model_validated") is False,
                            "non-SAT final record claims a model")
                    require(not incumbents, "non-SAT final contradicts earlier incumbent")
        result["incumbent_events_checked"] = len(incumbents)
        if final is not None and "optimality_sat_models_accepted" in final:
            accepted_models = final["optimality_sat_models_accepted"]
            require(type(accepted_models) is int and 0 <= accepted_models <= len(incumbents),
                    "adaptive optimality SAT count exceeds validated incumbent events")
        require(large_metadata or final is None or not any(field in final for field in LARGE_TARGET_FIELDS),
                "native large_target metadata lacks manifest configuration")
        require(unbounded_metadata or final is None or not any(field in final for field in UNBOUNDED_NATIVE_FIELDS),
                "native unbounded metadata lacks manifest configuration")
        require(optimality_metadata or final is None or not any(field in final for field in OPTIMALITY_FIELDS),
                "native optimality metadata lacks manifest configuration")
        require(row["termination"] != "unbounded_proved" or unbounded_metadata,
                "unbounded termination lacks proof configuration and metadata")
        if row["termination"] == "known_optimum_reached":
            require(known_metadata and known_stop_enabled and known_hit,
                    "known optimum termination requires enabled stopping and exact validated equality")
            require(not row.get("error") and (final is None or (
                    final["status"] == "sat" and final.get("termination") not in ("error", "worker_error")
                    and final.get("error") in (None, ""))),
                    "known optimum termination cannot hide a native/CSV error")
        elif row["termination"] not in ("wall_timeout", "interrupted", "error", "worker_error"):
            require(final is not None, "completed native search has no final event")
            require(final.get("termination") == row["termination"], "CSV/native termination mismatch")
            require(final["status"] == row["status"], "CSV/native status mismatch")
        else:
            expected_status = {"wall_timeout": "sat" if best is not None else "timeout",
                               "interrupted": "interrupted", "error": "error",
                               "worker_error": "error"}[row["termination"]]
            require(row["status"] == expected_status, "CSV status disagrees with its termination")

        progress = json_lines(artifact / "progress.jsonl")
        accepted, offset = [], None
        for record in progress:
            require(record.get("model_validated") is True, "progress record is not model-validated")
            value = integer(record.get("objective_value"), "progress objective")
            elapsed = seconds(record.get("elapsed_seconds"), "progress elapsed_seconds")
            total = seconds(record.get("time_seconds"), "progress time_seconds")
            require(total <= budget, "progress accepts an incumbent after the total budget")
            require(total + 1e-6 >= elapsed, "progress total time precedes native elapsed time")
            current_offset = total - elapsed
            if offset is None:
                offset = current_offset
            require(abs(current_offset - offset) <= 1e-6, "progress conversion/native time offsets disagree")
            require(not accepted or (better(value, accepted[-1][0], direction)
                                     and total >= accepted[-1][2]), "progress is not strictly improving in time")
            accepted.append((value, elapsed, total))
        if offset is not None:
            eligible = [(value, elapsed) for value, elapsed in incumbents if elapsed + offset <= budget]
            require([(value, elapsed) for value, elapsed, _ in accepted] == eligible,
                    "progress does not match every native incumbent accepted within budget")
        require(bool(accepted) == (best is not None), "CSV incumbent/progress presence mismatch")
        if best is not None:
            require(accepted[-1][0] == best, "CSV best differs from last accepted progress incumbent")
            for field, expected in (("time_to_first_seconds", accepted[0][2]),
                                    ("time_to_best_seconds", accepted[-1][2])):
                value = seconds(row[field], "CSV " + field)
                require(value <= budget + 0.0000005 and abs(value - expected) <= 0.00000051,
                        f"CSV {field} differs from accepted within-budget progress time")
                require(value <= wall + 1e-6, f"CSV {field} exceeds case wall time")
            require(incumbents and incumbents[-1][0] == best,
                    "retained model may be newer than the accepted within-budget CSV incumbent")
        else:
            require(row["time_to_first_seconds"] == row["time_to_best_seconds"] == "",
                    "CSV without incumbent has incumbent times")

        native_path, model_path = artifact / "input.nia", artifact / "model.tsv"
        native, model_text = None, None
        if native_path.is_file():
            native = parse_native(native_path.read_text(encoding="utf-8"))
            require(native["direction"] == direction, "native formula direction disagrees with manifest")
            require(native["source"] == case["source"], "native source disagrees with manifest")
            if best is not None:
                require(model_path.is_file(), "CSV incumbent has no retained model")
                model_text = model_path.read_text(encoding="utf-8")
                actual = evaluate_model(native, model_text)
                require(actual == best, f"retained model objective {actual} differs from accepted CSV best {best}")
                result.update(model_checked=True, objective_value=str(actual),
                              variables_checked=len(native["variables"]), clauses_checked=len(native["clauses"]))
            elif row["status"] == "unsat":
                require(native["status"] == "unsat", "UNSAT result lacks preprocessing-UNSAT native status")
        else:
            require(best is None and not events, "missing retained input.nia for native search/model audit")
        if best is None:
            require(not model_path.exists(), "retained model exists without an accepted within-budget incumbent")
        if row["termination"] == "target_beaten":
            require(use_target and declared_win, "target_beaten does not strictly beat the enabled historical target")
        large_hit = audit_large_target(final, row, large_target, best, direction, budget, wall) if large_metadata else False
        unbounded_hit = audit_unbounded(final, row, unbounded_enabled, unbounded_threshold, native, model_text,
                                       best, direction, budget, wall, offset) if unbounded_metadata else False
        optimality_hit = audit_optimality(final, row, optimality_enabled, optimality_stall, optimality_timeout,
                                         best, direction, budget, wall, offset) if optimality_metadata else False
        result.update(ok=True, verified_strict_win=declared_win,
                      verified_known_optimum_reached=known_hit,
                      verified_large_target_reached=large_hit,
                      verified_is_unbounded=unbounded_hit,
                      verified_is_optimal=optimality_hit,
                      verified_beats_both_present=declared_win and all(value is not None for value in references),
                      verification="final_model_and_events" if best is not None else "no_incumbent")
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError) as exc:
        result["error"] = str(exc)
    return result


def verify_run(directory, expected_cases=473):
    directory = Path(directory)
    report = {"run": str(directory.resolve()), "created_utc": datetime.now(timezone.utc).isoformat(),
              "ok": False, "expected_cases": expected_cases, "cases": [], "errors": [],
              "scope": "Exact final retained models against exported native formulas; CSV/manifest/artifact alignment; incumbent protocol and total-budget acceptance.",
              "limitations": ["Earlier incumbent models are not retained: only their objective ordering, event fields, and accepted timing are checked.",
                              "Original SMT-LIB equivalence and preprocessing UNSAT proofs are outside this native-formula audit."]}
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        cases = manifest["cases"]
        require(isinstance(cases, list) and len(cases) == expected_cases,
                f"manifest denominator {len(cases)} differs from expected {expected_cases}")
        for case in cases:
            require(isinstance(case, dict), "manifest case must be an object")
            for field in ("source", "suite", "category", "filename", "direction"):
                require(isinstance(case.get(field), str) and bool(case[field]),
                        f"manifest case {field} must be a nonempty string")
            require(Path(case["source"]).is_absolute(), "manifest source must be an absolute path")
            require(case["suite"] in ("original", "new"), "invalid manifest suite")
            require(case["direction"] in ("min", "max"), "invalid manifest direction")
        subset = manifest.get("subset", "unsolved_bounded_sol")
        require(subset in ("unsolved_bounded_sol", "bounded", "excluded", "all"), "invalid manifest subset")
        full_selection, full_tables = None, None
        if subset in ("excluded", "all"):
            full_selection, full_tables = historical_selection(subset, include_proofs=True)
            suite = manifest.get("selection_suite", "all")
            require(suite in ("all", "original", "new"), f"invalid {subset} selection_suite")
            complete = manifest.get("selection_complete", False)
            require(type(complete) is bool, f"invalid {subset} selection_complete")
            available = {key: value for key, value in full_selection.items()
                         if suite == "all" or key[0] == suite}
            for case in cases:
                key = case["suite"], case["category"], case["filename"]
                require(key in available, f"manifest case is absent from the historical {subset} selection")
                for field in ("source", "direction", "z3", "optimathsat"):
                    require(case.get(field) == available[key][field],
                            f"manifest {field} differs from historical {subset} source/reference")
            if complete:
                require({case["source"] for case in cases} == {item["source"] for item in available.values()},
                        f"complete {subset} selection omits historical cases")
            report.update(canonical_selection_checked=complete, historical_selection_checked=True,
                          historical_selection_tables=full_tables)
            if complete:
                report.update(canonical_cases=len(available), canonical_tables=full_tables)
            else:
                table_kind = "full-minus-bounded" if subset == "excluded" else "full historical"
                report["limitations"].append(f"Partial {subset} selection: every selected case is checked against {table_kind} tables; complete coverage is not claimed.")
        elif ((expected_cases == 473 and subset == "unsolved_bounded_sol")
                or (expected_cases == 4262 and subset == "bounded")):
            canonical, tables = canonical_selection("bounded") if expected_cases == 4262 else canonical_selection()
            for case in cases:
                key = case["suite"], case["category"], case["filename"]
                require(key in canonical, f"manifest case is outside the canonical subsets: {key!r}")
                for field, expected in canonical[key].items():
                    require(case.get(field) == expected,
                            f"manifest {field} differs from canonical source/reference for {key!r}")
            require({(case["suite"], case["category"], case["filename"]) for case in cases} == set(canonical),
                    "manifest does not contain every canonical case exactly once")
            report.update(canonical_selection_checked=True, canonical_cases=len(canonical), canonical_tables=tables)
        else:
            report["canonical_selection_checked"] = False
            report["limitations"].append("Subset audit: complete canonical selection provenance was not checked.")
        budget = seconds(manifest["timeout"], "manifest timeout")
        require(0 < budget <= 1200, "manifest total budget must be positive and at most 1200 seconds")
        require(type(manifest["jobs"]) is int and 1 <= manifest["jobs"] <= 180,
                "manifest job count must be between 1 and 180")
        require(type(manifest.get("scalar_target_enabled")) is bool, "missing manifest target setting")
        with (directory / "results.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        require(len(rows) == len(cases), "CSV/manifest denominator mismatch")
        keys = lambda item: tuple(item[key] for key in ("suite", "category", "filename"))
        row_map = {keys(row): row for row in rows}
        require(len(row_map) == len(rows), "duplicate CSV case identity")
        require(len({keys(case) for case in cases}) == len(cases), "duplicate manifest case identity")
        require({keys(case) for case in cases} == set(row_map), "CSV identities do not match manifest")
        require(len({case["source"] for case in cases}) == len(cases), "duplicate manifest source")
        known_fields = {"known_optimum_value", "known_optimum_source"}
        known_metadata = ("known_optimum_stop_enabled" in manifest
                          or any(known_fields.intersection(case) for case in cases)
                          or any((known_fields | {"known_optimum_reached"}).intersection(row) for row in rows)
                          or any(row["termination"] == "known_optimum_reached" for row in rows))
        known_stop_enabled, proof_lookup = False, {}
        if known_metadata:
            require(type(manifest.get("known_optimum_stop_enabled")) is bool,
                    "missing manifest known optimum stop setting")
            known_stop_enabled = manifest["known_optimum_stop_enabled"]
            if full_selection is not None:
                proof_lookup = {key: value for key, value in full_selection.items() if key in row_map}
                proof_tables = full_tables
            else:
                proof_lookup, proof_tables = historical_selection(subset, set(row_map), include_proofs=True)
            for case in cases:
                key = keys(case)
                require(key in proof_lookup, "known optimum case is absent from historical subset")
                require(known_fields.issubset(case), "missing manifest known optimum metadata")
                for field, expected in proof_lookup[key].items():
                    require(case.get(field) == expected,
                            f"manifest {field.replace('_', ' ')} differs from historical known optimum/source/reference")
            report.update(known_optimum_metadata_checked=True, known_optimum_tables=proof_tables)
            report["limitations"].append("Historical optimality flags are checked as recorded metadata; SMT optimality proofs are not replayed.")
        large_metadata = ("large_target_magnitude" in manifest
                          or any(set(LARGE_TARGET_FIELDS).intersection(row) for row in rows))
        magnitude = None
        if large_metadata:
            require("large_target_magnitude" in manifest, "missing manifest large_target_magnitude")
            magnitude = large_target_magnitude(manifest["large_target_magnitude"])
            report.update(large_target_metadata_checked=True, large_target_magnitude=manifest["large_target_magnitude"])
        unbounded_metadata = ("prove_unbounded" in manifest or "unbounded_threshold" in manifest
                              or any("prove_unbounded" in row or set(UNBOUNDED_FIELDS).intersection(row) for row in rows))
        unbounded_enabled, unbounded_threshold = False, 1000000
        if unbounded_metadata:
            require(type(manifest.get("prove_unbounded")) is bool,
                    "missing manifest prove_unbounded setting")
            unbounded_enabled = manifest["prove_unbounded"]
            require("unbounded_threshold" in manifest, "missing manifest unbounded_threshold")
            if unbounded_enabled:
                unbounded_threshold = integer(manifest["unbounded_threshold"], "manifest unbounded_threshold")
                require(0 < unbounded_threshold <= 2**127 - 1, "invalid manifest unbounded_threshold")
                require(manifest["scalar_target_enabled"] is False,
                        "unbounded proof mode must disable historical scalar target stopping")
            else:
                require(manifest["unbounded_threshold"] is None, "disabled manifest has an unbounded threshold")
            report.update(unbounded_metadata_checked=True, prove_unbounded=unbounded_enabled,
                          unbounded_threshold=manifest["unbounded_threshold"])
        optimality_metadata = ("prove_optimal" in manifest
                               or any(set(OPTIMALITY_FIELDS).intersection(row) for row in rows))
        optimality_enabled, optimality_stall, optimality_timeout = False, 50.0, 30.0
        if optimality_metadata:
            require(type(manifest.get("prove_optimal")) is bool, "missing manifest prove_optimal setting")
            optimality_enabled = manifest["prove_optimal"]
            optimality_stall = seconds(manifest.get("optimality_stall_seconds"), "manifest optimality_stall_seconds")
            optimality_timeout = seconds(manifest.get("optimality_timeout_seconds"), "manifest optimality_timeout_seconds")
            require(optimality_stall > 0 and optimality_timeout > 0, "nonpositive optimality timing setting")
            if optimality_enabled:
                require(manifest["scalar_target_enabled"] is False and known_stop_enabled is False,
                        "independent optimality mode must disable both historical stopping rules")
            report.update(optimality_metadata_checked=True, prove_optimal=optimality_enabled)
            if optimality_enabled:
                report["limitations"].append("Finite optimality is accepted only from the native strict-improvement UNSAT report; the SMT proof is not independently replayed.")
        artifacts = []
        for index, case in enumerate(cases):
            suffix = hashlib.sha256(case["source"].encode("utf-8")).hexdigest()[:12]
            artifacts.append(directory / "cases" / f"{index:04d}-{suffix}")
        actual_artifacts = {path.name for path in (directory / "cases").iterdir() if path.is_dir()}
        require(actual_artifacts == {path.name for path in artifacts}, "case artifact IDs do not align with manifest indices/source hashes")
        report["cases"] = [verify_case(case, row_map[keys(case)], artifact, budget,
                                       manifest["scalar_target_enabled"],
                                       None if not known_metadata or proof_lookup[keys(case)]["known_optimum_value"] is None
                                       else integer(proof_lookup[keys(case)]["known_optimum_value"]),
                                       known_stop_enabled, known_metadata, magnitude, large_metadata,
                                       unbounded_enabled, unbounded_threshold, unbounded_metadata,
                                       optimality_enabled, optimality_stall, optimality_timeout, optimality_metadata)
                           for case, artifact in zip(cases, artifacts)]
        verified_wins = sum(case.get("verified_strict_win", False) for case in report["cases"])
        csv_wins = sum(row["strictly_better_than_best"] == "true" for row in rows)
        report.update(manifest_cases=len(cases), csv_rows=len(rows), budget_seconds=budget,
                      parallel=manifest["jobs"], verified_models=sum(case["model_checked"] for case in report["cases"]),
                      cases_without_incumbent=sum(case.get("verification") == "no_incumbent" for case in report["cases"]),
                      case_failures=sum(not case["ok"] for case in report["cases"]),
                      csv_strict_wins=csv_wins, verified_strict_wins=verified_wins,
                      verified_known_optimum_reached=sum(case.get("verified_known_optimum_reached", False)
                                                        for case in report["cases"]),
                      verified_large_target_reached=sum(case.get("verified_large_target_reached", False)
                                                        for case in report["cases"]),
                      verified_is_unbounded=sum(case.get("verified_is_unbounded", False)
                                                for case in report["cases"]),
                      verified_is_optimal=sum(case.get("verified_is_optimal", False)
                                              for case in report["cases"]),
                      verified_strict_win_rate=verified_wins / len(cases),
                      verified_over_80_percent=verified_wins / len(cases) > 0.8,
                      verified_beats_both_present=sum(case.get("verified_beats_both_present", False) for case in report["cases"]),
                      wall_overhead_cases=sum(case.get("wall_overhead_seconds", 0) > 0 for case in report["cases"]),
                      max_wall_overhead_seconds=max((case.get("wall_overhead_seconds", 0) for case in report["cases"]), default=0),
                      execution_statuses=dict(Counter(row["status"] for row in rows)))
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        if large_metadata:
            require(summary.get("large_target_magnitude", object()) == manifest["large_target_magnitude"],
                    "summary large_target_magnitude disagrees with manifest")
            require(summary.get("large_target_reached") == sum(row.get("large_target_reached") == "true" for row in rows),
                    "summary large_target_reached disagrees with CSV")
        if unbounded_metadata:
            require(summary.get("prove_unbounded") is unbounded_enabled,
                    "summary prove_unbounded disagrees with manifest")
            require(summary.get("unbounded_threshold", object()) == manifest["unbounded_threshold"],
                    "summary unbounded_threshold disagrees with manifest")
            require(type(summary.get("is_unbounded")) is int and summary["is_unbounded"] ==
                    sum(row.get("is_unbounded") == "true" for row in rows),
                    "summary is_unbounded disagrees with CSV")
            require(summary.get("scalar_target_enabled") is manifest["scalar_target_enabled"],
                    "summary scalar_target_enabled disagrees with actual manifest target setting")
        for field, expected in (("selected", len(cases)), ("completed", len(rows)),
                                ("strictly_better_than_best", csv_wins)):
            require(summary[field] == expected, f"summary {field} disagrees with manifest/CSV")
        require(math.isclose(summary["strict_win_rate"], csv_wins / len(cases), rel_tol=0, abs_tol=1e-12),
                "summary win rate uses an incorrect numerator or denominator")
        require(summary["over_80_percent"] is (csv_wins / len(cases) > 0.8), "incorrect summary over-80-percent flag")
        report["ok"] = report["case_failures"] == 0
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError, ValueError, ModuleNotFoundError) as exc:
        report["errors"].append(str(exc))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="completed optimization result directory")
    parser.add_argument("--output", type=Path, help="report path (default: RUN/verification.json)")
    parser.add_argument("--expected-cases", type=int, default=473,
                        help="required manifest/CSV denominator (default: 473)")
    args = parser.parse_args(argv)
    if args.expected_cases < 1:
        parser.error("--expected-cases must be positive")
    report = verify_run(args.run, args.expected_cases)
    output = args.output or args.run / "verification.json"
    try:
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"verify_optimize.py: cannot write report: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: value for key, value in report.items() if key not in ("cases", "scope", "limitations")}, indent=2))
    print(f"Report: {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
