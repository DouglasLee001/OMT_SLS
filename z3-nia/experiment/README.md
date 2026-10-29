# Paired embedding experiment

This harness compares a fresh standalone build of commit
`6614f3c1ea13539918b6f641c64446b753e6db0e` with the embedded executable. It
does not build either executable. The historical full run supplies the exact
6,310-case manifest; its results are not reused as the HEAD baseline.

Run the scheduler-free tests:

```bash
python3 z3-nia/sls/tests/test_embedding_experiment.py
```

After both builds and integration checks pass, prepare a new output directory:

```bash
python3 z3-nia/experiment/embedding_experiment.py prepare \
  --baseline z3-nia/reference/NIA_LS/src/nia_ls \
  --baseline-build-record z3-nia/reference/baseline-build.json \
  --embedded z3-nia/build/z3
```

The printed `/data/shared/lbh/OMT/result/NIA_<UTC timestamp>/` path is `RUN`
below. The baseline receipt must contain `git_commit` and
`executable_sha256`; `embedding-build.json` must identify the current embedded
executable and the pinned baseline commit. Preparation checks the reference tree against the pinned
Git archive and freezes the source archives, executables, non-glibc native
libraries, baseline converter SDK, reference scripts/tables, configuration,
`embedding-build.json`, `build.sh` and `toolchain/*/provenance.json`.
Original benchmark paths remain shared; all 6,310 content hashes are checked
before admission and the assigned hashes are checked before and after a worker.
The Python interpreter path/hash is recorded and checked on each node.

To run regression and then continue automatically to full:

```bash
python3 "$RUN/source/harness/embedding_experiment.py" watch "$RUN"
```

The equivalent two commands, useful when inspecting regression evidence first:

```bash
python3 "$RUN/source/harness/embedding_experiment.py" watch "$RUN" --phase regression
python3 "$RUN/source/harness/embedding_experiment.py" watch "$RUN" --phase full
```

The full command completes regression first if necessary. It does not request
another approval. The default regression samples two cases per available
`suite/family-or-category/direction/reference availability` stratum, chosen by a
stable source-path hash. Original cases use the first family after `QF_NIA/`;
new cases use their category. It also forces the portfolio case
`gram-m12-q6-s1058--a34080af91dd.smt2` and a deterministic Leipzig representative;
missing mandatory cases are errors. The manifest records their indices and
`forced_case_count`. The current full manifest produces 52 regression cases.
Each regression variant runs all 52 cases in one 80-CPU job; full uses the
configured shards (six per variant by default).
Full starts only after both regression variants finish with
no objective losses, no errors, and no decrease in A1+A2 solved count. The gate
and its comparison evidence are written to `regression_gate.json`.

To collect a complete evaluation despite a failed strict regression gate, use
this explicit command on a **newly prepared** run:

```bash
python3 "$RUN/source/harness/embedding_experiment.py" watch "$RUN" --collect-full-evidence
```

This option preserves the failed `regression_gate.json` and the paired loss
details. It permits collection only; its terminal state is
`evaluation_complete`, which is not an assertion that the embedded solver is
at least as strong. Without the option, the original failed-gate stop remains.
Frozen earlier runs and their launchers are not changed by editing this tree.

Each new manifest declares the full-run aggregate criteria before execution:
no new embedded `error`/`interrupted` instances, no decrease in validated
feasible-model count, no decrease in A1+A2, and paired objective wins >= losses.
The same-error-count-on-different-instances case fails the first criterion.
`full_evaluation.json` reports these checks separately from the regression
gate. It cannot report that the criteria are met until all 6,310 paired cases
are complete. Zero individual objective losses remains a separate, stronger
diagnostic. These are observed single-run aggregate criteria, not a statistical
guarantee: wallclock-limited search can follow different trajectories even
with the same seed. Retain per-case changes and repeat evidence when needed.

```bash
python3 "$RUN/source/harness/embedding_experiment.py" status "$RUN"
python3 "$RUN/source/harness/embedding_experiment.py" report "$RUN"
```

Both variants use a 1,200-second total per-case deadline including conversion,
the same default seeds (`mt=0`, `lia_seed=0`, no explicit seed flag), no historical
target/known-optimum stopping, and the historical full proof configuration:
unbounded+optimal, resume/burst/cutset/balanced, stall 10, proof slice 30,
initial source Z3 after 300 with 120 seconds, magnitude/threshold 1,000,000.
The experiment adapter maps the reference runner's converter command to
`z3 --nia-ls INPUT --import-only --export-native=PATH` and its solver command to
`z3 --nia-ls --native-input INPUT.nia --optimize` with all original flags. It
never adds `--mode`. This adapter is experiment plumbing; the importer and SLS
are implemented inside the executable.

Only healthy IDLE/MIXED `server,node1,node2,node3,node4,node5` nodes with
`min(CPUTot,CPUEfctv)-CPUAlloc >= 80` and enough unallocated proportional memory
are admitted. Each single-node job requests exactly 80 CPUs and
`ceil(RealMemory*80/min(CPUTot,CPUEfctv))` MB. There is no exclusive allocation,
zero-memory request, cancellation, or node reconfiguration. A busy cluster
leaves the watcher waiting. Polling defaults to 30 seconds.

The watcher holds a nonblocking file lock. Its final external action before
each submission is another node resource check, followed by a durable exclusive
intent (including placement) and `sbatch`. One unfinished or uncertain task
reserves its node. A missing receipt is recovered only from the exact Slurm
comment or a worker-start record; it is never retried. A terminal Slurm job
without a worker completion record fails closed. A watcher interruption can be
recovered by rerunning `watch` on the same directory.

Case artifacts remain under `tasks/<phase>-<variant>-<shard>/run/cases/`.
`reports/<phase>/paired.csv` compares exact objectives and retained timestamps;
the variant subdirectories contain the original result schema and official
reference reports (`nia_ls.csv`, `combined.csv`, `summary.md`). A1 is proved
unbounded, A2 is a proved finite optimum, B has a model, and C has no model.
`comparison.json` records paired wins/ties/losses, state counts, solved deltas,
per-variant error counts and newly failing embedded instances,
the paired subset where both variants remain B/C, and official competitor
statistics. Top-level `summary.md` presents both variant state/solved counts and
the all-case and both-B/C paired WTL tables. Whole-batch makespan is not a solver metric
because admission and shared-node load can differ. Proof labels retain the
reference verifier's scope; the harness does not independently replay UNSAT.
