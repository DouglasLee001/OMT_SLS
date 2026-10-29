#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/tmp
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$TASK_ROOT/z3-nia/experiment/run_embedded_slurm.py" "$@"
