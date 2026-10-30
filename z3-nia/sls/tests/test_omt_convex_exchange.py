#!/usr/bin/env python3
"""Build and exercise the isolated source-AST convex exchange implementation."""
from pathlib import Path
import json
import os
import subprocess
import tempfile


def main():
    sls = Path(__file__).resolve().parents[1]
    prefix = Path(os.environ.get("NIA_CONVEX_TEST_Z3_PREFIX", sls.parents[1] / "competitor/z3-5.1.0/install"))
    with tempfile.TemporaryDirectory(prefix="omt-convex-exchange-") as folder:
        executable = Path(folder) / "test-omt-convex-exchange"
        subprocess.run([
            os.environ.get("CXX", "g++"), "-std=gnu++17", "-O1", "-g", "-w", f"-I{prefix / 'include'}",
            str(sls / "tests/test_omt_convex_exchange.cpp"), str(sls / "src/nia_omt_convex_exchange.cpp"),
            f"-L{prefix / 'lib'}", f"-Wl,-rpath,{prefix / 'lib'}", "-lz3", "-lgmpxx", "-lgmp", "-o", str(executable),
        ], check=True)
        subprocess.run([str(executable), folder], check=True, timeout=30)
        # Reparse/replay the saved final LIA query and the unchanged source
        # strict query with the official standalone solver, not the C++ helper.
        certificates = sorted(Path(folder).glob("certificate-*.json"))
        assert certificates, "no actual proof certificates were generated"
        for certificate in certificates:
            evidence = json.loads(certificate.read_text())
            assert evidence["version"] == "omt-convex-exchange-v1"
            for key in ("linear_query_smt2", "source_query_smt2"):
                replay = subprocess.run([str(prefix / "bin/z3"), "-in"],
                    input=evidence[key], text=True, capture_output=True, timeout=10, check=True)
                assert replay.stdout.strip() == "unsat", (certificate.name, key, replay.stdout, replay.stderr)
        print(f"PASS replayed {len(certificates)} linear/source certificate query pairs")


if __name__ == "__main__":
    main()
