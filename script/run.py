#!/usr/bin/env python3
"""Bounded parallel benchmark runner; solver workers emit improving values."""
import argparse
import concurrent.futures
import csv
from dataclasses import dataclass
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().with_name('solver_worker.py')
FIELDS = ['benchmark', 'solver', 'theory', 'status', 'best_value',
          'time_to_best_seconds', 'best_value_source', 'reported_optimum',
          'time_to_reported_optimum_seconds', 'model_value', 'lower_bound', 'upper_bound',
          'optimality_status', 'solver_reported_optimal',
          'time_to_optimal_seconds', 'total_seconds', 'native_exit_code',
          'worker_exit_code', 'reason', 'case_dir']


@dataclass
class Config:
    solver: str
    benchmark_root: Path
    lists: list
    result_root: Path
    jobs: int
    timeout: float


def load_config(solver):
    if solver not in ('z3', 'optimathsat'):
        raise ValueError('Solver must be z3 or optimathsat')
    bench = Path(os.environ.get('BENCHMARK_ROOT') or '/data/shared/lbh/OMT/benchmarks').resolve()
    override = os.environ.get('BENCHMARK_LIST')
    lists = ([Path(override).resolve()] if override else
             [bench/'nia_benchmarks_subset.txt'])
    jobs = int(os.environ.get('JOBS') or '180')
    timeout = float(os.environ.get('TIMEOUT') or '1200')
    if jobs < 1:
        raise ValueError('JOBS must be a positive integer')
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('TIMEOUT must be finite and nonnegative (0 disables timeout)')
    output = Path(os.environ.get('RESULT_ROOT') or ROOT/'result').resolve()
    return Config(solver, bench, lists, output, jobs, timeout)


def load_benchmarks(config):
    items, seen = [], set()
    for listing in config.lists:
        if not listing.is_file():
            raise ValueError(f'Benchmark list missing: {listing}')
        for line in listing.read_text().splitlines():
            name = line.strip()
            if not name or name.startswith('#'):
                continue
            source = (config.benchmark_root/name).resolve()
            if not source.is_relative_to(config.benchmark_root):
                raise ValueError(f'Benchmark path escapes root: {name}')
            if source.suffix != '.smt2' or not source.is_file():
                raise ValueError(f'Benchmark file missing or not SMT2: {source}')
            name = source.relative_to(config.benchmark_root).as_posix()
            if name not in seen:
                items.append(name)
                seen.add(name)
    if not items:
        raise ValueError('Benchmark list contains no inputs')
    return items


