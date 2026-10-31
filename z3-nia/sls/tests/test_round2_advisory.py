#!/usr/bin/env python3
"""Standalone component regression; no product build, jobs, or benchmark paths."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile


def main():
    sls = Path(__file__).resolve().parents[1]
    root = sls.parents[1]
    prefix = Path(os.environ.get("NIA_FALLBACK_TEST_Z3_PREFIX", root / "competitor/z3-5.1.0/install"))
    # Keep diagnosis/component verification on one CPU at lower priority.
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    os.nice(10)
    names = ["nia_omt_optimality.cpp", "nia_omt_model_bridge.cpp", "nia_omt_convex_exchange.cpp", "nia_omt_objective_slice.cpp",
             "nia_omt_cutset.cpp", "nia_initialize.cpp", "nia_build_instance.cpp", "nia_omt_input.cpp", "nia_ls.cpp"]
    with tempfile.TemporaryDirectory(prefix="smt-exchange-fallback-") as folder:
        executable = Path(folder) / "test-smt-exchange-fallback"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w",
            "-ffunction-sections", "-fdata-sections", f"-I{prefix / 'include'}",
            str(sls / "tests/test_round2_advisory.cpp"),
            *[str(sls / "src" / name) for name in names],
            "-Wl,--gc-sections", f"-L{prefix / 'lib'}", f"-Wl,-rpath,{prefix / 'lib'}",
            "-lz3", "-lgmpxx", "-lgmp", "-o", str(executable)], check=True)
        evidence = os.environ.get("NIA_FALLBACK_TEST_ARTIFACT_DIR")
        evidence = Path(evidence) if evidence else None
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
        records = []

        def run(name, command, timeout):
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            print(result.stdout, end="")
            print(result.stderr, end="")
            records.append({"name": name, "returncode": result.returncode, "command": command})
            if evidence:
                (evidence / (name + ".stdout")).write_text(result.stdout)
                (evidence / (name + ".stderr")).write_text(result.stderr)

        # Each mode starts a fresh process, including its first Z3 context.
        fixtures = sls / "tests/fixtures"
        for mode in ("default", "supplemental"):
            run("hanoi-" + mode, [str(executable), "--context-regression", str(fixtures),
                str(Path(folder) / mode), mode], 18)
        default_query = (Path(folder) / "default/hanoi.smt2").read_bytes()
        supplemental_query = (Path(folder) / "supplemental/hanoi.smt2").read_bytes()
        if default_query != supplemental_query:
            raise AssertionError("Hanoi control and supplemental runs must retain byte-identical original strict queries")
        run("components", [str(executable), str(fixtures / "smt_exchange_fallback_nia.smt2"), folder], 45)
        if evidence:
            (evidence / "report.json").write_text(json.dumps({
                "runs": records, "real_hanoi_strict_query_identical": True,
                "real_hanoi_strict_query_sha256": hashlib.sha256(default_query).hexdigest(),
                "optimality_source_sha256": hashlib.sha256((sls / "src/nia_omt_optimality.cpp").read_bytes()).hexdigest(),
                "test_source_sha256": hashlib.sha256((sls / "tests/test_round2_advisory.cpp").read_bytes()).hexdigest(),
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            }, indent=2) + "\n")
        if any(record["returncode"] for record in records):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
