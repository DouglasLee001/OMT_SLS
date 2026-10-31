#!/usr/bin/env python3
"""Generated public-API toys only; no benchmark fixture, product build or jobs."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", action="store_true", help="semantic RED against the unchanged old API")
    parser.add_argument("--dense-only", action="store_true")
    args = parser.parse_args()
    sls = Path(__file__).resolve().parents[1]
    root = sls.parents[1]
    prefix = Path(os.environ.get("NIA_ADVISORY_TEST_Z3_PREFIX", root / "competitor/z3-5.1.0/install"))
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    os.nice(10)
    names = ["nia_omt_optimality.cpp", "nia_omt_model_bridge.cpp", "nia_omt_convex_exchange.cpp",
             "nia_omt_objective_slice.cpp", "nia_omt_cutset.cpp", "nia_initialize.cpp",
             "nia_build_instance.cpp", "nia_omt_input.cpp", "nia_ls.cpp"]
    evidence = Path(os.environ["NIA_ADVISORY_TEST_ARTIFACT_DIR"]) if os.environ.get("NIA_ADVISORY_TEST_ARTIFACT_DIR") else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="omt-advisory-public-") as folder:
        folder = Path(folder)
        executable = folder / "test-advisory-budget"
        command = [os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w",
                   "-ffunction-sections", "-fdata-sections", f"-I{prefix / 'include'}"]
        if args.legacy:
            command.append("-DNIA_ADVISORY_TEST_LEGACY")
        command += [str(sls / "tests/test_omt_advisory_budget.cpp"),
                    *[str(sls / "src" / name) for name in names], "-Wl,--gc-sections",
                    "-Wl,--wrap=Z3_solver_check", f"-L{prefix / 'lib'}", f"-Wl,-rpath,{prefix / 'lib'}",
                    "-lz3", "-lgmpxx", "-lgmp", "-o", str(executable)]
        subprocess.run(command, check=True)
        modes = ["dense-default", "dense-prefix"]
        if not args.legacy and not args.dense_only:
            modes += ["dense-off", "dense-pause", "wide-models", "pipeline", "invalid-api"]
        for mode in modes:
            directory = folder / mode
            began = time.monotonic()
            result = subprocess.run([str(executable), mode, str(directory)], text=True,
                                    capture_output=True, timeout=24 if mode == "wide-models" else 5)
            elapsed = time.monotonic() - began
            print(result.stdout, end="")
            print(result.stderr, end="")
            records.append({"mode": mode, "returncode": result.returncode, "wall_seconds": elapsed,
                            "original_default_check_entries": result.stderr.count("advisory-test check_begin kind=dense_original"),
                            "stdout": result.stdout, "stderr": result.stderr})
            if evidence:
                (evidence / (mode + ".stdout")).write_text(result.stdout)
                (evidence / (mode + ".stderr")).write_text(result.stderr)
        queries = [(folder / mode / "strict-query.smt2").read_bytes() for mode in ("dense-default", "dense-prefix")]
        assert queries[0] == queries[1], "control and auxiliary sessions must retain byte-identical full strict queries"
        report = {"legacy_api": args.legacy, "generated_toys_only": True,
                  "same_one_second_parent_window": True, "original_source_witness": "all coordinates zero, objective zero",
                  "production_source_sha256": {name: hashlib.sha256((sls / "src" / name).read_bytes()).hexdigest() for name in names},
                  "test_sha256": {name: hashlib.sha256((sls / "tests" / name).read_bytes()).hexdigest() for name in
                                  ("test_omt_advisory_budget.cpp", "test_omt_advisory_budget.py")},
                  "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                  "identical_query_sha256": hashlib.sha256(queries[0]).hexdigest(), "runs": records}
        if evidence:
            (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        assert records[0]["returncode"] == 0 and records[0]["original_default_check_entries"] == 1, "real original default control must prove"
        if args.legacy:
            assert records[1]["returncode"] == 1 and "status=budget proved=0" in records[1]["stdout"], "legacy RED must fail semantically at the shared parent budget"
            assert records[1]["original_default_check_entries"] == 0, "real legacy auxiliary run never entered the original default SDK check"
            print("EXPECTED RED: unchanged public session spends the window before original default check")
            raise SystemExit(1)
        assert records[1]["returncode"] == 0 and records[1]["original_default_check_entries"] == 1, "half-prefix session must reach the real original default proof"
        by_mode = {record["mode"]: record for record in records}
        dense_on = by_mode["dense-prefix"]["stderr"]
        assert dense_on.count("omt_advisory_prefix phase=start ") == 1
        assert "omt_advisory_prefix phase=default " in dense_on and "main_remaining=inf" in dense_on
        assert "omt_advisory_prefix" not in by_mode["dense-default"]["stderr"]
        if not args.dense_only:
            assert all(record["returncode"] == 0 for record in records)
            off = by_mode["dense-off"]
            assert "status=budget proved=0" in off["stdout"] and off["original_default_check_entries"] == 0
            assert "omt_advisory_prefix" not in off["stderr"], "explicit zero preserves the legacy path"
            paused = by_mode["dense-pause"]
            assert paused["stderr"].count("omt_advisory_prefix phase=start ") == 1, "same-bound resume must not restart a prefix"
            assert paused["original_default_check_entries"] == 1 and "starts=1 resumes=1" in paused["stdout"]
            wide = by_mode["wide-models"]
            assert wide["stderr"].count("omt_advisory_prefix phase=start ") == 8, "each new feasible bound starts exactly one prefix"
            assert wide["stdout"].count("wide-result ") == 4
            pipeline = by_mode["pipeline"]["stderr"]
            entered = set(re.findall(r"omt_advisory_prefix phase=enter stage=(\w+)", pipeline))
            assert entered == {"source_slice", "convex", "cutset", "smt"}, "all enabled advisory helpers share this pipeline"
        print("PASS generated public advisory prefix")


if __name__ == "__main__":
    main()
