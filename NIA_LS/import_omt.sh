#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( $# < 1 || $# > 2 )); then
    echo "usage: $0 input.smt2 [output.nia]" >&2
    exit 2
fi
if [[ ! -x "$root/src/nia_ls" ]]; then
    echo "Build first: make -C \"$root/src\" -j8" >&2
    exit 2
fi
if (( $# == 2 )); then
    output=$2
else
    output=$(mktemp "${TMPDIR:-/tmp}/nia-ls-omt.XXXXXXXX.nia")
    trap 'rm -f -- "$output"' EXIT
fi
python3 "$root/converter/omt_to_ls.py" "$1" -o "$output"
"$root/src/nia_ls" "$output" --import-only
