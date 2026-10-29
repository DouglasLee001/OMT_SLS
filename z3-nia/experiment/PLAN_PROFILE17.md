# GNU++17 candidate: complete paired evidence

Candidate executable SHA256:
`29bbfe34cb9292cbd55aa02564728965a7a28c424a212126f71a92bc2b370ca8`.
Baseline commit: `6614f3c1ea13539918b6f641c64446b753e6db0e`.

The initial 52-case C++20 regression had two objective losses and no solved
count loss. The subsequent same-node, native-IR-only 15-case diagnostic also
had objective losses under both compiler profiles. Thus neither byte-identical
conversion nor GNU++17 alignment establishes performance noninferiority.

Prepare a new immutable experiment from the current receipt, then use
`watch --collect-full-evidence` to run all 6,310 cases after recording the
52-case gate, including any failure. Both variants get the same committed
configuration, seeds, instances and 1,200-second total deadline. Use only
healthy server/node1–5 allocations with 80 available CPUs per job.

Before that run, the aggregate evaluation criteria are fixed as follows:

- No new embedded error/interrupted instances relative to the paired baseline.
- No decrease in validated feasible-model count.
- No decrease in A1+A2 solved count.
- Objective-value wins are at least losses.

Report every per-instance loss and the stronger zero-individual-loss diagnostic
separately. A pending/partial full run cannot meet the criteria, and a finished
evaluation is not itself a performance claim. Retain failed checks and repeat
or investigate generic causes as appropriate; never alter earlier frozen data
or selectively choose passing subsets.
