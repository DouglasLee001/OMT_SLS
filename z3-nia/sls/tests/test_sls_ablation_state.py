#!/usr/bin/env python3
"""Build/run focused State ablation behavior and the existing plateau regression.

This uses tiny native fixtures, not experiments or external SMT solver checks.
"""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    sls = Path(__file__).resolve().parents[1]
    common = [sls / "src" / name for name in (
        "nia_initialize.cpp", "nia_build_instance.cpp", "nia_omt_input.cpp", "nia_ls.cpp")]
    with tempfile.TemporaryDirectory(prefix="sls-ablation-state-") as directory:
        for name in ("test_sls_ablation_state", "test_optimization_neutral"):
            executable = Path(directory) / name
            command = [os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g",
                       "-ffunction-sections", "-fdata-sections",
                       str(sls / "tests" / (name + ".cpp")), *map(str, common),
                       "-Wl,--gc-sections", "-lgmpxx", "-lgmp", "-o", str(executable)]
            subprocess.run(command, check=True)
            subprocess.run([str(executable)], check=True, timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
