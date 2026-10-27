# Restore the original OMT preprocessing

The user requests the original conversion behavior and an explanation of SAT
feedback after the Z3 5.1 upgrade. Restore the converter's exact 4.12.3 runtime;
keep native proof solving and competitor Z3 at 5.1.0. This deliberately supersedes
the earlier requirement to use 5.1 for preprocessing as well. Do not commit or
start a full optimization experiment.

The converter will use an independent SDK copy under
`competitor/z3-4.12.3-converter/build`, copied and hash-checked against the old
experiment's frozen SDK. Frozen runs use adjacent `z3-4.12.3/build`. No runtime
dependency on `converter/z3_fork` remains. Native SONAME resolution stays 5.1.
The conversion algorithm, objective protection, and SAT feedback policy stay
unchanged. Avoid patching Z3 5.1's arithmetic rewriter to imitate an older release.

- [x] Copy and verify the original converter SDK without modifying the fork or
  historical snapshots; record its source and version.
- [x] Add a failing nonlinear-encoding regression, then restore the converter's
  pinned SDK loader and loader tests.
- [x] Update frozen converter SDK paths and tests; retain verification of old
  snapshots and separate native 5.1 libraries.
- [x] Check objective semantics, native import, exact old encoding on the 17
  affected cases, and a real frozen conversion/native-solving smoke test.
- [x] Document the deliberate frontend/backend version split and report the
  measured global versus packing-specific SAT feedback counts.

Verification: all 6310 regenerated inputs are byte-identical to the old frozen
experiment, including its Leipzig `--keep-definitions` setting, and all 6310
import successfully into current native NIA_LS. Report:
`/home/lbh/.tmp/restored_converter_20261027.csv` and `.json`.
Five converter/encoding tests, nine snapshot tests, twenty semantic/import tests,
and sixteen native-version/Slurm-caller tests passed; one optional dataset test
was skipped in that unit suite, with full coverage supplied by the separate
6310-case check. A real fresh snapshot loaded converter 4.12.3 and native 5.1,
independently proved toy optimum 3, and retained all 43 recorded hashes. Both
historical real snapshots still verify. Independent code review found no issue.
No full optimization experiment was started.
