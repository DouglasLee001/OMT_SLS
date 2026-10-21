"""Contract tests for the NIA_LS first-feasible-model batch runner."""

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
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    path = ROOT / 'script/run_initial.py'
    assert path.is_file(), 'first-solution runner has not been implemented'
    spec = importlib.util.spec_from_file_location('run_initial', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FirstSolutionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def executable(self, name, code):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + code)
        path.chmod(0o755)
        return path

    def inputs(self):
        originals = self.root / 'benchmarks'
        source = originals / 'QF_NIA/group/case@0.smt2'
        source.parent.mkdir(parents=True)
        source.write_text('(check-sat)\n')
        listing = self.root / 'result/original/original.csv'
        listing.parent.mkdir(parents=True)
        listing.write_text('filename,z3_best_value\nQF_NIA/group/case@0.smt2,0\n')
        generated = self.root / 'new_benchmark/packing/instance/new.smt2'
        generated.parent.mkdir(parents=True)
        generated.write_text('(check-sat)\n')
        duplicate = generated.parent.parent / 'bounded/new.smt2'
        duplicate.parent.mkdir()
        duplicate.write_text('(check-sat)\n')
        return originals, source, generated

    def fake_setup(self, native_body=None, converter_body=None, timeout=2):
        source = self.root / 'input.smt2'
        source.write_text('(check-sat)\n')
        converter = self.executable('converter.py', converter_body or
            "import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n")
        native = self.executable('nia_ls', native_body or
            "import json,sys\nassert '--first-solution' in sys.argv\nassert any(x.startswith('--timeout=') for x in sys.argv)\nprint(json.dumps({'status':'sat','objective_value':'-37','search_seconds':0.001,'elapsed_seconds':0.001,'model_validated':True}))\n")
        config = self.runner.Config(jobs=180, timeout=timeout, solver=native,
                                    converter=converter)
        case = self.runner.InputCase(source, 'input.smt2', 'custom')
        return config, case

    def test_defaults_are_180_workers_and_1200_seconds(self):
        args = self.runner.make_parser({}).parse_args([])
        self.assertEqual((args.suite, args.jobs, args.timeout), ('all', 180, 1200))

    def test_selection_uses_original_csv_and_only_new_instance_directories(self):
        originals, source, generated = self.inputs()
        cases = self.runner.collect_inputs('all', self.root, originals)
        self.assertEqual(len(cases), 2)
        self.assertEqual([(c.filename, c.category) for c in cases],
            [('QF_NIA/group/case@0.smt2', 'original'),
             ('new_benchmark/packing/instance/new.smt2', 'packing')])
        self.assertEqual([c.source for c in cases], [source, generated])

    def test_original_path_escape_is_rejected(self):
        originals, _, _ = self.inputs()
        (self.root / 'result/original/original.csv').write_text('filename\n../escape.smt2\n')
        with self.assertRaises(ValueError):
            self.runner.collect_inputs('original', self.root, originals)

    def test_feasible_result_records_value_and_end_to_end_time(self):
        config, case = self.fake_setup()
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'sat')
        self.assertEqual(result['initial_value'], '-37')
        self.assertGreaterEqual(float(result['time_to_first_seconds']), 0.001)
        self.assertEqual(result['error'], '')

    def test_nonzero_exit_cannot_become_success_even_if_sat_was_printed(self):
        config, case = self.fake_setup(native_body=
            "import json,sys\nprint(json.dumps({'status':'sat','objective_value':'4'}))\nsys.exit(7)\n")
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['initial_value'], '')
        self.assertIn('7', result['error'])

    def test_native_elapsed_time_excludes_parent_detection_delay(self):
        config, case = self.fake_setup()
        stage = self.runner._stage

        def delayed_detection(command, *args):
            code = stage(command, *args)
            if '--first-solution' in command:
                time.sleep(0.25)
            return code

        with mock.patch.object(self.runner, '_stage', side_effect=delayed_detection):
            started = time.monotonic()
            result = self.runner.run_case(case, config, threading.Event())
            observed = time.monotonic() - started
        self.assertEqual(result['status'], 'sat')
        self.assertGreater(observed - float(result['time_to_first_seconds']), 0.20)

    def test_invalid_native_elapsed_time_is_not_recorded(self):
        for elapsed in (-1, float('inf'), 10000):
            with self.subTest(elapsed=elapsed):
                config, case = self.fake_setup(native_body=
                    "import json\nprint(json.dumps({'status':'sat','objective_value':'4','model_validated':True,'elapsed_seconds':"
                    + repr(elapsed).replace('inf', "float('inf')") + "}))\n")
                result = self.runner.run_case(case, config, threading.Event())
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['initial_value'], '')

    def test_nonzero_native_exit_prefers_json_error_to_stderr_logs(self):
        config, case = self.fake_setup(native_body=
            "import json,sys\nprint('local search diagnostics',file=sys.stderr)\n"
            "print(json.dumps({'status':'error','error':'unsupported polynomial exponent'}))\nsys.exit(2)\n")
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'error')
        self.assertIn('unsupported polynomial exponent', result['error'])

    def test_unknown_does_not_claim_a_feasible_value(self):
        config, case = self.fake_setup(native_body=
            "import json\nprint(json.dumps({'status':'unknown','objective_value':None,'search_seconds':0.1}))\n")
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['initial_value'], '')
        self.assertEqual(result['time_to_first_seconds'], '')

    def test_sat_without_exact_model_validation_is_an_error(self):
        config, case = self.fake_setup(native_body=
            "import json\nprint(json.dumps({'status':'sat','objective_value':'4','model_validated':False}))\n")
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['initial_value'], '')

    def test_failed_conversion_never_starts_native_search(self):
        marker = self.root / 'native-started'
        config, case = self.fake_setup(converter_body="import sys\nprint('bad input',file=sys.stderr)\nsys.exit(2)\n",
            native_body=f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n")
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'error')
        self.assertIn('bad input', result['error'])
        self.assertFalse(marker.exists())

    def test_conversion_and_search_share_one_wallclock_budget(self):
        config, case = self.fake_setup(timeout=0.35, converter_body=
            "import pathlib,sys,time\ntime.sleep(0.22)\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('converted')\n",
            native_body="import time\ntime.sleep(5)\n")
        start = time.monotonic()
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'timeout')
        self.assertLess(time.monotonic() - start, 1.5)

    def test_timeout_terminates_descendant_processes(self):
        pid_file = self.root / 'child.pid'
        body = ("import subprocess,sys,time,pathlib\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
                "time.sleep(60)\n")
        config, case = self.fake_setup(native_body=body, timeout=0.5)
        result = self.runner.run_case(case, config, threading.Event())
        self.assertEqual(result['status'], 'timeout')
        pid = int(pid_file.read_text())
        for _ in range(20):
            state = Path(f'/proc/{pid}/stat')
            if not state.exists() or state.read_text().split()[2] == 'Z':
                break
            time.sleep(0.05)
        else:
            self.fail('timed-out worker left a live descendant')

    def test_batch_keeps_only_one_csv_and_preserves_existing_results(self):
        config, case = self.fake_setup()
        output = self.root / 'output'
        code = self.runner.run_batch([case], config, output)
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in output.iterdir()], ['results.csv'])
        with (output / 'results.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['status'], 'sat')
        with self.assertRaises(FileExistsError):
            self.runner.run_batch([case], config, output)

    def test_parallel_jobs_bound_live_solver_processes(self):
        journal = self.root / 'active.log'
        lock = self.root / 'active.lock'
        state = self.root / 'active.count'
        body = ("import fcntl,json,pathlib,time\n"
                f"state=pathlib.Path({str(state)!r})\n"
                f"with open({str(lock)!r},'a') as lock:\n"
                " fcntl.flock(lock,fcntl.LOCK_EX)\n"
                " count=int(state.read_text()) if state.exists() else 0\n"
                " state.write_text(str(count+1))\n"
                f" with open({str(journal)!r},'a') as log: log.write(str(count+1)+'\\n')\n"
                "time.sleep(0.15)\n"
                f"with open({str(lock)!r},'a') as lock:\n"
                " fcntl.flock(lock,fcntl.LOCK_EX)\n"
                " state.write_text(str(int(state.read_text())-1))\n"
                "print(json.dumps({'status':'sat','objective_value':'0','elapsed_seconds':0.15,'model_validated':True}))\n")
        config, case = self.fake_setup(native_body=body)
        config = self.runner.replace(config, jobs=2)
        cases = [self.runner.InputCase(case.source, f'case-{i}.smt2', 'custom') for i in range(5)]
        self.assertEqual(self.runner.run_batch(cases, config, self.root / 'parallel'), 0)
        self.assertEqual(max(map(int, journal.read_text().splitlines())), 2)
        self.assertEqual(state.read_text(), '0')


if __name__ == '__main__':
    unittest.main()
