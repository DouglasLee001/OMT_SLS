#!/usr/bin/env bash
# Leave one SLS component out; keep the full proof backend unchanged.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$TASK_ROOT/script/run_sls_ablation.sh"

usage() {
  cat <<'EOF'
Usage: bash script/run_sls_ablation.sh VARIANT [launcher options]

Variants:
  all                         Run both groups sequentially in the background
  full                        Full solver baseline
  no-objective-neighborhoods   Disable definition-projected objective neighborhoods

Defaults: node3,node4,node5; 180 parallel cases/node; 1200 seconds/case.
Runs use the complete benchmark set and the normal NIA_<timestamp> result folder.
Additional options such as --nodes, --jobs, --output and --dry-run are forwarded.
all rejects --output and --prepare-only; all --dry-run stays in the foreground.
EOF
}

solver_hash() {
  local solver="$TASK_ROOT/z3-nia/build/z3" digest
  while (($#)); do
    case "$1" in
      --solver)
        if (($# < 2)); then echo '--solver requires a path' >&2; return 2; fi
        solver=$2; shift 2 ;;
      --solver=*) solver=${1#--solver=}; shift ;;
      *) shift ;;
    esac
  done
  digest=$(sha256sum < "$solver") || return 2
  printf '%s\n' "${digest%% *}"
}

run_suite() {
  local expected_hash=$1 variant current_hash code
  shift
  for variant in full no-objective-neighborhoods; do
    if [[ "$expected_hash" != dry-run ]]; then
      current_hash=$(solver_hash "$@") || return 2
      if [[ "$current_hash" != "$expected_hash" ]]; then
        echo "SLS ablation stopped: solver changed before $variant" >&2
        return 2
      fi
    fi
    printf 'Starting SLS ablation: %s\n' "$variant"
    if bash "$SCRIPT_PATH" "$variant" --wait "$@"; then
      printf 'Finished SLS ablation: %s\n' "$variant"
    else
      code=$?
      printf 'SLS ablation failed: %s (exit %s); remaining groups skipped.\n' "$variant" "$code" >&2
      return "$code"
    fi
  done
  echo 'All SLS ablations completed.'
}

if (($# == 0)); then
  usage >&2
  exit 2
fi

if [[ "$1" == all ]]; then
  shift
  SUITE_DRY_RUN=false
  for argument in "$@"; do
    case "$argument" in
      --output|--output=*|--prepare-only|--prepare-only=*)
        echo 'all does not accept --output or --prepare-only; each group needs its own completed run.' >&2
        exit 2 ;;
      --dry-run) SUITE_DRY_RUN=true ;;
    esac
  done
  if "$SUITE_DRY_RUN"; then
    run_suite dry-run "$@"
    exit
  fi
  SUITE_HASH=$(solver_hash "$@")
  SUITE_LOG_DIR=${SLS_ABLATION_LOG_DIR:-/data/shared/lbh/OMT/result}
  mkdir -p -- "$SUITE_LOG_DIR"
  SUITE_STAMP=$(TZ=Asia/Shanghai date +%Y%m%d_%H%M%S)
  SUITE_LOG=$(mktemp "$SUITE_LOG_DIR/sls_ablation_${SUITE_STAMP}_XXXXXX.log")
  nohup bash "$SCRIPT_PATH" _all_worker "$SUITE_HASH" "$@" >"$SUITE_LOG" 2>&1 </dev/null &
  printf 'Suite PID: %s\nSuite log: %s\n' "$!" "$SUITE_LOG"
  exit
fi

if [[ "$1" == _all_worker ]]; then
  shift
  run_suite "$@"
  exit
fi

ABLATION_OPTIONS=()
case "$1" in
  full) JOB_NAME=sls_full ;;
  no-objective-neighborhoods)
    JOB_NAME=sls_no_obj
    ABLATION_OPTIONS+=(--solver-option=--no-sls-objective-neighborhoods)
    ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
shift

exec bash "$TASK_ROOT/script/run_tactic_chain.sh" \
  --job-name "$JOB_NAME" \
  "${ABLATION_OPTIONS[@]}" \
  "$@"
