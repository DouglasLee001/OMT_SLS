#!/usr/bin/env bash
# All 6310 cases; direct default-Z3 strict-improvement proof, no auxiliary stages.
# Run with bash; the existing launcher submits Slurm workers.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes server,node4,node5 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--plain-z3-proof \
  "$@"
