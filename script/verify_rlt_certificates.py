#!/usr/bin/env python3
"""Replay native strict-bound contradiction certificates with exact Fractions.

No solver, numerical LP package, or certificate generator is imported or run.
Each accepted certificate proves that its exported native formula cannot have
an integer objective below the canonical historical minimum reference zero.
Earlier official UNSAT diagnostic claims are reported separately and are not
reproved by this checker. Native/frontend equivalence is outside this replay.
"""
from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

try:
    from verify_optimize import (ROOT, ORIGINAL_ROOT, VerificationError, historical_integer,
                                 parse_native, require)
    from smtlib import objective_sense
except ModuleNotFoundError:
    from script.verify_optimize import (ROOT, ORIGINAL_ROOT, VerificationError, historical_integer,
                                        parse_native, require)
    from script.smtlib import objective_sense


def index(value, label="index"):
    require(type(value) is int and value >= 0, f"invalid {label}: {value!r}")
    return value


def rational(value):
    require(isinstance(value, str) and re.fullmatch(r"-?[0-9]+(?:/[0-9]+)?", value),
            "rational coefficient must be an exact integer/fraction string")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise VerificationError("invalid exact rational coefficient") from exc


def monomial(factors, variables):
    require(isinstance(factors, (list, tuple)), "invalid monomial factors")
    result = {}
    for factor in factors:
        require(isinstance(factor, (list, tuple)) and len(factor) == 2, "invalid monomial factor")
        variable, exponent = index(factor[0], "variable"), index(factor[1], "exponent")
        require(exponent > 0 and variable in variables and variables[variable][0] == "I",
                "monomial must use known integer variables with positive exponents")
        result[variable] = result.get(variable, 0) + exponent
    return tuple(sorted(result.items()))


def add(left, right, scale=Fraction(1)):
    result = dict(left)
    for term, value in right.items():
        result[term] = result.get(term, Fraction(0)) + value * scale
        if result[term] == 0:
            del result[term]
    return result


def multiply(left, right):
    result = {}
    for lm, lv in left.items():
        for rm, rv in right.items():
            powers = dict(lm)
            for variable, exponent in rm:
                powers[variable] = powers.get(variable, 0) + exponent
            term = tuple(sorted(powers.items()))
            result[term] = result.get(term, Fraction(0)) + lv * rv
    return {term: value for term, value in result.items() if value}


def native_polynomial(expression, variables):
    constant, terms = expression
    result = {(): Fraction(constant)} if constant else {}
    for coefficient, factors in terms:
        result = add(result, {monomial(factors, variables): Fraction(coefficient)})
    return result


def attached_polynomial(encoded, variables):
    require(isinstance(encoded, list), "certificate polynomial must be a list")
    result = {}
    for entry in encoded:
        require(isinstance(entry, list) and len(entry) == 2, "invalid attached polynomial term")
        term, value = monomial(entry[0], variables), rational(entry[1])
        require(term not in result and value != 0, "duplicate or zero attached polynomial term")
        result[term] = value
    return result