def create_run_directory(config):
    parent = config.result_root/config.solver
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = parent/(stamp+'-'+uuid.uuid4().hex[:6])
    directory.mkdir()
    latest = parent/'latest'
    if latest.exists() and not latest.is_symlink():
        raise ValueError(f'Refusing to replace existing non-symlink: {latest}')
    temporary = parent/('.latest-'+uuid.uuid4().hex)
    temporary.symlink_to(directory.name)
    temporary.replace(latest)
    return directory


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=.3)
    except subprocess.TimeoutExpired:
        pass
    # Kill descendants too, even when the worker itself already exited.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_case(relative, config, run_dir, stop, worker_script=WORKER):
    identifier = hashlib.sha256(relative.encode()).hexdigest()[:16]
    case_dir = run_dir/'cases'/identifier
    case_dir.mkdir(parents=True)
    start = time.monotonic_ns()
    record = {'benchmark': relative, 'solver': config.solver,
              'theory': 'NRA' if relative.startswith('omtplan/') else ('NIA' if relative.startswith('QF_NIA/') else 'other'),
              'status': 'error', 'best_value': None, 'time_to_best_seconds': None,
              'best_value_source': None, 'reported_optimum': None, 'model_value': None,
              'time_to_reported_optimum_seconds': None, 'lower_bound': None, 'upper_bound': None,
              'optimality_status': 'none', 'solver_reported_optimal': False,
              'time_to_optimal_seconds': None, 'native_exit_code': None,
              'worker_exit_code': None, 'reason': None, 'case_dir': str(case_dir),
              'started_monotonic_ns': start, 'independently_verified': False}
    command = [sys.executable, str(worker_script), '--solver', config.solver,
               '--input', str(config.benchmark_root/relative), '--case-dir', str(case_dir),
               '--start-ns', str(start)]
    record['command'] = command
    proc = None
    final = None
    stopped_status = None
    malformed = []
    env = dict(os.environ, LC_ALL='C', PYTHONUNBUFFERED='1', OMP_NUM_THREADS='1')
    try:
        if stop.is_set():
            stopped_status = 'interrupted'
        else:
            with (case_dir/'worker-stderr.txt').open('wb') as err, (case_dir/'events.jsonl').open('w') as events:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=err, start_new_session=True, env=env, cwd=ROOT)
                pending = b''
                selector = selectors.DefaultSelector()
                selector.register(proc.stdout, selectors.EVENT_READ)
                try:
                    while selector.get_map():
                        elapsed = (time.monotonic_ns()-start)/1e9
                        if stopped_status is None and (stop.is_set() or (config.timeout and elapsed >= config.timeout)):
                            stopped_status = 'interrupted' if stop.is_set() else 'timeout'
                            _kill_group(proc)
                        wait = .1 if not config.timeout else max(0,min(.1,config.timeout-elapsed))
                        for key, _ in selector.select(wait if stopped_status is None else .1):
                            chunk = os.read(key.fd, 65536)
                            if chunk:
                                pending += chunk
                                lines = pending.split(b'\n')
                                pending = lines.pop()
                            else:
                                selector.unregister(key.fileobj)
                                lines = [pending] if pending else []
                                pending = b''
                            for raw in lines:
                                line = raw.decode('utf-8',errors='replace')
                                events.write(line+'\n')
                                events.flush()
                                try:
                                    event = json.loads(line)
                                except json.JSONDecodeError:
                                    malformed.append(line[:500])
                                    continue
                                if event.get('event') == 'candidate':
                                    timestamp = event.get('time_seconds')
                                    if timestamp is None or not isinstance(timestamp, (int,float)) or timestamp < 0:
                                        malformed.append('Invalid candidate timestamp')
                                    elif not config.timeout or timestamp <= config.timeout:
                                        record.update(best_value=event['value'],time_to_best_seconds=timestamp,
                                                      best_value_source=event.get('source'))
                                elif event.get('event') == 'final':
                                    final = event
                    proc.wait(timeout=1)
                finally:
                    selector.close()
                    proc.stdout.close()
                record['worker_exit_code'] = proc.returncode
            completed_seconds = final.get('completed_seconds') if final else None
            timely_final = (isinstance(completed_seconds,(int,float)) and math.isfinite(completed_seconds)
                            and completed_seconds >= 0 and
                            (not config.timeout or completed_seconds <= config.timeout))
            accepted_after_deadline = timely_final and stopped_status == 'timeout'
            if accepted_after_deadline:
                stopped_status = None
            if final is not None and stopped_status is None:
                record['worker_completed_seconds'] = completed_seconds
                for field in FIELDS:
                    if field in final and field not in ('benchmark','solver','theory','case_dir','total_seconds','worker_exit_code'):
                        record[field] = final[field]
                if proc.returncode != 0 and not accepted_after_deadline and record['status'] not in ('error','unknown'):
                    record['status']='error'
                    record['reason']=f'Worker exited {proc.returncode}'
            elif stopped_status is None:
                record['reason']=f'Worker exited {proc.returncode} without a final event'
            if malformed:
                record['protocol_diagnostics']=malformed
    except Exception as exc:
        if proc is not None:
            _kill_group(proc)
        record['reason']=f'{type(exc).__name__}: {exc}'
    finally:
        if stopped_status is not None:
            record.update(status=stopped_status,optimality_status='unconfirmed' if record['best_value'] is not None else 'none',
                          solver_reported_optimal=False,time_to_optimal_seconds=None,
                          reason=f'{config.timeout:g}s deadline exceeded' if stopped_status=='timeout' else 'Run interrupted')
        if proc is not None:
            _kill_group(proc)
        generated=case_dir/'input.generated.smt2'
        if generated.exists():
            generated.unlink()
        end=time.monotonic_ns()
        record['finished_monotonic_ns']=end
        record['total_seconds']=(end-start)/1e9
        (case_dir/'result.json').write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n')
    return record


