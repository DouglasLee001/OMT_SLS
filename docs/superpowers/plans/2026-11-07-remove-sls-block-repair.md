# Remove SLS block linearization repair

**Goal:** Physically delete the active factor-cover/LIA-SLS block-repair module and make the current full solver equivalent in configured functionality to the completed no-block-repair ablation.

**Scope:** Remove `nia_omt_bilinear.cpp/.h`, its optimizer state/scheduler/counters, dedicated semantic test and build references. Remove its CLI/config/adapter option and obsolete ablation variant. Retain objective neighborhoods, plateau pressure, legacy first-feasible search, strict-bound NIA repair, finite large-target probing, SLS ray certificates and all existing finite-optimality proof stages. Historical reference implementations and frozen experimental data remain records of those versions.

**Execution:**

- [x] Establish RED: old binary still accepts the removed control and advertises old metadata.
- [x] Delete implementation, dedicated test and CMake/Makefile entries; delete optimizer scheduling and fields.
- [x] Remove native and runtime controls; update the remaining two SLS ablations and three-group suite.
- [x] Rebuild the embedded executable and verify the removed symbols are absent from the linked binary.
- [x] Run CLI, adapter/suite, existing committed search/proof and focused State regressions.
- [x] Review that preserved search and proof modules were not changed; document the current default.

## Verification

- Embedded build and subsequent dependency-cache refresh exited 0; logs `/tmp/remove-sls-block-build.log` and `/tmp/remove-sls-block-dependency-pass.log`.
- Binary `z3-nia/build/z3` SHA256: `35f6a06d7fc9a4d126b6437b80e164723078ca72e7c5267ebef288d7445e6164`.
- `nm -C` has no `OmtBilinearRepair` or `nia::bilinear_detail` symbols. The dedicated orphan object/dependency files were removed; regenerated CMake dependency and object-list files have no references.
- Native ablation CLI 9, embedded CLI 11, runtime adapter 7, suite 6 and launcher 42 tests pass.
- Original committed search/proof regressions: 68 tests, 1 pre-existing optional-dataset skip, no failures/errors; `/tmp/no-block-repair-regressions.json`.
- Focused nonlinear objective-projection and original neutral-plateau executables pass.
- Active source/runtime/build-list scan and `git diff --check` pass. Read-only review confirmed preserved search/proof implementations are unchanged by this removal.

No formal experiment submission or git commit is requested.
