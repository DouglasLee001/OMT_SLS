"""Common CLI; each application's actual constraints remain in its src/model.py."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .core import (FAMILIES, ROOT, REPO, application_fingerprint, audit_smt,
                   digest, exact_int, find_cases, json_text, load_family, manifest, support_dir, write_case)
from .solver import probe_case

DEFAULT_Z3 = REPO/'competitor/z3-5.1.0/build/z3'


def generate_specs(specs, out):
    seen, skipped, produced = {}, [], []
    existing = find_cases(out)
    timings = []
    for specification in specs:
        specification = dict(specification)
        family = specification.pop('family')
        module = load_family(family)
        profile = specification.pop('profile')
        started = time.monotonic()
        for case in module.make(profile, **specification):
            fingerprint = (family, application_fingerprint(family, case))
            encoding = case['data'].get('encoding','default')
            if encoding in seen.get(fingerprint,set()):
                skipped.append({'family':family,'label':case['label'],'reason':'same normalized application data'})
                continue
            if fingerprint in seen:
                case['metadata']['encoding_variant'] = True
            seen.setdefault(fingerprint,set()).add(encoding)
            app_id = family+'-'+fingerprint[1][:20]
            prior = [row for row in existing if row['application_instance_id']==app_id]
            label = re.sub(r'[^A-Za-z0-9_.-]+','-',case['label']).strip('-')
            matching = [row for row in prior if row['instance_id'].startswith(label+'--')]
            if matching:
                if matching[0].get('encoding_variant'):
                    case['metadata']['encoding_variant'] = True
                else:
                    case['metadata'].pop('encoding_variant',None)
            if prior and not any(row['instance_id'].startswith(label+'--') for row in prior):
                different_encoding = False
                new_encoding = case['data'].get('encoding')
                if new_encoding:
                    previous = [json.loads((Path(row['path'])/'data.json').read_text()).get('encoding') for row in prior]
                    different_encoding = all(old and old != new_encoding for old in previous)
                if not different_encoding:
                    skipped.append({'family':family,'label':case['label'],
                                    'reason':'same application already exists under another label',
                                    'existing_instance_id':prior[0]['instance_id']})
                    continue
                case['metadata']['encoding_variant'] = True
            produced.append(write_case(family, case, module, out))
        timings.append({'family':family,'profile':profile,'parameters':specification,
                        'elapsed_seconds':time.monotonic()-started})
    report = manifest(out, skipped)
    reports = support_dir(out)/'reports'
    reports.mkdir(parents=True,exist_ok=True)
    (reports/'generation.json').write_text(json_text({'runs':timings,
        'elapsed_seconds':sum(row['elapsed_seconds'] for row in timings)}))
    print(json.dumps({'generated_or_replayed':len(produced), 'manifest':str(support_dir(out)/'manifest.json'),
                      'families':report['families'], 'duplicates_skipped':len(skipped)}, ensure_ascii=False))
    return report


def check_solution(path, solution_path):
    metadata = json.loads((path/'metadata.json').read_text())
    data = json.loads((path/'data.json').read_text())
    candidate = json.loads(solution_path.read_text())
    decision = candidate.get('decision', candidate)
    checked = load_family(metadata['family']).check(data, decision)
    if 'cost' in candidate and exact_int(candidate['cost']) != exact_int(checked['cost']):
        raise ValueError('Claimed cost differs from independent application calculation')
    return {'status':'feasible_only','cost':str(checked['cost']),
            'family':metadata['family'],'instance_id':metadata['instance_id']}


def validate(out, case=None):
    cases = [case] if case else find_cases(out)
    results, failures = [], []
    for item in cases:
        path = Path(item['path'])
        try:
            metadata = json.loads((path/'metadata.json').read_text())
            data = json.loads((path/'data.json').read_text())
            witness = json.loads((path/'witness.json').read_text())
            module = load_family(metadata['family'])
            base = module.build(data).base()
            if base != (path/'base.smt2').read_text():
                raise ValueError('Regenerated base mismatch')
            audit_smt(base)
            if (path/'opt.smt2').read_text() != base+f'({metadata["objective"]["sense"]} cost)\n(check-sat)\n':
                raise ValueError('Optimization file differs from the base and single objective')
            for name, expected in metadata['output_sha256'].items():
                if digest((path/name).read_bytes()) != expected:
                    raise ValueError('Hash mismatch: '+name)
            state = 'not_available'
            if 'decision' in witness:
                check = module.check(data, witness['decision'])
                if exact_int(check['cost']) != exact_int(witness['cost']):
                    raise ValueError('Incorrect witness objective')
                state = 'passed'
            elif metadata['new_instance']:
                raise ValueError('New generated application is missing a checked witness')
            results.append({'instance_id':metadata['instance_id'], 'witness':state, 'theory':'passed'})
        except Exception as exc:
            failures.append({'path':str(path),'error':str(exc)})
    report = {'cases':len(cases), 'passed':len(results), 'failures':failures,
              'witnesses':sum(x['witness']=='passed' for x in results), 'results':results}
    if not case:
        reports = support_dir(out)/'reports'
        reports.mkdir(parents=True, exist_ok=True)
        (reports/'application_validation.json').write_text(json_text(report))
    print(json.dumps({k:v for k,v in report.items() if k!='results'}, ensure_ascii=False))
    return 1 if failures else 0


def probe(out, executable, seconds, workers, family=None, one=None, verify=False):
    if workers < 1:
        raise ValueError('workers must be positive')
    executable = Path(executable).expanduser().resolve()
    version = subprocess.run([str(executable),'-version'],capture_output=True,text=True,check=True).stdout.strip()
    cases = [one] if one else [c for c in find_cases(out) if not family or c['family']==family]
    if not cases:
        raise ValueError('No instances selected')
    # Import sequentially before workers start; custom file imports publish a
    # module in sys.modules before its body has finished executing.
    modules = {name:load_family(name) for name in sorted({case['family'] for case in cases})}
    started = time.monotonic()
    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(probe_case, Path(c['path']), executable, seconds,
                                   module=modules[c['family']],verify_candidate=verify):c for c in cases}
        for i, future in enumerate(as_completed(futures),1):
            item = futures[future]
            try:
                result = future.result()
                row = {'family':item['family'],'instance_id':item['instance_id'], **result}
                rows.append(row)
                if result['pinned_witness']['status'] not in ('sat','not_available'):
                    failures.append({'instance_id':item['instance_id'],'error':'Pinned witness was not SAT'})
                if result.get('optimization',{}).get('application_check') == 'failed':
                    failures.append({'instance_id':item['instance_id'],'error':'SMT/app objective mismatch'})
                if result['optimality']['status'] == 'expected_value_refuted':
                    failures.append({'instance_id':item['instance_id'],'error':'Expected optimum refuted'})
                if result['optimization']['status']=='error':
                    failures.append({'instance_id':item['instance_id'],'error':'Z3 frontend or invocation error'})
            except Exception as exc:
                failures.append({'instance_id':item['instance_id'],'error':str(exc)})
            if i%10 == 0 or i==len(cases):
                print(f'Z3 checked {i}/{len(cases)}',flush=True)
    rows.sort(key=lambda row:(row['family'],row['instance_id']))
    counts = Counter(row['optimization']['status'] for row in rows)
    summary = {'cases':len(cases),'reported':len(rows),'optimization_statuses':dict(counts),
               'pinned_witness_sat':sum(row['pinned_witness']['status']=='sat' for row in rows),
               'independently_checked_solver_models':sum(row['optimization'].get('application_check')=='passed' for row in rows),
               'optimal_solver_checked':sum(row['optimality']['status']=='optimal_solver_checked' for row in rows),
               'failures':failures}
    report = {'verified_utc':datetime.now(timezone.utc).isoformat(), 'solver':version,
              'executable':str(executable),'seconds_per_query':seconds,'workers':workers,
              'elapsed_seconds':time.monotonic()-started,'summary':summary,'results':rows}
    reports = support_dir(out)/'reports'
    reports.mkdir(parents=True,exist_ok=True)
    report_path = reports/('z3_verify_optimum.json' if one else 'z3_smoke.json')
    report_path.write_text(json_text(report))
    print(json.dumps({'report':str(report_path),**summary},ensure_ascii=False))
    return 1 if failures else 0


def selftest():
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT/'packing/src')
    tests = [ROOT/'packing/src/tests']+[ROOT/family/'src' for family in FAMILIES]
    success = True
    for test_dir in tests:
        print('Testing '+str(test_dir),flush=True)
        result = subprocess.run([sys.executable,'-m','unittest','discover','-s',str(test_dir),'-p','test_*.py','-v'],env=env)
        success = success and result.returncode==0
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Application OMT(NIA) benchmark generation and exact checking')
    sub = parser.add_subparsers(dest='command',required=True)
    generate = sub.add_parser('generate')
    generate.add_argument('family',choices=FAMILIES)
    generate.add_argument('--profile',required=True)
    generate.add_argument('--out',type=Path,default=ROOT)
    generate.add_argument('--cache',type=Path)
    generate.add_argument('--params',help='Additional JSON object, e.g. exact rational input')
    for key in ('ids','n','encoding'):
        generate.add_argument('--'+key)
    for key in ('assets','risk-factors','seed','real-tasks','resources','period','extra-edges',
                'items','max-side','dimension','candidates','runs'):
        generate.add_argument('--'+key,type=int)
    suite = sub.add_parser('suite')
    suite.add_argument('--config',type=Path,default=support_dir(ROOT)/'configs/smoke.json')
    suite.add_argument('--out',type=Path,default=ROOT)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--family',choices=FAMILIES,required=True)
    fetch.add_argument('--profile')
    fetch.add_argument('--ids')
    fetch.add_argument('--cache',type=Path)
    val = sub.add_parser('validate')
    val.add_argument('path',type=Path,nargs='?',default=ROOT)
    val.add_argument('--tier',choices=('witness','solver'),default='witness')
    pro = sub.add_parser('probe')
    pro.add_argument('--out',type=Path,default=ROOT)
    pro.add_argument('--family',choices=FAMILIES)
    verify = sub.add_parser('verify-optimum')
    verify.add_argument('path',type=Path)
    check = sub.add_parser('check-solution')
    check.add_argument('path',type=Path)
    check.add_argument('--solution',type=Path,required=True)
    for command in (val,pro,verify):
        command.add_argument('--z3',type=Path,default=DEFAULT_Z3)
        command.add_argument('--timeout',type=float,default=3)
        command.add_argument('--workers',type=int,default=6)
    sub.add_parser('selftest')
    args = parser.parse_args(argv)
    try:
        if args.command == 'generate':
            skip = {'command','family','profile','out','params'}
            params = {k:v for k,v in vars(args).items() if k not in skip and v is not None}
            if args.params:
                params.update(json.loads(args.params))
            params = {k:str(v) if isinstance(v,Path) else v for k,v in params.items()}
            generate_specs([{'family':args.family,'profile':args.profile,**params}],args.out)
        elif args.command == 'suite':
            config = json.loads(args.config.read_text())
            generate_specs(config['cases'],args.out)
        elif args.command == 'fetch':
            args.cache = args.cache or ROOT/args.family/'src/data/raw'
            module = load_family(args.family)
            if hasattr(module,'fetch'):
                records = module.fetch(cache=args.cache,ids=args.ids)
            elif args.family == 'cyclic':
                source = REPO/'papers/generate_benchmark/sources/cyclic-rcpsp'
                destination = args.cache/'cyclic'
                destination.mkdir(parents=True,exist_ok=True)
                selected = ['rcmsp.mzn']+([name+'.dzn' for name in args.ids.split(',')] if args.ids else [p.name for p in sorted(source.glob('*.dzn'))])
                for name in selected:
                    shutil.copyfile(source/name,destination/name)
                records = {'offline_source':str(source),'copied':selected}
            else:
                raise ValueError('This profile uses local parameters and needs no fetch')
            print(json.dumps(records,ensure_ascii=False,default=str))
        elif args.command == 'validate':
            one = {'path':str(args.path)} if (args.path/'metadata.json').is_file() else None
            if args.tier == 'witness':
                return validate(args.path,one)
            if one:
                one['family'] = json.loads((args.path/'metadata.json').read_text())['family']
                one['instance_id'] = args.path.name
            return probe(args.path,args.z3,args.timeout,args.workers,one=one)
        elif args.command == 'probe':
            return probe(args.out,args.z3,args.timeout,args.workers,family=args.family)
        elif args.command == 'verify-optimum':
            meta = json.loads((args.path/'metadata.json').read_text())
            one = {'path':str(args.path),'family':meta['family'],'instance_id':meta['instance_id']}
            return probe(args.path,args.z3,args.timeout,1,one=one,verify=True)
        elif args.command == 'check-solution':
            print(json.dumps(check_solution(args.path,args.solution),ensure_ascii=False))
        elif args.command == 'selftest':
            return selftest()
    except (ValueError,FileExistsError,FileNotFoundError,KeyError) as exc:
        parser.error(str(exc))
    return 0
