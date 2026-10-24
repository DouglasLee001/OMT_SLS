# Finite large-objective probe implementation plan

**Goal:** Add an optional native local-search attempt to reach a finite objective
threshold (default experimental magnitude `10000000000`), and support testing the
historical cases excluded from the bounded lists. This is not an infinity proof.

**Architecture:** Reuse the exact feasibility continuation on a private copy of
the complete native formula. Preserve the existing own-incumbent strict repair.
Only the new finite-threshold mode enables wide checked128 moves. Validate every
returned assignment and the original objective with GMP before publication.

**Stack:** C++/GMP, the existing native local-search engine, Python batch runner.

## Design decisions

- Native CLI: `--large-target=10000000000`; positive finite magnitude, off when
  absent. Min uses `F <= -M`, max uses `F >= M`, including equality.
- Trigger after at least three strict improvements after the first validated
  feasible model and improvement of at least `max(100, ceil(abs(first)/10))`.
  If the current best already reaches the signed threshold, skip the probe.
- Retain one private search across slices; schedule approximately one second per slice,
  with at least five seconds between slices and total probe time at most
  `min(30 seconds, 5% of the instance budget)`, including construction. One non-preemptible construction/arithmetic/validation
  operation can finish after the local allowance; the external case deadline remains enforced.
- The original working state, feasible incumbent, and RNG are not overwritten
  by unsuccessful probes. Probe candidates pass the same final acceptance gate.
- Keep incumbent/final event types; add finite-target metrics to final output.
  No `unbounded`, `unsat`, or optimality claim follows from a probe outcome.
- Python exposes `--large-target M`, default off, forwards and records it.
  `--subset excluded` means full table minus bounded table by source identity;
  it is a historical large-absolute-value filter, not a proven-unbounded set.
- Existing original/new historical CSVs and benchmark directories remain intact.

## Tasks

- [x] Add failing native tests in `NIA_LS/tests/test_objective_repair.cpp` for
  inclusive min/max thresholds beyond int32 and the old `1e8` move limit;
  nonlinear auxiliary values beyond int64; bounded unreachable goals; invalid
  seeds, representation overflow and expired budgets. Run the test and observe
  failure before implementing the finite-threshold constructor.
- [x] Extend `nia_omt_repair.h/.cpp` with an explicit finite-threshold overload.
  Preserve old construction checks; derive the new inclusive literal using GMP,
  then checked signed128 conversion. Enable wide moves only on its private solver.
- [x] Guard the old assignment/long-term heuristics in `nia_ls.cpp` with a
  default-false private-probe flag in `nia_ls.h`; keep checked arithmetic active.
  Run `make -C NIA_LS/src test-objective-repair` and existing arithmetic tests.
- [x] Add failing scheduler and CLI/integration tests. Integrate progress-based
  triggering, deadlines, retained finite repair, final statistics and the native
  CLI option in `nia_omt_optimize.*` and `call.cpp`. Verify default-disabled
  behavior, both directions, inclusive threshold, and `is_optimal=false`.
- [x] Add failing Python selection/argument/worker tests, then implement runner
  forwarding, manifest/statistics metadata and `excluded` set selection. Run
  `python3 -m unittest script.tests.test_run_optimize script.tests.test_verify_optimize`.
- [x] Build the real binary, run native regression targets and real end-to-end
  tests on a small sample of the excluded cases. Independently validate retained
  final models. Include a bounded negative example and a synthetic max case,
  since all 2045 excluded original cases use minimization.
- [x] Review the diff and update `NIA_LS/实现细节.md` with trigger, finite semantics,
  numeric limits, results and reproducible user commands. Do not commit.

## Acceptance examples

```text
min x, H=true, feasible seed x=0, goal=-10000000000
  -> returned model must satisfy x<=-10000000000
max x, H=true, feasible seed x=0, goal=10000000000
  -> returned model must satisfy x>=10000000000
min x, H=(0<=x<=10), seed x=5, goal=-10000000000
  -> no threshold model; seed and original solver remain unchanged
max t, H=(t=x*x), seed (x,t)=(1,1), goal=10^20
  -> valid linked assignment or honest no-result, never truncated arithmetic
```

The historical excluded set contains two AProVE instances with an explicit
nonnegative objective and a recorded optimum of zero. They must not be described
as proven unbounded or classified unbounded after a failed finite probe.

## Verified results

- Native build succeeded; optimization integration: 23 tests, 22 passed and one optional dataset test skipped.
- Native repair, arithmetic, feasibility continuation, scheduling, decision-coordinate and conditional-projection targets passed.
- Python runner/verifier: 79 tests passed.
- Default-disabled incumbent sequences and step counts matched the prior binary on three deterministic cases.
- Real seven-case, twenty-second smoke: six independently validated models, five finite-threshold hits, one known optimum match and one case without a model.
- The five real threshold hits were already reached by main search; no new-operator performance improvement is claimed.
- Final user command dry-run selects 2045 original excluded cases at 180 workers and 1200 seconds.
- Code review fixes for slice deadlines and final-output cleanup races were verified.

## Signed128 maximum magnitude (2026-10-23)

The experimental magnitude is capped at `2^127-1`, precisely
`170141183460469231731687303715884105727`. All supported magnitudes use the
existing finite-threshold OmtBoundRepair. The separate GMP boundary backend,
branch and dedicated test target have been removed. CLI, scheduler, runner and
independent verifier reject `2^127` and larger values.

Validation covers exact max-magnitude min/max models through the ordinary repair
path, invalid CLI/magnitudes, the existing optimization suite, and Python
runner/verifier regressions. No complete experiment is launched for this change.

Final simplified verification: build passed; ordinary finite repair reaches both signed maximum thresholds; 24 integration tests (23 passed, optional dataset skipped), 81 Python tests and the scheduling target passed. Final 2045-case/180-worker/1200-second command passed dry-run.
