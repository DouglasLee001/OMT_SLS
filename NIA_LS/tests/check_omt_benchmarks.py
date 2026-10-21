#!/usr/bin/env python3
"""Check SMT2 conversion AND loading into the actual NIA_LS structures.

This does not run local search or either competitor's optimization algorithm.
Temporary intermediate files are deleted unless --keep-converted is supplied.
"""
import argparse
import concurrent.futures
import csv
import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import time

BASE = Path(__file__).resolve().parents[1]
PROJECT = BASE.parent


def cases(args):
    if args.input:
        for supplied in args.input:
            path = Path(supplied).resolve()
            paths = sorted(path.rglob('*.smt2')) if path.is_dir() else [path]
            for item in paths:
                yield 'supplied', str(item)
        return
    original_root = Path(args.original_root)
    if args.all_original:
        for item in sorted((original_root / 'QF_NIA').rglob('*.smt2')):
            yield 'original', str(item)
    else:
        with (PROJECT / 'result/original/original.csv').open(newline='') as stream:
            for row in csv.DictReader(stream):
                yield 'original', str(original_root / row['filename'])
    for family in ('portfolio', 'cyclic', 'packing', 'design'):
        for item in sorted((PROJECT / 'new_benchmark' / family / 'instance').glob('*.smt2')):
            yield family, str(item)


def run_one(case, args):
    category, filename = case
    row = dict(category=category, filename=filename, status='', seconds='',
               direction='', variables='', literals='', clauses='',
               objective_terms='', max_degree='', error='')
    start = time.monotonic()
    stage = 'conversion'
    try:
        with tempfile.TemporaryDirectory(prefix='nia-omt-check-') as work:
            output = Path(work) / 'input.nia'
            if args.keep_converted:
                import hashlib
                output = Path(args.keep_converted) / (hashlib.sha256(filename.encode()).hexdigest()[:20] + '.nia')
                output.parent.mkdir(parents=True, exist_ok=True)
            command = ['python3', str(BASE / 'converter/omt_to_ls.py'), filename, '-o', str(output)]
            converted = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
            if converted.returncode:
                row['status'] = 'conversion_error'
                row['error'] = (converted.stderr or converted.stdout).strip()[-3000:]
                return row
            stage = 'native_import'
            loaded = subprocess.run([args.native_binary, str(output), '--import-only'],
                                    capture_output=True, text=True, timeout=args.timeout)
            if loaded.returncode:
                row['status'] = 'import_error'
                row['error'] = (loaded.stderr or loaded.stdout).strip()[-3000:]
                return row
            summary = json.loads(loaded.stdout)
            if summary.get('native_solver_state') is not True or summary.get('search_started') is not False:
                raise ValueError('not a native import-only result')
            if summary['status'] not in ('imported', 'preprocessing_unsat'):
                raise ValueError('unexpected importer status')
            row['status'] = summary['status']
            for key in ('direction', 'variables', 'literals', 'clauses', 'objective_terms', 'max_degree'):
                row[key] = summary[key]
    except subprocess.TimeoutExpired:
        row['status'] = stage + '_timeout'
        row['error'] = 'stage exceeded %g seconds' % args.timeout
    except Exception as exc:
        row['status'] = stage + '_error'
        row['error'] = str(exc)
    finally:
        row['seconds'] = '%.3f' % (time.monotonic() - start)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', action='append', help='check this SMT2 file/directory instead of the default experimental set')
    parser.add_argument('--original-root', default='/data/shared/lbh/OMT/benchmarks')
    parser.add_argument('--all-original', action='store_true', help='all QF_NIA files, rather than the 5744 experimental files')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--report', default=str(BASE / 'validation/omt_import_coverage.csv'))
    parser.add_argument('--keep-converted', help='optional intermediate output directory')
    args = parser.parse_args()
    if args.jobs < 1 or args.timeout <= 0:
        parser.error('--jobs and --timeout must be positive')
    inputs = list(cases(args))
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    fields = ['category', 'filename', 'status', 'seconds', 'direction', 'variables',
              'literals', 'clauses', 'objective_terms', 'max_degree', 'error']
    # A build may replace src/nia_ls during a long audit. Use one complete,
    # executable snapshot for every case rather than opening a relinking file.
    with tempfile.TemporaryDirectory(prefix='nia-omt-audit-binary-') as session, report.open('w', newline='') as stream, concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        args.native_binary = str(Path(session) / 'nia_ls')
        shutil.copy2(BASE / 'src/nia_ls', args.native_binary)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        futures = [pool.submit(run_one, case, args) for case in inputs]
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = future.result()
            writer.writerow(row)
            stream.flush()
            counts[row['status']] = counts.get(row['status'], 0) + 1
            if number % 100 == 0 or number == len(inputs):
                print(json.dumps({'completed': number, 'total': len(inputs), 'counts': counts}), flush=True)
    print('report=' + str(report.resolve()))
    return 0 if all(k in ('imported', 'preprocessing_unsat') for k in counts) else 1


if __name__ == '__main__':
    raise SystemExit(main())
