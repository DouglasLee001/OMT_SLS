#!/usr/bin/env python3
"""Build/run isolated core-neighborhood semantics; never changes release objects.

The test executable alone links competitor libz3. Product objects resolve Z3
symbols against the embedded shell and must not acquire that library dependency.
"""
from pathlib import Path
import os
import subprocess
import tempfile


def main():
    sls = Path(__file__).resolve().parents[1]
    repository = sls.parents[1]
    prefix = Path(os.environ.get(
        "NIA_CORE_TEST_Z3_PREFIX", repository / "competitor/z3-5.1.0/install"))
    sources = [
        sls / "tests/test_omt_core_neighborhood.cpp",
        sls / "src/nia_omt_core_neighborhood.cpp",
        sls / "src/nia_initialize.cpp",
        sls / "src/nia_build_instance.cpp",
        sls / "src/nia_omt_input.cpp",
    ]
    with tempfile.TemporaryDirectory(prefix="omt-core-neighborhood-") as directory:
        executable = Path(directory) / "test-omt-core-neighborhood"
        command = [
            os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w",
            "-ffunction-sections", "-fdata-sections", f"-I{prefix / 'include'}",
            *map(str, sources), "-Wl,--gc-sections", f"-L{prefix / 'lib'}",
            f"-Wl,-rpath,{prefix / 'lib'}", "-lz3", "-lgmpxx", "-lgmp",
            "-o", str(executable),
        ]
        subprocess.run(command, check=True)
        subprocess.run([str(executable)], check=True, timeout=30)


if __name__ == "__main__":
    main()
