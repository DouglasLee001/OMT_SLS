# One external directory per NIA_LS attempt

The user requested that future NIA_LS results live below
`/data/shared/lbh/OMT/result`, in `NIA_<timestamp>` directories, including each
run's comparison report. Prepare a full-run command; do not start the full run.

## Design

- Keep solver options, input selection, time limits and search behavior intact.
- Default `run_optimize.py` to
  `/data/shared/lbh/OMT/result/NIA_YYYYMMDD_HHMMSS_microseconds`, using
  Asia/Shanghai time for the directory name. Preserve explicit `--output` and
  refusing to overwrite an existing run directory.
- After a complete run, invoke the existing report command on that exact run,
  writing `nia_ls.csv`, `combined.csv` and `summary.md` into the run itself.
  Continue using the fixed repository `result/competitor.csv` as the baseline.
  Interrupted runs retain raw records and do not receive a complete report.
  Reporting errors remain visible without discarding solver results.
- The standalone reporter discovers complete runs only under the new external
  root, with no silent legacy fallback. Explicit historical run paths remain
  supported. Its default output is the selected run directory; `--output` still
  overrides that choice.
- Retain the raw `results.csv`, manifest, summary JSON and case logs. Do not
  migrate or delete historical runs or change version-control ignore rules.

## Validation

1. Add failing tests for the new root/name/timezone, automatic report on success,
   exact explicit output, no report on interruption, reporting failure and
   read-only dry runs; then implement the runner changes.
2. Add failing reporter tests for in-place output and immutable raw inputs,
   new-root discovery, explicit historical runs and custom output; implement.
3. Run focused and existing runner/report/proof regressions, shell syntax and
   patch checks. Exercise the complete directory/report flow with tiny fake
   solver fixtures only, and dry-run the exact 6310-case user command.
4. Update the script/result documentation and return the full-run command.

## Completed validation

- 109 focused and existing runner, proof and reporting regression tests passed.
- A tiny fake-converter/fake-solver fixture exercised the real batch writer and
  real reporting subprocess, producing all raw and report files in one
  temporary run directory.
- The exact proposed full-run command dry-ran successfully: 6310 cases, 180 jobs,
  1200 seconds, with no build, search or experiment output creation.
- Default runner output and standalone report discovery both use
  `/data/shared/lbh/OMT/result`; the runner's folder name uses Asia/Shanghai.
- Independent read-only review, shell syntax and patch-format checks passed.
  Existing runs and the dirty Z3 fork were preserved. No full run was started.
