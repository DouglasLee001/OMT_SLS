#!/usr/bin/env python3
"""Prepare, launch and evaluate the search-information exchange study.

Cohorts use frozen outcomes only to select diagnostic cases and report changes.
No cohort label or competitor objective changes native search or stopping rules.
Launch is explicit; preparation and comparison never submit solver jobs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

if __package__:
    from . import run_optimize as runner, run_optimize_slurm as slurm
    from .report_results import case_key, case_name, completed_summary, load_competitors, load_nia_run
    from .result_comparison import _score
else:
    import run_optimize as runner
    import run_optimize_slurm as slurm
    from report_results import case_key, case_name, completed_summary, load_competitors, load_nia_run
    from result_comparison import _score

BASELINE = Path('/data/shared/lbh/OMT/result/NIA_20261028_011635_736862')
ALLOWED_NODES = ('server', 'node1', 'node2')
REQUIRED_CASES = 6310
BASELINE_SOLVED = 5874
REQUIRED_SOLVED = BASELINE_SOLVED + 50
BASELINE_LOSSES = 31
STATES = ('A1', 'A2', 'B', 'C')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_run(path, *, allow_incomplete=False):
    path = Path(path).resolve()
    manifest = json.loads((path / 'manifest.json').read_text())
    summary = json.loads((path / 'summary.json').read_text())
    nia, directions = load_nia_run(path, allow_incomplete=allow_incomplete)
    with (path / 'results.csv').open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    return dict(path=path, nia=nia, directions=directions,
                cases={case_name(c): c for c in manifest['cases']},
                competitors=load_competitors(path / 'source/result/competitor.csv'),
                manifest=manifest, summary=summary,
                interrupted=sum(row['status'] == 'interrupted' for row in rows))


def outcome(left, right):
    return 'win' if left > right else 'loss' if left < right else 'tie'


def score(run, name):
    return _score(run['nia'][name]['目标值'], run['directions'][name], name, 'NIA_LS')


def rival_score(run, name):
    return max(_score(run['competitors'][name][solver + '目标值'], run['directions'][name], name, solver)
               for solver in ('z3', 'optimathsat'))


def statistics(run, names):
    states = Counter(run['nia'][n]['状态'] for n in names)
    wdl = Counter(outcome(score(run, n), rival_score(run, n)) for n in names)
    return dict(instances=len(names), states={s: states[s] for s in STATES},
                solved=states['A1'] + states['A2'],
                vs_best={s: wdl[s] for s in ('win', 'tie', 'loss')})


def build_cohorts(baseline, evidence):
    names = set(baseline['nia'])
    groups = {'unsolved': {n for n in names if baseline['nia'][n]['状态'] in ('B', 'C')},
              'losses': {n for n in names if score(baseline, n) < rival_score(baseline, n)}}
    mappings = {'previous_focus': 'final_focus369', 'long_proofs': 'current_candidate_A2_proof_ge300_21',
                'controls': 'historical_controls128', 'observed_regressions': 'all_observed_success_regressions'}
    for label, key in mappings.items():
        groups[label] = set(evidence['groups'][key]['names'])
        if groups[label] - names:
            raise ValueError(f'Unknown baseline names in protection group {label}')
    groups['original_regressions'] = {
        row['name'] for row in evidence['study_rows']
        if row['suite'] == 'original' and (
            row['final_state'] in ('B', 'C') and any(row[k + '_state'] in ('A1', 'A2')
                for k in ('historical', 'fresh_control', 'prior_full'))
            or any(row['final_vs_' + k + '_objective'] == 'loss'
                   for k in ('historical', 'fresh_control', 'prior_full')))}
    groups['small_original_negatives'] = {
        n for n in groups['losses'] if baseline['cases'][n]['suite'] == 'original'
        and score(baseline, n)[0] == rival_score(baseline, n)[0] == 1
        and 0 < rival_score(baseline, n)[1] - score(baseline, n)[1] <= 2}
    groups['focus'] = groups['unsolved'] | groups['previous_focus']
    groups['compact'] = groups['unsolved'] | groups['long_proofs'] | groups['controls']
    groups['screen'] = groups['losses'] | groups['long_proofs'] | groups['original_regressions']
    for category in ('design', 'portfolio'):
        groups['screen'].update(sorted(n for n in groups['unsolved']
            if baseline['cases'][n]['category'] == category and baseline['nia'][n]['状态'] == 'B')[:24])
    return groups


def prepare_study(baseline_path=BASELINE, output=None):
    baseline = load_run(baseline_path)
    full = statistics(baseline, set(baseline['nia']))
    if (full['instances'], full['solved'], full['vs_best']['loss']) != (6310, 5874, 31):
        raise ValueError('Study must use the frozen 6310-case baseline with 5874 solved and 31 losses')
    evidence = json.loads((baseline['path'] / 'balanced_refinement_groups.json').read_text())
    with (baseline['path'] / 'study_cases.csv').open(encoding='utf-8-sig', newline='') as handle:
        evidence['study_rows'] = list(csv.DictReader(handle))
    groups = build_cohorts(baseline, evidence)
    if (len(groups['unsolved']), len(groups['focus']), len(groups['long_proofs']), len(groups['controls'])) != (436, 708, 21, 128):
        raise ValueError('Frozen diagnostic cohort counts differ from 436/708/21/128')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    output = Path(output or runner.RESULT_ROOT / ('search_exchange_study_' + stamp)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = dict(baseline=str(baseline['path']), baseline_statistics=full,
                  baseline_manifest_sha256=digest(baseline['path'] / 'manifest.json'),
                  competitor_sha256=digest(baseline['path'] / 'source/result/competitor.csv'),
                  required_solved=REQUIRED_SOLVED, allowed_nodes=list(ALLOWED_NODES),
                  jobs_per_node=160, timeout=1200,
                  selection_only=True, groups={})
    for label, names in groups.items():
        path = output / f'{label}{len(names)}.txt'
        path.write_text('\n'.join(sorted(baseline['cases'][n]['source'] for n in names)) + '\n')
        record['groups'][label] = dict(count=len(names), path=str(path), sha256=digest(path),
                                       baseline=statistics(baseline, names))
    with (output / 'membership.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['name', 'source', 'category', 'groups', 'baseline_state'])
        writer.writeheader()
        for name in sorted(groups['focus']):
            case = baseline['cases'][name]
            writer.writerow(dict(name=name, source=case['source'], category=case['category'],
                                 groups='|'.join(k for k, ns in groups.items() if name in ns),
                                 baseline_state=baseline['nia'][name]['状态']))
    slurm.write_json(output / 'study.json', record)
    (output / 'README.txt').write_text(
        'Cohorts are selection and reporting labels only. No reference values enter the new exchange mode.\n'
        'Use screen117 for initial diagnostics; focus708 includes all 436 unsolved plus protected solved cases.\n'
        'Only one complete, audited 6310-case run with at least 5924 A1+A2 can meet the full goal.\n'
        'Launch explicitly with search_exchange_study.py launch; solver reports use standard NIA directories.\n')
    return output


def check_idle_nodes(nodes):
    if not nodes or len(set(nodes)) != len(nodes) or set(nodes) - set(ALLOWED_NODES):
        raise ValueError('Choose unique nodes from allowed server,node1,node2 only')
    queue = subprocess.run(['squeue', '-a', '-h', '-w', ','.join(nodes), '-o', '%i|%u|%T|%N|%j|%R'],
                           check=True, text=True, capture_output=True, timeout=20).stdout
    if queue.strip():
        raise ValueError(f'Requested nodes are not idle in live squeue: {queue.strip()}')
    states = {}
    for node in nodes:
        raw = subprocess.run(['scontrol', 'show', 'node', '-o', node],
                             check=True, text=True, capture_output=True, timeout=20).stdout
        fields = dict(re.findall(r'(\w+)=(\S+)', raw))
        if (fields.get('NodeName') != node or fields.get('State') != 'IDLE'
                or fields.get('CPUAlloc') != '0' or int(fields.get('CPUTot', '0')) < 160):
            raise ValueError(f'Requested node is not idle with 160 CPUs available: {node}: {raw.strip()}')
        states[node] = raw
    return dict(checked_utc=datetime.now(timezone.utc).isoformat(), squeue=queue, nodes=states)


def launch_run(baseline_path, input_list, nodes, mode, *, output=None, candidate=None,
               partition='all', job_name=None):
    runner.validate_search_exchange(True, mode)
    if not nodes or len(set(nodes)) != len(nodes) or set(nodes) - set(ALLOWED_NODES):
        raise ValueError('Choose unique nodes from allowed server,node1,node2 only')
    if mode != 'off' and candidate is None:
        raise ValueError('Non-off modes require an explicit candidate executable')
    if candidate is not None and (Path(candidate).resolve().parent / 'correctness_status.json').is_file():
        raise ValueError('Candidate has a recorded correctness issue; use a fresh corrected candidate')
    baseline_path = Path(baseline_path).resolve()
    state = json.loads((baseline_path / 'slurm_state.json').read_text())
    slurm.check_snapshot(baseline_path, state['snapshot'])
    if digest(baseline_path / 'source/result/competitor.csv') != digest(runner.ROOT / 'result/competitor.csv'):
        raise ValueError('Live competitor references differ from the frozen baseline')
    config = runner.replace(slurm.config_from_record(state['config']), jobs=160, timeout=1200,
                            search_exchange=mode, selection_suite='all', subset='all',
                            selection_complete=input_list is None,
                            **({'solver': Path(candidate).resolve()} if candidate else {}))
    cases = runner.collect_inputs('all', subset='all')
    if input_list is not None:
        requested = {Path(line.strip()).resolve() for line in Path(input_list).read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith('#')}
        if not requested or requested - {c.source for c in cases}:
            raise ValueError('Input list is empty or contains foreign cases')
        cases = [c for c in cases if c.source in requested]
    output = Path(output or runner.default_output_directory()).resolve()
    # Preparation is reversible and does not run a solver or submit a job.
    prepared = slurm.prepare_run(cases, config, output, nodes, partition,
                                 job_name or ('nia_exchange_' + mode))
    command = slurm.make_sbatch_command(output, prepared)
    idle = check_idle_nodes(nodes)  # Last external check before sbatch.
    slurm.write_json(output / 'pre_submit_idle.json', idle)
    submitted = subprocess.run(command, text=True, capture_output=True, check=False)
    submission = dict(command=command, returncode=submitted.returncode, stderr=submitted.stderr)
    if submitted.returncode == 0:
        job = submitted.stdout.strip().split(';')[0]
        if job.isdigit():
            submission['job_id'] = job
    slurm.write_json(output / 'submission.json', submission)
    if 'job_id' not in submission:
        raise ValueError(f'Slurm submission failed: {submitted.stderr or submitted.stdout}')
    submission['output'] = str(output)
    return submission


def compare_runs(baseline, current, *, audit_ok=False):
    names = set(current['nia'])
    if not names or names - baseline['nia'].keys():
        raise ValueError('Current run must contain baseline case identities')
    for name in names:
        if (case_key(current['cases'][name]) != case_key(baseline['cases'][name])
                or current['directions'][name] != baseline['directions'][name]
                or current['cases'][name]['source'] != baseline['cases'][name]['source']
                or current['competitors'][name] != baseline['competitors'][name]):
            raise ValueError(f'Frozen identity, direction or competitor mismatch: {name}')
    old, new = statistics(baseline, names), statistics(current, names)
    gains, regressions, objective_regressions = [], [], []
    for name in sorted(names):
        before = baseline['nia'][name]['状态'] in ('A1', 'A2')
        after = current['nia'][name]['状态'] in ('A1', 'A2')
        if after and not before:
            gains.append(name)
        if before and not after:
            regressions.append(name)
        if score(current, name) < score(baseline, name):
            objective_regressions.append(name)
    reasons = []
    baseline_full = statistics(baseline, set(baseline['nia']))
    if (baseline_full['instances'], baseline_full['solved'], baseline_full['vs_best']['loss']) != (6310, 5874, 31):
        reasons.append('requires the frozen baseline with 5874 solved and 31 losses')
    if len(names) != REQUIRED_CASES or names != baseline['nia'].keys():
        reasons.append('requires exactly the same 6310 cases')
    manifest = current['manifest']
    if manifest.get('selection_complete') is not True:
        reasons.append('selection is partial')
    if not completed_summary(current['summary'], len(names)) or current['interrupted']:
        reasons.append('execution is incomplete or interrupted')
    if manifest.get('timeout') != 1200:
        reasons.append('requires 1200-second budgets')
    if (manifest.get('prove_optimal') is not True or manifest.get('scalar_target_enabled') is not False
            or manifest.get('known_optimum_stop_enabled') is not False):
        reasons.append('requires own optimality proof without reference stopping')
    if (manifest.get('scheduler') != 'slurm' or manifest.get('jobs_per_node') != 160
            or not manifest.get('nodes') or set(manifest['nodes']) - set(ALLOWED_NODES)):
        reasons.append('requires Slurm 160 workers per allowed node')
    if not audit_ok:
        reasons.append('fresh strict artifact audit has not passed')
    if (Path(current['path']) / 'correctness_status.json').is_file():
        reasons.append('run has a recorded correctness issue; use a fresh corrected run')
    eligible = not reasons
    return dict(baseline=str(baseline['path']), current=str(current['path']),
                instances=len(names), baseline_subset=old, current_statistics=new,
                baseline_loss_count=baseline_full['vs_best']['loss'],
                declared_baseline_loss_count=BASELINE_LOSSES,
                solved_delta=new['solved'] - old['solved'],
                vs_best_loss_delta=new['vs_best']['loss'] - old['vs_best']['loss'],
                solved_gains=gains, solved_regressions=regressions,
                objective_regressions=objective_regressions,
                full_goal=dict(eligible=eligible, ineligible_reasons=reasons,
                               required_solved=REQUIRED_SOLVED, required_net_gain=50,
                               maximum_objective_losses=BASELINE_LOSSES,
                               met=(new['solved'] >= REQUIRED_SOLVED and new['solved'] - old['solved'] >= 50
                                    and new['vs_best']['loss'] <= BASELINE_LOSSES)
                                   if eligible else None),
                baseline_conditions={k: baseline['manifest'].get(k)
                    for k in ('nodes', 'jobs_per_node', 'timeout')},
                current_conditions={k: manifest.get(k) for k in ('nodes', 'jobs_per_node', 'timeout')})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('prepare', help='write cohort lists only; no solver execution')
    prepare.add_argument('--output', type=Path)
    launch = commands.add_parser('launch', help='freeze a run, recheck idle nodes, then submit')
    launch.add_argument('--input-list', type=Path, help='omit for a complete 6310-case run')
    launch.add_argument('--nodes', default=','.join(ALLOWED_NODES))
    launch.add_argument('--mode', choices=runner.SEARCH_EXCHANGE_MODES, default='off')
    launch.add_argument('--candidate', type=Path, help='explicit executable; omit for frozen baseline control')
    launch.add_argument('--output', type=Path)
    launch.add_argument('--job-name')
    compare = commands.add_parser('compare', help='compare runs; --verify enables fresh full-goal validation')
    compare.add_argument('runs', type=Path, nargs='+')
    compare.add_argument('--verify', action='store_true')
    compare.add_argument('--output', type=Path, help='JSON file; default each run/search_exchange_comparison.json')
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            print(prepare_study(args.baseline, args.output))
        elif args.action == 'launch':
            print(json.dumps(launch_run(args.baseline, args.input_list, args.nodes.split(','), args.mode,
                candidate=args.candidate, output=args.output, job_name=args.job_name), indent=2))
        else:
            baseline = load_run(args.baseline)
            reports = []
            for path in args.runs:
                current = load_run(path, allow_incomplete=True)
                audit_ok = False
                if args.verify:
                    if __package__:
                        from .verify_optimize import verify_run
                    else:
                        from verify_optimize import verify_run
                    audit = verify_run(path, len(current['nia']))
                    slurm.write_json(path / 'verification.json', audit)
                    audit_ok = audit['ok']
                result = compare_runs(baseline, current, audit_ok=audit_ok)
                if not args.output:
                    slurm.write_json(path / 'search_exchange_comparison.json', result)
                reports.append(result)
            if args.output:
                slurm.write_json(args.output, reports)
            print(json.dumps(reports, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'search_exchange_study: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
