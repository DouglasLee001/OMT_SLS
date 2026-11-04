#!/usr/bin/env bash
# Current full-suite control: omit guarded-product and the custom BV120 stage.
# Original strict-improvement proof and other options remain enabled.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes server,node4,node5 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--smt-exchange=hints \
  "$@"
