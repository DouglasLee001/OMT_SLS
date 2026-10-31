#!/usr/bin/env python3
"""Task26 single-TU compile; zero-check policy/parity/resources; <=6s generated group."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[3]
HEADER=ROOT/'z3-nia/sls/src/nia_omt_guarded_product.h'
CPP=Path(__file__).with_suffix('.cpp')
LIBSHA='2641bd9dd1fb41c1051e5893fa070913d2e215161756a65a56206fbc8b372bd5'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def check_parity(folder):
 reference=ROOT/'z3-nia/logs/research-round2/native-indexed-port/producer-eb628.py'
 assert sha(reference)=='eb628f6b9b71c37ef23b32824974dec8d7741c5ac40653d2136ca119a1375ad1'
 spec=importlib.util.spec_from_file_location('_frozen_eb_native_parity',reference);producer=importlib.util.module_from_spec(spec);spec.loader.exec_module(producer)
 rows=[]
 for path in sorted(folder.glob('*.json')):
  native=json.loads(path.read_text());poly=lambda items:{tuple(m):int(c)for m,c in items}
  atoms=[dict(atom=a['atom'],relation=a['relation'],poly=poly(a['poly']))for a in native['atoms']]
  inventory,cuts,stats=producer._indexed_sparse_closure(atoms,native['variables'],poly(native['bound']),{tuple(m)for m in native['base_inventory']},lambda:None,lambda:True)
  assert inventory=={tuple(m)for m in native['inventory']},str(path)+': inventory mismatch'
  assert cuts==native['cuts'],str(path)+': exact cut order/guards mismatch'
  assert {k:stats[k]for k in native['stats']}==native['stats'],str(path)+': term-inclusive resource count mismatch'
  assert stats['total_scans']==stats['join_scans']+stats['index_scans']<=producer.MAX_PAIR_SCANS
  rows.append(dict(fixture=path.name,cuts=len(cuts),**native['stats']))
 assert len(rows)==7,'missing parity fixture'
 (folder/'parity-receipt.json').write_text(json.dumps(dict(passed=True,reference_sha256=sha(reference),fixtures=rows,actual_checks=0),indent=2)+'\n')
 return dict(fixtures=len(rows),reference_sha256=sha(reference),actual_checks=0)
def main(args):
 output=Path(args.output).absolute();output.mkdir(parents=True,exist_ok=False)
 assert HEADER.is_file(),'guarded-product header missing: expected RED, zero checks'
 library=Path(args.library).resolve();assert sha(library)==LIBSHA
 loader=output/'sdk-loader';loader.mkdir()
 for name in('libz3.so','libz3.so.5.1'):(loader/name).symlink_to(library)
 executable=output/'test-guarded-product'
 command=[os.environ.get('CXX','g++'),'-std=gnu++17','-O0','-g','-Wall','-Wextra','-isystem',str(Path(args.include).resolve()),str(CPP),str(library),'-Wl,-rpath,'+str(loader),'-Wl,--wrap=Z3_solver_check','-Wl,--wrap=Z3_optimize_check','-Wl,--wrap=Z3_solver_get_model','-lgmpxx','-lgmp','-ldl','-pthread','-o',str(executable)]
 os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(10)
 compiled=subprocess.run(command,text=True,capture_output=True,timeout=120)
 (output/'compile.stdout').write_text(compiled.stdout);(output/'compile.stderr').write_text(compiled.stderr)
 receipt=dict(compile_command=command,compile_returncode=compiled.returncode,header_sha256=sha(HEADER),cpp_test_sha256=sha(CPP),python_test_sha256=sha(__file__),library_sha256=sha(library),no_product_build=True,no_benchmark=True,runs=[])
 if compiled.returncode:
  (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print(compiled.stderr);return 2
 env=dict(os.environ);env.pop('LD_PRELOAD',None);env['LD_LIBRARY_PATH']=str(loader)
 for mode in(args.mode or(['pure','indexed','resources','parity','generated']if args.real else['pure','indexed','resources','parity'])):
  began=time.monotonic();timed_out=False
  try:
   result=subprocess.run([str(executable),mode,str(output/mode),str(library)],text=True,capture_output=True,timeout=6,env=env)
   stdout,stderr,returncode=result.stdout,result.stderr,result.returncode
  except subprocess.TimeoutExpired as error:
   timed_out=True;returncode=None
   decode=lambda value:value.decode(errors='replace')if isinstance(value,bytes)else(value or'')
   stdout,stderr=decode(error.stdout),decode(error.stderr)
  (output/(mode+'.stdout')).write_text(stdout);(output/(mode+'.stderr')).write_text(stderr)
  row=dict(mode=mode,returncode=returncode,seconds=time.monotonic()-began,actual_check_entries=stderr.count('gp actual_check_enter'),timed_out=timed_out)
  if mode=='parity'and returncode==0:
   try:row['eb_parity']=check_parity(output/mode)
   except Exception as error:row.update(returncode=1,parity_error=repr(error))
  receipt['runs'].append(row)
  if row['returncode']!=0:break
 receipt.update(executable_sha256=sha(executable),passed=all(r['returncode']==0 for r in receipt['runs']))
 (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt['runs'],indent=2));return 0 if receipt['passed']else 1
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--real',action='store_true');p.add_argument('--mode',action='append',choices=['pure','indexed','resources','parity','generated']);p.add_argument('--include',default=str(ROOT/'competitor/z3-5.1.0/install/include'));p.add_argument('--library',default=str(ROOT/'z3-nia/logs/research-round2/affine-ray-component/sdk-loader/libz3.so.5.1.0.0'));raise SystemExit(main(p.parse_args()))
