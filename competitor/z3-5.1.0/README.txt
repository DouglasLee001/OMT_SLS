Z3 stable release 5.1.0, with the local OMT preprocessing option

2026-10-27 local patch: omt-preprocess.patch adds one rewriter parameter,
factor_nonlinear (default true), guarding the nonlinear factoring fallback.
Default solving retains 5.1 behavior. NIA_LS conversion sets the option false
only on its own tactics to preserve polynomial atoms. It does not set a global
parameter. Converter and native NIA_LS load the same build/libz3.so.5.1.0.0.
The parameter does not change the C API or the 5.1.0 version number.
Source hashes/provenance: omt-preprocess.json.
Latest incremental build/install: factor-parameter-build.json.
The original upstream source archive and historical experiment copies remain
available; the source directory now has the three-file patch described above.

Release: https://github.com/Z3Prover/z3/releases/tag/z3-5.1.0
Published: 2026-08-16T04:38:21Z
Git commit: 0b6cdcdbc65da25ef0f73ac9da210574d0f66cf8
Official API confirmation: downloads/release.json (draft=false, prerelease=false)
Tag confirmation: downloads/tag.json
Source URL: https://codeload.github.com/Z3Prover/z3/tar.gz/refs/tags/z3-5.1.0
Source archive: downloads/z3-5.1.0-source.tar.gz
Archive SHA256: c433e1add0431c5edf1644bd9951c40588024d2d288f0e4215e5fcb6e3b4277d

Executable: /home/lbh/OMT/competitor/z3-5.1.0/install/bin/z3
Shared library: /home/lbh/OMT/competitor/z3-5.1.0/install/lib/libz3.so.5.1.0.0
Python package: /home/lbh/OMT/competitor/z3-5.1.0/install/lib/python3.12/site-packages/z3

Build and local install:
  python3 /home/lbh/OMT/competitor/z3-5.1.0/build-source.py
Re-run focused verification:
  python3 /home/lbh/OMT/competitor/z3-5.1.0/verify-source.py

Build configuration: Release, Ninja, GCC 13.3.0, CMake 3.28.3, Python 3.12.3.
Compile limit: 12 parallel tasks. Includes shell, shared libz3, Python bindings,
and separately compiled official C/C++ API examples.
All install paths remain under this directory.
Exact commands, exit codes, and elapsed times: build-info.json.
Configure/build/examples/install logs: logs/.

The upstream CMake Python installation retained an absolute build-directory
symlink. repair-python-install.py replaces the installed libz3.so.5.1 link with
../../../libz3.so.5.1. build-source.py runs this repair after installation.
The source directory includes the local patch above. Verification confirms the Python
binding loads install/lib/libz3.so.5.1.0.0.

Python API usage:
  PYTHONPATH=/home/lbh/OMT/competitor/z3-5.1.0/install/lib/python3.12/site-packages \
  Z3_LIBRARY_PATH=/home/lbh/OMT/competitor/z3-5.1.0/install/lib \
  python3 -c 'import z3; print(z3.get_full_version())'

Focused verification (6 checks, all passed):
- shell version: Z3 version 5.1.0 - 64 bit
- SMT-LIB integer optimization: maximum x=10
- SMT-LIB weighted MaxSAT: a=true, b=false, objective cost=1
- installed Python API: the same exact integer/MaxSAT optima and bounds
- official C API example: exit=0
- official C++ API example: exit=0 and final done marker
Results: verification-results.json and logs/verify-*.log.
Preserved initial verification errors and installation diagnosis: logs/.

Additional peer comparison on QF_NRA:
  0 <= x <= 10, x*x >= 9, minimize x
  sat, objective x=3, model x=3.0, exit=0.
Input/output/timing: tests/smoke-nra-peer.smt2, .out, .json.
The root agent owns the paper benchmark trial and its report.

Further provenance and installed artifact hashes: metadata.json.
