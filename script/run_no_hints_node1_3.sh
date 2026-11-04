#!/usr/bin/env bash
# Compatibility entry: the current full solver no longer supports soft hints.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$TASK_ROOT/script/run_full_node1_3.sh" "$@"
