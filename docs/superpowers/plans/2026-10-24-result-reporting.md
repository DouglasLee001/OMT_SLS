# Repeatable NIA_LS comparison reports

The user requested a reusable postprocessing command and a saved copy of the
current statistics under `result`. Existing `result/competitor.csv` is the fixed
two-solver baseline; it contains the older Chinese status labels.

## Design

- `script/report_results.sh [RUN_DIRECTORY]` defaults to the most recently
  started complete NIA_LS run under `result/optimize`. Explicit paths may point
  to the run directory or its `results.csv`.
- `--competitor` overrides `result/competitor.csv`; `--output` overrides
  `result/comparison/<run-name>`. The baseline remains unchanged.
- Read the run's manifest, results and completion summary. Align by the full
  logical name, preserving original `QF_NIA/...` names and generated
  `new_benchmark/<category>/instance/...` names. Use manifest directions instead
  of opening all SMT files. Reject missing/duplicate cases and unfinished runs.
- Export `nia_ls.csv` (four columns), `combined.csv` (ten columns), and
  `summary.md` (also printed to stdout). Subset experiments compare only their
  selected instances; all selected instances must exist in the baseline.
- Preserve A1/A2/B/C semantics and the recorded three time sources. A2 accepts
  a recorded own optimum flag or a validated incumbent equal to the run's
  recorded externally proved optimum. Do not infer proof from a large number.
- Compare exact integers, with unbounded best and NA worst. Honor min/max for
  finite values. Include NA/NA ties, and provide both best counts including and
  excluding all-NA rows. Report full and all-B/C subset statistics.

## Tasks

1. Add pure statistics tests before implementing
   `script/result_comparison.py`; cover exact huge integers, maximization,
   infinity/NA, ties, proof-status-independent comparisons and subset filtering.
2. Add entry-point tests before implementing `script/report_results.py` and
   `script/report_results.sh`; cover export/join, old Chinese baseline statuses,
   default selection, subset runs, known-optimum validation, timing semantics,
   duplicate/missing baseline cases and unfinished-run rejection.
3. Document the command in `script/README.md`, and link the actual saved report
   from `result/README.md`. Run focused tests, shell syntax and patch checks.
4. Execute the command on `20261023-071653-857674` and verify all 6310 exported
   rows against the existing tables. Independently reproduce the previously
   reported counts. No solver runs or commits are requested.

## Completed validation

- 24 focused entry-point and statistics tests passed, including regression tests
  for proof priority, termination/time acceptance, infinity direction and
  integers beyond Python's default 4300-digit conversion limit.
- Both automatic selection and explicit-run commands executed successfully.
- The saved report is `result/comparison/20261023-071653-857674/summary.md`.
  Its two CSV files match the previously checked 6310-row tables exactly.
- All full-suite and 612-case all-unsolved statistics reproduce the previous
  report. Independent read-only review confirmed the reviewed issues resolved.
- Shell syntax and patch-format checks passed. No solver was rerun, no baseline
  was changed, no git-ignore rule was changed, and no commit was created.