def run_batch(config, inputs, directory, worker_script=WORKER):
    stop=threading.Event()
    old_handlers={}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            old_handlers[sig]=signal.signal(sig,lambda number,frame:stop.set())
    counts={}
    completed=0
    batch_start=time.monotonic()
    metadata={'solver':config.solver,'benchmark_root':str(config.benchmark_root),
              'benchmark_lists':[str(p) for p in config.lists],'jobs':config.jobs,'timeout_seconds':config.timeout,
              'input_count':len(inputs),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'worker_sha256':hashlib.sha256(Path(worker_script).read_bytes()).hexdigest(),
              'value_note':'Best candidates and solver-reported optimum are separate; no independent optimality proof.',
              'time_note':'Monotonic seconds from worker launch, excluding queue; stream timestamps are observation times.'}
    (directory/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    (directory/'inputs.txt').write_text('\n'.join(inputs)+'\n')
    try:
        with (directory/'results.csv').open('w',newline='') as csvfile, (directory/'results.jsonl').open('w') as jsonfile:
            writer=csv.DictWriter(csvfile,fieldnames=FIELDS,extrasaction='ignore')
            writer.writeheader()
            csvfile.flush()
            iterator=iter(inputs)
            with concurrent.futures.ThreadPoolExecutor(max_workers=config.jobs) as pool:
                try:
                    pending={}
                    for _ in range(min(config.jobs,len(inputs))):
                        name=next(iterator)
                        pending[pool.submit(run_case,name,config,directory,stop,worker_script)]=name
                    while pending:
                        finished,_=concurrent.futures.wait(pending,timeout=.2,return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in finished:
                            name=pending.pop(future)
                            record=future.result()
                            writer.writerow(record)
                            csvfile.flush()
                            jsonfile.write(json.dumps(record,ensure_ascii=False)+'\n')
                            jsonfile.flush()
                            completed+=1
                            counts[record['status']]=counts.get(record['status'],0)+1
                            if completed<=5 or completed%25==0 or completed==len(inputs):
                                print(f'[{completed}/{len(inputs)}] {record["status"]}: {name}',flush=True)
                            if not stop.is_set():
                                next_name=next(iterator,None)
                                if next_name is not None:
                                    pending[pool.submit(run_case,next_name,config,directory,stop,worker_script)]=next_name
                except BaseException:
                    stop.set()
                    raise
    finally:
        for sig,handler in old_handlers.items():
            signal.signal(sig,handler)
        summary={'solver':config.solver,'completed':completed,'total':len(inputs),'not_started':len(inputs)-completed,
                 'interrupted':stop.is_set(),'counts':counts,'wall_seconds':time.monotonic()-batch_start,
                 'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(f'Results: {directory}/results.csv',flush=True)
    return 130 if stop.is_set() else (1 if counts.get('error',0) else 0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('solver',choices=['z3','optimathsat'])
    args=parser.parse_args()
    try:
        config=load_config(args.solver)
        executable=ROOT/'competitor/bin'/args.solver
        if not executable.is_file() or not os.access(executable,os.X_OK):
            raise ValueError(f'Solver executable missing: {executable}')
        if not WORKER.is_file():
            raise ValueError(f'Worker missing: {WORKER}')
        if config.solver=='z3' and not (ROOT/'competitor/z3-5.1.0/install/lib/python3.12/site-packages/z3').is_dir():
            raise ValueError('Locally built Z3 Python binding is missing')
        inputs=load_benchmarks(config)
        directory=create_run_directory(config)
    except (ValueError,OSError) as exc:
        print(f'run.sh: {exc}',file=sys.stderr)
        return 2
    print(f'Solver: {config.solver}; inputs: {len(inputs)}; parallel jobs: {config.jobs}; timeout: {config.timeout:g}s (0=unlimited)',flush=True)
    print(f'Output: {directory}',flush=True)
    return run_batch(config,inputs,directory)


if __name__=='__main__':
    raise SystemExit(main())