def objective_interval(native):
    objective = native_polynomial(native["objective"], native["variables"])
    variables = {variable for term in objective for variable, _ in term}
    require(len(variables) == 1, "strict-bound objective must be affine in one variable")
    variable = next(iter(variables))
    require(set(objective) <= {(), ((variable, 1),)}, "strict-bound objective is nonlinear")
    a, b = objective.get(((variable, 1),), 0), objective.get((), 0)
    require(a and a.denominator == b.denominator == 1, "invalid affine integer objective")
    a, b = int(a), int(b)
    # a*x+b <= -1, with exact integer rounding for either coefficient sign.
    lo, hi = (None, (-1 - b) // a) if a > 0 else (-((-(-1 - b)) // a), None)
    bound = add(add({}, objective, -1), {(): Fraction(-1)})
    return variable, lo, hi, bound


def entailed_truth(native, literal, reason, interval):
    kind, expression = native["literals"][literal]
    require(kind in ("L", "E"), "interval/constant reason cannot assign a Boolean literal")
    variable, lo, hi, _ = interval
    q = native_polynomial(expression, native["variables"])
    if reason == "constant_literal":
        require(set(q) <= {()}, "constant reason uses a nonconstant polynomial")
        value = q.get((), 0)
        return value == 0 if kind == "E" else value <= 0
    require(set(q) <= {(), ((variable, 1),)}, "interval reason uses another variable or a nonlinear polynomial")
    a, b = q.get(((variable, 1),), 0), q.get((), 0)
    require(a != 0, "constant literal must use constant_literal provenance")
    if kind == "E":
        root = -b / a
        if root.denominator != 1 or (lo is not None and root < lo) or (hi is not None and root > hi):
            return False
    else:
        lower_x, upper_x = (lo, hi) if a > 0 else (hi, lo)
        low = None if lower_x is None else a * lower_x + b
        high = None if upper_x is None else a * upper_x + b
        if high is not None and high <= 0:
            return True
        if low is not None and low > 0:
            return False
    raise VerificationError("claimed interval literal truth is not implied by objective <= -1")


def replay_trail(certificate, native, assumptions):
    require(certificate.get("branch_assumptions", []) == [[i, t] for i, t in assumptions],
            "leaf branch assumptions do not match the exhaustive split path")
    trail = certificate.get("unitprop_trail", certificate.get("trail"))
    require(isinstance(trail, list), "missing unit-propagation proof trail")
    interval, assigned, assumption_cursor = objective_interval(native), {}, 0
    for entry in trail:
        require(isinstance(entry, list) and len(entry) == 3, "invalid unit-propagation trail entry")
        literal, truth, why = entry
        literal = index(literal, "trail literal")
        require(literal in native["literals"] and type(truth) is bool and isinstance(why, dict), "invalid trail assignment")
        require(literal not in assigned, "trail reassigns an already assigned literal")
        reason = why.get("type")
        if reason in ("objective_interval", "constant_literal"):
            require(entailed_truth(native, literal, reason, interval) == truth, "incorrect initial literal truth")
        elif reason == "branch_assumption":
            require(assumption_cursor < len(assumptions) and assumptions[assumption_cursor] == (literal, truth),
                    "branch assumption trail is not the declared split path")
            require(why.get("literal", literal) == literal, "branch provenance literal mismatch")
            assumption_cursor += 1
        elif reason == "unit_clause":
            clause_id = index(why.get("clause"), "unit clause")
            require(clause_id < len(native["clauses"]), "unknown unit clause")
            clause = native["clauses"][clause_id]
            require(not any(abs(i) in assigned and assigned[abs(i)] == (i > 0) for i in clause),
                    "claimed unit clause is already satisfied")
            unassigned = [i for i in clause if abs(i) not in assigned]
            require(unassigned == [literal if truth else -literal], "unit step is not the sole unassigned clause literal")
        else:
            raise VerificationError("unknown trail provenance")
        assigned[literal] = truth
    require(assumption_cursor == len(assumptions), "trail omits declared branch assumptions")
    if "forced_literals" in certificate:
        require(certificate["forced_literals"] == len(assigned), "forced-literal count disagrees with replay")
    return assigned, interval[3]


def reconstruct(why, native, assigned, bound, depth=0):
    require(isinstance(why, dict) and depth < 100, "invalid or excessive polynomial provenance")
    kind = why.get("type")
    if kind == "strict_objective_bound":
        return "inequality", bound
    if kind in ("forced_literal", "forced_equality"):
        signed = why.get("literal")
        require(type(signed) is int and signed != 0, "invalid forced literal provenance")
        literal, truth = abs(signed), signed > 0
        require(literal in assigned and assigned[literal] == truth, "provenance cites an unproved literal truth")
        literal_kind, expression = native["literals"][literal]
        q = native_polynomial(expression, native["variables"])
        if kind == "forced_equality":
            require(literal_kind == "E" and truth, "only a true equality yields a free equality column")
            return "equality", q
        require(literal_kind == "L", "nonnegative forced-literal column must be an inequality")
        return "inequality", add({}, q, -1) if truth else add(q, {(): Fraction(-1)})
    if kind == "product_of_forced_nonnegative_polynomials":
        lk, left = reconstruct(why.get("left"), native, assigned, bound, depth + 1)
        rk, right = reconstruct(why.get("right"), native, assigned, bound, depth + 1)
        require(lk == rk == "inequality", "product factors are not proved nonnegative")
        return "inequality", multiply(left, right)
    if kind in ("forced_equality_times_variable", "equality_times_variable"):
        source_kind, source = reconstruct(why.get("source"), native, assigned, bound, depth + 1)
        variable = index(why.get("variable"), "equality multiplier variable")
        require(source_kind == "equality" and variable in native["variables"]
                and native["variables"][variable][0] == "I", "invalid equality multiplier")
        return "equality", multiply(source, {((variable, 1),): Fraction(1)})
    raise VerificationError("unknown polynomial provenance rule")


def verify_leaf(certificate, native, digest, assumptions):
    require(certificate.get("input_sha256") == digest, "leaf input SHA256 differs from root native input")
    leaf_input = Path(certificate["input"])
    if not leaf_input.is_absolute():
        leaf_input = ROOT / leaf_input
    require(hashlib.sha256(leaf_input.read_bytes()).hexdigest() == digest,
            "leaf input path does not contain the certified native input")
    assigned, bound = replay_trail(certificate, native, assumptions)
    method = certificate.get("method")
    if method == "exact_boolean_unitprop":
        conflict = index(certificate.get("unitprop_conflict"), "conflict clause")
        require(conflict < len(native["clauses"]), "unknown conflict clause")
        require(all(abs(i) in assigned and assigned[abs(i)] != (i > 0) for i in native["clauses"][conflict]),
                "final conflict clause is not entirely false")
        return {"leaves": 1, "terms": 0, "method": method}
    require(method == "exact_rational_RLT_polynomial_identity", "unrecognized certificate method")
    terms = certificate.get("certificate_terms")
    require(isinstance(terms, list) and terms, "missing polynomial contradiction terms")
    total = {}
    for term in terms:
        require(isinstance(term, dict), "invalid certificate column")
        kind, raw = reconstruct(term.get("why"), native, assigned, bound)
        require(term.get("kind") == kind, "claimed column kind disagrees with its provenance")
        divisor = term.get("normalization_divisor")
        require(type(divisor) is int and divisor != 0 and (kind == "equality" or divisor > 0),
                "invalid normalization divisor or reversed inequality sign")
        normalized = {m: value / divisor for m, value in raw.items()}
        require(normalized == attached_polynomial(term.get("polynomial"), native["variables"]),
                "attached polynomial differs from reconstructed normalized provenance")
        weight = rational(term.get("weight"))
        require(kind == "equality" or weight >= 0, "negative inequality weight")
        total = add(total, normalized, weight)
    require(total == {(): Fraction(-1)}, "weighted polynomial identity is not exactly -1")
    return {"leaves": 1, "terms": len(terms), "method": method}


def verify_tree(node, native, digest, assumptions=(), depth=0):
    require(isinstance(node, dict) and depth < 100, "invalid or excessive case-split tree")
    if "certificate" in node:
        require(not any(key in node for key in ("split_literal", "leftfalse", "righttrue")), "ambiguous split/leaf node")
        return verify_leaf(node["certificate"], native, digest, assumptions)
    literal = index(node.get("split_literal"), "split literal")
    require(literal in native["literals"] and native["literals"][literal][0] == "B", "split must use a known Boolean literal")
    require(literal not in {i for i, _ in assumptions}, "repeated split literal on one branch")
    require("leftfalse" in node and "righttrue" in node, "incomplete binary case split")
    left = verify_tree(node["leftfalse"], native, digest, assumptions + ((literal, False),), depth + 1)
    right = verify_tree(node["righttrue"], native, digest, assumptions + ((literal, True),), depth + 1)
    return {"leaves": left["leaves"] + right["leaves"], "terms": left["terms"] + right["terms"],
            "method": "boolean_case_split_RLT_tree"}


def context(run):
    references = {}
    with (ROOT / "result/original/original_unsolved_bounded_sol.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            values = [historical_integer(row[key]) for key in ("z3_best_value", "optimathsat_best_value")]
            source = (ORIGINAL_ROOT / row["filename"]).resolve()
            references[str(source)] = {"filename": row["filename"], "references": values}
    manifest = json.loads((run / "manifest.json").read_text())
    require(len(manifest["cases"]) == 473, "native run manifest must contain 473 canonical cases")
    artifacts = {}
    for number, case in enumerate(manifest["cases"]):
        source = case["source"]
        suffix = hashlib.sha256(source.encode()).hexdigest()[:12]
        require(source not in artifacts, "duplicate source in native run manifest")
        artifacts[source] = (run / "cases" / f"{number:04d}-{suffix}" / "input.nia", case)
    return references, artifacts


def verify_certificate(path, references, artifacts):
    result = {"certificate": str(path), "verified": False}
    try:
        certificate = json.loads(path.read_text())
        require(isinstance(certificate, dict), "certificate must be an object")
        if certificate.get("verified") is False:
            return dict(result, skipped="generator did not claim a proof")
        input_path = Path(certificate["input"])
        if not input_path.is_absolute():
            input_path = ROOT / input_path
        raw = input_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        require(certificate.get("input_sha256") == digest, "certificate/native input SHA256 mismatch")
        native = parse_native(raw.decode("utf-8"))
        source = native["source"]
        require(source in references and source in artifacts, "native SOURCE is not a canonical selected original case")
        metadata = references[source]
        if "filename" in certificate:
            require(certificate["filename"] == metadata["filename"], "certificate filename disagrees with canonical SOURCE")
        if "direction" in certificate:
            require(certificate["direction"] == "min", "certificate declares the wrong objective direction")
        recorded_input, case = artifacts[source]
        require(hashlib.sha256(recorded_input.read_bytes()).hexdigest() == digest,
                "certificate input does not match the native run's recorded case")
        require(case["filename"] == metadata["filename"] and case["suite"] == case["category"] == "original",
                "run case metadata disagrees with canonical original subset")
        direction = {"minimize": "min", "maximize": "max"}[objective_sense(Path(source).read_text())]
        require(native["direction"] == case["direction"] == direction == "min", "certificate requires canonical MIN direction")
        available = [value for value in metadata["references"] if value is not None]
        require(available and min(available) == 0, "canonical best historical reference is not zero")
        for key, reference in zip(("z3", "optimathsat"), metadata["references"]):
            require(case.get(key) == (None if reference is None else str(reference)), "run historical reference differs from canonical CSV")
        for key in ("reference", "reference_best_value"):
            if key in certificate:
                require(historical_integer(str(certificate[key])) == 0, "certificate declares a nonzero historical reference")
        require(native["status"] == "ready", "certificate expects a ready native formula")
        if "variables" in certificate:
            require(certificate["variables"] == len(native["variables"]), "native variable count mismatch")
        if certificate.get("method") == "boolean_case_split_RLT_tree":
            details = verify_tree(certificate.get("tree"), native, digest)
        else:
            details = verify_leaf(certificate, native, digest, ())
        result.update(verified=True, source=source, filename=metadata["filename"], input_sha256=digest,
                      direction="min", reference_best_value="0", **details)
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        result["error"] = str(exc)
    return result


def official_claims(path, references):
    claims = set()
    by_name = {value["filename"]: (source, value) for source, value in references.items()}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("strict_improvement_proven_impossible") != "true":
                continue
            has_official_unsat = any(row.get(key) == "unsat" for key in
                                     ("integer_decision", "real_decision", "integer_gap_real_decision"))
            if not has_official_unsat and row.get("rlt_independently_verified") == "true":
                # These rows are checked through their exact certificate files,
                # and must not be relabeled as historical solver UNSAT proofs.
                continue
            require(row["filename"] in by_name, "official diagnostic names a noncanonical source")
            source, metadata = by_name[row["filename"]]
            direction = {"minimize": "min", "maximize": "max"}[objective_sense(Path(source).read_text())]
            values = [value for value in metadata["references"] if value is not None]
            best = (min(values) if direction == "min" else max(values)) if values else None
            require(row["direction"] == direction and historical_integer(row["reference_best_value"]) == best,
                    "official diagnostic direction/reference differs from canonical subset")
            require(has_official_unsat,
                    "official impossible claim lacks a recorded UNSAT decision")
            require(source not in claims, "duplicate official impossible source")
            claims.add(source)
    return claims


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificates", nargs="+", type=Path, help="certificate JSON files or directories")
    parser.add_argument("--run", type=Path, default=ROOT / "result/optimize/full_v4_20261022")
    parser.add_argument("--official", type=Path, default=ROOT / "result/optimize/reference_diagnostics/results.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"ok": False, "checker": "independent_exact_Fraction_replay", "solver_calls": False,
              "lp_calls": False, "limitations": ["Native/frontend equivalence is not reproved.",
              "Official diagnostic UNSAT claims are counted separately; their solver proofs are not replayed here."], "certificates": [], "errors": []}
    try:
        references, artifacts = context(args.run)
        paths = []
        for supplied in args.certificates:
            if supplied.is_dir():
                for path in sorted(supplied.glob("*.json")):
                    value = json.loads(path.read_text())
                    if isinstance(value, dict) and "input_sha256" in value:
                        paths.append(path)
            else:
                paths.append(supplied)
        require(paths, "no certificate candidates found")
        report["certificates"] = [verify_certificate(path, references, artifacts) for path in paths]
        verified = {item["source"] for item in report["certificates"] if item["verified"]}
        official = official_claims(args.official, references)
        union = verified | official
        report.update(exact_verified_unique_sources=len(verified), official_recorded_unsat_unique_sources=len(official),
                      overlap=len(verified & official), combined_impossible_unique_sources=len(union),
                      total_cases=473, required_strict_wins_for_over80=379,
                      exact_native_certificates_only_strict_wins_upper_bound=473 - len(verified),
                      strict_wins_upper_bound=473 - len(union),
                      over80_impossible_with_recorded_official_claims=473 - len(union) < 379,
                      exact_verified_sources=sorted(verified), official_recorded_unsat_sources=sorted(official),
                      certificate_failures=sum("error" in item for item in report["certificates"]))
        report["ok"] = report["certificate_failures"] == 0
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        report["errors"].append(str(exc))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("certificates", "exact_verified_sources", "official_recorded_unsat_sources", "limitations")}, indent=2))
    print("Report:", args.output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
