#!/usr/bin/env python3
"""Compare retained native encodings; does not run either solver or converter."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def audit(run, phase):
    manifest = run / 'manifest.json'
    if digest(manifest.read_bytes()) != (run / 'manifest.sha256').read_text().strip():
        raise ValueError('frozen manifest changed')
    plan = json.loads(manifest.read_text())
    encodings = {'baseline': {}, 'embedded': {}}
    indices = set()
    for task in plan['tasks']:
        if task['phase'] != phase:
            continue
        for local_index, index in enumerate(task['indices']):
            indices.add(index)
            case = plan['cases'][index]
            name = f'{local_index:04d}-' + digest(case['source'].encode())[:12]
            path = run / 'tasks' / task['id'] / 'run/cases' / name / 'input.nia'
            if path.is_file():
                contents = path.read_bytes()
                # C++ exports are visible before close: never hash a partial IR.
                if contents.startswith(b'NIA_LS_OMT 1\n') and contents.endswith(b'\nEND\n'):
                    encodings[task['variant']][index] = digest(contents)
    rows = []
    for index in sorted(indices):
        case = plan['cases'][index]
        left = encodings['baseline'].get(index, '')
        right = encodings['embedded'].get(index, '')
        state = ('identical' if left == right else 'different') if left and right else 'not_available'
        rows.append(dict(index=index, suite=case['suite'], category=case['category'], filename=case['filename'],
                         baseline_ir_sha256=left, embedded_ir_sha256=right, encoding=state))
    counts = Counter(row['encoding'] for row in rows)
    directory = run / 'verification'
    directory.mkdir(exist_ok=True)
    with (directory / f'{phase}-native-ir.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    result = dict(phase=phase, expected=len(rows), counts=counts,
                  all_identical=counts['identical'] == len(rows),
                  manifest_sha256=digest(manifest.read_bytes()),
                  audit_script_sha256=digest(Path(__file__).read_bytes()),
                  executables={variant: runtime['executable_sha256'] for variant, runtime in plan['variants'].items()})
    (directory / f'{phase}-native-ir.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--phase', choices=('regression', 'full'), default='full')
    args = parser.parse_args()
    report = audit(args.run.resolve(), args.phase)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['all_identical'] else 1)
