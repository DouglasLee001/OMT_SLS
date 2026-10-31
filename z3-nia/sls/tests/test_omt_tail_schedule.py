#!/usr/bin/env python3
"""Exercise the pure scheduling policy without linking or starting a solver."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def run(folder):
    tests = Path(__file__).resolve().parent
    for name in ("test_omt_optimality_schedule", "test_omt_tail_schedule"):
        executable = folder / name
        subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-pedantic",
                        str(tests / (name + ".cpp")), "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
        run(args.artifacts)
    else:
        with tempfile.TemporaryDirectory(prefix="omt-tail-schedule-") as folder:
            run(Path(folder))


if __name__ == "__main__":
    main()
