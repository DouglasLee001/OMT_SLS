# LIA Unboundedness Proof Implementation Plan

> **For agentic workers:** Use subagent-driven development for the independent native engine and Python auditing components; integrate and review in the current workspace. The user has explicitly authorized implementation. Preserve the existing uncommitted finite-target changes; do not commit.

**Goal:** After an exact large-objective incumbent, prove some OMT(NIA) instances unbounded by fixing variables and asking Z3 for an integer recession direction.

**Architecture:** A native helper selects a sufficient true CNF branch, fixes variables until all selected constraints and the objective are linear, and solves a homogeneous QF_LIA direction query using the already linked Z3 API. GMP checks the resulting ray. The native optimizer stops only on a checked proof; the runner retains the certificate and an independent Python integer verifier checks it again.

**Tech Stack:** Existing C++ solver, Z3 C++ API, GMP, Python unittest, existing experiment runner and native format.

## Tasks

- [x] Record the approved mathematical design and future coefficient-search ideas in `NIA_LS/待实现idea.md`.
- [x] Add red end-to-end tests in `NIA_LS/tests/test_unbounded_optimization.py`: max/min proofs, finite large optimum, disabled mode, negative/Boolean branch, CLI validation, unchanged finite incumbent semantics.
- [x] Add a native helper in `nia_omt_unbounded.h/.cpp` and direct mathematical tests. Preserve all selected constraints, signed literals, integer disequality branches, zero factors, exact coefficients and objective direction. Bound Z3 time and independently validate every successful direction.
- [x] Wire `--prove-unbounded` and `--unbounded-threshold=M` into `call.cpp` and `run_omt_optimization`. Once a validated objective reaches the threshold, spend at most `min(5s, 5% timeout)` on one probe and stop on a verified result. Preserve defaults and all existing finite-target features.
- [x] Add scalar proof metadata and an embedded `lia_ray` certificate to the final JSON. Keep `status=sat`, the last finite incumbent, and `is_optimal=false`; use `termination=unbounded_proved` and `is_unbounded=true` only after proof completion inside the case deadline.
- [x] Extend `run_optimize.py` CLI/config, CSV, manifest and summary. In proof mode disable historical strict-win targets. Validate certificates before reporting proofs; preserve known-optimum stops and compatibility with old logs.
- [x] Extend `verify_optimize.py` with an independent Python integer certificate checker: complete model feasibility, branch coverage, fixed directions, exact linearization and monotone target. Reject forged proofs, mismatched models and out-of-budget conclusions.
- [x] Run targeted red/green tests, native build, previous native optimization tests and Python runner/verifier tests. Smoke-test a small selection from historical excluded instances within short limits, then independently verify retained artifacts. Do not start the full experiment.
- [x] Review the final change for mathematical soundness and budget/default compatibility, update README/implementation details, and provide the 180-job, 1200-second user command.

## Certificate contract

`{"kind":"lia_ray","version":1,"base":["integer"],"direction":["integer"],"fixed":[index],"literals":[signed_id],"objective_slope":"integer"}`.

Arrays use native variable indices; Booleans use base `1`/`-1` and direction `0`. The parameter is any integer `t>=0`. All selected signed literals cover the hard CNF. Fixed-variable substitution must leave only affine terms, and the resulting derivative must preserve the selected constraint. Negative equalities choose the basepoint sign and may never cross zero.

## Validation commands

`make -C NIA_LS/src -j4`

`make -C NIA_LS/src test-omt-unbounded`

`python3 NIA_LS/tests/test_unbounded_optimization.py -v`

`python3 NIA_LS/tests/test_optimization.py -v`

`python3 -m unittest script.tests.test_run_optimize script.tests.test_verify_optimize -v`

`./script/run_optimize.sh original --subset excluded --prove-unbounded --jobs 180 --timeout 1200 --dry-run`

## Completed validation

- Native build and exact-ray unit checks passed.
- New native integration suite: 8 passed. Existing native optimization suite: 23 passed, 1 optional skip.
- Runner/verifier suite: 105 passed.
- Command dry run selected 2045 original excluded instances with 180 jobs and 1200 seconds.
- Real smoke `result/optimize/lia_unbounded_smoke_20261023_01`: 6 instances, 2 jobs, 10 seconds each; 5 proved unbounded, 1 without an initial model. The final independent verifier checked all 5 models and certificates with zero failures.
- Mathematical/specification and code-quality reviews passed. Soft proof-slice overrun is reported honestly and does not turn a valid SAT result into an error; the overall case deadline remains enforced.
