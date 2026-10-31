#!/usr/bin/env python3
"""Standalone component regression; no product build, jobs, or benchmark paths."""
from pathlib import Path
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
             "nia_omt_cutset.cpp", "nia_initialize.cpp", "nia_build_instance.cpp", "nia_omt_input.cpp"]
    with tempfile.TemporaryDirectory(prefix="smt-exchange-fallback-") as folder:
        executable = Path(folder) / "test-smt-exchange-fallback"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w",
            "-ffunction-sections", "-fdata-sections", f"-I{prefix / 'include'}",
            str(sls / "tests/test_smt_exchange_fallback.cpp"),
            *[str(sls / "src" / name) for name in names],
            "-Wl,--gc-sections", f"-L{prefix / 'lib'}", f"-Wl,-rpath,{prefix / 'lib'}",
            "-lz3", "-lgmpxx", "-lgmp", "-o", str(executable)], check=True)
        subprocess.run([str(executable), str(sls / "tests/fixtures/smt_exchange_fallback_nia.smt2"), folder],
                       check=True, timeout=45)


if __name__ == "__main__":
    main()
