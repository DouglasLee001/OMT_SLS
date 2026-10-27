# Unified Z3 5.1 with the original nonlinear atom encoding

The user requests one Z3 5.1 implementation for conversion and native solving,
with the smallest practical change to preserve the previous conversion behavior.
This supersedes the interim 4.12.3 converter runtime. Preserve the user's restored
`NIA_LS/converter/z3_fork` and ZIP; they are reference material, not active runtime.
Do not commit or launch an unrequested full optimization experiment.

Design: add one arithmetic rewriter Boolean parameter, `factor_nonlinear`, default
true, guarding only the new `factor_le_ge_eq` fallback. Converter-local tactic
parameters set it false; native proof solving retains the default true behavior.
Avoid process-global parameter changes because future embedding should allow both
uses in the same process. Keep objective protection and the existing tactic order.
Use one rebuilt 5.1 library for the converter, native NIA_LS and CLI, with matching
Python bindings, and freeze identical library bytes into both Slurm locations.

The restored legacy fork has export plumbing but no custom arithmetic rewriter.
Port neither its search bypass nor its complete QF_NIA frontend. The objective is
the old polynomial atom representation, not a claim of identical old frontend
implementation or identical serialization for every input.

- [x] Add failing tests for the new local parameter, default 5.1 behavior, and
  preserving nonlinear equality/inequality atoms without global leakage.
- [x] Implement the three-file Z3 parameter guard and rebuild/install 5.1.
- [x] Switch converter and snapshots to 5.1; apply local parameters to every
  arithmetic rewrite stage, including solve-eqs and CNF fallback.
- [x] Verify objective semantics, same-process default behavior after conversion,
  frozen runtime identity, native proof smoke tests, and real benchmark coverage.
- [x] Compare affected 17/50 encodings with original references and run a bounded
  first-model smoke check on the previously diagnosed examples.
- [x] Document the patch, build, version/dependency checks and actual validation.

Evidence: `/home/lbh/.tmp/unified51_validation_20261027/summary.json` records all
6310 conversions/imports, 2995 exact old encodings, and all 17 affected cases
byte-identical to their old references. The selected 50 inputs are retained in
that validation directory; this is not a new 1200-second performance run.
`/home/lbh/.tmp/unified51_first_models_20261027/summary.json` records two successful
40-second native smoke runs. Fresh frozen converter/native libraries have the
same SHA256; the frozen smoke independently proved objective 3, and all three
historical snapshots still verify. Independent review found no material issue.
The unused interim 4.12 runtime was moved intact to `.tmp` reference storage;
the user's original fork checkout and ZIP remain untouched.
