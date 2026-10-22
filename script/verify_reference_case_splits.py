#!/usr/bin/env python3
"""Independently replay exact reference-bound certificates with complete splits.

No solver, LP library, proof generator, or historical model is used.  A proof
certifies the retained native formula, with source direction and reference value
cross-checked against the canonical original-case table and run manifest.
"""
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path

try:
    from verify_rlt_certificates import (ROOT, VerificationError, add, attached_polynomial, context,
                                         historical_integer, multiply, native_polynomial,
                                         objective_sense, parse_native, rational, replay_trail,
                                         require, reconstruct as base_reconstruct)
except ModuleNotFoundError:
    from script.verify_rlt_certificates import (ROOT, VerificationError, add, attached_polynomial,
                                                context, historical_integer, multiply,
                                                native_polynomial, objective_sense, parse_native,
                                                rational, replay_trail, require,
                                                reconstruct as base_reconstruct)


def reconstruct(why, native, assigned, bound, derived, depth=0):
    """Reconstruct a column from proved premises, never from its attached value."""
    require(isinstance(why, dict) and depth < 100, "invalid or excessive polynomial provenance")
    kind = why.get("type")
    if kind == "derived_equality":
        number = why.get("id")
        require(type(number) is int and 0 <= number < len(derived), "unproved derived equality")
        return "equality", derived[number]
    if kind in ("equality_times_variable", "forced_equality_times_variable"):
        sk, source = reconstruct(why.get("source"), native, assigned, bound, derived, depth + 1)
        variable = why.get("variable")
        require(type(variable) is int and variable in native["variables"]
                and native["variables"][variable][0] == "I" and sk == "equality",
                "invalid equality multiplier")
        return "equality", multiply(source, {((variable, 1),): Fraction(1)})
    if kind == "product_of_forced_nonnegative_polynomials":
        lk, left = reconstruct(why.get("left"), native, assigned, bound, derived, depth + 1)
        rk, right = reconstruct(why.get("right"), native, assigned, bound, derived, depth + 1)
        require(lk == rk == "inequality", "product factors are not proved nonnegative")
        return "inequality", multiply(left, right)
    # These base rules only use the replayed literal assignment or the strict
    # objective interval.  Do not delegate recursive rules containing new nodes.
    require(kind in ("forced_literal", "forced_equality", "strict_objective_bound"),
            "unknown polynomial provenance rule")
    return base_reconstruct(why, native, assigned, bound)


def derive_equalities(entries, native, assigned, bound):
    require(isinstance(entries, list), "derived equalities must be a list")
    derived = []
    for entry in entries:
        require(isinstance(entry, dict) and type(entry.get("id")) is int
                and entry["id"] == len(derived), "derived equalities must have sequential IDs")
        why = entry.get("why")
        require(isinstance(why, dict) and why.get("type") == "zero_product_cancellation",
                "unsupported equality derivation")
        factor = why.get("factor_variable")
        require(type(factor) is int and factor in native["variables"]
                and native["variables"][factor][0] == "I", "unknown integer cancellation factor")
        literal = why.get("nonzero_literal")
        require(type(literal) is int and literal in native["literals"]
                and assigned.get(literal) is False and native["literals"][literal][0] == "E",
                "cancellation requires a proved nonzero equality literal")
        nonzero = native_polynomial(native["literals"][literal][1], native["variables"])
        require(set(nonzero) == {((factor, 1),)} and nonzero[((factor, 1),)] != 0,
                "nonzero equality is not a nonzero multiple of the cancellation factor")
        source_kind, source = reconstruct(why.get("product"), native, assigned, bound, derived)
        require(source_kind == "equality", "cancellation product is not a proved zero equality")
        quotient = {}
        for monomial, coefficient in source.items():
            powers = dict(monomial)
            require(powers.get(factor, 0) > 0, "zero product is not exactly divisible by its nonzero factor")
            powers[factor] -= 1
            if not powers[factor]:
                del powers[factor]
            quotient[tuple(sorted(powers.items()))] = coefficient
        require(quotient == attached_polynomial(entry.get("polynomial"), native["variables"]),
                "attached derived equality differs from exact nonzero-factor cancellation")
        derived.append(quotient)
    return derived


def verify_leaf(certificate, native, digest, assumptions):
    require(isinstance(certificate, dict), "invalid leaf certificate")
    require(certificate.get("input_sha256") == digest, "leaf input SHA256 differs from root")
    leaf_path = Path(certificate["input"])
    if not leaf_path.is_absolute():
        leaf_path = ROOT / leaf_path
    require(hashlib.sha256(leaf_path.read_bytes()).hexdigest() == digest,
            "leaf input path differs from root input")
    assigned, bound = replay_trail(certificate, native, assumptions)
    derived = derive_equalities(certificate.get("derived_equalities", []), native, assigned, bound)
    if certificate.get("method") == "exact_boolean_unitprop":
        conflict = certificate.get("unitprop_conflict")
        require(type(conflict) is int and 0 <= conflict < len(native["clauses"]), "unknown conflict clause")
        require(all(abs(i) in assigned and assigned[abs(i)] != (i > 0)
                    for i in native["clauses"][conflict]), "final conflict clause is not entirely false")
        return {"leaves": 1, "terms": 0, "derived_equalities": len(derived)}
    require(certificate.get("method") == "exact_rational_RLT_polynomial_identity",
            "unrecognized leaf certificate method")
    terms = certificate.get("certificate_terms")
    require(isinstance(terms, list) and terms, "missing polynomial contradiction terms")
    total = {}
    for term in terms:
        require(isinstance(term, dict), "invalid certificate column")
        kind, raw = reconstruct(term.get("why"), native, assigned, bound, derived)
        require(term.get("kind") == kind, "claimed column kind disagrees with provenance")
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
    return {"leaves": 1, "terms": len(terms), "derived_equalities": len(derived)}


