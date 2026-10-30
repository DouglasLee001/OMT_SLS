"""Admit independent frozen shards with optional shared-node Slurm queuing."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY',
            'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text())


def durable_json(path, value, exclusive=False):
    """Persist the submission intent before invoking an external scheduler."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path if exclusive else path.with_name(path.name + f'.tmp-{os.getpid()}-{time.time_ns()}')
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    if not exclusive:
        os.replace(target, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def queue_workers_enabled(state):
    enabled = state.get('queue_workers', False)
    if type(enabled) is not bool:
        raise ValueError('queue_workers must be a boolean')
    return enabled


def admit_node(text, node, state):
    queued = queue_workers_enabled(state)
    values = dict(re.findall(r'(\w+)=([^\s]+)', text))
    try:
        healthy_states = ('IDLE', 'MIXED', 'ALLOCATED') if queued else ('IDLE', 'MIXED')
        if values.get('NodeName') != node or values.get('State') not in healthy_states:
            return None
        total, effective, allocated, memory, allocated_memory = (
            int(values[key]) for key in ('CPUTot', 'CPUEfctv', 'CPUAlloc', 'RealMemory', 'AllocMem'))
        capacity = min(total, effective)
        if (capacity <= 0 or memory <= 0 or not 0 <= allocated <= capacity
                or not 0 <= allocated_memory <= memory):
            return None
        free_cpus, free_memory = capacity - allocated, memory - allocated_memory
        admission_cpus, admission_memory = (capacity, memory) if queued else (free_cpus, free_memory)
        if admission_cpus < state['jobs_per_node'] or admission_memory < state['memory_mb']:
            return None
        resource = dict(node=node, state=values['State'], total_cpus=total, effective_cpus=effective,
                        capacity_cpus=capacity, allocated_cpus=allocated, free_cpus=free_cpus,
                        memory_mb=memory, allocated_memory_mb=allocated_memory,
                        free_memory_mb=free_memory, raw=text, observed_utc=utc())
        if queued:
            resource['mode'] = 'queued'
        return resource
    except (KeyError, ValueError, TypeError):
        return None


def query_node(node, state):
    try:
        answer = subprocess.run(['scontrol', 'show', 'node', node, '-o'], capture_output=True,
                                text=True, check=True, timeout=15)
        return admit_node(answer.stdout, node, state)
    except (OSError, subprocess.SubprocessError):
        return None


def queue_jobs():
    answer = subprocess.run(['squeue', '--noheader', '--format=%i|%k|%T|%N', '--user=' + str(os.getuid())],
                            capture_output=True, text=True, check=True, timeout=15)
    result = []
    for line in answer.stdout.splitlines():
        if not line.strip():
            continue
        values = line.split('|')
        if len(values) != 4 or not re.fullmatch(r'[0-9]+', values[0]):
            raise ValueError('unrecognized squeue response; no new jobs will be submitted')
        result.append(dict(zip(('job_id', 'comment', 'state', 'nodes'), values)))
    return result


def recover_receipt(where, intent, jobs):
    receipt = where / 'receipt.json'
    if receipt.exists():
        return read_json(receipt)['job_id']
    matches = [job for job in jobs if job['comment'] == intent['comment']]
    if len(matches) > 1:
        raise ValueError('multiple scheduler jobs match one durable submission intent')
    job_id = matches[0]['job_id'] if matches else None
    if job_id is None:
        for name in ('worker_started.json', 'worker_done.json', 'assignment.json'):
            path = where / name
            if path.exists():
                record = read_json(path)
                if record.get('job_id'):
                    if record.get('node', record.get('actual_node', intent['node'])) != intent['node']:
                        raise ValueError('worker recovery record has a foreign node')
                    job_id = str(record['job_id'])
                    break
    if job_id is not None:
        if not re.fullmatch(r'[0-9]+', str(job_id)):
            raise ValueError('invalid scheduler job ID in recovery record')
        durable_json(receipt, dict(job_id=str(job_id), recovered_utc=utc()), exclusive=True)
    return job_id


def record_terminal_failure(where, job_id):
    """A terminal allocation lacking worker completion is a failed shard."""
    if (where / 'worker_done.json').exists():
        return
    try:
        answer = subprocess.run(['sacct', '--noheader', '--parsable2', '-j', str(job_id),
                                 '--format=JobIDRaw,State,ExitCode'],
                                capture_output=True, text=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return  # Missing accounting never releases a node or permits a retry.
    for line in answer.stdout.splitlines():
        fields = line.split('|')
        if (len(fields) >= 3 and fields[0] == str(job_id)
                and fields[1].split()[0].rstrip('+') in TERMINAL):
            if not (where / 'worker_done.json').exists():
                durable_json(where / 'worker_done.json',
                             dict(returncode=2, job_id=str(job_id), scheduler_state=fields[1],
                                  exit_code=fields[2], utc=utc(),
                                  reason='allocation ended without worker completion'), exclusive=True)
            return


def attempt_submit(output, state, rank, node, launcher):
    where = output / 'shards' / f'{rank:02d}'
    if (where / 'intent.json').exists():
        return False
    _, _, snapshot = launcher.reference_modules()
    snapshot.check_snapshot(output, state['snapshot'])
    command = launcher.make_worker_sbatch_command(output, state, rank, node)
    comment = f'z3nia:{output.name}:{rank:02d}'
    command = [arg for arg in command if not arg.startswith('--comment=')]
    command.insert(-1, '--comment=' + comment)
    # Last external observation before a durable intent and sbatch. Slurm's
    # actual allocation remains authoritative if another user races this check.
    resource = query_node(node, state)
    if resource is None:
        return False
    intent = dict(rank=rank, node=node, workers=state['jobs_per_node'], memory_mb=state['memory_mb'],
                  comment=comment, command=command, resource=resource, created_utc=utc())
    try:
        durable_json(where / 'intent.json', intent, exclusive=True)
    except FileExistsError:
        return False
    try:
        durable_json(where / 'assignment.json', intent, exclusive=True)
        answer = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        job_id = answer.stdout.strip().split(';')[0]
        if not re.fullmatch(r'[0-9]+', job_id):
            raise ValueError('sbatch returned an uncertain receipt')
        durable_json(where / 'receipt.json', dict(job_id=job_id, submitted_utc=utc(),
                                                stdout=answer.stdout, stderr=answer.stderr), exclusive=True)
        return True
    except subprocess.CalledProcessError as error:
        durable_json(where / 'worker_done.json', dict(rank=rank, node=node, returncode=2,
                     submission_rejected=True, error=str(error), stdout=error.stdout, stderr=error.stderr,
                     utc=utc()), exclusive=True)
        return False
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        durable_json(where / 'submission_uncertain.json', dict(reason=str(error), utc=utc()))
        return False


def _schedule(output, launcher, interval=30, once=False):
    """One locked controller admits each frozen shard at most once."""
    output = Path(output).resolve()
    if not 0 < interval <= 60:
        raise ValueError('scheduler interval must be positive and at most 60 seconds')
    _, reference, snapshot = launcher.reference_modules()
    state = reference.load_state(output)
    queue_workers_enabled(state)
    if hasattr(launcher, 'check_queue_workers'):
        launcher.check_queue_workers(output, state)
    started = utc()
    def status(name, **details):
        durable_json(output / 'controller.json', dict(pid=os.getpid(), host=socket.gethostname(),
                     started_utc=started, updated_utc=utc(), state=name,
                     log=state.get('controller_log', str(output / 'controller.log')), **details))
    with (output / 'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status('starting')
        while True:
            if (output / 'finalized.json').exists():
                result = read_json(output / 'finalized.json')['returncode']
                status('complete' if result == 0 else 'failed', returncode=result)
                return result
            snapshot.check_snapshot(output, state['snapshot'])
            try:
                jobs = queue_jobs()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                status('waiting_for_scheduler', reason=str(error))
                if once:
                    return 0
                time.sleep(interval)
                continue
            jobs_by_id = {job['job_id']: job for job in jobs}
            active = set(jobs_by_id)
            busy, tasks, failed = set(), [], False
            for rank in range(len(state['shards'])):
                where = output / 'shards' / f'{rank:02d}'
                intent_path, done_path = where / 'intent.json', where / 'worker_done.json'
                if not intent_path.exists():
                    tasks.append(dict(rank=rank, state='prepared'))
                    continue
                intent = read_json(intent_path)
                job_id = recover_receipt(where, intent, jobs)
                if job_id and job_id not in active:
                    record_terminal_failure(where, job_id)
                done = read_json(done_path) if done_path.exists() else None
                failed |= done is not None and done.get('returncode') != 0
                # Completion can precede Slurm releasing CPUs. Keep that node
                # reserved until the allocation also leaves the active queue.
                rejected = done is not None and done.get('submission_rejected') is True
                reserved = not rejected and (done is None or job_id is None or job_id in active)
                if reserved:
                    busy.add(intent['node'])
                observation = jobs_by_id.get(job_id)
                if done is None:
                    task_state = ('submission_uncertain' if observation is None else
                                  'queued' if observation['state'] == 'PENDING' else
                                  'running' if observation['state'] == 'RUNNING' else 'awaiting_scheduler')
                else:
                    task_state = ('finishing' if reserved else
                                  'complete' if done.get('returncode') == 0 else 'failed')
                task = dict(rank=rank, node=intent['node'], job_id=job_id, state=task_state)
                if observation is not None:
                    task['slurm_state'] = observation['state']
                tasks.append(task)
            all_done = all(task['state'] == 'complete' for task in tasks)
            if not busy and (all_done or failed):
                code = reference.finalize_run(output, 2 if failed else 0)
                summary_path = output / 'summary.json'
                if summary_path.exists():
                    actual_nodes = set()
                    for rank in range(len(state['shards'])):
                        where = output / 'shards' / f'{rank:02d}'
                        started_path, assignment_path = where / 'worker_started.json', where / 'assignment.json'
                        node = (read_json(started_path).get('node') if started_path.exists() else
                                read_json(assignment_path).get('actual_node') if assignment_path.exists() else None)
                        if node:
                            actual_nodes.add(node)
                    summary = read_json(summary_path)
                    summary.update(node_pool=state['nodes'], actual_worker_nodes=sorted(actual_nodes),
                                   actual_node_count=len(actual_nodes), parallel_is_pool_capacity=True)
                    durable_json(summary_path, summary)
                durable_json(output / 'finalized.json', dict(returncode=code, utc=utc()))
                status('complete' if code == 0 else 'failed', returncode=code, tasks=tasks)
                return code
            submitted = 0
            if not failed:
                for task in tasks:
                    if failed:
                        break
                    if task['state'] != 'prepared':
                        continue
                    for node in state['nodes']:
                        if node in busy or query_node(node, state) is None:
                            continue
                        accepted = attempt_submit(output, state, task['rank'], node, launcher)
                        if accepted:
                            submitted += 1
                        where = output / 'shards' / f'{task["rank"]:02d}'
                        if (where / 'intent.json').exists():
                            task.update(node=node, state='submitted' if accepted else 'submission_uncertain')
                            receipt_path, done_path = where / 'receipt.json', where / 'worker_done.json'
                            task['job_id'] = read_json(receipt_path)['job_id'] if receipt_path.exists() else None
                            if done_path.exists() and read_json(done_path).get('submission_rejected') is True:
                                failed = True
                                task['state'] = 'failed'
                            else:
                                busy.add(node)
                            break
            status('waiting_for_workers_after_failure' if failed else 'watching',
                   busy_nodes=sorted(busy), submitted_this_poll=submitted, tasks=tasks)
            if once:
                return 0
            time.sleep(interval)


def schedule(output, launcher, interval=30, once=False):
    """Record failures only for this process, never a lock holder in another PID."""
    try:
        return _schedule(output, launcher, interval, once)
    except Exception as error:
        path = Path(output) / 'controller.json'
        try:
            controller = read_json(path)
            if controller.get('pid') == os.getpid():
                controller.update(state='failed', error=str(error), updated_utc=utc())
                durable_json(path, controller)
        except (OSError, ValueError):
            pass
        raise
