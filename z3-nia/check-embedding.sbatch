#!/usr/bin/env bash
set -euo pipefail
task_root=/home/lbh/OMT/z3-nia
cd /home/lbh/OMT
export PYTHONDONTWRITEBYTECODE=1
bash "$task_root/build-embedded.sbatch"
python3 -B "$task_root/sls/tests/test_embedded_cli.py" -v
NIA_NATIVE_IMPORT_DATASET_SMOKE=1 python3 -B "$task_root/sls/tests/test_native_import.py"
python3 -B "$task_root/sls/tests/test_embedding_experiment.py"
python3 -B "$task_root/sls/tests/run_committed_regressions.py" --variant baseline --report "$task_root/logs/committed-regressions-baseline.json"
python3 -B "$task_root/sls/tests/run_committed_regressions.py" --variant embedded --report "$task_root/logs/committed-regressions-embedded.json"
cd "$task_root/reference"
export CPATH="$task_root/toolchain/gmp/include"
make -C NIA_LS/src -j64 Z3_ROOT=/home/lbh/OMT/competitor/z3-5.1.0 \
    LDLIBS="-l:libz3.so.5.1.0.0 $task_root/toolchain/gmp/lib/libgmpxx.a $task_root/toolchain/gmp/lib/libgmp.a" \
    test-omt-cutset test-omt-optimality-schedule test-omt-model-bridge test-omt-optimality test-omt-unbounded
