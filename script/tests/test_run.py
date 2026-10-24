import csv
import importlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))

FAKE_WORKER = '''import argparse,json,time,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--solver');p.add_argument('--input');p.add_argument('--case-dir');p.add_argument('--start-ns',type=int)
a=p.parse_args()
value=Path(a.input).read_text().strip()
print(json.dumps({'event':'candidate','value':'2','time_seconds':(time.monotonic_ns()-a.start_ns)/1e9,'source':'test'}),flush=True)
if value=='timeout':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
    Path(a.case_dir,'child.pid').write_text(str(child.pid))
    time.sleep(120)
time.sleep(.06)
print(json.dumps({'event':'final','status':'sat','best_value':'2','time_to_best_seconds':.01,'best_value_source':'test','reported_optimum':'2','model_value':'2','optimality_status':'attained','solver_reported_optimal':True,'native_exit_code':0,'reason':None,'time_to_optimal_seconds':.08,'completed_seconds':(time.monotonic_ns()-a.start_ns)/1e9}),flush=True)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPT/'run.py').is_file(), 'Benchmark scheduler is not implemented')
        self.run = importlib.import_module('run')
        self.tmp = tempfile.TemporaryDirectory(prefix='omt-run-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bench = self.root/'benchmarks'
        self.bench.mkdir()
        self.fake = self.root/'fake_worker.py'
        self.fake.write_text(FAKE_WORKER)

    def config(self, timeout=2, jobs=3):
        return self.run.Config('z3',self.bench,[],self.root/'result',jobs,timeout)

    def test_default_parallelism_and_nia_list(self):
        with patch.dict(os.environ, {'JOBS':'','TIMEOUT':'','BENCHMARK_ROOT':str(self.bench),'RESULT_ROOT':str(self.root/'results'),'BENCHMARK_LIST':''}):
            config = self.run.load_config('z3')
        self.assertEqual(config.jobs, 180)
        self.assertEqual(config.timeout, 1200)
        self.assertEqual(config.lists, [self.bench/'nia_benchmarks_subset.txt'])

    def test_memory_limit_only_applies_to_worker(self):
        (self.bench/'a.smt2').write_text('sat')
        fake=self.root/'memory_worker.py'
        fake.write_text('import resource\n'+FAKE_WORKER.replace(
            "value=Path(a.input).read_text().strip()",
            "value=Path(a.input).read_text().strip()\n"
            "Path(a.case_dir,'memory-limit.json').write_text(json.dumps(resource.getrlimit(resource.RLIMIT_AS)))"))
        config=self.config()
        config.memory_mb=2048
        output=self.root/'limited'
        output.mkdir()
        parent_limit=resource.getrlimit(resource.RLIMIT_AS)
        record=self.run.run_case('a.smt2',config,output,threading.Event(),fake)
        observed=json.loads((Path(record['case_dir'])/'memory-limit.json').read_text())
        self.assertEqual(observed,[2147483648,2147483648])
        self.assertEqual(resource.getrlimit(resource.RLIMIT_AS),parent_limit)
        self.assertEqual(record['status'],'sat')
        self.assertEqual(record['best_value'],'2')

    def test_manifest_rejects_escape_and_removes_duplicates(self):
        source = self.bench/'a.smt2'
        source.write_text('sat')
        manifest = self.bench/'list.txt'
        manifest.write_text('a.smt2\na.smt2\n')
        config=self.config()
        config.lists=[manifest]
        self.assertEqual(self.run.load_benchmarks(config), ['a.smt2'])
        manifest.write_text('../outside.smt2\n')
        with self.assertRaises(ValueError):
            self.run.load_benchmarks(config)

    def test_generated_case_tags_and_maximize_sense_survive_worker_protocol(self):
        (self.bench/'a.smt2').write_text('sat')
        config=self.config()
        config.case_tags={'a.smt2':{'family':'design','scale_tier':'medium',
                                    'objective_sense':'maximize','theory':'NIA'}}
        output=self.root/'tagged'
        output.mkdir()
        fake=self.root/'tagged_worker.py'
        fake.write_text(FAKE_WORKER.replace("'event':'final'", "'event':'final','family':'wrong','scale_tier':'wrong'"))
        result=self.run.run_case('a.smt2',config,output,threading.Event(),fake)
        self.assertEqual(result['family'],'design')
        self.assertEqual(result['scale_tier'],'medium')
        self.assertEqual(result['objective_sense'],'maximize')
        self.assertEqual(result['theory'],'NIA')

    def test_timeout_keeps_preflight_objective_sense(self):
        (self.bench/'a.smt2').write_text('timeout')
        config=self.config(timeout=.2)
        config.case_tags={'a.smt2':{'family':'design','scale_tier':'large',
                                    'objective_sense':'maximize','theory':'NIA'}}
        output=self.root/'tagged_timeout'
        output.mkdir()
        result=self.run.run_case('a.smt2',config,output,threading.Event(),self.fake)
        self.assertEqual(result['status'],'timeout')
        self.assertEqual(result['objective_sense'],'maximize')
        self.assertEqual(result['best_value'],'2')

    def test_timeout_preserves_value_and_kills_child_process(self):
        (self.bench/'a.smt2').write_text('timeout')
        out=self.root/'one-run'
        out.mkdir()
        result=self.run.run_case('a.smt2',self.config(timeout=.25),out,threading.Event(),self.fake)
        self.assertEqual(result['status'],'timeout')
        self.assertEqual(result['best_value'],'2')
        self.assertGreater(result['time_to_best_seconds'],0)
        self.assertFalse(result['solver_reported_optimal'])
        self.assertLess(result['total_seconds'],3)
        pid=int((Path(result['case_dir'])/'child.pid').read_text())
        time.sleep(.05)
        stat=Path('/proc')/str(pid)/'stat'
        self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z', 'Grandchild must be stopped')

    def test_exited_worker_does_not_leave_descendants(self):
        fake=self.root/'orphan.py'
        fake.write_text(FAKE_WORKER.replace("if value=='timeout':", "if value=='orphan':").replace('    time.sleep(120)','    pass').replace("subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])", "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"))
        (self.bench/'a.smt2').write_text('orphan')
        output=self.root/'orphan-result'
        output.mkdir()
        result=self.run.run_case('a.smt2',self.config(),output,threading.Event(),fake)
        pid=int((Path(result['case_dir'])/'child.pid').read_text())
        self.addCleanup(self.clean_child,pid)
        time.sleep(.05)
        stat=Path('/proc')/str(pid)/'stat'
        self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z', 'Exited worker must not leave descendants alive')

    @staticmethod
    def clean_child(pid):
        import signal
        try:
            os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_output_write_error_cancels_other_jobs(self):
        (self.bench/'fast.smt2').write_text('sat')
        (self.bench/'slow.smt2').write_text('timeout')
        output=self.root/'write-error'
        output.mkdir()
        original=csv.DictWriter.writerow
        def write(writer,row):
            if row.get('is_optimal') == 'true':
                raise OSError('Simulated disk write failure')
            return original(writer,row)
        start=time.monotonic()
        with patch.object(csv.DictWriter,'writerow',write):
            with self.assertRaises(OSError):
                self.run.run_batch(self.config(jobs=2,timeout=1),['fast.smt2','slow.smt2'],output,self.fake)
        self.assertLess(time.monotonic()-start,.7,'A write error must interrupt other workers immediately')

    def test_timely_result_survives_delayed_parent_observation(self):
        (self.bench/'a.smt2').write_text('sat')
        output=self.root/'delayed-reader'
        output.mkdir()
        real_selector=self.run.selectors.DefaultSelector
        class DelayedSelector:
            def __init__(self):
                self.inner=real_selector()
                self.delayed=False
            def register(self,*a,**kw):return self.inner.register(*a,**kw)
            def unregister(self,*a,**kw):return self.inner.unregister(*a,**kw)
            def get_map(self):return self.inner.get_map()
            def close(self):return self.inner.close()
            def select(self,timeout=None):
                if not self.delayed:
                    self.delayed=True
                    time.sleep(.25)
                return self.inner.select(timeout)
        with patch.object(self.run.selectors,'DefaultSelector',DelayedSelector):
            result=self.run.run_case('a.smt2',self.config(timeout=.2),output,threading.Event(),self.fake)
        self.assertEqual(result['status'],'sat','Worker completed in budget before parent read the buffered result')
        self.assertLess(result['worker_completed_seconds'],.2)

    def test_timely_final_remains_valid_when_parent_stops_lingering_worker(self):
        (self.bench/'a.smt2').write_text('sat')
        fake=self.root/'lingering.py'
        fake.write_text(FAKE_WORKER+'\ntime.sleep(2)\n')
        output=self.root/'lingering-result'
        output.mkdir()
        result=self.run.run_case('a.smt2',self.config(timeout=.2),output,threading.Event(),fake)
        self.assertEqual(result['status'],'sat','Cleanup of a worker after a timely final must not change the solver result')
        self.assertLess(result['worker_completed_seconds'],.2)

    def test_batch_writes_complete_rows_and_limits_concurrency(self):
        names=[f'{i}.smt2' for i in range(9)]
        for name in names:
            (self.bench/name).write_text('sat')
        directory=self.root/'batch'
        directory.mkdir()
        code=self.run.run_batch(self.config(jobs=3),names,directory,self.fake)
        self.assertEqual(code,0)
        with (directory/'results.csv').open() as f:
            reader=csv.DictReader(f)
            self.assertEqual(reader.fieldnames,
                             ['benchmark','best_value','time_to_best_seconds','is_optimal'])
            rows=list(reader)
        self.assertEqual(len(rows),len(names))
        compact=[json.loads(x) for x in (directory/'results.jsonl').read_text().splitlines()]
        self.assertEqual(len(compact),len(names))
        for row,item in zip(rows,compact):
            self.assertEqual(set(item),set(reader.fieldnames))
            self.assertTrue(Path(item['benchmark']).is_absolute())
            self.assertTrue(Path(item['benchmark']).is_file())
            self.assertEqual(item['best_value'],'2')
            self.assertEqual(item['time_to_best_seconds'],.01)
            self.assertTrue(item['is_optimal'])
            self.assertEqual(row['benchmark'],item['benchmark'])
            self.assertEqual(row['is_optimal'],'true')
        records=[json.loads(p.read_text()) for p in (directory/'cases').glob('*/result.json')]
        boundaries=[]
        for x in records:
            self.assertEqual(x['best_value'],'2')
            boundaries.extend([(x['started_monotonic_ns'],1),(x['finished_monotonic_ns'],-1)])
        active=maximum=0
        for _,delta in sorted(boundaries):
            active+=delta
            maximum=max(maximum,active)
        self.assertGreaterEqual(maximum,2)
        self.assertLessEqual(maximum,3)

    def test_compact_result_does_not_treat_reported_value_as_proven_optimum(self):
        self.assertTrue(hasattr(self.run,'compact_result'), 'Compact result projection is missing')
        normal={'benchmark':'a.smt2','status':'sat','best_value':'(/ 6 2)',
                'time_to_best_seconds':12.5,'reported_optimum':'3','model_value':'3',
                'optimality_status':'attained','solver_reported_optimal':True}
        compact=self.run.compact_result(normal,self.bench)
        self.assertEqual(compact,{'benchmark':str(self.bench/'a.smt2'),
                                 'best_value':'(/ 6 2)','time_to_best_seconds':12.5,
                                 'is_optimal':True})
        for changes in ({'status':'timeout'}, {'optimality_status':'inconsistent'},
                        {'solver_reported_optimal':'False'}, {'model_value':'4'},
                        {'reported_optimum':'(- oo)','optimality_status':'unbounded'},
                        {'reported_optimum':None}):
            with self.subTest(changes=changes):
                result=self.run.compact_result({**normal,**changes},self.bench)
                self.assertFalse(result['is_optimal'])
                self.assertEqual(result['best_value'],'(/ 6 2)')
                self.assertEqual(result['time_to_best_seconds'],12.5)
        empty=self.run.compact_result({**normal,'best_value':None,
                                      'time_to_best_seconds':None},self.bench)
        self.assertIsNone(empty['best_value'])
        self.assertIsNone(empty['time_to_best_seconds'])
        self.assertFalse(empty['is_optimal'])

    def test_comparison_result_preserves_minimum_and_maximum_unbounded_reports(self):
        normal={'benchmark':'a.smt2','status':'sat','objective_sense':'minimize',
                'best_value':'2','time_to_best_seconds':.01,'reported_optimum':'(- oo)',
                'time_to_reported_optimum_seconds':.03,'model_value':'2',
                'optimality_status':'unbounded','solver_reported_optimal':True}
        fields=['benchmark','best_value','time_to_best_seconds','is_optimal',
                'objective_sense','status','reported_optimum','time_to_reported_optimum_seconds',
                'optimality_status','is_unbounded']
        for sense,reported in [('minimize','(- oo)'),('minimize','(* (- 1) oo)'),
                               ('minimize','-oo'),('maximize','oo'),('maximize','+oo')]:
            with self.subTest(sense=sense,reported=reported):
                result=self.run.comparison_result({**normal,'objective_sense':sense,
                                                   'reported_optimum':reported},self.bench)
                self.assertEqual(list(result),fields)
                self.assertIs(result['is_unbounded'],True)
                self.assertIs(result['is_optimal'],False)
                self.assertEqual(result['best_value'],'2')
                self.assertEqual(result['reported_optimum'],reported)
                self.assertEqual(result['time_to_reported_optimum_seconds'],.03)
        attained=self.run.comparison_result({**normal,'reported_optimum':'2',
                                             'optimality_status':'attained'},self.bench)
        self.assertIs(attained['is_optimal'],True)
        self.assertIs(attained['is_unbounded'],False)

    def test_comparison_result_rejects_progress_bounds_and_unconfirmed_infinity(self):
        normal={'benchmark':'a.smt2','status':'sat','objective_sense':'minimize',
                'best_value':'2','reported_optimum':'(- oo)','model_value':'2',
                'optimality_status':'unbounded','solver_reported_optimal':True,
                'lower_bound':'(- oo)','upper_bound':'2'}
        for changed in [{'status':'timeout'},{'status':'unknown'},{'status':'error'},
                        {'optimality_status':'unconfirmed'},{'optimality_status':'attained'},
                        {'solver_reported_optimal':False},{'solver_reported_optimal':'False'},
                        {'solver_reported_optimal':1},{'reported_optimum':None},
                        {'reported_optimum':'2'},{'reported_optimum':'oo'},
                        {'reported_optimum':'bad'},{'objective_sense':None},
                        {'objective_sense':'maximize'}]:
            with self.subTest(changed=changed):
                self.assertIs(self.run.comparison_result({**normal,**changed},self.bench)['is_unbounded'],False)

    def test_comparison_batch_preserves_reports_and_rejects_a_timed_out_final(self):
        records={
            'finite.smt2':dict(objective_sense='minimize',reported_optimum='2',optimality_status='attained'),
            'min.smt2':dict(objective_sense='minimize',reported_optimum='(- oo)',optimality_status='unbounded'),
            'max.smt2':dict(objective_sense='maximize',reported_optimum='oo',optimality_status='unbounded'),
            'timeout.smt2':dict(objective_sense='minimize',reported_optimum='(- oo)',optimality_status='unbounded',completed_seconds=9999),
        }
        for name,record in records.items():
            (self.bench/name).write_text(json.dumps(record))
        worker=self.root/'comparison_worker.py'
        worker.write_text('''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--solver');p.add_argument('--input');p.add_argument('--case-dir');p.add_argument('--start-ns',type=int)
