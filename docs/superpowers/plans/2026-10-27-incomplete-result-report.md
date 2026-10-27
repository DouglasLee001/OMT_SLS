# Explicit reporting of interrupted runs

The user expects summary.md for the ended full experiment. Its records cover
all 6310 selected cases, but three are interrupted. Preserve original data and
the incomplete status; generate an explicitly labelled provisional report.

- [x] Add regressions for opt-in reporting of retained validated incumbents,
  unchanged input files, default rejection, and missing-record/model rejection.
- [x] Add `--allow-incomplete` for an explicitly named run with every selected
  row present; preserve all proof/model/time validation and default discovery.
- [x] Generate current summary.md/CSV with a prominent interruption notice,
  verify independent counts, and document the reporting command.

RED: three added feature tests fail because the option is unavailable. GREEN:
33 reporter/comparison tests pass. Real generation for NIA_20261027_182302_810605
retains SHA256 of results.csv, manifest.json, summary.json and frozen competitor.
All 6310 export rows match independent counts: A1=2355, A2=3443, B=487, C=25;
best-opponent W/T/L=2131/4132/47. The three interrupted validated incumbents
remain B, and complete=false is unchanged.

No solver rerun, cleanup-policy change, frozen-source edit or completion-status
rewrite is authorized or needed for this reporting task.
