#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]] || [[ "$1" != "z3" && "$1" != "optimathsat" ]]; then
  echo "Usage: ./run.sh {z3|optimathsat}" >&2
  exit 2
fi
task_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$task_script_dir/run.py" "$1"
