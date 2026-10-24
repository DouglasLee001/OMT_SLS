# Competitor rerun with retained unbounded reports

The user approved rerunning both competitors after the short test established
that their final infinity reports were lost from compact historical CSVs.
Prepare commands and scripts; the user starts the full experiment.

## Design

- Reuse the existing scheduler and worker with identical solver settings.
- Keep existing four-column entry points compatible. An optional
  `preserve_optimality=True` scheduler mode adds objective direction, execution
  status, final reported optimum and its time, optimality status, and a strict
  `is_unbounded` flag to both CSV and JSONL.
- Confirm unbounded only from a successful final solver report with the
  appropriate infinity and completed optimization flag. An initial infinity
  bound or an improving finite value is insufficient.
- Add `script/run_unbounded.sh {z3|optimathsat}` and a Python entry point.
  Preflight original and new selections, then run them sequentially per solver.
  Default to 180 workers and 1200 seconds per instance without an added memory
  limit. Use every original NIA instance and every flat new `instance/*.smt2`,
  excluding filtered copies, for 5744 + 566 = 6310 current inputs.
- Write to `/data/shared/lbh/OMT/unbounded/{original,new}/{solver}/{timestamp}`
  with `latest` links; retain original logs and full case records. Do not launch
  full experiments or rewrite historical result tables.

## Tasks

1. Add regression tests to `script/tests/test_run.py` for final infinity reports,
   maximization, finite controls, timeouts and preserved CSV/JSONL fields. Observe
   failures, implement the optional scheduler mode in `script/run.py`, then run
   `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -p test_run.py -v`.
2. Add `script/tests/test_run_unbounded.py` with temporary original/new suites,
   dry-run no-write assertions, argument validation, preserved-result dispatch,
   continuing after a suite's solver errors, and stopping after interruption.
   Observe failures, implement the Python/shell entry points, and rerun tests.
3. Document defaults, commands, locations and infinity semantics in
   `script/README.md`. Run shell syntax checks and dry runs for both solvers to
   verify each selects exactly 6310 NIA inputs without creating output.
4. Run short real-solver finite/unbounded controls through the modified scheduler
   in a local ignored evidence folder. Verify CSV and JSONL retain infinity and
   flags, review the diff, then give sequential full-run commands to the user.

No commits are requested. Preserve all unrelated worktree changes.

## Validation completed

- Related runner, entry-point, worker and SMT-LIB tests: 59 passed.
- Both actual command dry runs select original 5744 + new 566 = 6310 inputs,
  with 180 jobs, 1200 seconds and the requested output roots.
- Real-solver entry-point smoke: each competitor ran seven tiny controls;
  four positive/negative infinity reports and three finite optima per solver
  were preserved and checked in both CSV and JSONL. Evidence:
  `result/unbounded_smoke/20261023-114835-rerun-format/verification.json`.
- Independent read-only review found no material issues. Shell syntax and
  patch whitespace checks passed. No full experiment was launched.
