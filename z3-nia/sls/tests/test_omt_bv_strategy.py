#!/usr/bin/env python3
"""Task19 focused component compile and bounded generated public worker tests."""
from pathlib import Path
import argparse, hashlib, json, os, subprocess, tempfile, time

ROOT=Path(__file__).resolve().parents[3]
SLS=ROOT/'z3-nia/sls'
NAMES=['nia_omt_optimality.cpp','nia_omt_model_bridge.cpp','nia_omt_convex_exchange.cpp',
       'nia_omt_objective_slice.cpp','nia_omt_cutset.cpp','nia_initialize.cpp','nia_build_instance.cpp',
       'nia_omt_input.cpp','nia_ls.cpp']
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main(args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=False)
    library=Path(args.library).resolve()
    assert sha(library)=='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5','build010 library required'
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(10)
    executable=output/'test-bv-strategy'
    command=[os.environ.get('CXX','g++'),'-std=gnu++17','-O1','-g','-w','-ffunction-sections','-fdata-sections',
             '-I'+str(Path(args.include).resolve())]
    if args.legacy:command+=['-DNIA_BV_STRATEGY_LEGACY']
    command += [str(SLS/'tests/test_omt_bv_strategy.cpp'),*[str(SLS/'src'/n) for n in NAMES],
                '-Wl,--gc-sections','-Wl,--wrap=Z3_mk_tactic','-Wl,--wrap=Z3_solver_check',
                str(library),'-Wl,-rpath,'+str(library.parent),'-lgmpxx','-lgmp','-ldl','-pthread','-o',str(executable)]
    compiled=subprocess.run(command,text=True,capture_output=True)
    (output/'compile.stdout').write_text(compiled.stdout);(output/'compile.stderr').write_text(compiled.stderr)
    report=dict(legacy=args.legacy,compile_command=command,compile_returncode=compiled.returncode,
                library_sha256=sha(library),source_sha256={n:sha(SLS/'src'/n) for n in NAMES},
                tests_sha256={n:sha(SLS/'tests'/n) for n in ('test_omt_bv_strategy.cpp','test_omt_bv_strategy.py')},runs=[])
    if compiled.returncode:
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(compiled.stderr);return 2
    modes=args.modes or (['red'] if args.legacy else ['models','guards','off-aux','timer-pause','width-lia'])
    failures=[]
    for mode in modes:
        began=time.monotonic()
        directory=Path(args.query_identity_directory) if mode=='query-identity' else output/mode
        result=subprocess.run([str(executable),mode,str(directory),str(library)],text=True,capture_output=True,timeout=9)
        elapsed=time.monotonic()-began
        (output/(mode+'.stdout')).write_text(result.stdout);(output/(mode+'.stderr')).write_text(result.stderr)
        entry=dict(mode=mode,returncode=result.returncode,elapsed_seconds=elapsed,
                   actual_check_entries=result.stderr.count('omt_optimality_bv phase=check'),
                   strategy_constructions=result.stderr.count('bv-test native_tactic name=qfnia-omt-bv'),
                   sdk_check_entries=result.stderr.count('bv-test sdk_check_enter'))
        report['runs'].append(entry);print(json.dumps(entry),flush=True)
        if result.returncode:failures.append(mode);break
        if mode=='red':
            if not entry['strategy_constructions'] or not entry['actual_check_entries']:
                failures.append('legacy requested native strategy missing (semantic RED)')
        elif mode=='models':
            assert entry['actual_check_entries']==8 and entry['strategy_constructions']==8
        elif mode=='guards':
            assert entry['actual_check_entries']==0 and entry['sdk_check_entries']==0
        elif mode=='off-aux':
            assert entry['actual_check_entries']==0 and entry['strategy_constructions']==1
        elif mode=='timer-pause':
            assert entry['actual_check_entries']==1,'same-bound resume must not restart the main check'
            assert 'raw_timer_exception=canceled' in result.stderr
        elif mode=='width-lia':assert entry['actual_check_entries']==2
        elif mode=='query-identity':assert entry['sdk_check_entries']==0
    windows={'red':.5,'models':4.,'guards':.5,'off-aux':1.5,'timer-pause':1.532,'width-lia':1.25,'query-identity':0}
    report.update(failures=failures,executable_sha256=sha(executable),nominal_parent_window_seconds=sum(windows[r['mode']] for r in report['runs']),
                  extra_real_tactic_applications=int('timer-pause' in [r['mode'] for r in report['runs']]),
                  no_product_build=True,no_benchmark=True)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 1 if failures else 0
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--legacy',action='store_true')
    parser.add_argument('--include',default=str(ROOT/'competitor/z3-5.1.0/install/include'))
    parser.add_argument('--library',default=str(ROOT/'z3-nia/build/libz3.so'))
    parser.add_argument('--modes',nargs='+',choices=['red','models','guards','off-aux','query-identity','timer-pause','width-lia'])
    parser.add_argument('--query-identity-directory')
    parser.add_argument('--output',required=True);raise SystemExit(main(parser.parse_args()))
