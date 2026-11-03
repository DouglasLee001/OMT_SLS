#!/usr/bin/env python3
"""Prepare and replay retained A1 bases without passing old ray directions.

The prepare phase independently checks each old certificate and saves only its
base. Replay invokes a native diagnostic driver, then independently validates
each newly constructed certificate with Python integers. It does not rerun the
outer optimization search and does not establish end-to-end performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time


TREE = Path(__file__).resolve().parents[1]
VERIFIER = TREE / 'reference/script/verify_optimize.py'


def load_verifier():
    spec = importlib.util.spec_from_file_location('ray_replay_verify', VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def structure_digest(native):
    # SOURCE is provenance, not a constraint. Keep variable identities/sorts,
    # literal and clause order, all coefficients, and the optimization direction.
    structure = {key: value for key, value in native.items() if key != 'source'}
    return digest(json.dumps(structure, sort_keys=True, separators=(',', ':')).encode())


def read_final(path):
    final = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.lstrip().startswith('{'):
                continue
            item = json.loads(line)
            if item.get('event') == 'final':
                final = item
    if final is None:
        raise ValueError('missing final event: ' + str(path))
    return final


def prepare(args):
    manifest_path = args.manifest.resolve()
    if manifest_path.exists():
        raise ValueError('refusing to replace existing manifest: ' + str(manifest_path))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    base_dir = manifest_path.parent / (manifest_path.stem + '-bases')
    base_dir.mkdir(exist_ok=False)
    verifier = load_verifier()
    cases, indexed, runs = [], {}, []
    for supplied in args.run:
        run = supplied.resolve()
        final = json.loads((run / 'finalized.json').read_text())
        if final.get('returncode') != 0:
            raise ValueError('source run did not finalize successfully: ' + str(run))
        source_manifest = json.loads((run / 'manifest.json').read_text())
        receipt = json.loads((run / 'embedded_receipt.json').read_text())
        with (run / 'results.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        unbounded = {row['filename']: row for row in rows if row['is_unbounded'] == 'true'}
        if len(rows) != len(source_manifest['cases']):
            raise ValueError('source CSV/manifest coverage mismatch: ' + str(run))
        found = set()
        for index, case in enumerate(source_manifest['cases']):
            filename = case['filename']
            if filename not in unbounded:
                continue
            suffix = digest(case['source'].encode())[:12]
            artifact = run / 'cases' / f'{index:04d}-{suffix}'
            if not artifact.is_dir():
                matches = list((run / 'cases').glob('*-' + suffix))
                if len(matches) != 1:
                    raise ValueError('ambiguous source case directory: ' + filename)
                artifact = matches[0]
            native_path = artifact / 'input.nia'
            native_bytes = native_path.read_bytes()
            native = verifier.parse_native(native_bytes.decode())
            final = read_final(artifact / 'native.stdout')
            if final.get('is_unbounded') is not True:
                raise ValueError('CSV A1 lacks final proof: ' + filename)
            certificate = final['unbounded_certificate']
            verifier.verify_unbounded_certificate(native, certificate)
            base = [str(int(x)) for x in certificate['base']]
            base_bytes = (str(len(base)) + '\n' + '\n'.join(base) + '\n').encode()
            native_hash, base_hash = digest(native_bytes), digest(base_bytes)
            structural_hash = structure_digest(native)
            key = (structural_hash, base_hash)
            provenance = dict(run=str(run), case=str(artifact), filename=filename,
                              original_seconds=float(unbounded[filename]['unbounded_seconds']))
            if key in indexed:
                indexed[key]['origins'].append(provenance)
            else:
                ident = f'{len(cases):04d}-{digest((native_hash + base_hash).encode())[:12]}'
                base_path = base_dir / (ident + '.txt')
                base_path.write_bytes(base_bytes)
                record = dict(id=ident, filename=filename, native=str(native_path),
                              native_sha256=native_hash, base=str(base_path), base_sha256=base_hash,
                              structure_sha256=structural_hash,
                              variables=len(base), origins=[provenance])
                cases.append(record)
                indexed[key] = record
            found.add(filename)
        if found != set(unbounded):
            raise ValueError('some source A1 cases absent from manifest: ' + str(run))
        runs.append(dict(path=str(run), rows=len(rows), a1=len(unbounded),
                         solver_sha256=receipt['solver_sha256']))
        print(json.dumps(dict(prepared_run=str(run), source_a1=len(found),
                              unique_bases_so_far=len(cases)), ensure_ascii=False), flush=True)
    document = dict(schema='omt_unbounded_base_replay_v1', runs=runs, cases=cases,
                    unique_instances=len({origin['filename'] for case in cases for origin in case['origins']}),
                    unique_bases=len(cases), old_directions_used_as_seeds=False,
                    baseline_certificates_independently_verified=True,
                    verifier_sha256=digest(VERIFIER.read_bytes()))
    manifest_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(manifest=str(manifest_path), unique_bases=len(cases),
                          unique_instances=document['unique_instances']), ensure_ascii=False), flush=True)


def replay_one(case, driver, seconds, output, verifier):
    began = time.monotonic()
    record = dict(id=case['id'], filename=case['filename'], status='error', proved=False,
                  certificate_valid=False, proof_seconds='', queries='', wall_seconds='', error='')
    record.update(direction_backend='', direction_steps='', guided_attempts='', fixed_set_adjustments='')
    detail = dict(case_id=case['id'], filename=case['filename'], command=[str(driver), case['native'], case['base'], str(seconds)])
    try:
        native_bytes = Path(case['native']).read_bytes()
        if digest(native_bytes) != case['native_sha256'] or digest(Path(case['base']).read_bytes()) != case['base_sha256']:
            raise ValueError('frozen native/base fingerprint changed')
        result = subprocess.run(detail['command'], capture_output=True, text=True,
                                timeout=max(10.0, seconds + 5.0))
        detail.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        if result.returncode:
            raise ValueError(f'driver exited {result.returncode}: {result.stderr[:1200]}')
        payload = json.loads(result.stdout)
        detail['result'] = payload
        record.update(status=payload['status'], proved=payload['proved'],
                      proof_seconds=payload['seconds'], queries=payload['queries'])
        for field in ('direction_backend', 'direction_steps', 'guided_attempts', 'fixed_set_adjustments'):
            record[field] = payload.get(field, '')
        if payload['proved']:
            if payload['status'] != 'proved' or payload['queries'] <= 0:
                raise ValueError('inconsistent proof status')
            if payload['seconds'] >= seconds:
                raise ValueError('proof returned after its supplied direction budget')
            verifier.verify_unbounded_certificate(verifier.parse_native(native_bytes.decode()), payload['certificate'])
            expected_base = Path(case['base']).read_text().split()[1:]
            if [str(int(x)) for x in payload['certificate']['base']] != expected_base:
                raise ValueError('new certificate changed the supplied base')
            record['certificate_valid'] = True
        elif payload['certificate'] is not None:
            raise ValueError('unsuccessful probe emitted a certificate')
    except Exception as error:
        record.update(status='error', proved=False, certificate_valid=False, error=str(error))
    record['wall_seconds'] = time.monotonic() - began
    detail['row'] = record
    (output / 'cases' / (case['id'] + '.json')).write_text(json.dumps(detail, ensure_ascii=False, indent=2) + '\n')
    return record


def replay(args):
    manifest_path = args.manifest.resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get('schema') != 'omt_unbounded_base_replay_v1':
        raise ValueError('unsupported replay manifest')
    cases = manifest['cases']
    if args.limit is not None:
        cases = cases[:args.limit]
    output, driver = args.output.resolve(), args.driver.resolve()
    if not driver.is_file():
        raise ValueError('missing replay driver: ' + str(driver))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'cases').mkdir()
    (output / 'manifest.json').write_bytes(raw)
    receipt = dict(driver=str(driver), driver_sha256=digest(driver.read_bytes()),
                   runner_sha256=digest(Path(__file__).read_bytes()),
                   manifest=str(manifest_path), manifest_sha256=digest(raw),
                   seconds_per_probe=args.seconds, jobs=args.jobs, selected=len(cases),
                   complete_manifest=len(cases) == len(manifest['cases']),
                   old_directions_used_as_seeds=False, source_runs=manifest['runs'])
    (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    verifier, results = load_verifier(), []
    fields = ['id', 'filename', 'status', 'proved', 'certificate_valid', 'proof_seconds', 'queries',
              'direction_backend', 'direction_steps', 'guided_attempts', 'fixed_set_adjustments',
              'wall_seconds', 'error']
    with (output / 'results.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(replay_one, case, driver, args.seconds, output, verifier) for case in cases]
            for future in as_completed(futures):
                row = future.result()
                writer.writerow(row)
                stream.flush()
                results.append(row)
                if len(results) % 100 == 0 or len(results) == len(cases):
                    print(json.dumps(dict(completed=len(results), total=len(cases),
                                          proved=sum(x['certificate_valid'] for x in results)), ensure_ascii=False), flush=True)
    result_by_id = {row['id']: row for row in results}
    coverage = []
    for run in manifest['runs']:
        related = [case for case in cases if any(origin['run'] == run['path'] for origin in case['origins'])]
        source_count = sum(sum(origin['run'] == run['path'] for origin in case['origins']) for case in related)
        good = sum(sum(origin['run'] == run['path'] for origin in case['origins'])
                   for case in related if result_by_id[case['id']]['certificate_valid'])
        coverage.append(dict(run=run['path'], source_a1=run['a1'], selected_source_bases=source_count,
                             proved_source_bases=good, all_preserved=source_count == good == run['a1']))
    summary = dict(receipt)
    summary.update(selected=len(cases), completed=len(results), statuses=dict(Counter(x['status'] for x in results)),
                   direction_backends=dict(Counter(x['direction_backend'] for x in results)),
                   independently_verified=sum(x['certificate_valid'] for x in results),
                   failed=sum(not x['certificate_valid'] for x in results), by_run=coverage,
                   all_preserved=receipt['complete_manifest'] and all(item['all_preserved'] for item in coverage),
                   diagnostic_scope='fixed-base proof replay; not an end-to-end optimizer experiment')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary['all_preserved'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='freeze verified A1 bases, excluding old directions')
    prep.add_argument('--run', action='append', type=Path, required=True)
    prep.add_argument('--manifest', type=Path, required=True)
    run = commands.add_parser('run', help='construct and independently verify new rays')
    run.add_argument('--manifest', type=Path, required=True)
    run.add_argument('--driver', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--jobs', type=int, default=8)
    run.add_argument('--seconds', type=float, default=5.0)
    run.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.command == 'run' and (args.jobs <= 0 or not 0 < args.seconds < float('inf') or (args.limit is not None and args.limit <= 0)):
        parser.error('jobs, seconds and limit must be positive and seconds finite')
    return prepare(args) if args.command == 'prepare' else replay(args)


if __name__ == '__main__':
    raise SystemExit(main())
