#!/usr/bin/env bash
# Current full suite: the custom convex module has been removed from the solver.
# Finite cutset stays enabled; guarded, unseeded SMT and BV120 remain on.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$TASK_ROOT/z3-nia/build/z3" \
  --nodes server,node4,node5 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--smt-exchange=backend \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product \
  "$@"