def verify_tree(node, native, digest, assumptions=(), depth=0):
    require(isinstance(node, dict) and depth < 100, "invalid or excessive case-split tree")
    if "certificate" in node:
        require(not any(k in node for k in ("split_literal", "leftfalse", "righttrue")),
                "ambiguous split/leaf node")
        return verify_leaf(node["certificate"], native, digest, assumptions)
    literal = node.get("split_literal")
    require(type(literal) is int and literal in native["literals"]
            and native["literals"][literal][0] in ("B", "E"),
            "split must use a known Boolean or equality literal")
    require(literal not in {i for i, _ in assumptions}, "repeated split literal on one branch")
    require("leftfalse" in node and "righttrue" in node, "incomplete binary case split")
    left = verify_tree(node["leftfalse"], native, digest, assumptions + ((literal, False),), depth + 1)
    right = verify_tree(node["righttrue"], native, digest, assumptions + ((literal, True),), depth + 1)
    return {"leaves": left["leaves"] + right["leaves"], "terms": left["terms"] + right["terms"],
            "derived_equalities": left["derived_equalities"] + right["derived_equalities"]}


def verify_certificate(path, references, artifacts):
    result = {"certificate": str(path), "verified": False}
    try:
        certificate = json.loads(Path(path).read_text())
        require(isinstance(certificate, dict), "certificate must be an object")
        require(certificate.get("method") == "exact_arithmetic_case_split_RLT_tree",
                "unsupported certificate method")
        input_path = Path(certificate["input"])
        if not input_path.is_absolute():
            input_path = ROOT / input_path
        raw = input_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        require(certificate.get("input_sha256") == digest, "native input SHA256 mismatch")
        native = parse_native(raw.decode("utf-8"))
        source = native["source"]
        require(source in references and source in artifacts,
                "native SOURCE is not a canonical selected original case")
        metadata = references[source]
        recorded, case = artifacts[source]
        require(hashlib.sha256(recorded.read_bytes()).hexdigest() == digest,
                "certificate input differs from retained native run input")
        require(certificate.get("filename") == case["filename"] == metadata["filename"],
                "certificate filename disagrees with canonical SOURCE")
        require(case["suite"] == case["category"] == "original", "case is not in the original subset")
        direction = {"minimize": "min", "maximize": "max"}[objective_sense(Path(source).read_text())]
        require(native["direction"] == case["direction"] == certificate.get("direction") == direction == "min",
                "certificate requires the canonical MIN direction")
        available = [v for v in metadata["references"] if v is not None]
        require(available and min(available) == 0, "canonical best historical reference is not zero")
        require(historical_integer(str(certificate.get("reference_best_value"))) == 0,
                "certificate declares a nonzero historical reference")
        for key, value in zip(("z3", "optimathsat"), metadata["references"]):
            require(case.get(key) == (None if value is None else str(value)),
                    "manifest reference disagrees with canonical historical CSV")
        require(native["status"] == "ready", "proof requires a ready native formula")
        details = verify_tree(certificate.get("tree"), native, digest)
        result.update(verified=True, source=source, filename=metadata["filename"], input_sha256=digest,
                      direction="min", reference_best_value="0", **details)
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError, ValueError,
            AttributeError, RecursionError) as exc:
        result["error"] = str(exc)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificates", nargs="+", type=Path)
    parser.add_argument("--run", type=Path, default=ROOT / "result/optimize/full_v8_20261022")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = {"ok": False, "checker": "independent_exact_arithmetic_case_split_replay",
              "solver_calls": False, "lp_calls": False, "certificates": [], "errors": [],
              "limitations": ["Native/frontend equivalence is not reproved.",
                              "No historical diagnostic counts or CSV files are updated."]}
    try:
        references, artifacts = context(args.run)
        paths = []
        for supplied in args.certificates:
            paths.extend(sorted(supplied.glob("*.json")) if supplied.is_dir() else [supplied])
        require(paths, "no certificate candidates")
        report["certificates"] = [verify_certificate(p, references, artifacts) for p in paths]
        sources = {r["source"] for r in report["certificates"] if r["verified"]}
        report.update(exact_verified_unique_sources=len(sources), exact_verified_sources=sorted(sources))
        report["ok"] = all(r["verified"] for r in report["certificates"])
    except (VerificationError, OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        report["errors"].append(str(exc))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
