# SLS mechanism ablations

**Goal:** Give the user four reproducible configurations for the complete OMT(NIA) suite: full, without objective neighborhoods, without plateau pressure, and without block repair. Prepare and verify code and commands; the user submits the experiments.

**Architecture:** Three independent default-on native switches, frozen by the existing experiment adapter. All configurations use one compiled binary, the same instances, seed policy, 1200-second allowance and complete proof backend. The wrapper inherits node3–5 and 180 concurrent cases per node from `run_tactic_chain.sh`.

**Boundary decisions:**

- Objective neighborhoods: turn off equality projection in feasible coordinate-improvement probes, effective projected-objective quadratic probes, paired exchanges, difference-constraint repairs, objective-frontier probes and disjunctive repairs. Keep projection used by initial/infeasible repair, plateau neutral moves, restart kicks and generic accepted-direction acceleration. Those are shared primitives of the other mechanisms. This is a grouped ablation, not a claim about an individual operator.
- Plateau: omit blocker-field construction and neutral pressure-lowering moves. Keep all strict-improvement neighborhoods.
- Block repair: omit construction/resumption of `OmtBilinearRepair`; keep strict-bound NIA repair and large-target probes. This is separate from proof-stage finite coverage.
- Guarded-product, finite coverage, arith2/BV/default tactics, unbounded certificates and initial-solution scheduling stay configured identically in all groups. Changed trajectories and time allocation are measured effects of ablation.

## Work

- [x] Add CLI/config controls and confirm unsupported-control tests fail on the old binary.
- [x] Add adapter/wrapper controls and confirm frozen solver options preserve independent variants.
- [x] Add optimizer guards and per-module diagnostic counters.
- [x] Verify projection-off candidates keep full exact scoring, while shared projection remains available; verify plateau-off has no neutral move calls.
- [x] Build the embedded binary; run native CLI, focused State and existing optimization regressions.
- [x] Document boundaries and commands in `z3-nia/README.md`; run dry-run selection validation for all four groups.

## Verification

- `make -C z3-nia/build -j12`: exit 0. Executable SHA256 `7c7c4efb56d52052385aa8a39aedfacb0ff5eaea58f0bd6e457b79fd0369508c`.
- New CLI 7 tests, new adapter/wrapper 6 tests, existing launcher 40 tests, embedded CLI 11 tests: pass.
- Existing committed regressions: 68 tests, 1 pre-existing optional-dataset skip, no failures/errors; report `/tmp/sls-ablation-regressions.json`.
- Focused exact State projection and existing neutral plateau executables: pass.
- All four real wrapper dry-runs select 6310 cases on node3–5, 180/node, 1200 seconds/case; no snapshot/submission/solver execution.
- `bash -n script/run_sls_ablation.sh` and `git diff --check`: pass.
- Independent review checked default argument equivalence and the preserved restart/shared-projection boundary, documented in the README.

No formal benchmark jobs or git commits are part of this implementation task.

## Follow-up: one-command sequential suite

User requested one command for all four groups. Add `all` to the existing wrapper,
launch a background suite with a single log, and wait for each group's existing
coordinator before starting the next. An explicit group failure stops the suite.
Keep each group's normal independent `NIA_<timestamp>` directory.

- [x] Add optional launcher `--wait` without changing the default asynchronous entry.
- [x] Verify controller exit-code propagation and no waiting in dry-run/prepare-only; 42 launcher tests pass.
- [x] Implement suite detachment, ordered invocation, failure propagation and no-write dry-run.
- [x] Verify all-mode behavior against a fake submission boundary; no real Slurm jobs. Six suite tests and six existing adapter/wrapper tests pass.
- [x] Document `bash script/run_sls_ablation.sh all` and the suite log.
