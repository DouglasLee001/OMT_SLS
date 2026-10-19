# Benchmark runner implementation plan

**Goal:** `./run.sh {z3|optimathsat}` from `script/` runs up to 180 benchmark jobs and records objective values and elapsed seconds under `result/{solver}`. The user starts the full run.

**Design:** A Bash entry point resolves the repository directory. A Python standard-library scheduler starts one isolated worker per input, monitors timeout and signals, and flushes each completed record to CSV and JSONL. Workers load the locally built solvers, emit improving objective events, and retain raw solver output. Results use a fresh timestamp directory and `latest` symlink.

**Defaults:** OMT(NIA) only, using `nia_benchmarks_subset.txt` (5744 NIA); benchmark root `/data/shared/lbh/OMT/benchmarks`; 180 concurrent jobs; 1200 seconds per input. `TIMEOUT=0` disables the deadline, and `BENCHMARK_LIST`, `BENCHMARK_ROOT`, `RESULT_ROOT`, `JOBS` provide explicit overrides for small checks and user choices.

**Value/time semantics:** Wall-clock seconds start at worker launch, excluding queue time. Capture strict improvements rather than replacing the timestamp on repeated values. Z3 uses the installed Optimize model callback; OptiMathSAT streams its `new:` messages and queries objectives/model after solving. Preserve exact SMT expressions, including rational, algebraic, infinity and epsilon. Solver-reported bounds and a concrete best model value are separate. Optimality is a solver report with a consistency check, not independent mathematical verification. Timeout/unknown/error/unsat retain their own statuses; feasible candidates survive timeout. Timing from stream output is observation time, and a value observed only in the final model is marked accordingly.

## Tasks

- [x] Write parser/value tests before implementation: quoted symbols containing parentheses, comment/string handling, objective extraction, exact rational/extended values, infinitesimal classification.
- [x] Implement `smtlib.py` and run the parser tests. Remove only query commands when preparing OptiMathSAT input; append `check-sat`, `get-objectives`, `get-value`, `exit` without changing assertions.
- [x] Write worker tests for simple attained optimum, duplicate progress timestamp, partial result on timeout, epsilon/unbounded results, and objective/model inconsistency. Keep probes tiny and use temporary result directories.
- [x] Implement `solver_worker.py`: isolated Z3 Python API and buffered-output-safe OptiMathSAT CLI; flushed JSON event protocol and raw logs; finite optimum consistency check.
- [x] Write scheduler tests for output CSV/JSONL, fresh run directories, concurrent job limit, timeout cleanup, interruption, empty/invalid input configuration and missing executable. Use miniature inputs/fake sleeping workers for scheduling tests.
- [x] Implement `run.py` and executable `run.sh`. Stream completed rows from a single writer, record metadata/commands and preserve previous runs.
- [x] Review requirement coverage and timing correctness independently; fix findings before completion.
- [x] Run focused tests, `bash -n`, help/invalid invocation checks and tiny real-solver checks only. Do not launch the 5744-case NIA run.
- [x] Document fields and commands in `README.md`, then return the two full-run commands to the user.
