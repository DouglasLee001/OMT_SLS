#!/usr/bin/env python3
"""Compare canonical deep-mix reports; write reproducible evidence to the candidate.

Exit 0 means the reports were produced, not that the experimental goal was met.
The goal decision is explicit in deepmix_compare.json and deepmix_compare.md.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
REFERENCE = Path(__file__).resolve().parents[1] / 'reference/script'
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))
def reference_module(name):
    spec = importlib.util.spec_from_file_location('_deepmix_reference_' + name, REFERENCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report_results = reference_module('report_results')
comparison = reference_module('result_comparison')
SOLVERS, STATES = comparison.SOLVERS, comparison.STATES
_score, compute_statistics = comparison._score, comparison.compute_statistics


def indexed_csv(path, fields):
    result = {}
    for row in report_results.read_csv(path, fields):
        name = row['文件名']
        if not name or name in result:
            raise ValueError(f'Empty or duplicate canonical instance name in {path}: {name}')
        result[name] = row
    return result


def native_evidence(directory, cases, directions):
    path = directory / 'results.csv'
    if not path.is_file():
        return dict(available=False, complete=False, recorded=0, errors=None, diagnostics=None,
                    interrupted=None, statuses={}, missing=sorted(cases), rows={})
    rows = {}
    for row in report_results.read_csv(path, ['suite', 'category', 'filename', 'direction', 'status']):
        name = report_results.case_name(row)
        if name not in cases or name in rows or row['direction'] != directions[name]:
            raise ValueError(f'Invalid native result alignment in {directory}: {name}')
        rows[name] = row
    errors = sum(row['status'] == 'error' for row in rows.values())
    diagnostics = sum(bool(row.get('error')) for row in rows.values())
    interrupted = sum(row['status'] == 'interrupted' for row in rows.values())
    return dict(available=True, complete=rows.keys() == cases.keys(), recorded=len(rows),
                errors=errors, diagnostics=diagnostics, interrupted=interrupted,
                statuses=dict(Counter(row['status'] for row in rows.values())),
                missing=sorted(cases.keys() - rows.keys()), rows=rows)


def load_run(directory):
    """Align canonical CSVs against their own original manifest, without summary.json."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    records = manifest.get('cases')
    if not isinstance(records, list) or not records:
        raise ValueError(f'Empty or invalid case manifest: {directory}')
    if type(manifest.get('selection_complete', False)) is not bool:
        raise ValueError(f'Invalid selection_complete flag: {directory}')
    cases, directions = {}, {}
    for record in records:
        name = report_results.case_name(record)
        direction = record.get('direction')
        if name in cases or direction not in ('min', 'max'):
            raise ValueError(f'Duplicate case or invalid original direction: {name}')
        cases[name], directions[name] = record, direction
    nia = indexed_csv(directory / 'nia_ls.csv', report_results.NIA_FIELDS)
    combined = indexed_csv(directory / 'combined.csv', report_results.COMBINED_FIELDS)
    if nia.keys() != cases.keys() or combined.keys() != cases.keys():
        raise ValueError(f'Canonical CSV names do not cover the selected manifest exactly: {directory}')
    for name, row in combined.items():
        for field in report_results.NIA_FIELDS[1:]:
            if row['NIA_LS' + field] != nia[name][field]:
                raise ValueError(f'nia_ls.csv/combined.csv disagreement: {name}, {field}')
        for solver in SOLVERS:
            state = row[solver + '状态']
            score = _score(row[solver + '目标值'], directions[name], name, solver)
            if state not in STATES or score[0] != {'A1': 2, 'A2': 1, 'B': 1, 'C': 0}.get(state):
                raise ValueError(f'Status/objective disagreement: {name}, {solver}')
            report_results.checked_time(row[solver + '时间（秒）'], name)
    native = native_evidence(directory, cases, directions)
    files = ['manifest.json', 'nia_ls.csv', 'combined.csv']
    if native['available']:
        files.append('results.csv')
    provenance = {name: dict(path=str(directory / name),
                            sha256=hashlib.sha256((directory / name).read_bytes()).hexdigest())
                  for name in files}
    return dict(directory=directory, manifest=manifest, cases=cases, directions=directions,
                nia=nia, combined=combined, native=native, provenance=provenance,
                stats=compute_statistics(list(combined.values()), directions))


