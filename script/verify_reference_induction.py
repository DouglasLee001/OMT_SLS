#!/usr/bin/env python3
"""Independently verify finite integer trace induction certificates.

The checker uses Fraction polynomials, exact substitution, original forced
facts, and induction on the nonnegative integer L-2.  It invokes no solver or
proof generator.  The fresh integer coordinate starts at 1 and advances by 1
until L-1; L=2 is the zero-step case.  L is a native integer variable or an
integer affine polynomial over native integer parameters.  Only transition
proofs may assume the interval slacks t-1 and L-t-2 are nonnegative.  A complete
binary tree may split native Boolean, equality, or inequality literals, with
each leaf replayed under exactly its own branch assumptions.
"""
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path

try:
    from verify_reference_case_splits import (derive_equalities as cancellation_equalities,
                                              reconstruct as cancellation_reconstruct,
                                              verify_leaf as cancellation_verify_leaf)
except ModuleNotFoundError:
    from script.verify_reference_case_splits import (derive_equalities as cancellation_equalities,
                                                     reconstruct as cancellation_reconstruct,
                                                     verify_leaf as cancellation_verify_leaf)

try:
    from verify_rlt_certificates import (ROOT, VerificationError, add, attached_polynomial,
                                         context, historical_integer, multiply, objective_sense,
                                         parse_native, rational, reconstruct as base_reconstruct,
                                         replay_trail, require)
except ModuleNotFoundError:
    from script.verify_rlt_certificates import (ROOT, VerificationError, add, attached_polynomial,
                                                context, historical_integer, multiply,
                                                objective_sense, parse_native, rational,
                                                reconstruct as base_reconstruct, replay_trail, require)


ONE = {(): Fraction(1)}


def variable(number):
    return {((number, 1),): Fraction(1)}


def substitute(polynomial, number, replacement):
    """Replace one formal variable by a polynomial with exact expansion."""
    result = {}
    for monomial, coefficient in polynomial.items():
        factors = dict(monomial)
        exponent = factors.pop(number, 0)
        term = {tuple(sorted(factors.items())): coefficient}
        power = ONE
        base = replacement
        while exponent:
            if exponent & 1:
                power = multiply(power, base)
            exponent >>= 1
            if exponent:
                base = multiply(base, base)
        result = add(result, multiply(term, power))
    return result


def reconstruct(why, native, assigned, bound, variables, loop_guard=None, start_guard=None, depth=0, derived=()):
    require(isinstance(why, dict) and depth < 100, "invalid or excessive polynomial provenance")
    kind = why.get("type")
    if kind == "derived_equality":
        return cancellation_reconstruct(why, native, assigned, bound, derived)
    if kind == "loop_interval_slack":
        require(loop_guard is not None, "loop interval slack is allowed only in a transition")
        return "inequality", loop_guard
    if kind == "loop_start_slack":
        require(start_guard is not None, "loop start slack is allowed only in a transition")
        return "inequality", start_guard
    if kind == "product_of_forced_nonnegative_polynomials":
        lk, left = reconstruct(why.get("left"), native, assigned, bound, variables, loop_guard, start_guard, depth + 1, derived)
        rk, right = reconstruct(why.get("right"), native, assigned, bound, variables, loop_guard, start_guard, depth + 1, derived)
        require(lk == rk == "inequality", "product factors are not proved nonnegative")
        return "inequality", multiply(left, right)
    if kind in ("equality_times_variable", "forced_equality_times_variable"):
        sk, source = reconstruct(why.get("source"), native, assigned, bound, variables, loop_guard, start_guard, depth + 1, derived)
        number = why.get("variable")
        require(sk == "equality" and type(number) is int and number in variables
                and variables[number][0] == "I", "invalid equality multiplier variable")
        return "equality", multiply(source, variable(number))
    require(kind in ("forced_literal", "forced_equality", "strict_objective_bound"),
            "unknown polynomial provenance rule")
    # Original source facts are always reconstructed against the original
    # native variables.  They cannot acquire the fresh iteration coordinate.
    return base_reconstruct(why, native, assigned, bound)


