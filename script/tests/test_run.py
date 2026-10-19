import csv
import importlib
import json
import os
from pathlib import Path
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
            if row.get('status')=='sat':
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
            rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),len(names))
        records=[json.loads(x) for x in (directory/'results.jsonl').read_text().splitlines()]
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
        for changes in [{'JOBS':'0'}, {'JOBS':'abc'}, {'TIMEOUT':'-1'}, {'TIMEOUT':'nan'}, {'TIMEOUT':'inf'}]:
            with self.subTest(changes=changes), patch.dict(os.environ,{'JOBS':'180','TIMEOUT':'300',**changes}):
                with self.assertRaises(ValueError):
                    self.run.load_config('z3')

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
            self.assertTrue(all(x['status']=='interrupted' for x in rows))
            self.assertTrue(any(x['best_value']=='2' for x in rows))
            summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['not_started'],7)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


if __name__=='__main__':
    unittest.main()
