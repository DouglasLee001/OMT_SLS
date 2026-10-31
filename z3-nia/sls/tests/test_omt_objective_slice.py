#!/usr/bin/env python3
"""Compile and exercise the isolated source assertion slice on generated toys."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile


def run(folder, only="all"):
    sls = Path(__file__).resolve().parents[1]
    prefix = Path(os.environ.get("NIA_OBJECTIVE_SLICE_TEST_Z3_PREFIX", sls.parents[1] / "competitor/z3-5.1.0/install"))
    executable = folder / "test-omt-objective-slice"
    subprocess.run([
        os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-Wall", "-Wextra", f"-I{prefix / 'include'}",
        str(sls / "tests/test_omt_objective_slice.cpp"), str(sls / "src/nia_omt_objective_slice.cpp"),
        f"-L{prefix / 'lib'}", f"-Wl,-rpath,{prefix / 'lib'}", "-lz3", "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable), str(folder), only], check=True, timeout=60)
    proofs = sorted(folder.glob("certificate-*.json"))
    assert proofs, "no actual subset proof certificates were generated"
    checker_path = sls.parent / "experiment/verify_objective_slice_certificate.py"
    spec = importlib.util.spec_from_file_location("_native_objective_slice_checker", checker_path)
    checker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = checker
    spec.loader.exec_module(checker)
    for path in proofs:
        proof = json.loads(path.read_text())
        assert proof["version"] == "omt-objective-slice-v1"
        assert proof["direction"] in ("min", "max")
        assert len(proof["selected_indices"]) == len(proof["selected_assertions"])
        assert all(0 <= index < proof["flattened_source_count"] for index in proof["selected_indices"])
        replay = subprocess.run([str(prefix / "bin/z3"), "-in"], input=proof["query_smt2"], text=True, capture_output=True, timeout=10, check=True)
        assert replay.stdout.strip() == "unsat", (path.name, replay.stdout, replay.stderr)
        verified = checker.verify_certificate(Path(str(path) + ".source.smt2"), path, timeout=10)
        assert verified["certificate_valid"] and verified["no_better_bound_verified"], (path.name, verified)
        assert not verified["optimality_verified"], "subset proof alone does not establish incumbent feasibility"
    assert not list(folder.glob("*.tmp.*")), "atomic proof publication left temporary files"
    print(f"PASS independently source-verified and replayed {len(proofs)} actual objective subset queries")


def main():
    parser = argparse.ArgumentParser()
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--artifacts", type=Path)
    destination.add_argument("--temporary-root", type=Path)
    parser.add_argument("--only", choices=("all", "publication", "chain40"), default="all")
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
        run(args.artifacts, args.only)
    else:
        owned = None
        try:
            with tempfile.TemporaryDirectory(prefix=".omt-objective-slice-", dir=args.temporary_root) as folder:
                owned = Path(folder)
                print(f"host={socket.gethostname()} only={args.only} owned_artifacts={owned}", flush=True)
                run(owned, args.only)
        finally:
            if owned is not None:
                print(f"owned_temporary_directory_removed={not owned.exists()}", flush=True)


if __name__ == "__main__":
    main()
