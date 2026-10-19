# OptiMathSAT 1.7.4 local SDK build

Checked the official Linux download selector on 2026-10-11. Its newest listed
Linux version is **1.7.4**. The archived download list and release notes still
stop at 1.7.3; they are stale relative to the selector and working 1.7.4 archive.
The package reports **1.7.4.b (2a04d1bee804)**, built by its publisher on
2023-07-24. The archive HTTP Last-Modified is 2023-07-27; this is not a verified
formal release date.

Official sources:

- https://optimathsat.disi.unitn.it/pages/download-js.html
- https://optimathsat.disi.unitn.it/js/download-js.js
- https://optimathsat.disi.unitn.it/pages/documentation.html
- https://optimathsat.disi.unitn.it/releases/optimathsat-1.7.4/optimathsat-1.7.4-linux-64-bit.tar.gz

Archive SHA-256:
`a62daa5e02b16de4edb67f8c972e268e787cb1bae9aed356ff7a642688f4e133`

## What was compiled

`install/bin/optimathsat` is a real native executable compiled locally from the
small official C `msat_main` entry point and linked against the vendor's
**precompiled solver-core archive**, `libmathsat.a`, and this machine's GMP and
C/C++ runtime libraries. The bundled official optimization C++ API example was
also compiled. **The solver core was not compiled from source.**

The official SDK archive includes headers, a static core library, a prebuilt
CLI, example source, and language binding source. It contains no solver-core
implementation source. The inspected official download pages provide binary
platform packages and no core source download. The official documentation says:
“The executable provided in the distribution is linked statically”. It provides
a `main.c` relinking recipe. That page calls the archive `liboptimathsat.a`, but
this 1.7.4 SDK actually ships `lib/libmathsat.a`, as documented by its bundled
README. `evidence/archive-contents.txt` records the full inspected archive list.

`install/bin/optimathsat-official-prebuilt` preserves the publisher's executable
for comparison. The final local executable is linked dynamically to system
`libgmp.so.10`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, and `libc.so.6`
(see `logs/local-libraries.log`). The `-version` string preserves the core's
publisher build metadata (GCC 9.4.0 / GMP 6.2.0), rather than describing the local
entry-point compiler or runtime. Local build tools are recorded separately in
`logs/build-environment.log`.

## Rebuild and usage

From this directory:

```sh
./build/build-sdk.sh
./install/bin/optimathsat -version
./install/bin/optimathsat -optimization=true build/smoke-lia.smt2
./build/optimization-api-example -f build/smoke-api.smt2 -o x -m
```

The entry point is first compiled as C using `gcc -O2 -c`, then linked using
`g++` so the solver's C++ dependencies resolve. Exact commands and exit codes
are recorded in `build/build-results.json` and `logs/compile-main.log`,
`logs/link-optimathsat.log`, and `logs/build-optimization-api-example.log`.
The repeatable script only writes inside this version directory; no global
installation is performed.

**Optimization must be enabled with `-optimization=true`.** Both the bundled
publisher CLI and locally linked executable default this option to false. A
missing flag produces `(error "optimization not supported")` even though the
process exits 0. Checking only the exit status is insufficient.

For the requested nonlinear benchmark trial, the CLI accepts:

```sh
./install/bin/optimathsat -optimization=true \
  -preprocessor.toplevel_propagation=false -opt.strategy=bin INPUT.smt2
```

Official release notes call OMT(NIRA) experimental and prescribe disabling
top-level propagation. Complete parameter help is in `logs/help.log`.

## Verification and limits

- Local entry point compilation, core linking, and official C++ optimization
  example compilation each exited 0.
- LIA smoke input returned `sat`, objective `x = 3`, and model `x = 3`.
- The official C++ optimization API example returned exact optimum 3 and a
  matching model for the same linear constraints.
- The CLI and official binary reported the same core version 1.7.4.b.
- Nonlinear feasibility input `0 <= x <= 2` and `x*x >= 9` returned `unsat`.
- **Nonlinear optimization probe found an inconsistent result.** For Real `x`,
  `0 <= x <= 10`, `x*x >= 9`, minimize `x`, the mathematical optimum is 3.
  This release returned objective `0 + epsilon` and model `x = 10`, with
  `-optimization=true`, both with and without the documented nonlinear flag.
  Changing boxed engine to `full` or priority to `lex` did not resolve it.
  `theory.na.enabled` was confirmed true in the configuration dump. This is a
  solver behavior limitation, not a local entry-point link failure. Do not treat
  `sat` or exit 0 as evidence that nonlinear optimization returned a correct
  optimum. Logs and exact inputs are preserved in `build/` and `logs/`.

This folder's smoke probes are not a paper benchmark reproduction. The parent
benchmark task records its selected real inputs and results separately.

The publisher license restricts use to research and evaluation; the original
license files are retained in `install/share/` and the SDK directory.
