#!/usr/bin/env bash
set -euo pipefail
task_root=/home/lbh/OMT/z3-nia
bash "$task_root/build.sh"
python3 - <<'PY'
from pathlib import Path
import hashlib,json,subprocess,os
p=Path('/home/lbh/OMT/z3-nia');exe=p/'build/z3'
ldd=subprocess.run(['ldd',str(exe)],text=True,capture_output=True,check=True).stdout
if 'libz3' in ldd:raise RuntimeError('embedded shell unexpectedly loads an external libz3')
record={'baseline_commit':(p/'BASELINE_COMMIT').read_text().strip(),
        'executable':str(exe),'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
        'version':subprocess.check_output([str(exe),'-version'],text=True).strip(),
        'embedded_version':subprocess.check_output([str(exe),'--nia-ls','--z3-version'],text=True).strip(),
        'command':['bash',str(p/'build.sh')],'ldd':ldd,
        'slurm_job_id':os.environ.get('SLURM_JOB_ID')}
record['source_sha256']={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest()
                        for d in ('sls/src','sls/frontend') for f in sorted((p/d).glob('*'))
                        if f.suffix in ('.cpp','.h')}
(p/'embedding-build.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:v for k,v in record.items() if k!='source_sha256'},indent=2))
PY
