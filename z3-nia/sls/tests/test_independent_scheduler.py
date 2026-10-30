"""Dynamic Slurm scheduling checks using only local fixtures and mocks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[2] / 'experiment/independent_scheduler.py'


def load():
    assert MODULE.is_file(), 'missing independent scheduler'
    spec = importlib.util.spec_from_file_location('independent_scheduler_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IndependentSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        temporary = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.state = dict(nodes=['server', 'node1', 'node2', 'node3', 'node4', 'node5'],
                          shards=[[i] for i in range(6)], jobs_per_node=80, memory_mb=53493,
                          snapshot={})
        (self.output / 'shards').mkdir()
        (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
        self.finalizations = []
        def finalize(output, step_code=0):
            self.finalizations.append((output, step_code))
            return step_code
        self.reference = SimpleNamespace(load_state=lambda output: json.loads((output / 'slurm_state.json').read_text()),
                                         finalize_run=finalize)
        self.snapshot = SimpleNamespace(check_snapshot=lambda *args: None)
        def command(output, state, rank, node):
            return ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', '--cpus-per-task=80',
                    '--nodelist=' + node, '--mem=53493M', str(output / 'shards' / f'{rank:02d}' / 'submit.sbatch')]
        self.launcher = SimpleNamespace(reference_modules=lambda: (None, self.reference, self.snapshot),
                                        make_worker_sbatch_command=command)

    def available(self, node, state):
        return dict(node=node, free_cpus=192, free_memory_mb=128000) if node == 'node3' else None

    def done(self, rank, code=0):
        where = self.output / 'shards' / f'{rank:02d}'
        (where / 'worker_done.json').write_text(json.dumps(dict(rank=rank, node='node3', returncode=code)))

    def test_only_available_node_submits_one_worker_with_durable_assignment(self):
        def submit(command, **kwargs):
            where = self.output / 'shards/00'
            self.assertTrue((where / 'intent.json').is_file())
            self.assertEqual(json.loads((where / 'assignment.json').read_text())['node'], 'node3')
            self.assertIn('--nodelist=node3', command)
            return subprocess.CompletedProcess(command, 0, '100;cluster\n', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=submit) as submit_call:
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertEqual(submit_call.call_count, 1)
        self.assertEqual(json.loads((self.output / 'shards/00/receipt.json').read_text())['job_id'], '100')

    def test_own_active_worker_blocks_second_even_when_112_cpus_still_free(self):
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        self.m.durable_json(self.output / 'shards/00/receipt.json', dict(job_id='100'))
        queue = [dict(job_id='100', comment='own-job', state='RUNNING', nodes='node3')]
        available = lambda node, state: dict(node=node, free_cpus=112, free_memory_mb=128000) if node == 'node3' else None
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m, 'query_node', side_effect=available), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no second worker')):
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertFalse((self.output / 'shards/01/intent.json').exists())

    def test_same_node_accepts_next_shard_after_completion(self):
        answer = subprocess.CompletedProcess([], 0, '100\n', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', return_value=answer) as submit:
            self.m.schedule(self.output, self.launcher, once=True)
            self.done(0)
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(json.loads((self.output / 'shards/01/assignment.json').read_text())['node'], 'node3')

    def test_sbatch_timeout_reserves_node_and_never_retries_intent(self):
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sbatch', 30)) as submit:
            self.m.schedule(self.output, self.launcher, once=True)
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(submit.call_count, 1)
        self.assertTrue((self.output / 'shards/00/intent.json').exists())
        self.assertFalse((self.output / 'shards/01/intent.json').exists())

    def test_explicit_sbatch_rejection_fails_without_retrying_or_reserving_forever(self):
        rejection = subprocess.CalledProcessError(1, ['sbatch'], output='', stderr='invalid request')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=rejection) as submit:
            self.m.schedule(self.output, self.launcher, once=True)
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 2)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.finalizations, [(self.output, 2)])
        done = json.loads((self.output / 'shards/00/worker_done.json').read_text())
        self.assertTrue(done['submission_rejected'])

    def test_unexpected_error_marks_current_controller_failed(self):
        self.m.durable_json(self.output / 'controller.json', dict(pid=os.getpid(), state='watching'))
        with patch.object(self.snapshot, 'check_snapshot', side_effect=ValueError('changed runtime')):
            with self.assertRaisesRegex(ValueError, 'changed runtime'):
                self.m.schedule(self.output, self.launcher, once=True)
        controller = json.loads((self.output / 'controller.json').read_text())
        self.assertEqual(controller['state'], 'failed')
        self.assertIn('changed runtime', controller['error'])

    def test_lock_rejection_does_not_overwrite_another_controller(self):
        original = dict(pid=os.getpid() + 100000, state='watching')
        self.m.durable_json(self.output / 'controller.json', original)
        with patch.object(self.m.fcntl, 'flock', side_effect=BlockingIOError('owned elsewhere')):
            with self.assertRaises(BlockingIOError):
                self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(json.loads((self.output / 'controller.json').read_text()), original)

    def test_comment_recovers_receipt_without_resubmitting(self):
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        queue = [dict(job_id='321', comment='own-job', state='RUNNING', nodes='node3')]
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m, 'query_node', return_value=None), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no retry')):
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(json.loads((self.output / 'shards/00/receipt.json').read_text())['job_id'], '321')

    def test_all_shards_cover_cases_once_then_finalize_once(self):
        assigned = []
        finalize = self.reference.finalize_run
        def finalize_with_summary(output, step_code=0):
            self.m.durable_json(output / 'summary.json', dict(parallel=480, node_count=6))
            return finalize(output, step_code)
        self.reference.finalize_run = finalize_with_summary
        def submit(command, **kwargs):
            rank = int(Path(command[-1]).parent.name)
            assigned.extend(self.state['shards'][rank])
            return subprocess.CompletedProcess(command, 0, f'{100 + rank}\n', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=submit):
            for rank in range(6):
                self.m.schedule(self.output, self.launcher, once=True)
                self.m.durable_json(self.output / 'shards' / f'{rank:02d}' / 'worker_started.json',
                                    dict(rank=rank, node='node3', job_id=str(100 + rank)))
                self.done(rank)
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertEqual(assigned, list(range(6)))
        self.assertEqual(self.finalizations, [(self.output, 0)])
        summary = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(summary.get('node_pool'), self.state['nodes'])
        self.assertEqual(summary.get('actual_worker_nodes'), ['node3'])
        self.assertEqual(summary.get('actual_node_count'), 1)
        self.assertTrue(summary.get('parallel_is_pool_capacity'))

    def test_scheduler_observation_failure_does_not_submit(self):
        with patch.object(self.m, 'queue_jobs', side_effect=subprocess.TimeoutExpired('squeue', 15)), \
             patch.object(self.m, 'query_node', side_effect=AssertionError('wait for queue visibility')), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no submission')):
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertFalse((self.output / 'shards/00/intent.json').exists())

    def test_final_resource_recheck_prevents_stale_admission(self):
        calls = 0
        def query(node, state):
            nonlocal calls
            if node != 'node3': return None
            calls += 1
            return dict(node=node, free_cpus=192, free_memory_mb=128000) if calls == 1 else None
        with patch.object(self.m, 'queue_jobs', return_value=[]), patch.object(self.m, 'query_node', side_effect=query), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('node became busy')):
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertFalse((self.output / 'shards/00/intent.json').exists())

    def test_failed_shard_stops_new_work_and_finalizes_incomplete(self):
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        self.m.durable_json(self.output / 'shards/00/receipt.json', dict(job_id='100'))
        self.done(0, 2)
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no new job')):
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 2)
        self.assertEqual(self.finalizations, [(self.output, 2)])

    def test_done_worker_keeps_node_busy_until_job_leaves_active_queue(self):
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        self.m.durable_json(self.output / 'shards/00/receipt.json', dict(job_id='100'))
        self.done(0)
        queue = [dict(job_id='100', comment='own-job', state='COMPLETING', nodes='node3')]
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('allocation still active')):
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertFalse((self.output / 'shards/01/intent.json').exists())
        self.assertEqual(self.finalizations, [])

    def test_terminal_job_without_done_is_failed_without_resubmission(self):
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        self.m.durable_json(self.output / 'shards/00/receipt.json', dict(job_id='100'))
        accounting = subprocess.CompletedProcess([], 0, '100|TIMEOUT|0:9\n', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m.subprocess, 'run', return_value=accounting) as command:
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 2)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][0], 'sacct')
        self.assertEqual(self.finalizations, [(self.output, 2)])

    def test_failed_shard_waits_for_another_active_worker_before_finalizing(self):
        for rank, node in ((0, 'node3'), (1, 'node4')):
            self.m.durable_json(self.output / 'shards' / f'{rank:02d}' / 'intent.json',
                                dict(rank=rank, node=node, comment=f'own-{rank}'))
            self.m.durable_json(self.output / 'shards' / f'{rank:02d}' / 'receipt.json', dict(job_id=str(100 + rank)))
        self.done(0, 2)
        queue = [dict(job_id='101', comment='own-1', state='RUNNING', nodes='node4')]
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no finalize or new submission')):
            self.assertEqual(self.m.schedule(self.output, self.launcher, once=True), 0)
        self.assertEqual(self.finalizations, [])
        self.assertFalse((self.output / 'shards/02/intent.json').exists())

    def test_node_resources_require_health_free_effective_cpus_and_memory(self):
        text = 'NodeName=node3 State=MIXED CPUTot=192 CPUEfctv=180 CPUAlloc=100 RealMemory=128000 AllocMem=70000'
        self.assertIsNotNone(self.m.admit_node(text, 'node3', self.state))
        for old, new in [('CPUAlloc=100', 'CPUAlloc=101'), ('State=MIXED', 'State=MIXED+DRAIN'),
                         ('AllocMem=70000', 'AllocMem=75000'), ('CPUEfctv=180', 'CPUEfctv=N/A')]:
            self.assertIsNone(self.m.admit_node(text.replace(old, new), 'node3', self.state))

    def test_queue_workers_admits_busy_capacity_without_inventing_free_resources(self):
        text = ('NodeName=node3 State=ALLOCATED CPUTot=192 CPUEfctv=180 CPUAlloc=180 '
                'RealMemory=128000 AllocMem=128000')
        self.assertIsNone(self.m.admit_node(text, 'node3', self.state))
        self.state['queue_workers'] = False
        self.assertIsNone(self.m.admit_node(text, 'node3', self.state))
        self.state['queue_workers'] = True
        resource = self.m.admit_node(text, 'node3', self.state)
        self.assertIsNotNone(resource)
        self.assertEqual(resource['mode'], 'queued')
        self.assertEqual(resource['free_cpus'], 0)
        self.assertEqual(resource['free_memory_mb'], 0)
        self.assertEqual(resource['capacity_cpus'], 180)
        self.assertEqual(resource['allocated_cpus'], 180)
        self.assertEqual(resource['memory_mb'], 128000)
        self.assertEqual(resource['allocated_memory_mb'], 128000)
        self.assertEqual(resource['state'], 'ALLOCATED')
        self.assertEqual(resource['raw'], text)

    def test_queue_workers_accepts_only_healthy_valid_total_capacity(self):
        self.state['queue_workers'] = True
        text = ('NodeName=node3 State=MIXED CPUTot=192 CPUEfctv=180 CPUAlloc=180 '
                'RealMemory=128000 AllocMem=128000')
        for healthy in ('IDLE', 'MIXED', 'ALLOCATED'):
            with self.subTest(healthy=healthy):
                self.assertIsNotNone(self.m.admit_node(text.replace('State=MIXED', 'State=' + healthy),
                                                     'node3', self.state))
        for old, new in [('State=MIXED', 'State=MIXED+DRAIN'), ('State=MIXED', 'State=DOWN'),
                         ('State=MIXED', 'State=DRAIN'), ('State=MIXED', 'State=ALLOCATED+DRAIN'),
                         ('State=MIXED', 'State=UNKNOWN'), ('CPUAlloc=180', 'CPUAlloc=-1'),
                         ('CPUAlloc=180', 'CPUAlloc=181'), ('AllocMem=128000', 'AllocMem=128001'),
                         ('CPUEfctv=180', 'CPUEfctv=N/A'), ('NodeName=node3', 'NodeName=node4')]:
            with self.subTest(observation=new):
                self.assertIsNone(self.m.admit_node(text.replace(old, new), 'node3', self.state))
        idle = text.replace('CPUAlloc=180', 'CPUAlloc=0').replace('AllocMem=128000', 'AllocMem=0')
        for old, new in [('CPUEfctv=180', 'CPUEfctv=79'), ('CPUTot=192', 'CPUTot=79'),
                         ('RealMemory=128000', 'RealMemory=50000')]:
            with self.subTest(insufficient_capacity=new):
                self.assertIsNone(self.m.admit_node(idle.replace(old, new), 'node3', self.state))
        exact = ('NodeName=node3 State=ALLOCATED CPUTot=80 CPUEfctv=80 CPUAlloc=80 '
                 'RealMemory=53493 AllocMem=53493')
        self.assertIsNotNone(self.m.admit_node(exact, 'node3', self.state))

    def test_queue_workers_submits_busy_node_only_when_explicitly_enabled(self):
        observation = ('NodeName=node3 State=ALLOCATED CPUTot=192 CPUEfctv=192 CPUAlloc=192 '
                       'RealMemory=128000 AllocMem=128000')
        submitted = []
        def command(args, **kwargs):
            if args[0] == 'scontrol':
                return subprocess.CompletedProcess(args, 0, observation, '')
            self.assertEqual(args[0], 'sbatch')
            self.assertIn('--nodelist=node3', args)
            self.assertFalse(any(arg in ('--exclusive', '--overlap') for arg in args))
            submitted.append(args)
            return subprocess.CompletedProcess(args, 0, '100\n', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m.subprocess, 'run', side_effect=command):
            self.m.schedule(self.output, self.launcher, once=True)
            self.assertEqual(submitted, [])
            self.state['queue_workers'] = True
            (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(len(submitted), 1)
        intent = json.loads((self.output / 'shards/00/intent.json').read_text())
        self.assertEqual(intent['resource']['mode'], 'queued')
        self.assertEqual(intent['resource']['free_cpus'], 0)
        self.assertFalse((self.output / 'shards/01/intent.json').exists())
        task = json.loads((self.output / 'controller.json').read_text())['tasks'][0]
        self.assertEqual(task['state'], 'submitted')
        self.assertEqual(task['job_id'], '100')
        self.assertNotIn('slurm_state', task)

    def test_queue_workers_status_and_reservations_follow_pending_and_running_observations(self):
        self.state['queue_workers'] = True
        (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
        for rank, node in ((0, 'node3'), (1, 'node4')):
            self.m.durable_json(self.output / 'shards' / f'{rank:02d}' / 'intent.json',
                                dict(rank=rank, node=node, comment=f'own-{rank}'))
            self.m.durable_json(self.output / 'shards' / f'{rank:02d}' / 'receipt.json', dict(job_id=str(100 + rank)))
        queue = [dict(job_id='100', comment='own-0', state='PENDING', nodes='(Resources)'),
                 dict(job_id='101', comment='own-1', state='RUNNING', nodes='node4')]
        def available(node, state):
            return dict(node=node, free_cpus=192, free_memory_mb=128000) if node in ('node3', 'node4') else None
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m, 'query_node', side_effect=available), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('one unfinished worker per node')):
            self.m.schedule(self.output, self.launcher, once=True)
        controller = json.loads((self.output / 'controller.json').read_text())
        self.assertEqual(controller['busy_nodes'], ['node3', 'node4'])
        self.assertEqual(controller['tasks'][0]['state'], 'queued')
        self.assertEqual(controller['tasks'][0]['slurm_state'], 'PENDING')
        self.assertEqual(controller['tasks'][1]['state'], 'running')
        self.assertEqual(controller['tasks'][1]['slurm_state'], 'RUNNING')
        self.assertFalse((self.output / 'shards/02/intent.json').exists())

    def test_queue_workers_missing_queue_receipt_remains_uncertain_without_resubmission(self):
        self.state['queue_workers'] = True
        (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        self.m.durable_json(self.output / 'shards/00/receipt.json', dict(job_id='100'))
        def accounting(args, **kwargs):
            self.assertEqual(args[0], 'sacct')
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=accounting) as command:
            self.m.schedule(self.output, self.launcher, once=True)
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(command.call_count, 2)
        controller = json.loads((self.output / 'controller.json').read_text())
        self.assertEqual(controller['busy_nodes'], ['node3'])
        self.assertEqual(controller['tasks'][0]['state'], 'submission_uncertain')
        self.assertFalse((self.output / 'shards/01/intent.json').exists())

    def test_queue_workers_uncertain_intent_never_retries_or_releases_node(self):
        self.state['queue_workers'] = True
        (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
        with patch.object(self.m, 'queue_jobs', return_value=[]), \
             patch.object(self.m, 'query_node', side_effect=self.available), \
             patch.object(self.m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sbatch', 30)) as submit:
            self.m.schedule(self.output, self.launcher, once=True)
            self.m.schedule(self.output, self.launcher, once=True)
        self.assertEqual(submit.call_count, 1)
        self.assertFalse((self.output / 'shards/01/intent.json').exists())

    def test_queue_workers_requires_exact_boolean_before_scheduler_observation(self):
        for invalid in (None, 0, 1, 'false', 'true', [], {}):
            with self.subTest(invalid=invalid):
                self.state['queue_workers'] = invalid
                (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
                with patch.object(self.m, 'queue_jobs', side_effect=AssertionError('invalid frozen flag')):
                    with self.assertRaisesRegex(ValueError, 'queue_workers.*bool'):
                        self.m.schedule(self.output, self.launcher, once=True)

    def test_queue_workers_duplicate_recovery_matches_never_submit(self):
        self.state['queue_workers'] = True
        (self.output / 'slurm_state.json').write_text(json.dumps(self.state))
        self.m.durable_json(self.output / 'shards/00/intent.json', dict(rank=0, node='node3', comment='own-job'))
        queue = [dict(job_id='100', comment='own-job', state='PENDING', nodes='(Resources)'),
                 dict(job_id='101', comment='own-job', state='RUNNING', nodes='node3')]
        with patch.object(self.m, 'queue_jobs', return_value=queue), \
             patch.object(self.m, 'query_node', side_effect=AssertionError('duplicate recovery must stop')), \
             patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no duplicate submission')):
            with self.assertRaisesRegex(ValueError, 'multiple scheduler jobs'):
                self.m.schedule(self.output, self.launcher, once=True)
        self.assertFalse((self.output / 'shards/00/receipt.json').exists())
        self.assertFalse((self.output / 'shards/01/intent.json').exists())

    def test_scheduler_rechecks_frozen_queue_policy_on_its_loaded_state(self):
        def check(output, state):
            self.assertEqual(output, self.output)
            self.assertEqual(state, self.state)
            raise ValueError('queue_workers frozen policy changed')
        self.launcher.check_queue_workers = check
        with patch.object(self.m, 'queue_jobs', side_effect=AssertionError('check frozen policy first')):
            with self.assertRaisesRegex(ValueError, 'queue_workers frozen policy changed'):
                self.m.schedule(self.output, self.launcher, once=True)


if __name__ == '__main__':
    unittest.main()
