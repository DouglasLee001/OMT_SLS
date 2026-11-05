#!/usr/bin/env bash
# Full-suite control: disable only the custom BV120 proof stage.
# Run with bash, not sbatch: the existing launcher submits Slurm workers.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes node3,node4,node5 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--smt-exchange=backend \
  --solver-option=--optimality-bv-seconds=0 \
  --solver-option=--guarded-product \
  "$@"
