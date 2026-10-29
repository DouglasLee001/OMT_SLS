#!/usr/bin/env python3
"""Check final incumbent bounds with the ordinary Z3 command-line SMT solver."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from NIA_LS.converter.omt_to_ls import _query, _commands, _render
from script.report_results import case_key, case_name, load_nia_run, load_competitors
from script.result_comparison import _score

DEFAULT_RUN = Path('/data/shared/lbh/OMT/result/NIA_20261028_011635_736862')
OUTPUT_ROOT = Path('/data/shared/lbh/OMT/result/strict_bound')
DEFAULT_SOLVER = ROOT / 'competitor/z3-5.1.0/build/z3'
GROUPS = {
    'proved-equal': '已达到对手证明的最优值，本轮未证明',
    'unproved-tie': '双方均未证明，与更好的对手同值',
    'unproved-better': '双方均未证明，本轮优于两个对手',
}
FIELDS = ('filename', 'category', 'selection_group', 'direction', 'M', 'proved_by',
          'z3_reference_state', 'optimathsat_reference_state',
          'z3_reference_value', 'optimathsat_reference_value',
          'z3_reference_time_seconds', 'optimathsat_reference_time_seconds', 'status',
          'time_seconds', 'nia_time_to_best_seconds', 'nia_optimality_seconds',
          'nia_optimality_attempts', 'query_file', 'returncode', 'error')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def build_query(source, best, direction):
    """Use text parsing only: retain hard commands and append a strict bound."""
    if not isinstance(best, str) or not re.fullmatch(r'-?[0-9]+', best):
        raise ValueError('M must be an exact finite decimal integer')
    filtered, actual_direction, objective = _query(source)
    if direction not in ('min', 'max') or direction != actual_direction:
        raise ValueError('objective direction differs from the experiment manifest')
    hard = [form for form in _commands(filtered) if form[0] != 'minimize']
    number = int(best)
    literal = str(number) if number >= 0 else f'(- {-number})'
    comparison = '<' if direction == 'min' else '>'
    text = '\n'.join(_render(form) for form in hard)
    text += f'\n(assert ({comparison} {objective} {literal}))\n(check-sat)\n(exit)\n'
    return text, objective


def select_cases(source_run, expected_count=None, groups=('proved-equal',)):
    groups = tuple(groups)
    if not groups or not set(groups).issubset(GROUPS):
        raise ValueError('select at least one valid strict-bound group')
    source_run = Path(source_run).resolve()
    nia, directions = load_nia_run(source_run)
    opponents = load_competitors(source_run / 'source/result/competitor.csv')
    manifest = json.loads((source_run / 'manifest.json').read_text())
    with (source_run / 'results.csv').open(encoding='utf-8-sig', newline='') as handle:
        raw = {case_key(row): row for row in csv.DictReader(handle)}
    if set(nia) != set(opponents):
        raise ValueError('source run and frozen competitors have different selections')
    selected = []
    for case in manifest['cases']:
        name = case_name(case)
        result, reference = nia[name], opponents[name]
        if result['状态'] != 'B':
            continue
        score = _score(result['目标值'], directions[name], name, 'NIA_LS')
        reference_scores = {
            solver: _score(reference[solver + '目标值'], directions[name], name, solver)
            for solver in ('z3', 'optimathsat')
        }
        proved_by = [solver for solver in ('z3', 'optimathsat')
                     if reference[solver + '状态'] == 'A2'
                     and reference_scores[solver] == score]
        if any(reference[solver + '状态'] == 'A1' for solver in reference_scores):
            continue
        if proved_by:
            group = 'proved-equal'
        elif all(reference[solver + '状态'] in ('B', 'C') for solver in reference_scores):
            best = max(reference_scores.values())
            group = 'unproved-tie' if score == best else 'unproved-better' if score > best else None
        else:
            group = None
        if group not in groups:
            continue
        row = raw[case_key(case)]
        selected.append(dict(filename=name, source=case['source'],
            category='original' if case['suite'] == 'original' else case['category'],
            selection_group=group, direction=directions[name], M=result['目标值'], proved_by=proved_by,
            z3_reference_state=reference['z3状态'],
            optimathsat_reference_state=reference['optimathsat状态'],
            z3_reference_value=reference['z3目标值'],
            optimathsat_reference_value=reference['optimathsat目标值'],
            z3_reference_time_seconds=reference['z3时间（秒）'],
            optimathsat_reference_time_seconds=reference['optimathsat时间（秒）'],
            nia_time_to_best_seconds=row['time_to_best_seconds'],
            nia_optimality_seconds=row.get('optimality_seconds', ''),
            nia_optimality_attempts=row.get('optimality_attempts', '')))
    if expected_count is not None and len(selected) != expected_count:
        raise ValueError(f'expected {expected_count} strict-bound B cases, selected {len(selected)}')
    return selected


def prepare_queries(cases, output, source_run):
    output, source_run = Path(output).resolve(), Path(source_run).resolve()
    if not cases:
        raise ValueError('no strict-bound queries selected')
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    records, texts = [], []
    for index, case in enumerate(cases):
        original = Path(case['source']).read_bytes()
        query, objective = build_query(original.decode('utf-8'), case['M'], case['direction'])
        name = f'{index:03d}-{hashlib.sha256(case["filename"].encode()).hexdigest()[:12]}.smt2'
        data = query.encode('utf-8')
        records.append(dict(case, query_file='instance/' + name, objective=objective,
                            source_sha256=hashlib.sha256(original).hexdigest(),
                            query_sha256=hashlib.sha256(data).hexdigest()))
        texts.append(data)
    output.mkdir(parents=True)
    (output / 'instance').mkdir()
    for case, data in zip(records, texts):
        (output / case['query_file']).write_bytes(data)
    inputs = {name: digest(source_run / name)
              for name in ('manifest.json', 'results.csv', 'source/result/competitor.csv')
              if (source_run / name).is_file()}
    manifest = dict(kind='strict_optimum_bound', source_run=str(source_run),
                    prepared_utc=datetime.now(timezone.utc).isoformat(),
                    selected=len(records), source_run_hashes=inputs, instances=records,
                    selection_groups=dict(Counter(case.get('selection_group') or 'proved-equal'
                                                  for case in records)))
    write_json(output / 'manifest.json', manifest)
    return manifest


def load_prepared(prepared):
    prepared = Path(prepared).resolve()
    manifest = json.loads((prepared / 'manifest.json').read_text())
    cases = manifest.get('instances')
    if (manifest.get('kind') != 'strict_optimum_bound' or not cases
            or manifest.get('selected') != len(cases)
            or len({case['filename'] for case in cases}) != len(cases)
            or len({case['query_file'] for case in cases}) != len(cases)):
        raise ValueError('invalid or duplicate strict-bound selection')
    for case in cases:
        if (case.get('selection_group') or 'proved-equal') not in GROUPS:
            raise ValueError('invalid prepared selection group')
        relative = Path(case['query_file'])
        query = prepared / relative
        if (relative.is_absolute() or '..' in relative.parts
                or relative.parts[0] != 'instance' or query.suffix != '.smt2'
                or not query.resolve().is_relative_to(prepared / 'instance')
                or query.is_symlink() or digest(query) != case['query_sha256']):
            raise ValueError(f'query is missing, changed, or outside instance/: {relative}')
    return manifest


def run_case(case, prepared, solver, timeout, output, stop=None):
    stop = stop or threading.Event()
    logs = Path(output) / 'logs'
    logs.mkdir(exist_ok=True)
    stem = Path(case['query_file']).stem
    stdout, stderr = logs / (stem + '.stdout.txt'), logs / (stem + '.stderr.txt')
    result = {field: case.get(field, '') for field in FIELDS}
    result.update(selection_group=case.get('selection_group') or 'proved-equal',
                  proved_by=','.join(case.get('proved_by', [])), status='error',
                  time_seconds=0.0, returncode=None, error='')
    process = None
    started = time.monotonic()
    try:
        with stdout.open('wb') as out, stderr.open('wb') as err:
            process = subprocess.Popen([str(solver), '-smt2', str(Path(prepared) / case['query_file'])],
                                       stdout=out, stderr=err, start_new_session=True)
            while process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if stop.is_set() or remaining <= 0:
                    result['status'] = 'interrupted' if stop.is_set() else 'timeout'
                    result['error'] = 'run interrupted' if stop.is_set() else f'{timeout:g}s wallclock limit'
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    break
                try:
                    process.wait(timeout=min(.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
        elapsed = time.monotonic() - started
        result.update(time_seconds=elapsed, returncode=process.returncode)
        if result['status'] not in ('timeout', 'interrupted'):
            lines = [line.strip() for line in stdout.read_text(errors='replace').splitlines()]
            answers = [line for line in lines if line in ('unsat', 'sat', 'unknown')]
            if elapsed > timeout:
                result.update(status='timeout', error=f'{timeout:g}s wallclock limit')
            elif process.returncode or any(line.startswith('(error') for line in lines):
                result.update(status='error', error=f'Z3 exit={process.returncode}; '
                              + ('\n'.join(lines) + '\n' + stderr.read_text(errors='replace'))[-1000:])
            elif len(answers) != 1:
                result.update(status='error', error='expected exactly one SMT answer in stdout')
            else:
                result['status'] = answers[0]
    except OSError as error:
        result.update(status='error', error=str(error), time_seconds=time.monotonic() - started)
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return result


def write_summary(output, manifest, rows, complete):
    counts = Counter(row['status'] for row in rows)
    grouped = {key: [row for row in rows
                     if (row.get('selection_group') or 'proved-equal') == key] for key in GROUPS}
    selected = Counter(case.get('selection_group') or 'proved-equal'
                       for case in manifest.get('instances', rows))
    summary = dict(selected=manifest['selected'], completed=len(rows), complete=complete,
                   statuses=dict(counts), solver=manifest['solver'],
                   solver_version=manifest['solver_version'], jobs=manifest['jobs'],
                   timeout=manifest['timeout'],
                   selection_groups={key: dict(selected=selected[key], completed=len(group),
                                               statuses=dict(Counter(r['status'] for r in group)))
                                     for key, group in grouped.items()},
                   proved_equal_counterexamples=sum(r['status'] == 'sat'
                                                    for r in grouped['proved-equal']))
    write_json(output / 'summary.json', summary)
    lines = ['# Z3 严格改善界 SMT 实验', '',
        f'原NIA实验：`{manifest["source_run"]}`',
        f'Z3：`{manifest["solver"]}`；{manifest["solver_version"]}',
        f'并行上限{manifest["jobs"]}，每例{manifest["timeout"]:g}秒；'
        f'已完成{len(rows)}/{manifest["selected"]}，完整结束：{complete}。', '',
        '普通SMT查询仅含原硬约束和严格改善界：min为obj<M，max为obj>M。'
        '不调用优化接口、不运行NIA_LS。', '',
        '| 集合 | 实例数 | UNSAT | SAT | unknown | timeout | error | interrupted |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    groups = [('全部', rows),
              *[(f'{key}：{GROUPS[key]}', group) for key, group in grouped.items()],
              ('Z3原已证明最优', [r for r in grouped['proved-equal']
                                 if r['z3_reference_state'] == 'A2']),
              ('仅OptiMathSAT原已证明最优', [r for r in grouped['proved-equal']
                                           if r['z3_reference_state'] != 'A2'
                                           and r['optimathsat_reference_state'] == 'A2'])]
    for name, group in groups:
        c = Counter(r['status'] for r in group)
        values = [len(group)] + [c[s] for s in ('unsat', 'sat', 'unknown', 'timeout', 'error', 'interrupted')]
        lines.append('| ' + name + ' | ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', 'results.csv 的 time_seconds 是本次Z3进程从启动到退出/截止的墙钟时间；'
              'nia_time_to_best_seconds、nia_optimality_seconds、nia_optimality_attempts 为原NIA轮次的记录。',
              'UNSAT表示本次独立排除了更优解；SAT表示存在比M更好的解，M尚非最优。'
              'unknown/timeout不作证明。只有proved-equal组的SAT与已有最优报告冲突，需单独核查；'
              '另两组的SAT是正常测试结果，不是运行错误。', '']
    (output / 'summary.md').write_text('\n'.join(lines))
    return summary


def run_experiment(prepared, output, solver, jobs, timeout):
    prepared, output, solver = Path(prepared).resolve(), Path(output).resolve(), Path(solver).resolve()
    manifest = load_prepared(prepared)
    if not solver.is_file() or not os.access(solver, os.X_OK):
        raise ValueError(f'missing executable: {solver}')
    version = subprocess.run([str(solver), '--version'], text=True, capture_output=True,
                             check=True, timeout=10).stdout.strip()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prepared / 'instance', output / 'instance')
    manifest.update(prepared_from=str(prepared), prepared_manifest_sha256=digest(prepared / 'manifest.json'),
                    started_utc=datetime.now(timezone.utc).isoformat(), solver=str(solver),
                    solver_version=version, solver_sha256=digest(solver), jobs=jobs, timeout=timeout)
    write_json(output / 'manifest.json', manifest)
    load_prepared(output)
    print(f'Z3 ordinary SMT: {len(manifest["instances"])} cases; jobs={jobs}; {timeout:g}s/case', flush=True)
    print(f'Results: {output}', flush=True)
    stop, rows, complete = threading.Event(), [], False
    with (output / 'results.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=min(jobs, manifest['selected'])) as pool:
            futures = [pool.submit(run_case, case, output, solver, timeout, output, stop)
                       for case in manifest['instances']]
            try:
                for future in as_completed(futures):
                    row = future.result()
                    rows.append(row)
                    writer.writerow(row)
                    handle.flush()
                    print(f'{len(rows)}/{manifest["selected"]} {row["status"]} '
                          f'{row["time_seconds"]:.3f}s {row["filename"]}', flush=True)
                complete = True
            except KeyboardInterrupt:
                stop.set()
                for future in futures:
                    future.cancel()
    summary = write_summary(output, manifest, rows, complete)
    print((output / 'summary.md').read_text(), flush=True)
    if not complete:
        return 130
    return 1 if summary['statuses'].get('error', 0) or summary['proved_equal_counterexamples'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--run', type=Path, help='completed NIA run whose final B incumbents are tested')
    source.add_argument('--prepared', type=Path, help='reuse previously prepared, hash-checked queries')
    parser.add_argument('--prepare-only', action='store_true', help='generate SMT files without launching Z3')
    parser.add_argument('--groups', nargs='+', choices=tuple(GROUPS),
                        help='B groups to select from --run; default: proved-equal')
    parser.add_argument('--expected-count', type=int, help='optional exact selection-count check')
    parser.add_argument('--solver', type=Path, default=DEFAULT_SOLVER)
    parser.add_argument('--jobs', type=int, default=180)
    parser.add_argument('--timeout', type=float, default=1200)
    parser.add_argument('--output', type=Path, help='new output directory; never overwrites an existing one')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.jobs <= 180 or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('jobs must be 1..180 and timeout must be positive and finite')
        if args.expected_count is not None and args.expected_count < 1:
            raise ValueError('expected-count must be positive')
        if args.prepared and args.prepare_only:
            raise ValueError('--prepare-only requires --run, not --prepared')
        if args.prepared and args.groups:
            raise ValueError('--groups selects from --run; prepared groups are fixed in their manifest')
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
        output = (args.output or OUTPUT_ROOT / (('prepared_' if args.prepare_only else 'Z3_') + stamp)).resolve()
        if output.exists():
            raise ValueError(f'output already exists: {output}')
        if args.prepared:
            prepared = args.prepared.resolve()
        else:
            source_run = (args.run or DEFAULT_RUN).resolve()
            cases = select_cases(source_run, args.expected_count, args.groups or ('proved-equal',))
            prepared = output if args.prepare_only else output.with_name(output.name + '_queries')
            manifest = prepare_queries(cases, prepared, source_run)
            print(f'Prepared {manifest["selected"]} ordinary SMT files: {prepared}', flush=True)
        if args.prepare_only:
            return 0
        manifest = load_prepared(prepared)
        if args.expected_count is not None and manifest['selected'] != args.expected_count:
            raise ValueError(f'prepared selection differs from expected count {args.expected_count}')
        return run_experiment(prepared, output, args.solver, args.jobs, args.timeout)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'run_strict_bound: {error}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
