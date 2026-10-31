#!/usr/bin/env python3
"""Task18 component recipe; coordinator imports no Z3 and never builds.

Registration-only is the default. --run-generated is an explicit one-shot
11s externally bounded group after root builds the new CLI/library.
"""
from pathlib import Path
import argparse,ctypes,hashlib,importlib.util,json,os,re,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[3]
OLD=Path('/data/shared/lbh/OMT/result/NIA_20261031_005923_310524/diagnostics/NIA_20261031_070532_764366/source')
NAMES=('qfnia-omt-bv','qfnia-omt')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value;spec.loader.exec_module(value);return value
def sdk(args):
    import builtins
    library=Path(args.library).resolve();ctypes.CDLL(str(library));builtins.Z3_LIB_DIRS=[str(library.parent)]
    for key in ('Z3_LIBRARY_PATH','PYTHONPATH','PATH'):os.environ.pop(key,None)
    sys.path.insert(0,str(Path(args.sdk_python).resolve()))
    import z3
    assert Path(z3.__file__).resolve().is_relative_to(Path(args.sdk_python).resolve())
    assert z3.get_version()[:3]==(5,1,0)
    runner=module(ROOT/'z3-nia/experiment/closed_bound_diagnostic.py','_native_bv_library_audit')
    return z3,runner._mapped_libraries(library)
def saved_model(z3,model,constants):
    values={}
    for constant in constants:
        value=model.eval(constant,model_completion=True)
        assert z3.is_int_value(value) or z3.is_true(value) or z3.is_false(value)
        values[str(constant.decl().name())]=value.sexpr()
    return values
def checked(z3,name,forms,constants,expected,description):
    solver=z3.Tactic(name).solver();solver.set(timeout=400);solver.add(*forms)
    answer=solver.check();record=dict(tactic=name,case=description,answer=str(answer),reason=solver.reason_unknown() if answer==z3.unknown else '',query_smt2=solver.to_smt2())
    if expected=='not-unsat':assert answer!=z3.unsat,record
    else:assert str(answer)==expected,record
    if answer==z3.sat:
        model=solver.model();assert all(z3.is_true(model.eval(f,model_completion=True)) for f in forms),record
        record['source_model']=saved_model(z3,model,constants);record['full_original_query_verified']=True
    return record
