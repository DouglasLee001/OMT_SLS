# Remove core-to-SLS scan feedback

> **For agentic workers:** Use subagent-driven development for the isolated C++ cleanup, then review and verify the integrated change. Do not commit or launch experiments.

**Goal:** Remove core-based SLS variable ordering entirely while preserving core-driven Z3 neighborhood relaxation and SAT-model feedback.

**Architecture:** Delete the SLS priority set, its scan reordering, counters, CLI switch, and feedback-only result fields. Keep neighborhood assumptions, random core-member release, candidate validation, and the independent original-formula optimality proof unchanged. Completed ablation artifacts remain available in their frozen result directory.

**Tech Stack:** C++17, embedded Z3 5.1, Python unittest, existing CMake Makefiles.

## Execution

- [x] Rewrite CLI tests so real core queries still run, feedback fields disappear, and `--core-sls-feedback=on/off` is rejected. Rewrite adapter tests to reject the removed option while forwarding `--core-neighborhood` unchanged. Run both against the existing code and confirm failures.
- [x] Remove feedback-only state and exports from `z3-nia/sls/src/nia_omt_optimize.{cpp,h}`, `nia_omt_core_neighborhood.{cpp,h}`, CLI parsing in `call.cpp`, and embedded help in `sls/frontend/nia_frontend.cpp`. Keep local core extraction and random release intact; adjust C++ semantic tests to observe releases rather than the removed feedback payload.
- [x] Remove the adapter allow-list entries. Remove the completed experiment's temporary workspace launcher and tests, retaining the frozen analysis script and all results under `/data/shared/lbh/OMT/result/core_sls_ablation_20261103_124241_102526/`.
- [x] Update the operational README and historical experiment record so they distinguish the removed scan feedback from the retained Z3 neighborhood mechanism. Do not rewrite the paper or implement the discussed UNKNOWN retry policy.
- [x] Build with `make -C z3-nia/build -j4 shell`. Run core CLI and adapter tests, C++ neighborhood semantic tests, Slurm launcher regressions, and the existing committed end-to-end regression runner.
- [x] Review the final diff and source search for remaining live feedback references. Confirm proof boundaries and historical frozen artifacts are unchanged.

## Test commands

```sh
python3 -B z3-nia/sls/tests/test_core_neighborhood_cli.py
python3 -B z3-nia/sls/tests/test_core_sls_feedback_adapter.py
python3 -B z3-nia/sls/tests/test_omt_core_neighborhood.py
python3 -B z3-nia/sls/tests/run_committed_regressions.py --report /tmp/omt-remove-core-feedback-regressions.json
git diff --check
```

## Verification

The rebuilt executable is `/home/lbh/OMT/z3-nia/build/z3`, SHA256 `35df5ef5fe3ac2dbcee308a4871781ea2b956470d352df820098d9a21c3f8198`. The 7 CLI tests, 3 removed-option adapter tests, 37 embedded launcher tests, 15 standard Slurm tests, 2 other exchange adapter tests, and isolated C++ neighborhood semantics passed. The 68 committed end-to-end tests completed with 67 passes and 1 pre-existing optional dataset skip; receipt: `/tmp/omt-remove-core-feedback-regressions.json`. Independent static review found no defects. No experiment was submitted and no commit was created.
