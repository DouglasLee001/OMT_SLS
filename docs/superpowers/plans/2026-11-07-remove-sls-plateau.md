# Remove objective-blocker plateau guidance

**Goal:** Physically remove the feasible neutral-move pressure guidance, keeping the remaining optimization and proof mechanisms.

**Scope:** Delete `State::Pressure/Plateau`, blocker-field construction, pressure scoring, neutral-step selection, its exclusive linear-inequality index, scheduling and telemetry. Remove the same-value representative assignment and resume ordinary diversification from the best validated incumbent. Delete the dedicated neutral test/Makefile target and remove its case from the shared decision-coordinate tests. Remove the native/runtime option and obsolete ablation variant; `all` runs the current full solver and the remaining objective-neighborhood ablation.

Objective projections and effective quadratic/frontier/joint moves, zero-product repairs, ordinary restart/kicks, strict-bound repair, large-target probes, ray-search hints and all finite/unbounded proof backends are preserved. Original NIA_LS, frozen references and historical experiment records remain records of those versions.

## Work

- [x] Confirm the old executable fails the removed-control/telemetry contract.
- [x] Delete the active implementation, scheduling, exclusive state and dedicated tests/build entries.
- [x] Remove native/runtime options and update scripts/current documentation.
- [x] Build and verify the removed methods no longer occur in active code or binary symbols.
- [x] Run native, adapter/suite, retained objective/decision and committed search/proof regressions.
- [x] Review preserved algorithms and record verification.

## Verification

- `make -C z3-nia/build -j12` exited 0; log `/tmp/remove-sls-plateau-build.log`.
- Rebuilt `build/z3` SHA256: `cb67c88ebe6f1225439032ee6631e12a1c74b6be68bb77226cbec1d0a6948fc1`.
- Native plateau-only implementation/control names are absent. `nm -C` has no `State::plateau_field`, `State::neutral_step`, `State::pressure`, or previously removed block-repair symbols.
- Native CLI 10, embedded CLI 11, adapter/wrapper 8, suite 6 and launcher 42 tests pass.
- Committed search/proof regressions: 68 tests, 1 pre-existing optional-dataset skip, no failures/errors; `/tmp/no-plateau-regressions.json`.
- Focused nonlinear objective-projection and retained decision-coordinate executables pass.
- Read-only review found no unintended changes to retained neighborhoods, derived-variable projection, ordinary repair/restart, ray hints or proof backends. `reset(best)` matches the former plateau-disabled path.
- `git diff --check` passes; no formal experiments or git commits were performed.

No git commit or formal experiment submission is requested.
