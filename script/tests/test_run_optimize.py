"""Contract tests for the native optimization benchmark runner."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    path = ROOT / 'script/run_optimize.py'
    assert path.is_file(), 'optimization runner has not been implemented'
    spec = importlib.util.spec_from_file_location('run_optimize', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OptimizationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def executable(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o755)
        return path

    def setup_case(self, native_body=None, converter_body=None, timeout=2, direction='min', z3=10, oms=12):
        source = self.root / 'input.smt2'
        source.write_text('(minimize x)\n(check-sat)\n')
        converter = self.executable('converter.py', converter_body or
            "import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n")
        native = self.executable('nia_ls', native_body or
            "import json,sys\nassert '--optimize' in sys.argv\nassert '--target=10' in sys.argv\n"
            "print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.002,'model_validated':True,'is_optimal':False,'termination':'target_beaten'}))\n")
        config = self.runner.Config(jobs=180, timeout=timeout, solver=native, converter=converter)
        case = self.runner.InputCase(source, 'input.smt2', 'custom', 'custom', direction, z3, oms)
        return config, case

    def input_tables(self):
        originals = self.root / 'benchmarks'
        source = originals / 'QF_NIA/group/a.smt2'
        source.parent.mkdir(parents=True)
        source.write_text('; (maximize ignored)\n(minimize x)\n(check-sat)\n')
        listing = self.root / 'result/original/original_unsolved_bounded_sol.csv'
        listing.parent.mkdir(parents=True)
        listing.write_text('filename,z3_best_value,optimathsat_best_value\nQF_NIA/group/a.smt2,(- 34),12\n')
        generated = self.root / 'new_benchmark/design/instance/b.smt2'
        generated.parent.mkdir(parents=True)
        generated.write_text('(set-info :notes "(minimize ignored)")\n(maximize x)\n(check-sat)\n')
        listing = self.root / 'result/new/new_unsolved_bounded_sol.csv'
        listing.parent.mkdir(parents=True)
        listing.write_text('filename,category,z3_best_value,optimathsat_best_value\nb.smt2,design,,81\n')
        excluded = generated.parent / 'excluded.smt2'
        excluded.write_text('(minimize x)\n(check-sat)\n')
        return originals, source, generated

    def test_defaults_and_budget_cap(self):
        args = self.runner.make_parser({}).parse_args([])
        self.assertEqual((args.suite, args.jobs, args.timeout), ('all', 180, 1200))
        with self.assertRaises(ValueError):
            self.runner.validate_budget(1, 1200.1)
        with self.assertRaises(ValueError):
            self.runner.validate_budget(0, 1)
        with self.assertRaises(ValueError):
            self.runner.validate_budget(181, 1)

    def test_exact_integer_parsing_and_rejection(self):
        for value, expected in [('0',0), ('+12',12), ('-0012',-12), ('(- 34)',-34), ('(- (- 9))',9), ('',None), (None,None), ('999999999999999999999999999999999',999999999999999999999999999999999)]:
            self.assertEqual(self.runner.parse_integer(value), expected)
        for value in ['1.5', 'oo', '(/ 1 2)', 'nan', 'true']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.parse_integer(value)

    def test_comparison_direction_strict_ties_and_missing_reference(self):
        for direction, value, z3, oms, expected in [
            ('min',8,10,12,('better','better','better')),
            ('min',10,10,12,('tie','better','tie')),
            ('min',11,10,12,('worse','better','worse')),
            ('max',13,10,12,('better','better','better')),
            ('max',12,10,12,('better','tie','tie')),
            ('max',11,None,12,('no_reference','worse','worse')),
            ('min',8,None,None,('no_reference','no_reference','no_reference')),
            ('min',None,10,12,('no_incumbent','no_incumbent','no_incumbent')),
        ]:
            with self.subTest(direction=direction,value=value,z3=z3,oms=oms):
                result=self.runner.comparisons(value,z3,oms,direction)
                self.assertEqual((result['vs_z3'],result['vs_optimathsat'],result['vs_best']),expected)
                self.assertEqual(result['strictly_better_than_best'],str(expected[2]=='better').lower())

    def test_selection_uses_only_subset_rows_and_source_direction(self):
        originals, source, generated = self.input_tables()
        cases=self.runner.collect_inputs('all',self.root,originals)
        self.assertEqual([(c.source,c.filename,c.direction,c.z3,c.optimathsat) for c in cases],[(source,'QF_NIA/group/a.smt2','min',-34,12),(generated,'b.smt2','max',None,81)])
        self.assertEqual([c.suite for c in cases],['original','new'])

    def test_generated_category_and_name_cannot_escape(self):
        originals,_,_=self.input_tables()
        listing=self.root/'result/new/new_unsolved_bounded_sol.csv'
        for name,category in [('b.smt2','../design'),('../b.smt2','design'),('b.smt2','/tmp')]:
            listing.write_text('filename,category,z3_best_value,optimathsat_best_value\n'+name+','+category+',0,1\n')
            with self.subTest(name=name,category=category), self.assertRaises(ValueError):
                self.runner.collect_inputs('new',self.root,originals)

    def test_selection_rejects_missing_duplicate_and_escaping_rows(self):
        for row in ['../escape.smt2,0,1\n','QF_NIA/missing.smt2,0,1\n','QF_NIA/group/a.smt2,0,1\nQF_NIA/group/a.smt2,0,1\n']:
            with self.subTest(row=row):
                originals,_,_=self.input_tables()
                (self.root/'result/original/original_unsolved_bounded_sol.csv').write_text('filename,z3_best_value,optimathsat_best_value\n'+row)
                with self.assertRaises(ValueError): self.runner.collect_inputs('original',self.root,originals)
                import shutil
                shutil.rmtree(originals); shutil.rmtree(self.root/'result'); shutil.rmtree(self.root/'new_benchmark')

    def test_result_retains_first_and_best_validated_progress(self):
        config,case=self.setup_case(native_body=
            "import json,sys\nassert '--target=10' in sys.argv\n"
            "for value,t in [('12',0.001),('8',0.002),('9',0.003)]: print(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':value,'elapsed_seconds':t,'model_validated':True}),flush=True)\n"
            "print(json.dumps({'status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.004,'model_validated':True,'termination':'timeout','is_optimal':False}))\n")
        artifacts=self.root/'artifacts'; artifacts.mkdir()
        result=self.runner.run_case(case,config,threading.Event(),artifacts)
        self.assertEqual((result['status'],result['best_value'],result['termination']),('sat','8','timeout'))
        self.assertEqual(result['vs_best'],'better')
        self.assertEqual(result['model_validated'],'true')
        self.assertGreater(float(result['time_to_best_seconds']),float(result['time_to_first_seconds']))
        progress=[json.loads(line) for line in (artifacts/'progress.jsonl').read_text().splitlines()]
        self.assertEqual([x['objective_value'] for x in progress],['12','8'])

    def test_final_record_recovers_actual_incumbent_time(self):
        config,case=self.setup_case(native_body=
            "import json,time\ntime.sleep(.12)\nprint(json.dumps({'event':'final','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':.12,'time_to_best_seconds':.001,'model_validated':True,'termination':'timeout'}))\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['best_value'],'8')
        self.assertGreater(float(result['wall_seconds'])-float(result['time_to_best_seconds']),.09)

    def test_maximize_passes_better_reference_target(self):
        config,case=self.setup_case(direction='max',native_body=
            "import json,sys\nassert '--target=12' in sys.argv\nprint(json.dumps({'status':'unknown','direction':'max','termination':'timeout'}))\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'unknown')

    def test_target_is_optional_and_missing_refs_do_not_supply_one(self):
        body="import json,sys\nassert not any(x.startswith('--target=') for x in sys.argv)\nprint(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n"
        for use_target,z3,oms in [(False,10,12),(True,None,None)]:
            config,case=self.setup_case(native_body=body,z3=z3,oms=oms)
            config=self.runner.replace(config,use_target=use_target)
            self.assertEqual(self.runner.run_case(case,config,threading.Event())['status'],'unknown')

    def test_streamed_validated_incumbent_survives_external_timeout(self):
        config,case=self.setup_case(timeout=0.3,native_body=
            "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
        started=time.monotonic();result=self.runner.run_case(case,config,threading.Event())
        self.assertLess(time.monotonic()-started,1.5)
        self.assertEqual((result['status'],result['termination'],result['best_value']),('sat','wall_timeout','8'))
        self.assertEqual(result['vs_best'],'better')

    def test_invalid_records_never_become_incumbents(self):
        for changed in [{'model_validated':False},{'objective_value':'1.5'},{'objective_value':8},{'direction':'max'},{'elapsed_seconds':-1},{'elapsed_seconds':99999}]:
            record={'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True};record.update(changed)
            config,case=self.setup_case(native_body='import json\nprint(json.dumps('+repr(record)+'))\n')
            result=self.runner.run_case(case,config,threading.Event())
            self.assertEqual(result['status'],'error',changed)
            self.assertEqual(result['best_value'],'',changed)

    def test_native_failure_retains_prior_validated_value_and_honest_error(self):
        config,case=self.setup_case(native_body=
            "import json,sys\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\nprint(json.dumps({'status':'error','error':'native fault'}))\nsys.exit(7)\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual((result['status'],result['best_value']),('error','8'))
        self.assertIn('native fault',result['error'])

    def test_failed_conversion_never_starts_search(self):
        marker=self.root/'started'
        config,case=self.setup_case(converter_body="import sys\nprint('bad conversion',file=sys.stderr)\nsys.exit(2)\n",native_body=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'error');self.assertIn('bad conversion',result['error']);self.assertFalse(marker.exists())

    def test_conversion_and_search_share_total_budget(self):
        config,case=self.setup_case(timeout=0.3,converter_body="import pathlib,sys,time\ntime.sleep(.20)\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n",native_body="import time\ntime.sleep(30)\n")
        started=time.monotonic();result=self.runner.run_case(case,config,threading.Event())
        self.assertEqual(result['status'],'timeout');self.assertLess(time.monotonic()-started,1)

    def test_timeout_cleans_up_descendants_even_after_parent_exit(self):
        pidfile=self.root/'child.pid'
        config,case=self.setup_case(timeout=.3,native_body=
            "import subprocess,sys,pathlib,json\nchild=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
            + f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
            + "print(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n")
        self.runner.run_case(case,config,threading.Event())
        pid=int(pidfile.read_text())
        for _ in range(20):
            state=Path(f'/proc/{pid}/stat')
            if not state.exists() or state.read_text().split()[2]=='Z': break
            time.sleep(.03)
        else: self.fail('solver left a live child process')

    def test_interruption_retains_progress_and_cleans_up(self):
        config,case=self.setup_case(timeout=2,native_body=
            "import json,time\nprint(json.dumps({'event':'incumbent','status':'sat','direction':'min','objective_value':'8','elapsed_seconds':0.001,'model_validated':True}),flush=True)\ntime.sleep(30)\n")
        stop=threading.Event()
        timer=threading.Timer(.2,stop.set);timer.start()
        try:
            result=self.runner.run_case(case,config,stop)
        finally:
            timer.cancel()
        self.assertEqual((result['status'],result['termination'],result['best_value']),('interrupted','interrupted','8'))

    def test_parallel_jobs_bound_live_solver_processes(self):
        lock=self.root/'count.lock';state=self.root/'count';journal=self.root/'active'
        body=("import fcntl,pathlib,time,json\n"
              +f"state=pathlib.Path({str(state)!r})\n"
              +f"with open({str(lock)!r},'a') as lock:\n"
              +" fcntl.flock(lock,fcntl.LOCK_EX)\n count=int(state.read_text()) if state.exists() else 0\n state.write_text(str(count+1))\n"
              +f" with open({str(journal)!r},'a') as journal: journal.write(str(count+1)+'\\n')\n"
              +"time.sleep(.1)\n"
              +f"with open({str(lock)!r},'a') as lock:\n"
              +" fcntl.flock(lock,fcntl.LOCK_EX)\n state.write_text(str(int(state.read_text())-1))\n"
              +"print(json.dumps({'status':'unknown','direction':'min','termination':'timeout'}))\n")
        config,case=self.setup_case(native_body=body)
        config=self.runner.replace(config,jobs=2)
        cases=[self.runner.replace(case,filename=f'{i}.smt2') for i in range(5)]
        self.assertEqual(self.runner.run_batch(cases,config,self.root/'parallel'),0)
        self.assertEqual(max(map(int,journal.read_text().splitlines())),2)
        self.assertEqual(state.read_text(),'0')

    def test_summary_counts_all_selected_including_failures(self):
        config,case=self.setup_case()
        rows=[{'status':'error','vs_z3':'no_incumbent','vs_optimathsat':'no_incumbent','vs_best':'no_incumbent','strictly_better_than_best':'false'},
              {'status':'sat','vs_z3':'better','vs_optimathsat':'better','vs_best':'better','strictly_better_than_best':'true'}]
        summary=self.runner._summary([case]*3,rows,config)
        self.assertEqual(summary['strict_win_rate'],1/3)
        self.assertFalse(summary['over_80_percent'])

    def test_batch_preserves_existing_outputs_and_records_summary(self):
        config,case=self.setup_case();output=self.root/'results'
        self.assertEqual(self.runner.run_batch([case],config,output),0)
        with (output/'results.csv').open(newline='') as handle:
            rows=list(csv.DictReader(handle))
        self.assertEqual(rows[0]['best_value'],'8')
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual((summary['selected'],summary['strictly_better_than_best'],summary['strict_win_rate']), (1,1,1.0))
        self.assertEqual(len(list((output/'cases').iterdir())),1)
        artifact=next((output/'cases').iterdir())
        self.assertEqual((artifact/'input.nia').read_text(),'converted')
        with self.assertRaises(FileExistsError): self.runner.run_batch([case],config,output)

if __name__=='__main__':unittest.main()
