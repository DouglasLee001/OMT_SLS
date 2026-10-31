#!/usr/bin/env python3
"""Build/run isolated native slack candidates; no benchmark or release rebuild."""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                        help="exercise the same Gram contract with existing difference repair")
    parser.add_argument("--state", action="store_true",
                        help="exercise real optimizer State projection/evaluation through native import")
    parser.add_argument("--ubsan", action="store_true",
                        help="enable undefined-behavior checks in the isolated test executable")
    args = parser.parse_args()
    sls = Path(__file__).resolve().parents[1]
    test = "test_omt_slack_state.cpp" if args.state else "test_omt_slack_repair.cpp"
    sources = [sls / "tests" / test,
               sls / "src/nia_initialize.cpp", sls / "src/nia_build_instance.cpp",
               sls / "src/nia_omt_input.cpp"]
    if args.state:
        sources.append(sls / "src/nia_ls.cpp")
    with tempfile.TemporaryDirectory(prefix="omt-slack-repair-") as folder:
        executable = Path(folder) / "test-omt-slack-repair"
        command = [os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g",
                   "-ffunction-sections", "-fdata-sections", *map(str, sources),
                   "-Wl,--gc-sections", "-lgmpxx", "-lgmp", "-o", str(executable)]
        if args.baseline:
            command.insert(1, "-DOMT_SLACK_STATE_BASELINE" if args.state else "-DOMT_SLACK_BASELINE")
        if args.ubsan:
            command[1:1] = ["-fsanitize=undefined", "-fno-sanitize-recover=undefined"]
        subprocess.run(command, check=True)
        result = subprocess.run([str(executable), *(["--gram-only"] if args.baseline else [])],
                                timeout=30)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
