#!/usr/bin/env python3
"""Standalone component regression; no product build, jobs, or benchmark paths."""
from pathlib import Path
import argparse
import hashlib
import os
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chain-only', action='store_true', help='only composed-check and pause/resume regressions')
    args = parser.parse_args()
    sls = Path(__file__).resolve().parents[1]
    root = sls.parents[1]
    configured_prefix = os.environ.get("NIA_FALLBACK_TEST_Z3_PREFIX")
    prefix = Path(configured_prefix) if configured_prefix else root / 'competitor/z3-5.1.0/install'
    library_directory = prefix / 'lib' if configured_prefix else root / 'z3-nia/build'
    library = (library_directory / 'libz3.so').resolve()
    print('Component library:', library, 'SHA256:', hashlib.sha256(library.read_bytes()).hexdigest(), flush=True)
    # Keep diagnosis/component verification on one CPU at lower priority.
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    os.nice(10)
    names = ["nia_omt_optimality.cpp", "nia_omt_model_bridge.cpp", "nia_omt_objective_slice.cpp",
             "nia_omt_cutset.cpp", "nia_initialize.cpp", "nia_build_instance.cpp", "nia_omt_input.cpp"]
    with tempfile.TemporaryDirectory(prefix="smt-exchange-fallback-") as folder:
        executable = Path(folder) / "test-smt-exchange-fallback"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w",
            "-ffunction-sections", "-fdata-sections", f"-I{prefix / 'include'}",
            str(sls / "tests/test_smt_exchange_fallback.cpp"),
            *[str(sls / "src" / name) for name in names],
            "-Wl,--gc-sections", "-Wl,--wrap=Z3_solver_check", f"-L{library_directory}", f"-Wl,-rpath,{library_directory}",
            "-lz3", "-lgmpxx", "-lgmp", "-o", str(executable)], check=True)
        fixture = sls / 'tests/fixtures/smt_exchange_fallback_nia.smt2'
        for mode in ('finite', 'resume'):
            result = subprocess.run([str(executable), '--chain-check', str(fixture),
                str(Path(folder) / mode), mode], capture_output=True, text=True, timeout=10)
            print(result.stdout, end='')
            print(result.stderr, end='')
            assert result.returncode == 0, 'full-query composed check and source invariants must succeed'
            count = result.stderr.count('chain-test source_check\n')
            assert count == 1, f'{mode}: expected one public full-query check for the whole tactic chain, got {count}'
        if args.chain_only:
            print('PASS one composed public check in finite and resumed workers')
            return
        subprocess.run([str(executable), str(sls / "tests/fixtures/smt_exchange_fallback_nia.smt2"), folder],
                       check=True, timeout=45)


if __name__ == "__main__":
    main()