def outcome(left, right):
    return 'win' if left > right else 'loss' if left < right else 'tie'


def aligned_group(baseline, candidate, names):
    names = sorted(names)
    left = compute_statistics([baseline['combined'][name] for name in names], baseline['directions'])
    right = compute_statistics([candidate['combined'][name] for name in names], candidate['directions'])
    wdl = dict.fromkeys(('win', 'tie', 'loss'), 0)
    for name in names:
        direction = baseline['directions'][name]
        wdl[outcome(_score(candidate['nia'][name]['目标值'], direction, name, 'candidate'),
                    _score(baseline['nia'][name]['目标值'], direction, name, 'baseline'))] += 1
    return dict(instances=len(names), baseline=left['all'], candidate=right['all'],
                candidate_vs_baseline=wdl,
                success_delta=right['all']['solved']['NIA_LS'] - left['all']['solved']['NIA_LS'])


def public_run(run):
    native = {key: value for key, value in run['native'].items() if key != 'rows'}
    native['error_instances'] = [name for name, row in run['native']['rows'].items()
                                 if row['status'] == 'error']
    native['diagnostic_instances'] = [name for name, row in run['native']['rows'].items() if row.get('error')]
    native['interrupted_instances'] = [name for name, row in run['native']['rows'].items()
                                       if row['status'] == 'interrupted']
    return dict(directory=str(run['directory']), selection_complete=run['manifest'].get('selection_complete', False),
                all=run['stats']['all'], all_unsolved=run['stats']['all_unsolved'], native=native)


def full_protocol(run, *, current_resources=False):
    manifest, native = run['manifest'], run['native']
    return (manifest.get('selection_complete') is True
            and len(run['cases']) == 6310
            and Counter(case['suite'] for case in run['cases'].values()) == {'original': 5744, 'new': 566}
            and Decimal(str(manifest.get('timeout', 0))) == 1200
            and (not current_resources or (
                sorted(manifest.get('nodes', [])) == ['node1', 'node2', 'node4', 'node5', 'server']
                and manifest.get('node_count') == 5
                and manifest.get('jobs_per_node') == 40
                and manifest.get('jobs') == 200))
            and manifest.get('scalar_target_enabled') is False
            and manifest.get('known_optimum_stop_enabled') is False
            and native['available'] and native['complete']
            and native['errors'] == native['interrupted'] == 0)


