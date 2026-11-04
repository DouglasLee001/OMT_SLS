#!/usr/bin/env bash
# Current z3-nia: remaining proof helpers enabled, all 6310 instances.
# Run with bash, not sbatch: the existing launcher submits Slurm workers.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes node1,node2,node3 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--smt-exchange=hints \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product \
  "$@"
