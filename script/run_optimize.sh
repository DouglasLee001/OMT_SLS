#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OPTIMIZE_BUILD=1
for OPTIMIZE_ARG in "$@"; do
    case "$OPTIMIZE_ARG" in
        --dry-run|--help|-h) OPTIMIZE_BUILD=0 ;;
    esac
done
if [[ "$OPTIMIZE_BUILD" == 1 ]]; then
    make -C "$TASK_ROOT/NIA_LS/src" -j"${BUILD_JOBS:-8}"
fi
exec python3 "$TASK_ROOT/script/run_optimize.py" "$@"