def child(args):
    z3,libraries=sdk(args);report=dict(mode=args.child,library_sha256=sha(args.library),mapped_libraries=libraries,solver_checks=0)
    output=Path(args.output);output.mkdir(exist_ok=False)
    try:
        if args.child=='registration':
            names=z3.tactics();report['tactics']=[name for name in NAMES if name in names]
            for name in NAMES:assert name in names,'unregistered native tactic: '+name
            for name in NAMES:z3.Tactic(name).solver().to_smt2()
        elif args.child=='models':
            x,g=z3.Ints('x g');b=z3.Bool('b')
            hard=[x>=-2,x<=2,b==(x>=0),g==x*x]
            cases=[('min-strict-sat',hard+[g<1],'sat'),('min-strict-unsat',hard+[g<0],'unsat'),
                   ('max-strict-sat',hard+[g>1],'sat'),('max-strict-unsat',hard+[g>4],'unsat')]
            records=[]
            for name in NAMES:
                for description,forms,expected in cases:
                    report['solver_checks']+=1;records.append(checked(z3,name,forms,[x,g,b],expected,description))
                forms=[x*x==10000,x<100]
                assert all(z3.is_true(z3.simplify(z3.substitute(f,(x,z3.IntVal(-100))))) for f in forms)
                report['solver_checks']+=1;records.append(checked(z3,name,forms,[x],'not-unsat','outside-box-source-witness-minus100'))
            report['solver_checks']+=1;records.append(checked(z3,'qfnia',hard+[g<0],[x,g,b],'unsat','unchanged-default-control'))
            report['records']=records
        elif args.child=='width':
            x,y=z3.Ints('width_x width_y');base=1<<65
            forms=[x>=base,x<=base+1,y>=base,y<=base+1,x*y>=base*base,x+y<2*base+2]
            assert all(z3.is_true(z3.simplify(z3.substitute(f,(x,z3.IntVal(base)),(y,z3.IntVal(base))))) for f in forms)
            goal=z3.Goal();goal.add(*forms)
            failure=''
            try:z3.With(z3.Tactic('nla2bv'),nla2bv_max_bv_size=64)(goal)
            except z3.Z3Exception as error:failure=str(error)
            assert failure,'fixture must actually encounter the64-bit conversion guard'
            large=z3.With(z3.Tactic('nla2bv'),nla2bv_max_bv_size=256)(goal)
            widths=set();seen=set();todo=[f for subgoal in large for f in subgoal]
            while todo:
                node=todo.pop()
                if node.get_id() in seen:continue
                seen.add(node.get_id())
                if z3.is_bv(node):widths.add(node.size())
                todo.extend(node.children())
            assert widths and max(widths)>64,(failure,widths)
            report.update(raw64_failure=failure,raw256_bitwidths=sorted(widths),source_witness=dict(x=str(base),y=str(base)))
            records=[]
            report['solver_checks']+=1;records.append(checked(z3,NAMES[0],forms,[x,y],'unknown','actual-width-dedicated-inconclusive'))
            report['solver_checks']+=1;records.append(checked(z3,NAMES[1],forms,[x,y],'sat','same-query-full-tail-fallback'))
            report['records']=records
        elif args.child=='under':
            # Keep the same public raw primitive/cleanup recipe as Task13.
            # The new factories include a preamble and are NOT used to claim
            # intermediate precision for this separate raw-scope regression.
            bv=module(ROOT/'z3-nia/experiment/bv_budget_diagnostic.py','_native_bv_raw_cleanup')
            x,y=z3.Ints('under_x under_y');forms=[z3.Or(x==10,x==11),y==x*x,x<=10]
            goal=z3.Goal();goal.add(*forms);primitive=z3.With(z3.Tactic('nla2bv'),nla2bv_max_bv_size=64)
            translated=primitive(goal);assert len(translated)==1 and translated[0].prec()==z3.Z3_GOAL_UNDER
            boxed=z3.TryFor(bv._bv_cleanup(z3,'bv_extended'),400)(translated[0])
            assert len(boxed)==1 and boxed[0].prec()==z3.Z3_GOAL_UNDER and boxed[0].inconsistent()
            assert not z3.z3core.Z3_goal_is_decided_unsat(boxed[0].ctx.ref(),boxed[0].goal)
            solver=z3.Then(primitive,bv._bv_cleanup(z3,'bv_extended'),z3.Tactic('fail-if-undecided')).solver()
            solver.set(timeout=400);solver.add(*forms);report['solver_checks']+=1;answer=solver.check();assert answer==z3.unknown
            assert all(z3.is_true(z3.simplify(z3.substitute(f,(x,z3.IntVal(10)),(y,z3.IntVal(100))))) for f in forms)
            positive=[x*x==9,x<=2];goal=z3.Goal();goal.add(*positive)
            translated_positive=primitive(goal);assert len(translated_positive)==1 and translated_positive[0].prec()==z3.Z3_GOAL_UNDER
            solver=z3.Then(primitive,bv._bv_cleanup(z3,'bv_extended'),z3.Tactic('fail-if-undecided')).solver()
            solver.set(timeout=400);solver.add(*positive);report['solver_checks']+=1;assert solver.check()==z3.sat
            model=solver.model();assert model.eval(x).as_long()==-3 and all(z3.is_true(model.eval(f,model_completion=True)) for f in positive)
            report.update(negative_precision='UNDER',negative_bounded_goal_false=True,negative_global_unsat=False,negative_answer=str(answer),
                          positive_precision='UNDER',positive_answer='sat',positive_original_model=saved_model(z3,model,[x]),
                          extra_tactical_sat_applications=1)
        report['passed']=True
    except Exception as error:
        report.update(passed=False,error=type(error).__name__+': '+str(error))
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