def compare_runs(baseline_directory, candidate_directory):
    """Return a JSON-compatible report and one row per union of canonical names."""
    baseline, candidate = load_run(baseline_directory), load_run(candidate_directory)
    common = baseline['cases'].keys() & candidate['cases'].keys()
    if not common:
        raise ValueError('Baseline and candidate have no common canonical instances')
    for name in common:
        direction = baseline['directions'][name]
        if candidate['directions'][name] != direction:
            raise ValueError(f'Original optimization direction changed: {name}')
        for solver in ('z3', 'optimathsat'):
            left, right = baseline['combined'][name], candidate['combined'][name]
            if (left[solver + '状态'] != right[solver + '状态']
                    or _score(left[solver + '目标值'], direction, name, solver)
                    != _score(right[solver + '目标值'], direction, name, solver)):
                raise ValueError(f'Fixed competitor changed between reports: {name}, {solver}')
    common_unsolved = {name for name in common
                       if all(run['combined'][name][solver + '状态'] in ('B', 'C')
                              for run in (baseline, candidate) for solver in SOLVERS)}
    baseline_unsolved = {name for name in common if baseline['nia'][name]['状态'] in ('B', 'C')}
    same_names = baseline['cases'].keys() == candidate['cases'].keys()
    full_coverage = same_names and len(common) == 6310
    # The user updated the required gain from 50 to 45 on 2026-10-30.
    required = baseline['stats']['all']['solved']['NIA_LS'] + 45
    loss_cap = baseline['stats']['all']['nia_wdl']['best_competitor']['loss']
    achieved_success = candidate['stats']['all']['solved']['NIA_LS'] >= required
    achieved_losses = candidate['stats']['all']['nia_wdl']['best_competitor']['loss'] <= loss_cap
    eligible = full_coverage and full_protocol(baseline) and full_protocol(candidate, current_resources=True)
    report = dict(
        schema_version=1,
        semantics=dict(success='A1+A2', objective='exact integer; direction-compatible infinity > finite > NA',
                       wdl='candidate or NIA_LS perspective; proof status/time never break objective ties',
                       common_unsolved='baseline, candidate, z3 and optimathsat all have status B/C',
                       summary_json_used=False),
        provenance=dict(baseline=baseline['provenance'], candidate=candidate['provenance'],
                        comparison_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        canonical_helper_sha256=hashlib.sha256((REFERENCE / 'report_results.py').read_bytes()).hexdigest(),
                        exact_comparison_helper_sha256=hashlib.sha256((REFERENCE / 'result_comparison.py').read_bytes()).hexdigest()),
        coverage=dict(baseline_selected=len(baseline['cases']), candidate_selected=len(candidate['cases']),
                      common_instances=len(common), same_selected_instances=same_names,
                      full_6310_coverage=full_coverage,
                      missing_candidate=sorted(baseline['cases'].keys() - candidate['cases'].keys()),
                      extra_candidate=sorted(candidate['cases'].keys() - baseline['cases'].keys())),
        baseline=public_run(baseline), candidate=public_run(candidate),
        common=aligned_group(baseline, candidate, common),
        common_unsolved=aligned_group(baseline, candidate, common_unsolved),
        baseline_unsolved_common=aligned_group(baseline, candidate, baseline_unsolved),
        goal=dict(required_success=required, maximum_best_competitor_losses=loss_cap,
                  observed_success_condition=achieved_success, observed_loss_condition=achieved_losses,
                  eligible_full_protocol=eligible,
                  full_goal_achieved=bool(eligible and achieved_success and achieved_losses),
                  decision=('full_goal_achieved' if eligible and achieved_success and achieved_losses else
                            'full_goal_not_achieved' if eligible else 'partial_or_unverified_no_full_goal_claim')))
    rows = []
    for name in sorted(baseline['cases'].keys() | candidate['cases'].keys()):
        row = dict(filename=name, direction=(baseline['directions'].get(name) or candidate['directions'][name]))
        for label, run in (('baseline', baseline), ('candidate', candidate)):
            own = run['nia'].get(name, {})
            native = run['native']['rows'].get(name, {})
            row.update({label + '_status': own.get('状态', ''), label + '_objective': own.get('目标值', ''),
                        label + '_native_status': native.get('status', ''),
                        label + '_error': native.get('error', '')})
            if name in run['combined']:
                combined = run['combined'][name]
                scores = {solver: _score(combined[solver + '目标值'], row['direction'], name, solver)
                          for solver in SOLVERS}
                row[label + '_vs_best_competitor'] = outcome(scores['NIA_LS'], max(scores['z3'], scores['optimathsat']))
            else:
                row[label + '_vs_best_competitor'] = ''
        row['candidate_vs_baseline'] = (outcome(
            _score(candidate['nia'][name]['目标值'], row['direction'], name, 'candidate'),
            _score(baseline['nia'][name]['目标值'], row['direction'], name, 'baseline')) if name in common else '')
        row['common_unsolved'] = name in common_unsolved
        row['baseline_unsolved'] = name in baseline_unsolved
        rows.append(row)
    return report, rows


