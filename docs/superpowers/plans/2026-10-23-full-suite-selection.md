# Full OMT(NIA) Suite Selection

**Goal:** Provide one command selecting all 5744 original and 566 generated instances, with the existing 180-job/1200-second and optional unbounded-proof behavior.

**Design:** Add `--subset all` while preserving the historical default subset. Read `result/{suite}/{suite}.csv` directly, preserving the existing path, duplicate, objective and known-optimum checks. Extend independent historical/canonical selection auditing to the same full set.

- [x] Add a failing runner test for the full option, original/new union, omitted filtered tables and manifest recording.
- [x] Implement the minimal full-list branch in the runner.
- [x] Add failing verifier tests, implement full historical/canonical selection support and preserve legacy auditing.
- [x] Run the runner/verifier regression suites and dry-run the real full command. Do not start a full experiment.
- [x] Document and return the verified command; preserve existing working changes and do not commit.

Validation: 112 runner/verifier/proof tests passed. The real full command dry run selected 6310 unique instances (5744 original, 566 generated), 6143 min / 167 max, with 180 jobs and a 1200-second limit. No full experiment was started.
