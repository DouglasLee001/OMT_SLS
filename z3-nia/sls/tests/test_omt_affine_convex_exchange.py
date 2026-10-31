#!/usr/bin/env python3
"""Compile the isolated affine component and independently replay its toy proofs."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile


def exercise(sls, prefix, folder):
    folder.mkdir(parents=True, exist_ok=True)
    executable=folder/'test-omt-affine-convex-exchange'
    command=[os.environ.get('CXX','g++'),'-std=gnu++17','-O1','-g','-Wall','-Wextra',
             '-isystem',str(prefix / 'include'),str(sls/'tests/test_omt_affine_convex_exchange.cpp'),
             str(sls/'src/nia_omt_convex_exchange.cpp'),f'-L{prefix / "lib"}',f'-Wl,-rpath,{prefix / "lib"}',
             '-lz3','-lgmpxx','-lgmp','-o',str(executable)]
    red=os.environ.get('NIA_AFFINE_TEST_RED_CONTROL')=='1'
    if red:command.insert(1,'-DOMT_AFFINE_RED_CONTROL')
    build=subprocess.run(command,capture_output=True,text=True)
    (folder/'build.stdout').write_text(build.stdout);(folder/'build.stderr').write_text(build.stderr)
    print(build.stdout,end='');print(build.stderr,end='');build.check_returncode()
    run=subprocess.run([str(executable),str(folder),*(['--core'] if red else [])],capture_output=True,text=True,timeout=35)
    (folder/'native.stdout').write_text(run.stdout);(folder/'native.stderr').write_text(run.stderr)
    print(run.stdout,end='');print(run.stderr,end='')
    report=dict(red_control=red,compile_command=command,native_returncode=run.returncode,
                executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                source_sha256={str(p.relative_to(sls)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [sls/'src/nia_omt_convex_exchange.cpp',sls/'src/nia_omt_convex_exchange.h',
                     sls/'tests/test_omt_affine_convex_exchange.cpp',sls/'tests/test_omt_affine_convex_exchange.py']})
    (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    run.check_returncode()
    certificates=sorted(folder.glob('*.json'))
    certificates=[p for p in certificates if p.name!='report.json']
    assert certificates,'no actual native proof certificates generated'
    for certificate in certificates:
        payload=json.loads(certificate.read_text())
        replay=subprocess.run([str(prefix/'bin/z3'),'-in'],input=payload['linear_query_smt2'],capture_output=True,text=True,timeout=10,check=True)
        assert replay.stdout.strip()=='unsat',(certificate.name,replay.stdout,replay.stderr)
    path=sls.parent/'experiment/verify_convex_certificate.py'
    spec=importlib.util.spec_from_file_location('_native_affine_checker',path)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    z3=checker.converter.load_z3()
    implicit=json.loads((folder/'off-implicit.json').read_text())
    explicit=json.loads((folder/'off-explicit.json').read_text())
    assert implicit.keys()==explicit.keys()
    for key in implicit:
        if key.endswith('_smt2'):
            left=list(z3.parse_smt2_string(implicit[key]));right=list(z3.parse_smt2_string(explicit[key]))
            assert len(left)==len(right) and all(z3.eq(z3.simplify(a),z3.simplify(b)) for a,b in zip(left,right)),key
        else:assert implicit[key]==explicit[key],key
    if os.environ.get('NIA_AFFINE_SKIP_CERTIFICATE_CHECK')!='1':
        for certificate in certificates:
            verified=checker.verify_certificate(Path(str(certificate)+'.source.smt2'),certificate,timeout=10)
            assert verified['no_better_bound_verified'],(certificate.name,verified)
    print(f'PASS independently replayed {len(certificates)} native LIA toy proofs')


def main():
    sls=Path(__file__).resolve().parents[1]
    prefix=Path(os.environ.get('NIA_CONVEX_TEST_Z3_PREFIX',sls.parents[1]/'competitor/z3-5.1.0/install'))
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(10)
    folder=os.environ.get('NIA_AFFINE_TEST_ARTIFACT_DIR')
    if folder:exercise(sls,prefix,Path(folder).resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='omt-affine-component-') as temporary:exercise(sls,prefix,Path(temporary))


if __name__=='__main__':main()
