#!/usr/bin/env bash
# Matched full-suite control for NIA_20261103_161607_578759.
# Run after the reference experiment finishes to avoid mutual CPU contention.
# Use its frozen solver; omit only the core-guided relaxation neighborhood.
# Full original-query optimality checks and SAT-model feedback remain enabled.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REFERENCE_RUN="/data/shared/lbh/OMT/result/NIA_20261103_161607_578759"
exec bash "$TASK_ROOT/script/run_z3_nia_slurm.sh" \
  --solver "$REFERENCE_RUN/source/nia_ls" \
  --nodes server,node3,node4 --partition all \
  --jobs 180 --timeout 1200 --memory-mb 96000 --queue-workers \
  --solver-option=--convex-exchange \
  --solver-option=--smt-exchange=hints \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product \
  "$@"
