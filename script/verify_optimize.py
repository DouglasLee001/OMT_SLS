#!/usr/bin/env python3
"""Independently audit a retained native optimization run with Python integers.

Usage: python3 script/verify_optimize.py result/optimize/RUN

The default report is RUN/verification.json. This verifier never invokes a
solver. It validates the final retained model against the exported native
formula, aligns CSV rows and artifacts to the manifest, and audits objective
events and accepted-model timing. Earlier incumbent assignments are not
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
        constant, terms = expression
        result = constant
        for coefficient, factors in terms:
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


def canonical_selection():
    """Read the requested subset tables and source directions, independently of the runner."""
    try:
        from smtlib import objective_sense
    except ModuleNotFoundError:
        from script.smtlib import objective_sense
    selected, tables = {}, []
    for suite in ("original", "new"):
        listing = ROOT / "result" / suite / f"{suite}_unsolved_bounded_sol.csv"
        tables.append({"path": str(listing), "sha256": hashlib.sha256(listing.read_bytes()).hexdigest()})
        with listing.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"filename", "z3_best_value", "optimathsat_best_value"}
            if suite == "new":
                required.add("category")
            require(required.issubset(reader.fieldnames or []), "canonical subset table has missing columns")
            for row in reader:
                name = row["filename"]
                category = "original" if suite == "original" else row["category"]
                require(category and Path(category).name == category, "invalid canonical category")
                base = ORIGINAL_ROOT if suite == "original" else ROOT / "new_benchmark" / category / "instance"
                source = (base / name).resolve()
                require(name and source.is_relative_to(base.resolve()) and source.is_file()
                        and source.suffix == ".smt2", "invalid canonical benchmark source")
                if suite == "new":
                    require(Path(name).name == name, "generated canonical filename is not a basename")
                direction = {"minimize": "min", "maximize": "max"}[objective_sense(source.read_text(encoding="utf-8"))]
                references = [historical_integer(row[field]) for field in ("z3_best_value", "optimathsat_best_value")]
                require(any(value is not None for value in references), "canonical case lacks a finite historical reference")
                key = suite, category, name
                require(key not in selected, "duplicate canonical case identity")
                selected[key] = {"source": str(source), "direction": direction,
                                 "z3": None if references[0] is None else str(references[0]),
                                 "optimathsat": None if references[1] is None else str(references[1])}
    require(len(selected) == 473, f"canonical subset denominator is {len(selected)}, expected 473")
    require(len({case["source"] for case in selected.values()}) == len(selected), "duplicate canonical source")
    return selected, tables


def verify_case(case, row, artifact, budget, use_target):
    result = {"filename": case["filename"], "category": case["category"],
              "suite": case["suite"], "artifact": artifact.name, "ok": False,
              "model_checked": False, "incumbent_events_checked": 0}
    try:
        require(artifact.is_dir(), "missing case artifact directory")
        direction = case["direction"]
        require(direction in ("min", "max") and row["direction"] == direction,
                "CSV objective direction disagrees with manifest")
        require(row["is_optimal"] == "false", "CSV claims unproved optimality")
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

        events = json_lines(artifact / "native.stdout")
        incumbents, final, previous_time = [], None, -1.0
        for event in events:
            require(final is None, "native event appears after the final record")
            require(event.get("event") in ("incumbent", "final"), "unknown native event kind")
            elapsed = seconds(event.get("elapsed_seconds"), "native elapsed_seconds")
            require(elapsed >= previous_time, "native event times decrease")
            previous_time = elapsed
            require(event.get("direction") == direction, "native event direction disagrees with manifest")
            require(event.get("is_optimal") is False, "native event claims unproved optimality")
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
        if row["termination"] not in ("wall_timeout", "interrupted", "error", "worker_error"):
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
        if native_path.is_file():
            native = parse_native(native_path.read_text(encoding="utf-8"))
            require(native["direction"] == direction, "native formula direction disagrees with manifest")
            require(native["source"] == case["source"], "native source disagrees with manifest")
            if best is not None:
                require(model_path.is_file(), "CSV incumbent has no retained model")
                actual = evaluate_model(native, model_path.read_text(encoding="utf-8"))
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
        result.update(ok=True, verified_strict_win=declared_win,
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
        if expected_cases == 473:
            canonical, tables = canonical_selection()
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
            report["limitations"].append("Subset audit: canonical 473-case selection provenance was not checked.")
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
        artifacts = []
        for index, case in enumerate(cases):
            suffix = hashlib.sha256(case["source"].encode("utf-8")).hexdigest()[:12]
            artifacts.append(directory / "cases" / f"{index:04d}-{suffix}")
        actual_artifacts = {path.name for path in (directory / "cases").iterdir() if path.is_dir()}
        require(actual_artifacts == {path.name for path in artifacts}, "case artifact IDs do not align with manifest indices/source hashes")
        report["cases"] = [verify_case(case, row_map[keys(case)], artifact, budget,
                                       manifest["scalar_target_enabled"])
                           for case, artifact in zip(cases, artifacts)]
        verified_wins = sum(case.get("verified_strict_win", False) for case in report["cases"])
        csv_wins = sum(row["strictly_better_than_best"] == "true" for row in rows)
        report.update(manifest_cases=len(cases), csv_rows=len(rows), budget_seconds=budget,
                      parallel=manifest["jobs"], verified_models=sum(case["model_checked"] for case in report["cases"]),
                      cases_without_incumbent=sum(case.get("verification") == "no_incumbent" for case in report["cases"]),
                      case_failures=sum(not case["ok"] for case in report["cases"]),
                      csv_strict_wins=csv_wins, verified_strict_wins=verified_wins,
                      verified_strict_win_rate=verified_wins / len(cases),
                      verified_over_80_percent=verified_wins / len(cases) > 0.8,
                      verified_beats_both_present=sum(case.get("verified_beats_both_present", False) for case in report["cases"]),
                      wall_overhead_cases=sum(case.get("wall_overhead_seconds", 0) > 0 for case in report["cases"]),
                      max_wall_overhead_seconds=max((case.get("wall_overhead_seconds", 0) for case in report["cases"]), default=0),
                      execution_statuses=dict(Counter(row["status"] for row in rows)))
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
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