def check_column(column, native, assigned, bound, variables, loop_guard=None, start_guard=None, derived=()):
    require(isinstance(column, dict), "invalid proof column")
    kind, raw = reconstruct(column.get("why"), native, assigned, bound, variables, loop_guard, start_guard, derived=derived)
    require(column.get("kind") == kind, "claimed column kind differs from provenance")
    divisor = column.get("normalization_divisor")
    require(type(divisor) is int and divisor != 0 and (kind == "equality" or divisor > 0),
            "invalid normalization divisor or reversed inequality sign")
    normalized = {m: value / divisor for m, value in raw.items()}
    require(normalized == attached_polynomial(column.get("polynomial"), variables),
            "attached polynomial differs from reconstructed normalized provenance")
    weight = rational(column.get("weight"))
    require(kind == "equality" or weight >= 0, "negative inequality weight")
    return kind, add({}, normalized, weight)


def prove(proof, target, native, assigned, bound, variables, label, loop_guard=None, start_guard=None, derived=()):
    require(isinstance(proof, dict) and isinstance(proof.get("terms"), list) and proof["terms"],
            f"missing {label} nonnegative polynomial proof")
    total = {}
    for term in proof["terms"]:
        _, polynomial = check_column(term, native, assigned, bound, variables, loop_guard, start_guard, derived=derived)
        total = add(total, polynomial)
    require(total == target, f"{label} polynomial identity differs from its required target")
    return len(proof["terms"])


def check_induction(certificate, native, assumptions=()):
    for key, value in (("start", 1), ("step", 1), ("step_count_offset", -2), ("terminal_offset", -1)):
        require(type(certificate.get(key)) is int and certificate[key] == value,
                f"{key} must be {value} for this finite integer induction rule")
    t = certificate.get("iteration_variable")
    require(type(t) is int and t >= 0 and t not in native["variables"],
            "iteration variable must be a fresh nonnative integer ID")
    length = certificate.get("length_variable")
    if "length_polynomial" in certificate:
        require("length_variable" not in certificate, "ambiguous length encodings")
        length_poly = attached_polynomial(certificate["length_polynomial"], native["variables"])
        require(all(coefficient.denominator == 1 for coefficient in length_poly.values()),
                "affine length must have integer coefficients")
        require(all(not monomial or (len(monomial) == 1 and monomial[0][1] == 1
                    and native["variables"][monomial[0][0]][0] == "I") for monomial in length_poly),
                "length polynomial must be affine over native integers")
    else:
        require(type(length) is int and length in native["variables"]
                and native["variables"][length][0] == "I", "length must be a native integer variable")
        length_poly = variable(length)
    variables = dict(native["variables"])
    variables[t] = ("I", "fresh_integer_iteration")
    definitions = certificate.get("functions")
    require(isinstance(definitions, list) and definitions, "missing polynomial functions")
    functions = [attached_polynomial(p, variables) for p in definitions]
    size = len(functions)
    initial, transitions = certificate.get("initial"), certificate.get("transitions")
    require(isinstance(initial, list) and len(initial) == size, "missing initial proof for a function")
    require(isinstance(transitions, list) and len(transitions) == size, "missing transition proof for a function")
    matrix = certificate.get("transition_matrix")
    require(isinstance(matrix, list) and len(matrix) == size
            and all(isinstance(row, list) and len(row) == size for row in matrix),
            "transition matrix must be square with one row per function")
    multipliers = certificate.get("exit_multipliers")
    require(isinstance(multipliers, list) and len(multipliers) == size,
            "missing nonnegative exit multipliers")
    assigned, bound = replay_trail(certificate, native, assumptions)
    derived = cancellation_equalities(certificate.get("derived_equalities", []), native, assigned, bound)

    def nonnegative(column):
        kind, polynomial = check_column(column, native, assigned, bound, native["variables"], derived=derived)
        require(kind == "inequality", "multiplier has no nonnegative source")
        # check_column uses native metadata here, so even an attached polynomial
        # mentioning t is rejected.  M and mu are fixed across the whole trace.
        return polynomial

    matrix = [[nonnegative(entry) for entry in row] for row in matrix]
    multipliers = [nonnegative(entry) for entry in multipliers]
    steps = add(length_poly, ONE, -2)
    terms = prove(certificate.get("length_nonnegative"), steps, native, assigned, bound,
                  native["variables"], "length", derived=derived)
    for j, function in enumerate(functions):
        target = add({}, substitute(function, t, ONE), -1)
        terms += prove(initial[j], target, native, assigned, bound, native["variables"], f"initial {j}", derived=derived)
    loop_guard = add(steps, variable(t), -1)
    start_guard = add(variable(t), ONE, -1)
    next_t = add(variable(t), ONE)
    for j, function in enumerate(functions):
        target = {}
        for multiplier, source in zip(matrix[j], functions):
            target = add(target, multiply(multiplier, source))
        target = add(target, substitute(function, t, next_t), -1)
        terms += prove(transitions[j], target, native, assigned, bound, variables,
                       f"transition {j}", loop_guard, start_guard, derived=derived)
    terminal = add(length_poly, ONE, -1)
    final_combination = {}
    for multiplier, function in zip(multipliers, functions):
        final_combination = add(final_combination, multiply(multiplier, substitute(function, t, terminal)))
    terms += prove(certificate.get("exit"), add(final_combination, ONE, -2), native,
                   assigned, bound, native["variables"], "exit", derived=derived)
    # L is an integer and L-2>=0.  Start at t=1 with all f<=0; each of
    # exactly L-2 transitions preserves this because M>=0 and the proved
    # transition inequality implies f(t+1)<=M*f(t).  Thus every f(L-1)<=0,
    # including when L=2 and no transition is taken.  Since mu>=0, G<=0.
    # The separately proved exit inequality G-2>=0 is a contradiction.
    return {"functions": size, "identities": 2 * size + 2, "proof_terms": terms,
            "multiplier_columns": size * size + size, "iteration_variable": t,
            "length_variable": length, "length_polynomial": certificate.get("length_polynomial"),
            "steps": f"v{length}-2" if length is not None else "affine_length-2", "zero_step_case_included": True}


