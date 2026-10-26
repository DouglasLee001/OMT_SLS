#!/usr/bin/env python3
"""Export one completed NIA_LS run and compare it with a fixed competitor CSV."""
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
                  objective_value=best, prove_optimal=True,
                  optimality_stall_seconds=manifest['optimality_stall_seconds'],
                  optimality_timeout_seconds=manifest['optimality_timeout_seconds'],
                  optimality_attempts=int(attempts), optimality_seconds=float(checked_time(duration, name)),
                  optimality_status=status, elapsed_seconds=(proof_time if proof_time is not None else float(wall)),
                  time_to_best_seconds=(None if not row['time_to_best_seconds'] else
                                        float(checked_time(row['time_to_best_seconds'], name))),
                  time_to_optimality_seconds=proof_time,
                  optimality_proved_value=row.get('optimality_proved_value') or None)
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
        float(checked_time(manifest.get('timeout'), name)))


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


def load_nia_run(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    own_proof_mode = manifest.get('prove_optimal', False)
    if type(own_proof_mode) is not bool:
        raise ValueError('Invalid manifest prove_optimal setting')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or not cases or not completed_summary(summary, len(cases)):
        raise ValueError(f'Run is incomplete or interrupted: {directory}')
    case_map = {case_key(case): case for case in cases}
    if len(case_map) != len(cases):
        raise ValueError('Duplicate instance in run manifest')
    raw = read_csv(directory / 'results.csv', [
        'filename', 'category', 'suite', 'direction', 'status', 'best_value',
        'model_validated', 'time_to_best_seconds', 'wall_seconds'])
    if len(raw) != len(cases):
        raise ValueError('Run CSV does not contain every selected instance')
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


def render_report(stats, run, competitor, baseline_count, own_proof_mode=False):
    optimality_note = (
        '本轮启用独立有限最优性检查：NIA_LS 的 A2 只采用自身严格改进查询的 UNSAT 结果，'
        '不因达到对手已知最优值而标记 A2。脚本核对报告、模型标记、目标值与时间元数据，不独立回放 SMT 证明。'
        if own_proof_mode else
        'NIA_LS 的 A2 包括该轮记录的已证明最优标记，以及达到对手已证明的最优值'
        '（known_optimum_reached）。后者沿用既有实验口径。')
    lines = ['# NIA_LS 与对手结果比较', '', f'实验目录：`{run}`', '',
             f'固定对手基线：`{competitor}`（{baseline_count} 例）。仅比较本轮实际运行的实例。', '',
             '比较规则：无界优于所有有限值，NA 最差；有限值按各例原来的最小化或最大化方向比较，'
             '使用精确整数，不比较时间。并列均计为达到最好，NA 对 NA 计平。', '',
             '状态：A1 证明无界；A2 已确认有限最优；B 有可行解但未证明最优；C 未找到可行解。'
             '已解决数为 A1+A2。', '',
             optimality_note, '']
    for key, title in (('all', '全部实例'), ('all_unsolved', '三者均未解决的实例')):
        group = stats[key]
        lines += [f'## {title}：{group["instances"]} 例', '',
                  f'三者均为 NA：{group["all_na"]} 例。下面同时列出包含和排除这些并列的最好值数量。', '']
        lines += markdown_table(['求解器', '达到三者最好（含并列）', '排除全 NA 后', '已解决 A1+A2'], [
            [solver, group['best_including_na'][solver], group['best_excluding_na'][solver],
             group['solved'][solver]] for solver in SOLVERS])
        lines += ['', '状态数量：', '']
        lines += markdown_table(['求解器', 'A1', 'A2', 'B', 'C'], [
            [solver, *(group['states'][solver][state] for state in ('A1', 'A2', 'B', 'C'))]
            for solver in SOLVERS])
        lines += ['', 'NIA_LS 的胜平负：', '']
        lines += markdown_table(['比较对象', '胜', '平', '负'], [
            [label, *(group['nia_wdl'][opponent][outcome] for outcome in ('win', 'tie', 'loss'))]
            for opponent, label in [('z3', 'Z3'), ('optimathsat', 'OptiMathSAT'),
                                    ('best_competitor', '两个对手逐实例中较好的那个')]])
        lines += ['']
    lines += ['“三者均未解决”要求同一实例的三个状态全部为 B/C。', '',
              '导出时间：A1 使用证明无界的时间；A2/B 使用首次找到最终保留有限值的时间；'
              'C 使用该例实际完整运行时间（包括提前异常退出与超时清理耗时），不统一补成 1200 秒。', '',
              '结果文件：[NIA_LS 四列表](nia_ls.csv)、[三算法合并表](combined.csv)。', '']
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
    args = parser.parse_args(argv)
    try:
        run = (args.run or latest_complete_run()).resolve()
        if run.is_file():
            if run.name != 'results.csv':
                raise ValueError('The input file must be the run results.csv')
            run = run.parent
        nia, directions = load_nia_run(run)
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
        own_proof_mode = json.loads((run / 'manifest.json').read_text()).get('prove_optimal', False)
        report = render_report(stats, run, competitor_path, len(baseline), own_proof_mode)
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
