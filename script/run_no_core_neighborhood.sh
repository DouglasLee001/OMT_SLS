#!/usr/bin/env bash
# Legacy entry name for the current full solver; the core module was removed.
# Full original-query optimality checks and SAT-model feedback remain enabled.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes server,node3,node4 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--smt-exchange=backend \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product \
  "$@"
