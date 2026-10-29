#!/usr/bin/env bash
set -euo pipefail
task_root=/home/lbh/OMT/z3-nia
export CPATH="$task_root/toolchain/gmp/include${CPATH:+:$CPATH}"
make -C "$task_root/reference/NIA_LS/src" -j64 \
    Z3_ROOT=/home/lbh/OMT/competitor/z3-5.1.0 \
    LDLIBS="-l:libz3.so.5.1.0.0 $task_root/toolchain/gmp/lib/libgmpxx.a $task_root/toolchain/gmp/lib/libgmp.a"
python3 - <<'PY'
from pathlib import Path
import hashlib,json,subprocess
p=Path('/home/lbh/OMT/z3-nia')
exe=p/'reference/NIA_LS/src/nia_ls'
record={'git_commit':(p/'BASELINE_COMMIT').read_text().strip(),
        'executable':str(exe),'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
        'version':subprocess.check_output([str(exe),'--z3-version'],text=True).strip(),
        'command':['make','-j64','Z3_ROOT=/home/lbh/OMT/competitor/z3-5.1.0'],
        'slurm_job_id':__import__('os').environ.get('SLURM_JOB_ID')}
(p/'reference/baseline-build.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
PY
