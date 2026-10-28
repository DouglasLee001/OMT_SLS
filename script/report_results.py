#!/usr/bin/env python3
"""Export a NIA_LS run; incomplete full-row runs require explicit opt-in."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile

if __package__:
    from .result_comparison import compute_statistics
    from .smtlib import parse
else:
    from result_comparison import compute_statistics
    from smtlib import parse


ROOT = Path(__file__).resolve().parents[1]
OPTIMIZE_ROOT = Path('/data/shared/lbh/OMT/result')
COMPETITOR = ROOT / 'result/competitor.csv'
NIA_FIELDS = ['文件名', '状态', '目标值', '时间（秒）']
BASE_FIELDS = ['文件名', 'z3状态', 'z3目标值', 'z3时间（秒）',
               'optimathsat状态', 'optimathsat目标值', 'optimathsat时间（秒）']
COMBINED_FIELDS = BASE_FIELDS + ['NIA_LS状态', 'NIA_LS目标值', 'NIA_LS时间（秒）']
STATUS_NAMES = {'证明无界': 'A1', '找到最优解并证明最优': 'A2',
                '找到解但未证明最优性': 'B', '未找到可行解': 'C'}
SOLVERS = ('z3', 'optimathsat', 'NIA_LS')


def read_csv(path, required):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f'Missing required columns in {path}')
        rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise ValueError(f'Empty or malformed CSV: {path}')
    return rows


def true(value):
    return value is True or (isinstance(value, str) and value.strip().lower() == 'true')


def integer_value(value):
    text = str(value)
    forms = parse(text)

    def normalize(form):
        if isinstance(form, str) and re.fullmatch(r'[+-]?[0-9]+', form):
            number = Decimal(form)
            return '0' if number.is_zero() else format(number, 'f')
        if isinstance(form, list) and len(form) == 2 and form[0] == '-':
            number = normalize(form[1])
            return number[1:] if number.startswith('-') else ('0' if number == '0' else '-' + number)
        raise ValueError(f'Expected a finite integer, got {text[:100]!r}')

    if len(forms) != 1:
        raise ValueError(f'Expected a finite integer, got {text[:100]!r}')
    return normalize(forms[0])


def checked_time(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'Missing or invalid time for {name}') from None
    if not number.is_finite() or number < 0:
        raise ValueError(f'Missing or invalid time for {name}')
    return str(value)


def case_key(case):
    return tuple(case.get(field) for field in ('suite', 'category', 'filename'))


def case_name(case):
    name = case.get('filename')
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Invalid instance filename in run manifest')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in name.split('/') or path.suffix != '.smt2':
        raise ValueError(f'Invalid instance filename: {name}')
    if case.get('suite') == 'original':
        return path.as_posix()
    category = case.get('category')
    if (case.get('suite') != 'new' or not isinstance(category, str) or not category
            or category in ('.', '..') or PurePosixPath(category).name != category
            or path.name != name):
        raise ValueError(f'Invalid generated instance identity: {name}')
    return f'new_benchmark/{category}/instance/{name}'


def completed_summary(summary, count):
    return (summary.get('selected') == summary.get('completed') == count
            and summary.get('complete', True) is True
            and not summary.get('interrupted', False)
            and not summary.get('statuses', {}).get('interrupted', 0))


def own_optimality_report(row, manifest, name, best, wall):
    """Accept own finite proofs only with the opt-in protocol and matching budget."""
    if __package__:
        from .verify_optimize import OPTIMALITY_ADAPTIVE_FIELDS, validate_optimality_metadata
    else:
        from verify_optimize import OPTIMALITY_ADAPTIVE_FIELDS, validate_optimality_metadata
    if (manifest.get('scalar_target_enabled') is not False
            or manifest.get('known_optimum_stop_enabled') is not False
            or row.get('prove_optimal') != 'true'):
        raise ValueError(f'Independent optimality mode has inconsistent stopping settings: {name}')
    for field in ('optimality_stall_seconds', 'optimality_timeout_seconds'):
        expected = manifest.get(field)
        if (not isinstance(expected, (int, float)) or isinstance(expected, bool)
                or not Decimal(str(expected)).is_finite() or expected <= 0
                or Decimal(checked_time(row.get(field), name)) != Decimal(str(expected))):
            raise ValueError(f'Inconsistent optimality configuration for {name}: {field}')
    window = manifest.get('optimality_search_window_seconds', 0)
    if (not isinstance(window, (int, float)) or isinstance(window, bool)
            or not Decimal(str(window)).is_finite() or window < 0
            or Decimal(checked_time(row.get('optimality_search_window_seconds') or '0', name)) != Decimal(str(window))):
        raise ValueError(f'Inconsistent optimality configuration for {name}: optimality_search_window_seconds')
    resume = manifest.get('optimality_resume', False)
    csv_resume = row.get('optimality_resume', 'false') or 'false'
    if type(resume) is not bool or csv_resume not in ('true', 'false') or csv_resume != str(resume).lower():
        raise ValueError(f'Inconsistent optimality configuration for {name}: optimality_resume')
    burst = manifest.get('optimality_burst', False)
    csv_burst = row.get('optimality_burst', 'false')
    if type(burst) is not bool or csv_burst not in ('true', 'false') or csv_burst != str(burst).lower():
        raise ValueError(f'Inconsistent optimality configuration for {name}: optimality_burst')
    cutset = manifest.get('optimality_cutset', False)
    csv_cutset = row.get('optimality_cutset', 'false')
    if type(cutset) is not bool or csv_cutset not in ('true', 'false') or csv_cutset != str(cutset).lower():
        raise ValueError(f'Inconsistent optimality configuration for {name}: optimality_cutset')
    balanced = manifest.get('optimality_balanced', False)
    csv_balanced = row.get('optimality_balanced', 'false')
    if (type(balanced) is not bool or csv_balanced not in ('true', 'false')
            or csv_balanced != str(balanced).lower() or (balanced and window > 0)):
        raise ValueError(f'Inconsistent optimality configuration for {name}: optimality_balanced')
    attempts, duration, status = (row.get(field) for field in
                                  ('optimality_attempts', 'optimality_seconds', 'optimality_status'))
    if attempts == duration == status == '':
        if (true(row.get('is_optimal')) or row.get('optimality_proved_value')
                or row.get('time_to_optimality_seconds') or row.get('termination') == 'optimality_proved'
                or any(row.get(field) not in (None, '') for field in OPTIMALITY_ADAPTIVE_FIELDS)):
            raise ValueError(f'Optimality claim lacks final proof metadata: {name}')
        return False
    if not isinstance(attempts, str) or re.fullmatch(r'[0-9]+', attempts) is None:
        raise ValueError(f'Invalid optimality attempts for {name}')
    completion = row.get('time_to_optimality_seconds')
    proof_time = None if not completion else float(checked_time(completion, name))
    if proof_time is not None and Decimal(str(proof_time)) > wall + Decimal('0.000001'):
        raise ValueError(f'Optimality proof occurs after actual run duration: {name}')
    native = dict(event='final', direction=row['direction'], status=row['status'],
                  termination=row['termination'], model_validated=true(row['model_validated']),
                  is_optimal=true(row['is_optimal']), is_unbounded=true(row.get('is_unbounded')),
                  objective_value=best, prove_optimal=True, optimality_resume=resume, optimality_burst=burst,
                  optimality_cutset=cutset,
                  optimality_balanced=balanced,
                  optimality_stall_seconds=manifest['optimality_stall_seconds'],
                  optimality_timeout_seconds=manifest['optimality_timeout_seconds'],
                  optimality_attempts=int(attempts), optimality_seconds=float(checked_time(duration, name)),
                  optimality_status=status, elapsed_seconds=(proof_time if proof_time is not None else float(wall)),
                  time_to_best_seconds=(None if not row['time_to_best_seconds'] else
                                        float(checked_time(row['time_to_best_seconds'], name))),
                  time_to_optimality_seconds=proof_time,
                  optimality_proved_value=row.get('optimality_proved_value') or None)
    if row.get('optimality_search_window_seconds') not in (None, ''):
        native['optimality_search_window_seconds'] = float(checked_time(row['optimality_search_window_seconds'], name))
    adaptive = [row.get(field) for field in OPTIMALITY_ADAPTIVE_FIELDS]
    if any(value not in (None, '') for value in adaptive):
        if any(value in (None, '') for value in adaptive):
            raise ValueError(f'Incomplete adaptive optimality metadata for {name}')
        schedule, next_timeout, accepted = adaptive
        if re.fullmatch(r'[0-9]+', accepted) is None:
            raise ValueError(f'Invalid adaptive SAT-model count for {name}')
        native.update(optimality_schedule=schedule,
                      optimality_next_timeout_seconds=float(checked_time(next_timeout, name)),
                      optimality_sat_models_accepted=int(accepted))
    return validate_optimality_metadata(
        native, True, manifest['optimality_stall_seconds'], manifest['optimality_timeout_seconds'],
        None if best is None else int(Decimal(best)), row['direction'],
        float(checked_time(manifest.get('timeout'), name)), search_window=window, resume=resume,
        burst=burst, cutset=cutset, balanced=balanced)


def latest_complete_run():
    candidates = []
    for directory in OPTIMIZE_ROOT.iterdir() if OPTIMIZE_ROOT.exists() else []:
        if not directory.is_dir():
            continue
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
            summary = json.loads((directory / 'summary.json').read_text())
            cases = manifest.get('cases')
            if (not isinstance(cases, list) or not cases
                    or not completed_summary(summary, len(cases))
                    or not (directory / 'results.csv').is_file()):
                continue
            started = datetime.fromisoformat(manifest['started_utc'])
            if started.tzinfo is None:
                continue
            candidates.append((started.timestamp(), str(directory), directory))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        raise ValueError(f'No completed NIA_LS experiment found under {OPTIMIZE_ROOT}')
    return max(candidates)[2]


def load_nia_run(directory, *, allow_incomplete=False):
    manifest = json.loads((directory / 'manifest.json').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    own_proof_mode = manifest.get('prove_optimal', False)
    if type(own_proof_mode) is not bool:
        raise ValueError('Invalid manifest prove_optimal setting')
    resume = manifest.get('optimality_resume', False)
    if type(resume) is not bool or (resume and not own_proof_mode):
        raise ValueError('Invalid manifest optimality_resume setting')
    burst = manifest.get('optimality_burst', False)
    if type(burst) is not bool or (burst and not own_proof_mode):
        raise ValueError('Invalid manifest optimality_burst setting')
    if summary.get('optimality_burst', False) is not burst:
        raise ValueError('Summary/manifest optimality_burst mismatch')
    cutset = manifest.get('optimality_cutset', False)
    if type(cutset) is not bool or (cutset and not own_proof_mode):
        raise ValueError('Invalid manifest optimality_cutset setting')
    if summary.get('optimality_cutset', False) is not cutset:
        raise ValueError('Summary/manifest optimality_cutset mismatch')
    balanced = manifest.get('optimality_balanced', False)
    if type(balanced) is not bool or (balanced and not own_proof_mode):
        raise ValueError('Invalid manifest optimality_balanced setting')
    if summary.get('optimality_balanced', False) is not balanced:
        raise ValueError('Summary/manifest optimality_balanced mismatch')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError(f'Run is incomplete or interrupted: {directory}')
    if not completed_summary(summary, len(cases)):
        full_rows_recorded = summary.get('selected') == summary.get('completed') == len(cases)
        valid_flag = 'complete' not in summary or type(summary['complete']) is bool
        if not (allow_incomplete and full_rows_recorded and valid_flag):
            raise ValueError(f'Run is incomplete or interrupted: {directory}')
    case_map = {case_key(case): case for case in cases}
    if len(case_map) != len(cases):
        raise ValueError('Duplicate instance in run manifest')
    raw = read_csv(directory / 'results.csv', [
        'filename', 'category', 'suite', 'direction', 'status', 'best_value',
        'model_validated', 'time_to_best_seconds', 'wall_seconds'])
    if len(raw) != len(cases):
        raise ValueError('Run CSV does not contain every selected instance')
    if not allow_incomplete and any(row['status'] == 'interrupted' for row in raw):
        raise ValueError(f'Run is incomplete or interrupted: {directory}')
    result, directions, seen_keys = {}, {}, set()
    for row in raw:
        key = case_key(row)
        if key not in case_map or key in seen_keys:
            raise ValueError(f'Missing or duplicate manifest alignment: {row.get("filename")}')
        seen_keys.add(key)
        case = case_map[key]
        name = case_name(case)
        direction = case.get('direction')
        if direction not in ('min', 'max') or direction != row['direction']:
            raise ValueError(f'Missing or inconsistent optimization direction: {name}')
        if name in result:
            raise ValueError(f'Duplicate instance name: {name}')
        best = integer_value(row['best_value']) if row['best_value'] else None
        wall = Decimal(checked_time(row['wall_seconds'], name))
        if best is not None and not true(row['model_validated']):
            raise ValueError(f'Incumbent model was not validated: {name}')
        csv_resume = row.get('optimality_resume', 'false') or 'false'
        if csv_resume not in ('true', 'false') or csv_resume != str(resume).lower():
            raise ValueError(f'CSV/manifest optimality_resume mismatch: {name}')
        csv_burst = row.get('optimality_burst', 'false')
        if csv_burst not in ('true', 'false') or csv_burst != str(burst).lower():
            raise ValueError(f'CSV/manifest optimality_burst mismatch: {name}')
        csv_cutset = row.get('optimality_cutset', 'false')
        if csv_cutset not in ('true', 'false') or csv_cutset != str(cutset).lower():
            raise ValueError(f'CSV/manifest optimality_cutset mismatch: {name}')
        csv_balanced = row.get('optimality_balanced', 'false')
        if csv_balanced not in ('true', 'false') or csv_balanced != str(balanced).lower():
            raise ValueError(f'CSV/manifest optimality_balanced mismatch: {name}')
        if true(row.get('prove_optimal')) != own_proof_mode:
            raise ValueError(f'CSV/manifest independent optimality mode mismatch: {name}')
        independently_proven = own_optimality_report(row, manifest, name, best, wall) if own_proof_mode else False
        if true(row.get('is_unbounded')):
            if (row['status'] != 'sat' or row.get('unbounded_status') != 'proved'
                    or row.get('termination') != 'unbounded_proved' or best is None):
                raise ValueError(f'Unconfirmed or conflicting unbounded report: {name}')
            state, value = 'A1', '-oo' if direction == 'min' else '+oo'
            when = row.get('time_to_unbounded_seconds')
        elif best is not None:
            state, value, when = 'B', best, row['time_to_best_seconds']
            if own_proof_mode:
                if independently_proven:
                    state = 'A2'
            elif true(row.get('known_optimum_reached')):
                if (row['status'] != 'sat' or best != integer_value(row.get('known_optimum_value'))
                        or best != integer_value(case.get('known_optimum_value'))):
                    raise ValueError(f'Recorded known optimum does not match incumbent: {name}')
                state = 'A2'
            elif true(row.get('is_optimal')):
                if row['status'] != 'sat':
                    raise ValueError(f'Optimality report without completed solve: {name}')
                state = 'A2'
        else:
            if true(row.get('known_optimum_reached')) or true(row.get('is_optimal')):
                raise ValueError(f'Finite optimum report without incumbent: {name}')
            state, value, when = 'C', 'NA', row['wall_seconds']
        when = checked_time(when, name)
        # Native event and wall timestamps are recorded to six decimal places.
        if Decimal(when) > wall + Decimal('0.000001'):
            raise ValueError(f'Result event occurs after the actual run duration: {name}')
        result[name] = dict(zip(NIA_FIELDS, (name, state, value, when)))
        directions[name] = direction
    return result, directions


def load_competitors(path):
    result = {}
    for row in read_csv(path, BASE_FIELDS):
        name = row['文件名']
        if not name or name in result:
            raise ValueError(f'Empty or duplicate instance name in competitor CSV: {name}')
        normalized = {'文件名': name}
        for solver in ('z3', 'optimathsat'):
            raw_state = row[solver + '状态']
            state = STATUS_NAMES.get(raw_state, raw_state)
            if state not in ('A1', 'A2', 'B', 'C'):
                raise ValueError(f'Unknown competitor status for {name}: {raw_state}')
            value = row[solver + '目标值'].strip()
            value = {'oo': '+oo', '(- oo)': '-oo', '(* (- 1) oo)': '-oo'}.get(value, value)
            if value not in ('-oo', '+oo', 'NA'):
                value = integer_value(value)
            if ((state == 'A1' and value not in ('-oo', '+oo'))
                    or (state == 'C' and value != 'NA')
                    or (state in ('A2', 'B') and value in ('-oo', '+oo', 'NA'))):
                raise ValueError(f'Competitor status/value disagreement: {name}, {solver}')
            normalized.update({solver + '状态': state, solver + '目标值': value,
                               solver + '时间（秒）': checked_time(row[solver + '时间（秒）'], name)})
        result[name] = normalized
    return result


def markdown_table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |',
            '| ' + ' | '.join('---' for _ in headers) + ' |',
            *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]]


def render_report(stats, run, warning=None):
    lines = ['# NIA_LS 与对手结果比较', '', f'实验目录：`{run}`', '',
             'A1：证明无界；A2：确认最优；B：有解未证明；C：未找到可行解。'
             '胜平负均为 NIA_LS 对比所列对手。', '',
             '目标值按各例 min/max 方向比较：无界优于有限值，NA 最差，同值（含 NA/NA）计平；'
             '不使用时间或证明状态打破目标值相同的平局。', '',
             '## 1. 求解状态统计', '']
    if warning:
        lines[2:2] = [f'> **未完整完成**：{warning}', '']
    lines += markdown_table(['求解器', 'A1', 'A2', 'B', 'C',
                             '成功求解个数(A1+A2)', '未成功求解个数(B+C)'], [
        [label, *(stats['all']['states'][solver][state] for state in ('A1', 'A2', 'B', 'C')),
         stats['all']['solved'][solver],
         stats['all']['states'][solver]['B'] + stats['all']['states'][solver]['C']]
        for solver, label in [('z3', 'Z3'), ('optimathsat', 'OptiMathSAT'), ('NIA_LS', 'NIA_LS')]])
    for number, key, title in ((2, 'all', '全部实例'),
                               (3, 'all_unsolved', '三者均未成功求解')):
        group = stats[key]
        lines += ['', f'## {number}. 目标值胜平负（{title}：{group["instances"]} 例）', '']
        if key == 'all_unsolved':
            lines += ['仅保留三个求解器状态都为 B/C 的实例。', '']
        lines += markdown_table(['比较对象', '胜', '平', '负'], [
            [label, *(group['nia_wdl'][opponent][outcome] for outcome in ('win', 'tie', 'loss'))]
            for opponent, label in [('z3', 'Z3'), ('optimathsat', 'OptiMathSAT'),
                                    ('best_competitor', '两个对手逐实例优胜者')]])
    lines += ['']
    return '\n'.join(lines)


def atomic_write(path, text, encoding='utf-8'):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding=encoding, newline='',
                                         prefix='.' + path.stem + '-', suffix='.tmp',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def csv_text(fields, rows):
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', nargs='?', type=Path,
                        help=f'Run directory or results.csv; default: latest complete run under {OPTIMIZE_ROOT}')
    parser.add_argument('--competitor', type=Path, default=COMPETITOR)
    parser.add_argument('--output', type=Path, help='Report directory; default: the input run directory')
    parser.add_argument('--allow-incomplete', action='store_true',
                        help='report an explicitly named incomplete run with all selected rows retained')
    args = parser.parse_args(argv)
    try:
        if args.allow_incomplete and args.run is None:
            raise ValueError('--allow-incomplete requires an explicit run directory')
        run = (args.run or latest_complete_run()).resolve()
        if run.is_file():
            if run.name != 'results.csv':
                raise ValueError('The input file must be the run results.csv')
            run = run.parent
        nia, directions = load_nia_run(run, allow_incomplete=args.allow_incomplete)
        competitor_path = args.competitor.resolve()
        baseline = load_competitors(competitor_path)
        missing = nia.keys() - baseline.keys()
        if missing:
            raise ValueError(f'{len(missing)} run instances missing from competitor.csv: {sorted(missing)[:3]}')
        combined = []
        for name in sorted(nia):
            row = dict(baseline[name])
            row.update({'NIA_LS' + field: nia[name][field] for field in NIA_FIELDS[1:]})
            combined.append(row)
        stats = compute_statistics(combined, directions)
        warning = None
        if args.allow_incomplete:
            summary = json.loads((run / 'summary.json').read_text())
            retained = read_csv(run / 'results.csv', ['status'])
            interrupted = sum(row['status'] == 'interrupted' for row in retained)
            if not completed_summary(summary, len(nia)) or interrupted:
                warning = (f'已保留 {len(nia)}/{len(nia)} 条记录，其中 {interrupted} 个实例提前中断。'
                           '以下按已验证的最好结果统计，不表示全部跑满时限；原始完成状态不变。')
        report = render_report(stats, run, warning=warning)
        output = (args.output or run).resolve()
        if competitor_path in {output / name for name in ('nia_ls.csv', 'combined.csv', 'summary.md')}:
            raise ValueError('Output would overwrite the fixed competitor baseline')
        output.mkdir(parents=True, exist_ok=True)
        atomic_write(output / 'nia_ls.csv', csv_text(NIA_FIELDS, [nia[name] for name in sorted(nia)]), 'utf-8-sig')
        atomic_write(output / 'combined.csv', csv_text(COMBINED_FIELDS, combined), 'utf-8-sig')
        atomic_write(output / 'summary.md', report)
        print(report)
        print(f'Results: {output}')
        return 0
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        print(f'report_results.sh: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
