#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INITIAL_BUILD=1
for INITIAL_ARG in "$@"; do
    case "$INITIAL_ARG" in
        --dry-run|--help|-h) INITIAL_BUILD=0 ;;
    esac
done
if [[ "$INITIAL_BUILD" == 1 ]]; then
    make -C "$TASK_ROOT/NIA_LS/src" -j"${BUILD_JOBS:-8}"
fi
exec python3 "$TASK_ROOT/script/run_initial.py" "$@"
