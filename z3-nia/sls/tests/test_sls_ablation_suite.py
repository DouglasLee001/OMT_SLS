"""Exercise the serial shell suite at a fake launcher boundary, never Slurm."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
ORDER = ['sls_full', 'sls_no_obj', 'sls_no_plateau']


class SlsAblationSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sls-suite-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        scripts = self.root / 'script'
        scripts.mkdir()
        self.script = scripts / 'run_sls_ablation.sh'
        shutil.copyfile(ROOT / 'script/run_sls_ablation.sh', self.script)
        self.solver = self.root / 'z3-nia/build/z3'
        self.solver.parent.mkdir(parents=True)
        self.solver.write_text('test executable identity\n')
        self.solver.chmod(0o755)
        self.events = self.root / 'events.jsonl'
        self.logs = self.root / 'logs'
        fake = self.root / 'fake_launcher.py'
        fake.write_text('''import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
name = args[args.index('--job-name') + 1]
def record(event, **extra):
    with Path(os.environ['SUITE_TEST_EVENTS']).open('a') as handle:
        handle.write(json.dumps(dict(event=event, name=name, args=args, **extra)) + '\\n')
record('start')
if '--dry-run' not in args:
    time.sleep(float(os.environ.get('SUITE_TEST_DELAY', '0')))
    if os.environ.get('SUITE_TEST_MUTATE') == name:
        Path(os.environ['SUITE_TEST_SOLVER']).write_text('changed solver identity\\n')
code = 7 if os.environ.get('SUITE_TEST_FAIL') == name else 0
record('end', returncode=code)
sys.exit(code)
''')
        (scripts / 'run_tactic_chain.sh').write_text(
            '#!/usr/bin/env bash\nexec python3 ' + shlex.quote(str(fake)) + ' "$@"\n')
        self.environment = dict(os.environ, SUITE_TEST_EVENTS=str(self.events),
                                SUITE_TEST_SOLVER=str(self.solver),
                                SLS_ABLATION_LOG_DIR=str(self.logs))

    def invoke(self, *arguments):
        return subprocess.run(['bash', str(self.script), *arguments],
                              env=self.environment, text=True, capture_output=True, timeout=10)

    def worker(self, *arguments):
        digest = hashlib.sha256(self.solver.read_bytes()).hexdigest()
        return self.invoke('_all_worker', digest, *arguments)

    def records(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_worker_runs_groups_in_order_and_waits_for_each(self):
        result = self.worker('--nodes', 'node1,node2', '--jobs', '12')
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.records()
        self.assertEqual([(r['event'], r['name']) for r in records],
                         [(event, name) for name in ORDER for event in ('start', 'end')])
        for record in records:
            self.assertIn('--wait', record['args'])
            self.assertEqual(record['args'][-4:], ['--nodes', 'node1,node2', '--jobs', '12'])

    def test_failure_stops_before_the_next_group_and_preserves_exit_code(self):
        self.environment['SUITE_TEST_FAIL'] = 'sls_no_obj'
        result = self.worker()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual([r['name'] for r in self.records() if r['event'] == 'start'], ORDER[:2])

    def test_dry_run_stays_foreground_and_creates_no_suite_log(self):
        result = self.invoke('all', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.logs.exists())
        self.assertEqual([r['name'] for r in self.records() if r['event'] == 'start'], ORDER)
        self.assertTrue(all('--dry-run' in r['args'] for r in self.records()))

    def test_background_launcher_returns_before_all_groups_finish(self):
        self.environment['SUITE_TEST_DELAY'] = '.4'
        result = self.invoke('all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Suite PID:', result.stdout)
        self.assertIn('Suite log:', result.stdout)
        logfile = Path(result.stdout.split('Suite log: ', 1)[1].splitlines()[0])
        self.assertTrue(logfile.is_relative_to(self.logs))
        self.assertTrue(logfile.name.startswith('sls_ablation_'))
        self.assertNotIn('All SLS ablations completed.', logfile.read_text())
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if 'All SLS ablations completed.' in logfile.read_text():
                break
            time.sleep(.02)
        self.assertIn('All SLS ablations completed.', logfile.read_text())
        self.assertEqual([r['name'] for r in self.records() if r['event'] == 'start'], ORDER)

    def test_shared_output_and_prepare_only_are_rejected_before_detaching(self):
        for arguments in [('--output', '/tmp/shared'), ('--output=/tmp/shared',), ('--prepare-only',)]:
            with self.subTest(arguments=arguments):
                result = self.invoke('all', *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.logs.exists())
                self.assertFalse(self.events.exists())

    def test_solver_change_stops_before_mixing_ablation_binaries(self):
        self.environment['SUITE_TEST_MUTATE'] = 'sls_full'
        result = self.worker()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('solver changed', result.stderr)
        self.assertEqual([r['name'] for r in self.records() if r['event'] == 'start'], ORDER[:1])


if __name__ == '__main__':
    unittest.main()