a=p.parse_args()
record=json.loads(Path(a.input).read_text())
print(json.dumps(dict(event='candidate',value='2',time_seconds=.01,objective_sense=record['objective_sense'],source='test')),flush=True)
final=dict(event='final',status='sat',best_value='2',time_to_best_seconds=.01,model_value='2',solver_reported_optimal=True,native_exit_code=0,time_to_reported_optimum_seconds=.03,completed_seconds=.04)
final.update(record)
print(json.dumps(final),flush=True)
if record.get('completed_seconds')==9999:time.sleep(2)
''')
        directory=self.root/'comparison'
        directory.mkdir()
        self.assertEqual(self.run.run_batch(self.config(timeout=.3,jobs=4),list(records),directory,
                                           worker,preserve_optimality=True),0)
        with (directory/'results.csv').open() as handle:
            reader=csv.DictReader(handle)
            self.assertEqual(len(reader.fieldnames),10)
            csvrows={Path(row['benchmark']).name:row for row in reader}
        jsonrows={Path(row['benchmark']).name:row for row in map(json.loads,(directory/'results.jsonl').read_text().splitlines())}
        for name,row in jsonrows.items():
            self.assertIs(type(row['is_unbounded']),bool)
            self.assertIs(type(row['is_optimal']),bool)
            for field in ('is_unbounded','is_optimal'):
                self.assertEqual(csvrows[name][field],str(row[field]).lower())
            self.assertEqual(row['best_value'],'2')
        self.assertIs(jsonrows['finite.smt2']['is_optimal'],True)
        self.assertIs(jsonrows['min.smt2']['is_unbounded'],True)
        self.assertEqual(jsonrows['max.smt2']['reported_optimum'],'oo')
        self.assertIs(jsonrows['max.smt2']['is_unbounded'],True)
        self.assertEqual(jsonrows['timeout.smt2']['status'],'timeout')
        self.assertIs(jsonrows['timeout.smt2']['is_unbounded'],False)
        raw=[json.loads(path.read_text()) for path in (directory/'cases').glob('*/result.json')]
        self.assertEqual(len(raw),4)
        self.assertTrue(all((Path(row['case_dir'])/'events.jsonl').is_file() for row in raw))
        metadata=json.loads((directory/'metadata.json').read_text())
        self.assertIs(metadata['preserve_optimality'],True)
        self.assertEqual(metadata['result_fields'],list(next(iter(jsonrows.values()))))
        summary=json.loads((directory/'summary.json').read_text())
        self.assertEqual((summary['finite_optimal'],summary['unbounded']),(1,2))

    def test_new_runs_preserve_previous_results(self):
        config=self.config()
        first=self.run.create_run_directory(config)
        (first/'marker').write_text('keep')
        second=self.run.create_run_directory(config)
        self.assertNotEqual(first,second)
        self.assertEqual((first/'marker').read_text(),'keep')
        self.assertEqual((config.result_root/'z3/latest').resolve(),second)

    def test_entry_point_rejects_invalid_solver(self):
        self.assertTrue((SCRIPT/'run.sh').is_file(), 'Bash entry point is not implemented')
        for args in [[],['bogus'],['z3','extra']]:
            p=subprocess.run(['bash',str(SCRIPT/'run.sh'),*args],capture_output=True,text=True)
            self.assertEqual(p.returncode,2)
            self.assertIn('run.sh',p.stderr)

    def test_configuration_rejects_invalid_budgets(self):
        for changes in [{'JOBS':'0'}, {'JOBS':'abc'}, {'TIMEOUT':'-1'}, {'TIMEOUT':'nan'}, {'TIMEOUT':'inf'},
                        {'MEMORY_MB':'-1'}, {'MEMORY_MB':'abc'}]:
            with self.subTest(changes=changes), patch.dict(os.environ,{'JOBS':'180','TIMEOUT':'300',**changes}):
                with self.assertRaises(ValueError):
                    self.run.load_config('z3')

    def test_configuration_records_requested_memory_limit(self):
        with patch.dict(os.environ,{'JOBS':'70','TIMEOUT':'1200','MEMORY_MB':'2048'}):
            config=self.run.load_config('z3')
        self.assertEqual(config.jobs,70)
        self.assertEqual(config.timeout,1200)
        self.assertEqual(getattr(config,'memory_mb',None),2048)

    def test_interrupt_stops_running_jobs_and_saves_partial_rows(self):
        names=[f'{i}.smt2' for i in range(9)]
        for name in names:
            (self.bench/name).write_text('timeout')
        output=self.root/'interrupt'
        output.mkdir()
        harness='\n'.join([
            'import sys',
            f'sys.path.insert(0,{str(SCRIPT)!r})',
            'from pathlib import Path',
            'from run import Config,run_batch',
            f'c=Config("z3",Path({str(self.bench)!r}),[],Path({str(self.root)!r}),2,120)',
            f'raise SystemExit(run_batch(c,{names!r},Path({str(output)!r}),Path({str(self.fake)!r})))',
        ])
        proc=subprocess.Popen([sys.executable,'-c',harness],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+3
        try:
            while time.monotonic()<deadline and not any(p.stat().st_size for p in output.glob('cases/*/events.jsonl')):
                time.sleep(.01)
            self.assertTrue(any(p.stat().st_size for p in output.glob('cases/*/events.jsonl')))
            proc.terminate()
            stdout,stderr=proc.communicate(timeout=5)
            self.assertEqual(proc.returncode,130,(stdout,stderr))
            rows=[json.loads(line) for line in (output/'results.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertTrue(all(x['is_optimal'] is False for x in rows))
            details=[json.loads(p.read_text()) for p in (output/'cases').glob('*/result.json')]
            self.assertTrue(all(x['status']=='interrupted' for x in details))
            self.assertTrue(any(x['best_value']=='2' for x in rows))
            summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['not_started'],7)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


if __name__=='__main__':
    unittest.main()