def verify_tree(node, native, digest, assumptions=(), depth=0):
    require(isinstance(node, dict) and depth < 100, "invalid or excessive induction case-split tree")
    if "certificate" in node:
        require(not any(k in node for k in ("split_literal", "leftfalse", "righttrue")),
                "ambiguous split/leaf node")
        certificate = node["certificate"]
        require(isinstance(certificate, dict) and certificate.get("method") in
                ("exact_affine_integer_trace_induction", "exact_rational_RLT_polynomial_identity"),
                "unsupported induction leaf method")
        require(certificate.get("input_sha256") == digest, "leaf input SHA256 differs from root")
        path = Path(certificate["input"])
        if not path.is_absolute():
            path = ROOT / path
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                "leaf input path differs from root native input")
        if certificate["method"] == "exact_rational_RLT_polynomial_identity":
            # This is only composition of an existing exact algebraic leaf
            # checker with the same input, strict objective, and split path.
            details = cancellation_verify_leaf(certificate, native, digest, assumptions)
            return {"leaves": 1, "functions": 0, "identities": 1,
                    "proof_terms": details["terms"], "multiplier_columns": 0}
        return dict(check_induction(certificate, native, assumptions), leaves=1)
    literal = node.get("split_literal")
    require(type(literal) is int and literal in native["literals"]
            and native["literals"][literal][0] in ("B", "E", "L"),
            "split must use a known native Boolean/arithmetic literal")
    require(literal not in {i for i, _ in assumptions}, "repeated split literal on one branch")
    require("leftfalse" in node and "righttrue" in node, "incomplete binary induction case split")
    left = verify_tree(node["leftfalse"], native, digest, assumptions + ((literal, False),), depth + 1)
    right = verify_tree(node["righttrue"], native, digest, assumptions + ((literal, True),), depth + 1)
    return {key: left[key] + right[key] for key in
            ("leaves", "functions", "identities", "proof_terms", "multiplier_columns")}


def verify_certificate(path, references, artifacts):
    result = {"certificate": str(path), "verified": False}
    try:
        certificate = json.loads(Path(path).read_text())
        require(isinstance(certificate, dict)
                and certificate.get("method") in ("exact_affine_integer_trace_induction", "exact_integer_trace_induction_tree"),
                "unsupported induction certificate method")
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
        if certificate["method"] == "exact_integer_trace_induction_tree":
            details = verify_tree(certificate.get("tree"), native, digest)
        else:
            details = dict(check_induction(certificate, native), leaves=1)
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
    report = {"ok": False, "checker": "independent_exact_Fraction_integer_trace_induction",
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