def render_report(report):
    goal, coverage = report['goal'], report['coverage']
    lines = ['# Deep-mix comparison', '',
             f'Baseline: `{report["baseline"]["directory"]}`',
             f'Candidate: `{report["candidate"]["directory"]}`', '',
             f'Decision: **{goal["decision"]}**.', '',
             'Success = A1+A2. Objective comparisons use the original min/max direction, exact integers, '
             'compatible infinity above every finite value, and NA last; NA/NA ties. '
             'summary.json is never used.', '',
             f'Coverage: baseline {coverage["baseline_selected"]}; candidate {coverage["candidate_selected"]}; '
             f'common {coverage["common_instances"]}; missing candidate {len(coverage["missing_candidate"])}; '
             f'extra candidate {len(coverage["extra_candidate"])}.', '',
             f'Full goal requires success >= {goal["required_success"]}, '
             f'losses vs per-instance best competitor <= {goal["maximum_best_competitor_losses"]}, '
             'complete matching 6310-case manifests, the 1200s protocol, candidate resources on '
             'server/node1/node2/node4/node5 with 40 cases per node, and complete error-free native records.', '',
             '| Run / group | Cases | A1 | A2 | B | C | Success | Win | Tie | Loss |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    groups = [('baseline all', report['baseline']['all']), ('candidate all', report['candidate']['all']),
              ('baseline all three unsolved', report['baseline']['all_unsolved']),
              ('candidate all three unsolved', report['candidate']['all_unsolved'])]
    for key in ('common', 'common_unsolved', 'baseline_unsolved_common'):
        for label in ('baseline', 'candidate'):
            groups.append((f'{label} {key}', report[key][label]))
    for label, group in groups:
        states, wdl = group['states']['NIA_LS'], group['nia_wdl']['best_competitor']
        values = [label, group['instances'], *(states[s] for s in STATES), group['solved']['NIA_LS'],
                  *(wdl[s] for s in ('win', 'tie', 'loss'))]
        lines.append('| ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', 'WDL above is NIA_LS vs the better of the two fixed competitors. '
              'Common unsolved requires baseline, candidate and both competitors to be B/C. '
              'Each run also reports its own three-solver-unsolved group in JSON.', '',
              '| Group | Candidate vs baseline win | Tie | Loss | Success delta |',
              '| --- | --- | --- | --- | --- |']
    for key in ('common', 'common_unsolved', 'baseline_unsolved_common'):
        group = report[key]
        values = [key, *(group['candidate_vs_baseline'][s] for s in ('win', 'tie', 'loss')), group['success_delta']]
        lines.append('| ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', '| Native evidence | Available | Complete | Errors | Diagnostics | Interrupted |',
              '| --- | --- | --- | --- | --- | --- |']
    for label in ('baseline', 'candidate'):
        native = report[label]['native']
        values = [label, native['available'], native['complete'], native['errors'], native['diagnostics'], native['interrupted']]
        lines.append('| ' + ' | '.join(map(str, values)) + ' |')
    if not goal['eligible_full_protocol']:
        lines += ['', '**This is partial or unverified evidence and does not establish the full experimental goal.**']
    lines += ['', 'Native errors count status=error; nonempty diagnostic messages are listed separately, '
              'because an unsuccessful proof attempt may accompany a valid sat incumbent.', '',
              'File hashes, complete coverage lists, per-solver WDL and native error instance names are in '
              '`deepmix_compare.json`; every canonical instance is retained in `deepmix_cases.csv`.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-run', required=True, type=Path)
    parser.add_argument('--candidate-run', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        baseline, candidate = args.baseline_run.resolve(), args.candidate_run.resolve()
        if baseline == candidate:
            raise ValueError('Candidate must differ from baseline; the baseline is read-only')
        report, rows = compare_runs(baseline, candidate)
        output_names = ('deepmix_compare.json', 'deepmix_compare.md', 'deepmix_cases.csv')
        for name in output_names:
            output = candidate / name
            if output.is_symlink() or (output.exists() and not output.is_file()):
                raise ValueError(f'Report output must be a regular candidate file: {output}')
        rendered = render_report(report)
        report_results.atomic_write(candidate / output_names[0], json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        report_results.atomic_write(candidate / output_names[1], rendered)
        report_results.atomic_write(candidate / output_names[2], report_results.csv_text(list(rows[0]), rows), 'utf-8-sig')
        print(rendered)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'deepmix_compare.py: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
