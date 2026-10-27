# Strict-bound SMT experiment implementation plan

> **For agentic workers:** Use the existing parallel agent workflow for independent tests and review. The user already authorized preparation of this experiment; the full solver batch is run by the user. Do not commit.

**Goal:** Turn the fixed 127 B instances that match a competitor's proven finite optimum into ordinary SMT strict-improvement queries, then provide a direct Z3 CLI experiment command.

**Architecture:** Recompute the selection from the latest completed server run and its frozen competitors. Reuse the converter's pure text parser to retain original hard constraints, remove optimization and source run-control commands, append `< M` for minimization or `> M` for maximization. Preparation writes immutable query files and a manifest; execution invokes the requested Z3 binary in separate processes, with a shared thread pool controlling concurrency and a hard per-query wall-clock deadline.

**Tech Stack:** Python standard library, existing SMT-LIB text parser, Z3 executable. No Python Z3 solving API and no solver-code changes.

- [x] Inspect the fixed run and independently confirm 127 instances, 117 min/10 max. Do not reuse stale optimality.smt2 files.
- [x] Write and run failing tests for direction, expression preservation, filtering, unsupported inputs and solver timeout/status handling.
- [x] Add `script/run_strict_bound.py` and `.sh`: `--run` prepares a fresh selection; `--prepare-only` stops before solving; `--prepared` runs an existing prepared set; `--jobs 180 --timeout 1200` remain defaults. Reject overwritten output directories and changed query hashes.
- [x] Record source text SHA256, strict-query SHA256, optimum M, reference solver proof state, and original NIA proof timing for each instance. Save stdout/stderr, results.csv and summary.md for each actual run.
- [x] Run focused tests and a tiny solver smoke test; prepare all127 queries without running the full experiment. Independently verify all hard assertions and final bound expressions against source and M.
- [x] Document a server-only Slurm command and a direct local command, and give the user the exact invocation. A configured capacity of180 means127 actual concurrent cases for this selection.

**Validation commands:** `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -p test_strict_bound.py -v`; `bash -n script/run_strict_bound.sh`; `git diff --check`. Full1200-second runs are left to the user. All preparation and result paths live under `/data/shared/lbh/OMT/result/strict_bound/`, away from ordinary NIA experiment discovery.

Completed: four focused regression tests passed after initial RED, plus a real Z3 5.1.0 CLI smoke test (two UNSAT queries), changed-query rejection and output-overwrite rejection. Independent review checked all127 source/query ASTs and hashes:6224 declarations and15491 original hard assertions retained,117 min/10 max. Prepared directory: `/data/shared/lbh/OMT/result/strict_bound/queries127_NIA_20261026_211519_926612`. No full127 solver batch was started.