def section(text,name):
    start=text.index(name);opening=text.index('{',start);depth=0
    for end in range(opening,len(text)):
        depth+=(text[end]=='{')-(text[end]=='}')
        if depth==0:return text[start:end+1]
    raise AssertionError('unterminated function')
def source_invariants():
    current=ROOT/'z3-nia/source/src/tactic/smtlogics/qfnia_tactic.cpp'
    before=ROOT/'z3-nia/logs/research-round2/bv-native-integration-design/before-source/qfnia_tactic.cpp'
    text=current.read_text();old=before.read_text()
    for name in ('static tactic * mk_qfnia_preamble','static tactic * mk_qfnia_nlsat_solver','static tactic * mk_qfnia_smt_solver','tactic * mk_qfnia_tactic'):
        assert section(text,name)==section(old,name),'default source changed: '+name
    assert 'if (!extended)' in text and 'mem_p.set_uint("max_memory", 100)' in text and 'mem_p.set_uint("max_conflicts", 500)' in text
    return dict(current_cpp_sha256=sha(current),original_cpp_sha256=sha(before),default_functions_byte_identical=True)
def main(args):
    out=Path(args.output);out.mkdir(exist_ok=False)
    records=[];failures=[]
    # This shell lists tactics then still requires an input. An exit-only file
    # validates the CLI path without invoking any satisfiability check.
    empty=out/'registration-only.smt2';empty.write_text('(exit)\n')
    cli=subprocess.run([args.solver,'-tactics',str(empty)],text=True,capture_output=True,timeout=2)
    (out/'cli-tactics.stdout').write_text(cli.stdout);(out/'cli-tactics.stderr').write_text(cli.stderr)
    cli_names=set(re.findall(r'^-\s+(\S+)',cli.stdout,re.M))
    cli_protocol_valid=cli.returncode==0 and 'qfnia' in cli_names
    cli_pass=cli_protocol_valid and all(name in cli_names for name in NAMES)
    if not cli_pass:failures.append('cli-registration' if cli_protocol_valid else 'cli-protocol')
    argv=[sys.executable,str(Path(__file__).resolve()),'--sdk-python',args.sdk_python,'--library',args.library]
    modes=[('registration',2)]
    if args.run_generated and cli_pass:modes += [('models',7),('width',2),('under',2)]
    for mode,limit in modes:
        result=subprocess.run(argv+['--child',mode,'--output',str(out/mode)],text=True,capture_output=True,timeout=limit)
        (out/(mode+'.stdout')).write_text(result.stdout);(out/(mode+'.stderr')).write_text(result.stderr)
        records.append(dict(mode=mode,returncode=result.returncode,external_seconds=limit,report=str(out/mode/'report.json')))
        if result.returncode:
            failures.append(mode)
            if args.run_generated:break
    report=dict(records=records,cli_returncode=cli.returncode,cli_registered=[n for n in NAMES if n in cli_names],
                cli_listing_protocol_verified=cli_protocol_valid,
                failures=failures,library_sha256=sha(args.library),solver_sha256=sha(args.solver),test_sha256=sha(__file__),
                no_build_performed=True,no_benchmarks=True)
    if args.run_generated and not failures:report['source_invariants']=source_invariants()
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 1 if failures else 0
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sdk-python',default=str(OLD/'python'))
    parser.add_argument('--library',default=str(OLD/'python/z3/lib/libz3.so'))
    parser.add_argument('--solver',default=str(OLD/'solver/z3'))
    parser.add_argument('--output',required=True);parser.add_argument('--run-generated',action='store_true')
    parser.add_argument('--child',choices=['registration','models','width','under'])
    args=parser.parse_args();raise SystemExit(child(args) if args.child else main(args))
